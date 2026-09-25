import asyncio

import pytest

from app.services.okx_client import okx_manager
from app.services.trade_service import TradeService


def test_symbol_and_trading_mode_mapping_is_stable():
    service = TradeService()
    assert service._symbol_to_okx("BTCUSDT", "SWAP") == "BTC-USDT-SWAP"
    assert service._symbol_to_okx("BTC-USDT-SWAP", "SWAP") == "BTC-USDT-SWAP"
    assert service._symbol_to_okx("BTCUSDT", "SPOT") == "BTC-USDT"
    assert service._get_td_mode("SPOT", "cross") == "cash"
    assert service._get_td_mode("SWAP", "isolated") == "isolated"


def test_position_side_mapping_distinguishes_open_and_reduce():
    service = TradeService()
    assert service._get_pos_side("BUY", "SWAP") == "long"
    assert service._get_pos_side("SELL", "SWAP") == "short"
    assert service._get_pos_side("SELL", "SWAP", pos_side="LONG") == "long"
    assert service._get_pos_side("SELL", "SWAP", reduce_only=True) is None
    assert service._get_pos_side("BUY", "SPOT") is None


def test_open_quantity_rounds_down_without_exceeding_risk_budget(monkeypatch):
    async def instruments(_inst_type):
        return [{
            "instId": "TEST-USDT-SWAP",
            "lotSz": "0.1",
            "minSz": "0.1",
            "maxMktSz": "100",
        }]

    monkeypatch.setattr(okx_manager, "get_instruments", instruments)
    quantity = asyncio.run(
        TradeService()._normalize_order_size("TEST-USDT-SWAP", 1.29, "SWAP")
    )
    assert quantity == 1.2


def test_open_below_minimum_is_rejected_instead_of_silently_upsized(monkeypatch):
    async def instruments(_inst_type):
        return [{"instId": "TEST-USDT-SWAP", "lotSz": "1", "minSz": "1"}]

    monkeypatch.setattr(okx_manager, "get_instruments", instruments)
    with pytest.raises(ValueError, match="allow_min_size_bump"):
        asyncio.run(
            TradeService()._normalize_order_size("TEST-USDT-SWAP", 0.9, "SWAP")
        )


def test_reduce_below_minimum_can_still_close_a_residual(monkeypatch):
    async def instruments(_inst_type):
        return [{"instId": "TEST-USDT-SWAP", "lotSz": "1", "minSz": "1"}]

    monkeypatch.setattr(okx_manager, "get_instruments", instruments)
    quantity = asyncio.run(
        TradeService()._normalize_order_size(
            "TEST-USDT-SWAP", 0.4, "SWAP", reduce_only=True
        )
    )
    assert quantity == 1.0


def test_exchange_maximum_caps_order_quantity(monkeypatch):
    async def instruments(_inst_type):
        return [{
            "instId": "TEST-USDT-SWAP",
            "lotSz": "0.1",
            "minSz": "0.1",
            "maxMktSz": "5",
        }]

    monkeypatch.setattr(okx_manager, "get_instruments", instruments)
    quantity = asyncio.run(
        TradeService()._normalize_order_size("TEST-USDT-SWAP", 8, "SWAP")
    )
    assert quantity == 5.0
