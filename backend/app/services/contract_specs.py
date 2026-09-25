"""Static OKX perpetual-contract face-value (ctVal) fallback table.

Single source of truth for the last-resort ctVal used when OKX's live
instrument spec can't be reached (see
`StrategyEngine._get_contract_value_live`, which always tries the real
OKX instrument list first and only falls back to `get_static_ct_val`
below on failure or an unlisted symbol).

Previously this table existed twice with different unlisted-coin
defaults (`monitor_service.CT_VALS` defaulted to 0.01, unused anywhere;
`strategy_engine._get_contract_value` defaulted to 1.0, the only one
actually consumed by live position sizing/PnL). This module keeps the
one with real behavioral history (1.0) as the single default.
"""

CT_VALS = {
    "BTC": 0.01,
    "ETH": 0.1,
    "SOL": 1.0,
    "DOGE": 1000.0,
    "XRP": 100.0,
    "TRX": 1000.0,
    "LTC": 1.0,
    "BCH": 0.1,
    "FIL": 0.1,
}

DEFAULT_CT_VAL = 1.0


def get_static_ct_val(symbol_or_coin: str) -> float:
    """Look up the static ctVal fallback for a bare coin or an OKX symbol like BTC-USDT-SWAP."""
    coin = (symbol_or_coin or "").split("-")[0].upper()
    return CT_VALS.get(coin, DEFAULT_CT_VAL)
