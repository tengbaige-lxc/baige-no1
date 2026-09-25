import asyncio
import base64
import copy
import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional
import httpx

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import os
from urllib.parse import urlencode

# 旧硬编码 AES 兜底钥。已随 git 历史公开（S3/S4）：拿到仓库 + 库文件即可离线解出
# 交易所 API 密钥明文。
_BURNED_EXCHANGE_KEY = "baige-no1-secret-key-32bytes!!"


def _load_exchange_key_secret() -> str:
    """Phase 3b（S3）：EXCHANGE_KEY_SECRET 必填、无兜底——缺失即启动失败。

    与 SECRET_KEY 不同，旧泄露值这里**放行但大声告警**：已有加密数据的旧部署
    必须先用旧值解密才能轮换，直接拒收会让服务器在重加密完成前无法启动。
    过渡窗口应尽快用 backend/rotate_exchange_key.py 收口（见 docs/07 手册）。
    """
    value = os.environ.get("EXCHANGE_KEY_SECRET", "")
    if not value:
        raise RuntimeError(
            "EXCHANGE_KEY_SECRET 未设置，拒绝启动（Phase 3b fail-closed）。"
            "它是交易所 API 密钥的 AES 加密钥。新部署："
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\" 生成；"
            "已有加密数据的旧部署：先设为原硬编码值保持可解密，"
            "再用 backend/rotate_exchange_key.py 轮换（docs/07 手册）"
        )
    if value == _BURNED_EXCHANGE_KEY:
        print(
            "⚠️ EXCHANGE_KEY_SECRET 仍是已随 git 历史泄露的旧值——仅可作迁移过渡。"
            "请尽快生成新钥并执行 backend/rotate_exchange_key.py 重加密（docs/07 手册）"
        )
    return value


_KEY_SECRET = _load_exchange_key_secret()
# OKX 模拟盘与实盘共用同一域名——环境差异不在 URL，而在签名请求头
# `x-simulated-trading: 1` + 模拟盘专用 API key（Phase 3f/S6，见 _get_auth_headers）。
# 旧的 OKX_DEMO_URL 常量（与 BASE 相同、全库零消费）就是这个误解的残留，已删。
OKX_BASE_URL = "https://www.okx.com"


class OkxTransientNetworkError(Exception):
    """Raised after retryable OKX network errors are exhausted."""


async def _retry_request(func, retries: int = 3, delay: float = 2.0):
    """通用重试包装，网络异常时自动重试"""
    last_err = None
    for i in range(retries):
        try:
            return await func()
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code if e.response is not None else 0
            if status_code not in (429,) and status_code < 500:
                raise
            last_err = e
            if i < retries - 1:
                retry_after = None
                if e.response is not None:
                    retry_after = e.response.headers.get("Retry-After")
                try:
                    sleep_for = float(retry_after) if retry_after else None
                except (TypeError, ValueError):
                    sleep_for = None
                if sleep_for is None:
                    multiplier = 2.0 if status_code == 429 else 1.0
                    sleep_for = delay * (i + 1) * multiplier
                await asyncio.sleep(sleep_for)
        except (httpx.TimeoutException, httpx.TransportError, OSError) as e:
            last_err = e
            if i < retries - 1:
                await asyncio.sleep(delay * (i + 1))
    raise OkxTransientNetworkError(str(last_err)) from last_err


class OkxApiError(Exception):
    """Raised when OKX returns an HTTP, top-level, or per-order error."""


def generate_cl_ord_id() -> str:
    """下单幂等键（OKX clOrdId：1-32 位字母数字）。bg2 前缀便于在交易所侧识别本系统订单。"""
    return "bg2" + secrets.token_hex(14)


NATIVE_STOP_ALGO_PREFIX = "bg2sl"


def generate_algo_cl_ord_id() -> str:
    """兜底止损条件单幂等键。bg2sl 前缀同时用于自愈清理时识别本系统挂的兜底单。"""
    return NATIVE_STOP_ALGO_PREFIX + secrets.token_hex(13)


def _get_key() -> bytes:
    return hashlib.sha256(_KEY_SECRET.encode()).digest()


def encrypt_text(plain: str) -> str:
    if not plain:
        return ""
    key = _get_key()
    cipher = AES.new(key, AES.MODE_CBC)
    ct_bytes = cipher.encrypt(pad(plain.encode(), AES.block_size))
    return base64.b64encode(cipher.iv + ct_bytes).decode()


def decrypt_text(encrypted: str) -> str:
    if not encrypted:
        return ""
    key = _get_key()
    data = base64.b64decode(encrypted.encode())
    iv = data[:16]
    ct = data[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    return unpad(cipher.decrypt(ct), AES.block_size).decode()


def generate_signature(timestamp: str, method: str, request_path: str, body: str, secret: str) -> str:
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(secret.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


class OkxClientManager:
    """OKX API client manager."""
    
    def __init__(self):
        self._public_client = httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=30)
        self._public_cache = {}
        self._rate_lock = asyncio.Lock()
        self._rate_windows: dict[str, deque[float]] = {}
        self._rate_cooldowns: dict[str, float] = {}
        self._rate_limits = {
            "market_ticker": (8, 2.0),
            "market_tickers": (8, 2.0),
            "market_candles": (20, 2.0),
            "market_books": (20, 2.0),
            "public_instruments": (8, 2.0),
            "account_balance": (8, 2.0),
            "account_positions": (8, 2.0),
            "account_positions_history": (8, 2.0),
            "private_default": (8, 2.0),
        }

    def _get_cached_public(self, key: tuple, max_age_seconds: int):
        cached = self._public_cache.get(key)
        if not cached:
            return None
        cached_at, value = cached
        if time.time() - cached_at > max_age_seconds:
            return None
        return copy.deepcopy(value)

    def _set_cached_public(self, key: tuple, value):
        self._public_cache[key] = (time.time(), copy.deepcopy(value))

    async def _throttle(self, rate_key: str) -> None:
        limit, window_seconds = self._rate_limits.get(
            rate_key,
            self._rate_limits["private_default"],
        )
        while True:
            async with self._rate_lock:
                now = time.monotonic()
                cooldown_until = self._rate_cooldowns.get(rate_key, 0.0)
                if now < cooldown_until:
                    sleep_for = cooldown_until - now
                else:
                    sleep_for = 0.0
                bucket = self._rate_windows.setdefault(rate_key, deque())
                while bucket and now - bucket[0] >= window_seconds:
                    bucket.popleft()
                if sleep_for <= 0 and len(bucket) < limit:
                    bucket.append(now)
                    return
                if sleep_for <= 0:
                    sleep_for = window_seconds - (now - bucket[0]) + 0.03
            await asyncio.sleep(max(0.03, sleep_for))

    def _parse_retry_after(self, retry_after: str | None, default_seconds: float = 5.0) -> float:
        try:
            seconds = float(retry_after) if retry_after else default_seconds
        except (TypeError, ValueError):
            seconds = default_seconds
        return max(1.0, min(seconds, 30.0))

    async def _note_rate_limited(self, rate_key: str, retry_after: str | None = None) -> None:
        sleep_for = self._parse_retry_after(retry_after)
        async with self._rate_lock:
            self._rate_cooldowns[rate_key] = max(
                self._rate_cooldowns.get(rate_key, 0.0),
                time.monotonic() + sleep_for,
            )

    def _private_rate_key(self, path: str) -> str:
        if path == "/api/v5/account/balance":
            return "account_balance"
        if path == "/api/v5/account/positions":
            return "account_positions"
        if path == "/api/v5/account/positions-history":
            return "account_positions_history"
        return "private_default"

    def _candles_cache_seconds(self, bar: str) -> int:
        normalized = (bar or "").lower()
        if normalized in {"1m", "3m"}:
            # A full white-dove pass scans 40 symbols. Twelve seconds is
            # shorter than a single pass, so the second strategy repeatedly
            # downloaded the same still-open minute candle.
            return 30
        if normalized in {"5m", "15m"}:
            return 60
        return 90

    async def aclose(self):
        await self._public_client.aclose()
    
    def _get_auth_headers(self, api_key: str, api_secret: str, passphrase: str, method: str, path: str, body: str = "", simulated: bool = False) -> dict:
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        signature = generate_signature(timestamp, method, path, body, api_secret)
        headers = {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
        }
        if simulated:
            # Phase 3f（S6）：OKX 模拟盘与实盘同域名，区别只有这个头 + 模拟盘专用
            # API key。此前 is_testnet 字段存在但从不进请求路径——模拟盘配置形同虚设，
            # Phase 2c 的原生止损（E5）也因此一直无法安全冒烟。
            headers["x-simulated-trading"] = "1"
        return headers

    def _raise_for_okx_error(self, data: dict, path: str) -> None:
        for item in data.get("data") or []:
            s_code = str(item.get("sCode", "0") or "0")
            if s_code != "0":
                s_msg = item.get("sMsg") or "order-level error"
                raise OkxApiError(f"OKX {path} failed: {s_msg} (sCode={s_code})")

        if data.get("code") != "0":
            raise OkxApiError(
                f"OKX {path} failed: {data.get('msg') or 'request failed'} "
                f"(code={data.get('code')})"
            )

    async def _private_json_request(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body_dict: dict | None = None,
        retries: int = 3,
        simulated: bool = False,
    ) -> dict:
        """Signed OKX private request with retry for transient network failures."""
        method = method.upper()
        body = json.dumps(body_dict) if body_dict is not None else ""
        query = urlencode(params or {})
        full_path = f"{path}?{query}" if query else path

        async def _do():
            await self._throttle(self._private_rate_key(path))
            headers = self._get_auth_headers(api_key, api_secret, passphrase, method, full_path, body, simulated=simulated)
            async with httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=30) as client:
                if method == "GET":
                    resp = await client.get(path, headers=headers, params=params)
                elif method == "POST":
                    resp = await client.post(path, headers=headers, content=body)
                else:
                    raise ValueError(f"Unsupported OKX method: {method}")
                if resp.status_code == 429:
                    await self._note_rate_limited(
                        self._private_rate_key(path),
                        resp.headers.get("Retry-After"),
                    )
                resp.raise_for_status()
                return resp.json()

        return await _retry_request(_do, retries=retries, delay=1.0)
    
    # === Public API (no auth needed) ===
    async def get_ticker(self, inst_id: str) -> dict:
        """Get ticker price."""
        cache_key = ("ticker", inst_id)
        cached = self._get_cached_public(cache_key, max_age_seconds=2)
        if cached:
            return cached

        async def _do():
            await self._throttle("market_ticker")
            resp = await self._public_client.get("/api/v5/market/ticker", params={"instId": inst_id})
            if resp.status_code == 429:
                await self._note_rate_limited("market_ticker", resp.headers.get("Retry-After"))
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return data["data"][0]
        try:
            ticker = await _retry_request(_do)
            self._set_cached_public(cache_key, ticker)
            return ticker
        except OkxTransientNetworkError:
            cached = self._get_cached_public(cache_key, max_age_seconds=60)
            if cached:
                cached["_cache_fallback"] = True
                return cached
            raise

    async def get_open_interest(self, inst_id: str) -> dict:
        """Get public perpetual open interest for one instrument."""
        cache_key = ("open_interest", inst_id)
        cached = self._get_cached_public(cache_key, max_age_seconds=15)
        if cached:
            return cached

        async def _do():
            await self._throttle("public_open_interest")
            resp = await self._public_client.get(
                "/api/v5/public/open-interest", params={"instType": "SWAP", "instId": inst_id}
            )
            if resp.status_code == 429:
                await self._note_rate_limited("public_open_interest", resp.headers.get("Retry-After"))
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0" or not data.get("data"):
                raise Exception(data.get("msg") or "open interest unavailable")
            return data["data"][0]

        value = await _retry_request(_do, retries=2, delay=0.5)
        self._set_cached_public(cache_key, value)
        return value

    async def get_funding_rate(self, inst_id: str) -> dict:
        """Get public current funding rate for one perpetual instrument."""
        cache_key = ("funding_rate", inst_id)
        cached = self._get_cached_public(cache_key, max_age_seconds=60)
        if cached:
            return cached

        async def _do():
            await self._throttle("public_funding_rate")
            resp = await self._public_client.get(
                "/api/v5/public/funding-rate", params={"instId": inst_id}
            )
            if resp.status_code == 429:
                await self._note_rate_limited("public_funding_rate", resp.headers.get("Retry-After"))
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0" or not data.get("data"):
                raise Exception(data.get("msg") or "funding rate unavailable")
            return data["data"][0]

        value = await _retry_request(_do, retries=2, delay=0.5)
        self._set_cached_public(cache_key, value)
        return value

    async def get_tickers(self, inst_type: str = "SWAP") -> list:
        """Get all tickers for an OKX instrument type."""
        inst_type = (inst_type or "SWAP").upper()
        cache_key = ("tickers", inst_type)
        cached = self._get_cached_public(cache_key, max_age_seconds=2)
        if cached:
            return cached

        async def _do():
            await self._throttle("market_tickers")
            resp = await self._public_client.get("/api/v5/market/tickers", params={"instType": inst_type})
            if resp.status_code == 429:
                await self._note_rate_limited("market_tickers", resp.headers.get("Retry-After"))
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return data.get("data", [])

        try:
            tickers = await _retry_request(_do)
            self._set_cached_public(cache_key, tickers)
            for ticker in tickers:
                inst_id = ticker.get("instId")
                if inst_id:
                    self._set_cached_public(("ticker", inst_id), ticker)
            return tickers
        except OkxTransientNetworkError:
            cached = self._get_cached_public(cache_key, max_age_seconds=60)
            if cached:
                return cached
            raise

    async def get_candles(self, inst_id: str, bar: str = "1H", limit: int = 100) -> list:
        """Get candlestick data in chronological order, oldest first."""
        cache_key = ("candles", inst_id, bar, limit)
        max_age_seconds = self._candles_cache_seconds(bar)
        cached = self._get_cached_public(cache_key, max_age_seconds=max_age_seconds)
        if cached:
            return cached

        # Factor consumers request different lookbacks for the same interval.
        # A fresh larger window contains the smaller one, so reuse its trailing
        # rows instead of issuing another identical REST call.
        for candidate_key in tuple(self._public_cache):
            if (
                len(candidate_key) != 4
                or candidate_key[0] != "candles"
                or candidate_key[1] != inst_id
                or candidate_key[2] != bar
            ):
                continue
            try:
                candidate_limit = int(candidate_key[3])
            except (TypeError, ValueError):
                continue
            if candidate_limit < limit:
                continue
            larger_window = self._get_cached_public(
                candidate_key, max_age_seconds=max_age_seconds
            )
            if larger_window and len(larger_window) >= limit:
                return larger_window[-limit:]

        async def _do():
            await self._throttle("market_candles")
            resp = await self._public_client.get("/api/v5/market/candles", params={
                "instId": inst_id, "bar": bar, "limit": str(limit)
            })
            if resp.status_code == 429:
                await self._note_rate_limited("market_candles", resp.headers.get("Retry-After"))
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return sorted(data["data"], key=lambda row: int(row[0]))
        try:
            candles = await _retry_request(_do)
            self._set_cached_public(cache_key, candles)
            return candles
        except OkxTransientNetworkError:
            cached = self._get_cached_public(cache_key, max_age_seconds=180)
            if cached:
                return cached
            raise

    async def get_orderbook(self, inst_id: str, sz: int = 5) -> dict:
        """Get public order book."""
        size = max(1, min(int(sz or 5), 400))
        cache_key = ("orderbook", inst_id, size)
        cached = self._get_cached_public(cache_key, max_age_seconds=1)
        if cached:
            return cached

        async def _do():
            await self._throttle("market_books")
            resp = await self._public_client.get(
                "/api/v5/market/books",
                params={"instId": inst_id, "sz": str(size)},
            )
            if resp.status_code == 429:
                await self._note_rate_limited("market_books", resp.headers.get("Retry-After"))
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return data["data"][0]

        try:
            book = await _retry_request(_do, retries=2, delay=0.5)
            self._set_cached_public(cache_key, book)
            return book
        except OkxTransientNetworkError:
            cached = self._get_cached_public(cache_key, max_age_seconds=5)
            if cached:
                cached["_cache_fallback"] = True
                return cached
            raise

    async def get_instruments(self, inst_type: str = "SWAP") -> list:
        """Get public OKX instrument definitions."""
        cache_key = ("instruments", inst_type)
        async def _do():
            await self._throttle("public_instruments")
            resp = await self._public_client.get("/api/v5/public/instruments", params={"instType": inst_type})
            if resp.status_code == 429:
                await self._note_rate_limited("public_instruments", resp.headers.get("Retry-After"))
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return data.get("data", [])
        try:
            instruments = await _retry_request(_do)
            self._set_cached_public(cache_key, instruments)
            return instruments
        except OkxTransientNetworkError:
            cached = self._get_cached_public(cache_key, max_age_seconds=86400)
            if cached:
                return cached
            raise
    
    # === Private API (auth needed) ===
    async def get_balance(self, api_key: str, api_secret: str, passphrase: str, simulated: bool = False) -> list:
        """Get account balance."""
        path = "/api/v5/account/balance"
        data = await self._private_json_request(api_key, api_secret, passphrase, "GET", path, simulated=simulated)
        if data.get("code") != "0":
            raise Exception(data.get("msg"))
        return data["data"][0].get("details", [])

    async def get_positions(self, api_key: str, api_secret: str, passphrase: str, simulated: bool = False) -> list:
        """Get current account positions."""
        path = "/api/v5/account/positions"
        data = await self._private_json_request(api_key, api_secret, passphrase, "GET", path, simulated=simulated)
        if data.get("code") != "0":
            raise Exception(data.get("msg"))
        return data.get("data", [])

    async def set_leverage(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        inst_id: str,
        leverage: int,
        margin_mode: str = "cross",
        pos_side: Optional[str] = None, simulated: bool = False) -> dict:
        """Set leverage for derivatives instruments before placing an order."""
        path = "/api/v5/account/set-leverage"
        body_dict = {
            "instId": inst_id,
            "lever": str(leverage),
            "mgnMode": margin_mode,
        }
        if pos_side:
            body_dict["posSide"] = pos_side.lower()

        data = await self._private_json_request(
            api_key,
            api_secret,
            passphrase,
            "POST",
            path,
            body_dict=body_dict, simulated=simulated)
        self._raise_for_okx_error(data, path)
        return data["data"][0] if data.get("data") else {}
    
    async def _lookup_order_after_lost_response(self, api_key: str, api_secret: str, passphrase: str,
                                                inst_id: str, cl_ord_id: str, simulated: bool = False) -> Optional[dict]:
        """按 clOrdId 查单，确认一笔“响应丢失”的下单是否已被交易所受理。

        返回订单详情 dict；交易所确认订单不存在时返回 None（此时才允许重试下单）；
        查询本身失败则向上抛错——无法确认时绝不允许调用方再次下单（宁可漏单不可重复开仓）。
        """
        path = "/api/v5/trade/order"
        try:
            data = await self._private_json_request(
                api_key,
                api_secret,
                passphrase,
                "GET",
                path,
                params={"instId": inst_id, "clOrdId": cl_ord_id},
                retries=2, simulated=simulated)
        except httpx.HTTPStatusError as e:
            # OKX 业务错误偶尔以 4xx + JSON body 返回；能解析出“订单不存在”就视为未受理
            try:
                body = e.response.json() if e.response is not None else {}
            except ValueError:
                body = {}
            if str(body.get("code", "")) == "51603":
                return None
            raise
        except OkxTransientNetworkError as e:
            raise OkxTransientNetworkError(
                f"下单响应丢失且按 clOrdId={cl_ord_id} 查单失败，为避免重复开仓不再自动重试: {e}"
            ) from e

        code = str(data.get("code", ""))
        if code == "0":
            orders = data.get("data") or []
            return orders[0] if orders else None
        if code == "51603":  # Order does not exist
            return None
        raise OkxApiError(f"OKX {path} 按 clOrdId 查询失败: {data.get('msg')} (code={code})")

    async def create_order(self, api_key: str, api_secret: str, passphrase: str,
                           inst_id: str, side: str, ord_type: str, sz: str,
                           px: str = None, td_mode: str = "cash", pos_side: str = None,
                           reduce_only: bool = False, cl_ord_id: str = None,
                           max_attempts: int = 3, retry_delay: float = 1.0, simulated: bool = False) -> dict:
        """Create a new order（幂等）。

        每笔订单强制携带 clOrdId。超时/5xx/429 等“响应可能丢失”的失败不再盲目重发：
        先按 clOrdId 查单——已受理则直接返回该订单；确认未受理才带同一 clOrdId 重试；
        查单也失败时直接报错（重复开仓的风险 > 漏单，漏单下一轮策略会重新评估）。
        """
        path = "/api/v5/trade/order"
        cl_ord_id = cl_ord_id or generate_cl_ord_id()
        body_dict = {
            "instId": inst_id,
            "tdMode": td_mode,      # cash (spot) | cross (margin) | isolate (contract)
            "side": side.lower(),    # buy | sell
            "ordType": ord_type.lower(),  # market | limit
            "sz": sz,
            "clOrdId": cl_ord_id,
        }
        if px and ord_type.upper() == "LIMIT":
            body_dict["px"] = px
        if pos_side:
            body_dict["posSide"] = pos_side.lower()
        if reduce_only:
            body_dict["reduceOnly"] = True

        print(f"[OKX下单] {body_dict}")
        for attempt in range(max_attempts):
            try:
                data = await self._private_json_request(
                    api_key,
                    api_secret,
                    passphrase,
                    "POST",
                    path,
                    body_dict=body_dict,
                    retries=1, simulated=simulated)
            except OkxTransientNetworkError:
                existing = await self._lookup_order_after_lost_response(
                    api_key, api_secret, passphrase, inst_id, cl_ord_id, simulated=simulated)
                if existing is not None:
                    print(f"[OKX下单] 响应丢失但订单已受理，按已有订单返回 clOrdId={cl_ord_id}")
                    return existing
                if attempt < max_attempts - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))
                    continue
                raise

            try:
                self._raise_for_okx_error(data, path)
            except OkxApiError:
                # 51016 = clOrdId 重复：说明先前某次“失败”的尝试实际已被受理
                codes = {str(item.get("sCode")) for item in (data.get("data") or [])}
                codes.add(str(data.get("code")))
                if "51016" in codes:
                    existing = await self._lookup_order_after_lost_response(
                        api_key, api_secret, passphrase, inst_id, cl_ord_id, simulated=simulated)
                    if existing is not None:
                        return existing
                raise
            return data["data"][0]
    
    async def get_algo_order(self, api_key: str, api_secret: str, passphrase: str,
                             algo_cl_ord_id: str, simulated: bool = False) -> Optional[dict]:
        """按 algoClOrdId 查条件单。确认不存在返回 None；查询失败上抛（调用方不得据此重发）。

        注：错误码按 OKX v5 文档 51603=订单不存在；若实盘验证发现差异需在此调整。
        """
        path = "/api/v5/trade/order-algo"
        data = await self._private_json_request(
            api_key,
            api_secret,
            passphrase,
            "GET",
            path,
            params={"algoClOrdId": algo_cl_ord_id, "ordType": "conditional"},
            retries=2, simulated=simulated)
        code = str(data.get("code", ""))
        if code == "0":
            orders = data.get("data") or []
            return orders[0] if orders else None
        if code == "51603":
            return None
        raise OkxApiError(f"OKX {path} 按 algoClOrdId 查询失败: {data.get('msg')} (code={code})")

    async def place_algo_stop_loss(self, api_key: str, api_secret: str, passphrase: str,
                                   inst_id: str, td_mode: str, side: str, sz: str,
                                   trigger_px: str, pos_side: str = None,
                                   reduce_only: bool = True, algo_cl_ord_id: str = None, simulated: bool = False) -> dict:
        """挂交易所侧条件止损单（触发后按市价平仓），软件轮询止损失效时的最后防线。

        与 create_order 相同的幂等纪律：强制 algoClOrdId，响应丢失先查单，
        已受理直接复用，查单失败宁可报错也不重发。
        """
        path = "/api/v5/trade/order-algo"
        algo_cl_ord_id = algo_cl_ord_id or generate_algo_cl_ord_id()
        body_dict = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side.lower(),
            "ordType": "conditional",
            "sz": sz,
            "slTriggerPx": trigger_px,
            "slOrdPx": "-1",  # 触发后市价执行
            "algoClOrdId": algo_cl_ord_id,
        }
        if pos_side:
            body_dict["posSide"] = pos_side.lower()
        if reduce_only:
            body_dict["reduceOnly"] = True

        print(f"[OKX兜底止损] {body_dict}")
        try:
            data = await self._private_json_request(
                api_key,
                api_secret,
                passphrase,
                "POST",
                path,
                body_dict=body_dict,
                retries=1, simulated=simulated)
        except OkxTransientNetworkError:
            existing = await self.get_algo_order(api_key, api_secret, passphrase, algo_cl_ord_id, simulated=simulated)
            if existing is not None:
                print(f"[OKX兜底止损] 响应丢失但挂单已受理 algoClOrdId={algo_cl_ord_id}")
                return existing
            raise
        self._raise_for_okx_error(data, path)
        return data["data"][0]

    async def get_pending_algo_stops(self, api_key: str, api_secret: str, passphrase: str,
                                     inst_id: str, simulated: bool = False) -> list:
        """列出该合约上未触发的条件单（用于兜底单自愈清理）。失败上抛。"""
        path = "/api/v5/trade/orders-algo-pending"
        data = await self._private_json_request(
            api_key,
            api_secret,
            passphrase,
            "GET",
            path,
            params={"ordType": "conditional", "instId": inst_id}, simulated=simulated)
        if data.get("code") != "0":
            raise OkxApiError(f"OKX {path} failed: {data.get('msg')} (code={data.get('code')})")
        return data.get("data", [])

    async def cancel_algo_orders(self, api_key: str, api_secret: str, passphrase: str,
                                 items: list, simulated: bool = False) -> list:
        """撤条件单。items: [{"algoId":..., "instId":...}]。

        撤单幂等（重复撤/已不存在只是逐项 sCode 报错），按项返回结果不整体抛错，
        调用方按需检查；传输层错误仍上抛。
        """
        if not items:
            return []
        path = "/api/v5/trade/cancel-algos"
        data = await self._private_json_request(
            api_key,
            api_secret,
            passphrase,
            "POST",
            path,
            body_dict=items, simulated=simulated)
        return data.get("data", [])

    async def cancel_order(self, api_key: str, api_secret: str, passphrase: str,
                           inst_id: str, ord_id: str, simulated: bool = False) -> dict:
        """Cancel an order."""
        path = "/api/v5/trade/cancel-order"
        body_dict = {"instId": inst_id, "ordId": ord_id}
        data = await self._private_json_request(
            api_key,
            api_secret,
            passphrase,
            "POST",
            path,
            body_dict=body_dict, simulated=simulated)
        self._raise_for_okx_error(data, path)
        return data["data"][0]
    
    async def get_orders(self, api_key: str, api_secret: str, passphrase: str,
                         inst_id: str = None, limit: int = 100, inst_type: str = "SWAP", simulated: bool = False) -> list:
        """Get order history."""
        path = "/api/v5/trade/orders-history"
        params = {"limit": str(limit), "instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        data = await self._private_json_request(
            api_key,
            api_secret,
            passphrase,
            "GET",
            path,
            params=params, simulated=simulated)
        if data.get("code") != "0":
            raise Exception(data.get("msg"))
        return data["data"]

    async def get_positions_history(self, api_key: str, api_secret: str, passphrase: str,
                                    inst_id: str = None, limit: int = 100, simulated: bool = False) -> list:
        """Get positions history (closed positions with PnL)."""
        path = "/api/v5/account/positions-history"
        params = {"limit": str(limit)}
        if inst_id:
            params["instId"] = inst_id
        data = await self._private_json_request(
            api_key,
            api_secret,
            passphrase,
            "GET",
            path,
            params=params, simulated=simulated)
        if data.get("code") != "0":
            raise Exception(data.get("msg"))
        return data["data"]



    async def get_order_detail(self, api_key: str, api_secret: str, passphrase: str,
                               inst_id: str, ord_id: str, simulated: bool = False) -> dict:
        """查询单个订单详情（获取成交价等）"""
        path = "/api/v5/trade/order"
        data = await self._private_json_request(
            api_key,
            api_secret,
            passphrase,
            "GET",
            path,
            params={"instId": inst_id, "ordId": ord_id}, simulated=simulated)
        if data.get("code") != "0":
            raise Exception(data.get("msg"))
        orders = data.get("data", [])
        return orders[0] if orders else {}


okx_manager = OkxClientManager()
