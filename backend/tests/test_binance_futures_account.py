import asyncio
import hashlib
import hmac
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.trading import ExchangeConfigCreate
from app.services.binance_client import (
    BINANCE_FUTURES_TESTNET_URL,
    BinanceApiError,
    BinanceFuturesClient,
)
from app.services.exchange_accounts import (
    build_binance_account_summary,
    normalize_exchange,
)


def _query_text(request: httpx.Request) -> str:
    query = request.url.query
    return query.decode("ascii") if isinstance(query, bytes) else query


def test_signed_account_request_uses_futures_v3_and_exact_hmac():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json={
                "totalMarginBalance": "123.45",
                "availableBalance": "100.00",
                "positions": [],
            },
        )

    transport = httpx.MockTransport(handler)
    client = BinanceFuturesClient(
        client_factory=lambda base_url: httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
        ),
        timestamp_ms=lambda: 1_700_000_000_000,
    )

    account = asyncio.run(
        client.get_account("api-key", "api-secret", testnet=True)
    )

    request = seen["request"]
    assert str(request.url).startswith(
        f"{BINANCE_FUTURES_TESTNET_URL}/fapi/v3/account?"
    )
    assert request.headers["X-MBX-APIKEY"] == "api-key"
    unsigned, signature = _query_text(request).rsplit("&signature=", 1)
    expected = hmac.new(
        b"api-secret", unsigned.encode("ascii"), hashlib.sha256
    ).hexdigest()
    assert signature == expected
    assert parse_qs(unsigned) == {
        "recvWindow": ["5000"],
        "timestamp": ["1700000000000"],
    }
    assert account["totalMarginBalance"] == "123.45"


def test_position_mode_requires_explicit_binance_field():
    responses = iter(
        (
            httpx.Response(200, json={"dualSidePosition": True}),
            httpx.Response(200, json={}),
        )
    )
    transport = httpx.MockTransport(lambda _request: next(responses))
    client = BinanceFuturesClient(
        client_factory=lambda base_url: httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
        ),
        timestamp_ms=lambda: 1,
    )

    assert asyncio.run(client.get_position_mode("key", "secret")) == "hedge"
    with pytest.raises(BinanceApiError, match="position-mode"):
        asyncio.run(client.get_position_mode("key", "secret"))


def test_binance_api_error_is_fail_closed():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            401,
            json={"code": -2015, "msg": "Invalid API-key"},
        )
    )
    client = BinanceFuturesClient(
        client_factory=lambda base_url: httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
        ),
        timestamp_ms=lambda: 1,
    )

    with pytest.raises(BinanceApiError, match="-2015") as error:
        asyncio.run(client.get_account("key", "top-secret"))
    assert "top-secret" not in str(error.value)


def test_binance_account_summary_maps_hedge_and_one_way_positions():
    config = SimpleNamespace(
        id=7,
        name="Binance test",
        exchange="binance",
        is_active=True,
        is_testnet=True,
    )
    account = {
        "totalMarginBalance": "150.25",
        "availableBalance": "90.50",
        "positions": [
            {
                "symbol": "BTCUSDT",
                "positionSide": "LONG",
                "positionAmt": "0.01",
                "notional": "700.00",
                "initialMargin": "35.00",
                "unrealizedProfit": "5.25",
            },
            {
                "symbol": "ETHUSDT",
                "positionSide": "BOTH",
                "positionAmt": "-0.2",
                "notional": "-500.00",
                "initialMargin": "25.00",
                "unrealizedProfit": "-2.00",
            },
            {"symbol": "SOLUSDT", "positionSide": "LONG", "positionAmt": "0"},
        ],
    }

    summary = build_binance_account_summary(config, account, "hedge")

    assert summary["exchange"] == "binance"
    assert summary["execution_supported"] is False
    assert summary["position_mode"] == "hedge"
    assert summary["usdt_equity"] == 150.25
    assert summary["usdt_available"] == 90.5
    assert summary["position_count"] == 2
    assert summary["long_count"] == 1
    assert summary["short_count"] == 1
    assert summary["position_notional_usd"] == 1200.0
    assert summary["position_margin_usd"] == 60.0
    assert summary["unrealized_pnl"] == 3.25


def test_exchange_schema_normalizes_supported_values_and_rejects_others():
    assert normalize_exchange(" BINANCE ") == "binance"
    config = ExchangeConfigCreate(
        name="Binance",
        exchange="BINANCE",
        api_key="key",
        api_secret="secret",
    )
    assert config.exchange == "binance"
    assert config.is_testnet is False

    with pytest.raises(ValidationError, match="okx.*binance"):
        ExchangeConfigCreate(
            name="Unknown",
            exchange="kraken",
            api_key="key",
            api_secret="secret",
        )
