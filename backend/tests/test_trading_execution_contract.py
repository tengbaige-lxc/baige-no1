import asyncio

from app.services.trading_execution import (
    NativeStopRequest,
    OpenPositionRequest,
    ReducePositionRequest,
    TradingExecutionGateway,
)


class RecordingOrderService:
    def __init__(self):
        self.calls = []

    async def place_order(self, db, user_id, account, order):
        self.calls.append((db, user_id, account, order))
        return order


def gateway_fixture():
    orders = RecordingOrderService()
    stop_calls = []

    async def refresh(account, symbol, close_side, params):
        stop_calls.append((account, symbol, close_side, params))
        return 2.5

    return TradingExecutionGateway(orders, refresh), orders, stop_calls


def test_open_position_maps_direction_without_exposing_exchange_schema():
    gateway, orders, _ = gateway_fixture()
    request = OpenPositionRequest(
        symbol="BTC-USDT-SWAP",
        direction="LONG",
        quantity=2,
        market_type="SWAP",
        margin_mode="cross",
        leverage=20,
        remark="strategy-501",
        allow_min_size_bump=True,
    )
    result = asyncio.run(gateway.open_position("db", 7, "account", request))
    assert result.side == "BUY"
    assert result.order_type == "MARKET"
    assert result.reduce_only is False
    assert result.allow_min_size_bump is True
    assert orders.calls[0][:3] == ("db", 7, "account")


def test_reduce_position_inverts_position_direction_and_is_reduce_only():
    gateway, _, _ = gateway_fixture()
    request = ReducePositionRequest(
        symbol="BTC-USDT-SWAP",
        direction="SHORT",
        quantity=1,
        market_type="SWAP",
        margin_mode="cross",
        leverage=20,
        pos_side="short",
        remark="hard stop",
    )
    result = asyncio.run(gateway.reduce_position("db", 7, "account", request))
    assert result.side == "BUY"
    assert result.reduce_only is True
    assert result.pos_side == "short"


def test_native_stop_refresh_uses_the_position_close_side():
    gateway, _, stop_calls = gateway_fixture()
    result = asyncio.run(gateway.refresh_native_stop(
        "account",
        NativeStopRequest("BTC-USDT-SWAP", "LONG", {"leverage": 20}),
    ))
    assert result == 2.5
    assert stop_calls == [
        ("account", "BTC-USDT-SWAP", "SELL", {"leverage": 20})
    ]
