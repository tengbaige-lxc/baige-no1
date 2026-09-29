import asyncio
import hashlib
import hmac
import os
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlencode

import httpx

# Keep the historical single encryption-key import contract while account
# credentials continue to be encrypted by okx_client at the persistence edge.
# This module does not copy or hard-code the key and does not encrypt secrets.
from app.services.okx_client import _KEY_SECRET  # noqa: F401


BINANCE_FUTURES_BASE_URL = os.environ.get(
    "BINANCE_FUTURES_BASE_URL",
    "https://fapi.binance.com",
).rstrip("/")
BINANCE_FUTURES_TESTNET_URL = os.environ.get(
    "BINANCE_FUTURES_TESTNET_URL",
    "https://testnet.binancefuture.com",
).rstrip("/")


class BinanceApiError(Exception):
    """Raised when Binance rejects a request or returns an invalid payload."""


def generate_signature(query_string: str, api_secret: str) -> str:
    """Return the lowercase HMAC-SHA256 signature required by Binance."""
    return hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class BinanceFuturesClient:
    """Minimal async client for Binance USD-M futures.

    Account access stays separate from Baige V5 order execution. This provides
    signed credential validation and read-only visibility without silently
    enabling live Binance trading.
    """

    def __init__(
        self,
        *,
        client_factory: Callable[[str], httpx.AsyncClient] | None = None,
        timestamp_ms: Callable[[], int] | None = None,
        recv_window_ms: int = 5_000,
    ) -> None:
        self._client_factory = client_factory or (
            lambda base_url: httpx.AsyncClient(base_url=base_url, timeout=30)
        )
        self._timestamp_ms = timestamp_ms or (lambda: int(time.time() * 1000))
        self._recv_window_ms = recv_window_ms

    def _base_url(self, testnet: bool) -> str:
        return BINANCE_FUTURES_TESTNET_URL if testnet else BINANCE_FUTURES_BASE_URL

    def _signed_query(
        self,
        api_secret: str,
        params: Mapping[str, Any] | None = None,
    ) -> str:
        payload = list((params or {}).items())
        payload.extend(
            (
                ("recvWindow", self._recv_window_ms),
                ("timestamp", self._timestamp_ms()),
            )
        )
        query = urlencode(payload, doseq=True)
        return f"{query}&signature={generate_signature(query, api_secret)}"

    async def _signed_request(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        api_secret: str,
        params: Mapping[str, Any] | None = None,
        testnet: bool = False,
        retries: int = 3,
    ) -> Any:
        if not api_key or not api_secret:
            raise BinanceApiError("Binance API key and secret are required")

        headers = {"X-MBX-APIKEY": api_key}
        last_error: Exception | None = None

        for attempt in range(retries):
            try:
                # A retry receives a fresh timestamp so a slow timeout cannot
                # make the next signed request fail its recvWindow check.
                query = self._signed_query(api_secret, params)
                async with self._client_factory(self._base_url(testnet)) as client:
                    response = await client.request(
                        method.upper(),
                        f"{path}?{query}",
                        headers=headers,
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise BinanceApiError(
                        f"Binance {path} returned non-JSON HTTP {response.status_code}"
                    ) from exc

                if response.status_code >= 400:
                    code = data.get("code") if isinstance(data, dict) else None
                    message = data.get("msg") if isinstance(data, dict) else None
                    raise BinanceApiError(
                        f"Binance {path} failed: {message or 'request failed'} "
                        f"(code={code}, HTTP {response.status_code})"
                    )
                if isinstance(data, dict) and int(data.get("code", 0) or 0) < 0:
                    raise BinanceApiError(
                        f"Binance {path} failed: {data.get('msg') or 'request failed'} "
                        f"(code={data.get('code')})"
                    )
                return data
            except BinanceApiError:
                raise
            except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
                last_error = exc
                if attempt < retries - 1:
                    await asyncio.sleep(attempt + 1)

        raise BinanceApiError(f"Binance {path} network failure: {last_error}") from last_error

    async def get_account(
        self,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = False,
    ) -> dict:
        data = await self._signed_request(
            "GET",
            "/fapi/v3/account",
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        if not isinstance(data, dict):
            raise BinanceApiError("Binance account response is not an object")
        return data

    async def get_balances(
        self,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = False,
    ) -> list[dict]:
        data = await self._signed_request(
            "GET",
            "/fapi/v3/balance",
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        if not isinstance(data, list):
            raise BinanceApiError("Binance balance response is not a list")
        return data

    async def get_positions(
        self,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = False,
    ) -> list[dict]:
        data = await self._signed_request(
            "GET",
            "/fapi/v3/positionRisk",
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        if not isinstance(data, list):
            raise BinanceApiError("Binance position response is not a list")
        return data

    async def get_position_mode(
        self,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = False,
    ) -> str:
        data = await self._signed_request(
            "GET",
            "/fapi/v1/positionSide/dual",
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )
        if not isinstance(data, dict) or "dualSidePosition" not in data:
            raise BinanceApiError("Binance position-mode response is invalid")
        dual_side = data["dualSidePosition"]
        if isinstance(dual_side, str):
            dual_side = dual_side.lower() == "true"
        return "hedge" if bool(dual_side) else "one_way"

    async def validate_credentials(
        self,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = False,
    ) -> dict:
        account, position_mode = await asyncio.gather(
            self.get_account(api_key, api_secret, testnet=testnet),
            self.get_position_mode(api_key, api_secret, testnet=testnet),
        )
        return {"account": account, "position_mode": position_mode}


binance_futures_client = BinanceFuturesClient()

# Compatibility alias for code that imported the old manager name. The old
# implementation used Binance Spot endpoints and must not be used for V5.
binance_manager = binance_futures_client
