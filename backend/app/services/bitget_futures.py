import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FUTURES_SYMBOLS = [
    {"symbol": "BTCUSDT", "name": "BTC USDT 永续", "productType": "USDT-FUTURES"},
    {"symbol": "ETHUSDT", "name": "ETH USDT 永续", "productType": "USDT-FUTURES"},
    {"symbol": "SOLUSDT", "name": "SOL USDT 永续", "productType": "USDT-FUTURES"},
    {"symbol": "XRPUSDT", "name": "XRP USDT 永续", "productType": "USDT-FUTURES"},
    {"symbol": "DOGEUSDT", "name": "DOGE USDT 永续", "productType": "USDT-FUTURES"},
]


@dataclass
class BitgetFuturesConfig:
    base_url: str
    api_key: str | None
    api_secret: str | None
    passphrase: str | None
    dry_run: bool
    allow_live_trading: bool
    single_order_usdt_limit: float
    daily_order_usdt_limit: float
    max_open_symbols: int
    default_leverage: int
    margin_mode: str
    product_type: str
    margin_coin: str


class BitgetFuturesService:
    def __init__(self) -> None:
        self.config_path = Path(
            os.getenv(
                "BITGET_FUTURES_CONFIG_FILE",
                "/root/.openclaw/workspace/baige-no.1/backend/.bitget_futures_config.json",
            )
        )
        self.place_order_path = "/api/v2/mix/order/place-order"
        self.set_leverage_path = "/api/v2/mix/account/set-leverage"

    def get_config(self) -> BitgetFuturesConfig:
        saved = self._read_saved_config()
        return BitgetFuturesConfig(
            base_url=saved.get("base_url") or os.getenv("BITGET_FUTURES_BASE_URL", "https://api.bitget.com"),
            api_key=saved.get("api_key") or os.getenv("BITGET_FUTURES_API_KEY"),
            api_secret=saved.get("api_secret") or os.getenv("BITGET_FUTURES_API_SECRET"),
            passphrase=saved.get("passphrase") or os.getenv("BITGET_FUTURES_PASSPHRASE"),
            dry_run=self._bool_value(saved.get("dry_run"), self._env_bool("BITGET_FUTURES_DRY_RUN", True)),
            allow_live_trading=self._bool_value(
                saved.get("allow_live_trading"),
                self._env_bool("BITGET_FUTURES_ALLOW_LIVE_TRADING", False),
            ),
            single_order_usdt_limit=float(saved.get("single_order_usdt_limit") or 10),
            daily_order_usdt_limit=float(saved.get("daily_order_usdt_limit") or 50),
            max_open_symbols=int(saved.get("max_open_symbols") or 2),
            default_leverage=int(saved.get("default_leverage") or 5),
            margin_mode=saved.get("margin_mode") or "isolated",
            product_type=saved.get("product_type") or "USDT-FUTURES",
            margin_coin=saved.get("margin_coin") or "USDT",
        )

    def status(self) -> dict[str, Any]:
        cfg = self.get_config()
        return {
            "configured": bool(cfg.api_key and cfg.api_secret and cfg.passphrase),
            "dry_run": cfg.dry_run,
            "allow_live_trading": cfg.allow_live_trading,
            "single_order_usdt_limit": cfg.single_order_usdt_limit,
            "daily_order_usdt_limit": cfg.daily_order_usdt_limit,
            "max_open_symbols": cfg.max_open_symbols,
            "default_leverage": cfg.default_leverage,
            "margin_mode": cfg.margin_mode,
            "product_type": cfg.product_type,
            "margin_coin": cfg.margin_coin,
            "base_url": cfg.base_url,
            "api_key_hint": self._mask(cfg.api_key),
            "place_order_path": self.place_order_path,
            "set_leverage_path": self.set_leverage_path,
        }

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._read_saved_config()
        next_config = {
            **current,
            "base_url": payload.get("base_url") or current.get("base_url") or "https://api.bitget.com",
            "dry_run": self._bool_value(payload.get("dry_run"), True),
            "allow_live_trading": self._bool_value(payload.get("allow_live_trading"), False),
            "single_order_usdt_limit": float(payload.get("single_order_usdt_limit") or 10),
            "daily_order_usdt_limit": float(payload.get("daily_order_usdt_limit") or 50),
            "max_open_symbols": int(payload.get("max_open_symbols") or 2),
            "default_leverage": int(payload.get("default_leverage") or 5),
            "margin_mode": payload.get("margin_mode") or "isolated",
            "product_type": payload.get("product_type") or "USDT-FUTURES",
            "margin_coin": payload.get("margin_coin") or "USDT",
        }
        for field in ("api_key", "api_secret", "passphrase"):
            value = payload.get(field)
            if value:
                next_config[field] = value

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(next_config, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.config_path)
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass
        return self.status()

    def symbols(self) -> list[dict[str, str]]:
        return FUTURES_SYMBOLS

    def preview_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self.get_config()
        order = self._build_order_payload(cfg, payload)
        return {
            "dry_run": True,
            "will_submit": False,
            "request_path": self.place_order_path,
            "order": order,
            "risk": self._risk_summary(cfg, order, payload),
        }

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self.get_config()
        order = self._build_order_payload(cfg, payload)
        risk = self._risk_summary(cfg, order, payload)
        if cfg.dry_run or not cfg.allow_live_trading:
            return {
                "dry_run": True,
                "will_submit": False,
                "message": "合约实盘未开启，已返回下单预览。",
                "request_path": self.place_order_path,
                "order": order,
                "risk": risk,
            }
        self._ensure_configured(cfg)
        self._check_order_limit(cfg, payload)
        return {
            "dry_run": False,
            "will_submit": True,
            "request_path": self.place_order_path,
            "order": order,
            "risk": risk,
            "response": self._private_request("POST", self.place_order_path, order, cfg),
        }

    def set_leverage(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = self.get_config()
        body = {
            "symbol": str(payload.get("symbol") or "BTCUSDT").upper(),
            "productType": payload.get("productType") or cfg.product_type,
            "marginCoin": payload.get("marginCoin") or cfg.margin_coin,
            "leverage": str(payload.get("leverage") or cfg.default_leverage),
        }
        hold_side = payload.get("holdSide")
        if hold_side:
            body["holdSide"] = hold_side
        if cfg.dry_run or not cfg.allow_live_trading:
            return {
                "dry_run": True,
                "will_submit": False,
                "message": "合约实盘未开启，已返回杠杆设置预览。",
                "request_path": self.set_leverage_path,
                "request": body,
            }
        self._ensure_configured(cfg)
        return {
            "dry_run": False,
            "will_submit": True,
            "request_path": self.set_leverage_path,
            "request": body,
            "response": self._private_request("POST", self.set_leverage_path, body, cfg),
        }

    def _build_order_payload(self, cfg: BitgetFuturesConfig, payload: dict[str, Any]) -> dict[str, Any]:
        symbol = str(payload.get("symbol") or "BTCUSDT").upper()
        side = payload.get("side") or "buy"
        trade_side = payload.get("tradeSide") or "open"
        order_type = payload.get("orderType") or "market"
        size = str(payload.get("size") or "")
        if not size:
            raise ValueError("size is required")
        order = {
            "symbol": symbol,
            "productType": payload.get("productType") or cfg.product_type,
            "marginMode": payload.get("marginMode") or cfg.margin_mode,
            "marginCoin": payload.get("marginCoin") or cfg.margin_coin,
            "size": size,
            "side": side,
            "tradeSide": trade_side,
            "orderType": order_type,
            "clientOid": payload.get("clientOid") or f"baige-bitget-{int(time.time() * 1000)}",
        }
        price = payload.get("price")
        if order_type == "limit":
            if not price:
                raise ValueError("price is required for limit orders")
            order["price"] = str(price)
            order["force"] = payload.get("force") or "gtc"
        if payload.get("presetStopLossPrice"):
            order["presetStopLossPrice"] = str(payload["presetStopLossPrice"])
        if payload.get("presetStopSurplusPrice"):
            order["presetStopSurplusPrice"] = str(payload["presetStopSurplusPrice"])
        if payload.get("reduceOnly"):
            order["reduceOnly"] = payload["reduceOnly"]
        return order

    def _risk_summary(
        self,
        cfg: BitgetFuturesConfig,
        order: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        notional_usdt = float(payload.get("notional_usdt") or 0)
        leverage = int(payload.get("leverage") or cfg.default_leverage)
        return {
            "single_order_usdt_limit": cfg.single_order_usdt_limit,
            "daily_order_usdt_limit": cfg.daily_order_usdt_limit,
            "max_open_symbols": cfg.max_open_symbols,
            "notional_usdt": notional_usdt,
            "leverage": leverage,
            "over_single_limit": bool(notional_usdt and notional_usdt > cfg.single_order_usdt_limit),
            "direction": self._direction_label(order.get("side"), order.get("tradeSide")),
        }

    def _check_order_limit(self, cfg: BitgetFuturesConfig, payload: dict[str, Any]) -> None:
        notional_usdt = float(payload.get("notional_usdt") or 0)
        if notional_usdt and notional_usdt > cfg.single_order_usdt_limit:
            raise ValueError(f"single order limit exceeded: {notional_usdt} > {cfg.single_order_usdt_limit}")

    def _private_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        cfg: BitgetFuturesConfig,
    ) -> dict[str, Any]:
        body_text = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False) if body else ""
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(cfg.api_secret or "", timestamp, method, path, "", body_text)
        req = urllib.request.Request(
            cfg.base_url.rstrip("/") + path,
            data=body_text.encode("utf-8") if body_text else None,
            headers={
                "ACCESS-KEY": cfg.api_key or "",
                "ACCESS-SIGN": signature,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": cfg.passphrase or "",
                "Content-Type": "application/json",
                "locale": "zh-CN",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Bitget request failed HTTP {exc.code}: {raw}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    def _sign(
        self,
        secret: str,
        timestamp: str,
        method: str,
        path: str,
        query: str,
        body: str,
    ) -> str:
        if query:
            message = f"{timestamp}{method.upper()}{path}?{query}{body}"
        else:
            message = f"{timestamp}{method.upper()}{path}{body}"
        digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _ensure_configured(self, cfg: BitgetFuturesConfig) -> None:
        if not (cfg.api_key and cfg.api_secret and cfg.passphrase):
            raise ValueError("Bitget futures API is not configured")

    def _direction_label(self, side: str | None, trade_side: str | None) -> str:
        if side == "buy" and trade_side == "open":
            return "开多"
        if side == "sell" and trade_side == "open":
            return "开空"
        if side == "buy" and trade_side == "close":
            return "平多"
        if side == "sell" and trade_side == "close":
            return "平空"
        return f"{side or '-'} / {trade_side or '-'}"

    def _mask(self, value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 8:
            return "***"
        return f"{value[:4]}...{value[-4:]}"

    def _read_saved_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _bool_value(self, value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _env_bool(self, name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}


bitget_futures_service = BitgetFuturesService()
