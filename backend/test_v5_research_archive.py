from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from v5_research_archive import archive_cross_sectional_scan, research_summary


def payload(completed, price, status="SHADOW_REBALANCE"):
    observation = {
        "pool": "crypto", "symbol": "AAA-USDT-SWAP", "price": price,
        "market": {"return_24h": 0.02, "quote_volume_24h": 1_000_000},
    }
    return {
        "completed": completed,
        "observations_by_pool": {
            "crypto": [
                {**observation, "direction": "LONG", "score": 8.0},
                {**observation, "direction": "SHORT", "score": 3.0},
            ]
        },
        "cross_sectional_shadow_by_pool": {
            "crypto": {
                "status": status,
                "gross_weight": 1.0,
                "net_weight": 0.0,
                "estimated_one_way_turnover": 1.0,
                "estimated_cost_fraction": 0.0003,
                "liquidity_universe_month": "2026-09",
                "liquidity_universe": ["AAA-USDT-SWAP", "BBB-USDT-SWAP"],
                "legs": [{
                    "symbol": "AAA-USDT-SWAP", "direction": "LONG",
                    "risk_factor": "CRYPTO", "notional_weight": 0.5,
                    "price": price, "alpha": 5.0, "score_spread": 5.0,
                }],
            }
        },
    }


def test_archive_settles_first_available_24h_outcome():
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)
    with TemporaryDirectory() as folder:
        first = archive_cross_sectional_scan(folder, payload(start.isoformat(), 100.0))
        second = archive_cross_sectional_scan(
            folder,
            payload((start + timedelta(hours=25)).isoformat(), 110.0, "SHADOW_HOLD"),
        )
        summary = research_summary(folder)
    assert first["new_shadow_legs"] == 1
    assert second["settled_24h_outcomes"] == 1
    assert summary["rebalances"] == 1
    assert summary["settled_24h_legs"] == 1
    assert abs(summary["average_24h_directional_return"] - 0.1) < 1e-12
    assert summary["settled_24h_win_share"] == 1.0
