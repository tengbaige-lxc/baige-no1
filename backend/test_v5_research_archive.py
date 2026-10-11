from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from v5_research_archive import (
    ARCHIVE_FILE,
    _connect,
    archive_cross_sectional_scan,
    research_summary,
)


def payload(completed, price, status="SHADOW_REBALANCE", target_slot=None):
    observation = {
        "pool": "crypto", "symbol": "AAA-USDT-SWAP", "price": price,
        "market": {"return_24h": 0.02, "volatility": 0.01,
                   "quote_volume_24h": 1_000_000},
        "trend_4h_aligned": True,
        "structure_30m_aligned": True,
        "adx_4h_strong": True,
        "adx_4h": 35.0,
    }
    return {
        "completed": completed,
        "observations_by_pool": {
            "crypto": [
                {**observation, "direction": "LONG", "score": 3.5},
                {**observation, "direction": "SHORT", "score": 1.0},
            ]
        },
        "cross_sectional_shadow_by_pool": {
            "crypto": {
                "status": status,
                "target_slot": target_slot,
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
                    "directional_score": 3.5, "opposite_score": 1.0,
                    "directional_score_edge": 2.5,
                    "normalized_momentum": 0.5,
                    "expected_edge_fraction": 0.02,
                    "volatility_30m": 0.01,
                    "liquidity_trailing": 1_000_000,
                    "trend_4h_aligned": True,
                    "structure_30m_aligned": True,
                    "adx_4h_strong": True,
                    "adx_4h": 35.0,
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
    assert summary["readiness"]["stage"] == "collecting"


def test_archive_preserves_separate_five_minute_timing_score():
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)
    row = payload(start.isoformat(), 100.0, "LIVE_HOLD", "2026-09-07T00")
    row["cross_sectional_shadow_by_pool"]["crypto"]["legs"] = []
    for observation in row["observations_by_pool"]["crypto"]:
        is_long = observation["direction"] == "LONG"
        observation["trigger_5m"] = is_long
        observation["trigger_score_5m"] = 1.0 if is_long else 0.0
        observation["timing_score_5m"] = 1.5 if is_long else 0.0
    with TemporaryDirectory() as folder:
        archive_cross_sectional_scan(folder, row)
        with closing(sqlite3.connect(Path(folder) / ARCHIVE_FILE)) as connection:
            stored = connection.execute(
                "SELECT long_trigger_score_5m, long_timing_score_5m, "
                "short_timing_score_5m FROM signal_observations"
            ).fetchone()
    assert stored == (1.0, 1.5, 0.0)


def test_existing_archive_adds_timing_columns_without_replacing_rows():
    with TemporaryDirectory() as folder:
        path = Path(folder) / ARCHIVE_FILE
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "CREATE TABLE signal_observations ("
                "scan_at TEXT, scan_ts REAL, pool TEXT, symbol TEXT, "
                "long_trigger_score_5m REAL, short_trigger_score_5m REAL)"
            )
            connection.execute(
                "INSERT INTO signal_observations VALUES "
                "('old', 1, 'crypto', 'AAA-USDT-SWAP', 1, 0)"
            )
            connection.commit()
        with closing(_connect(folder)) as connection:
            columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(signal_observations)"
            )}
            old = connection.execute(
                "SELECT long_trigger_score_5m, short_trigger_score_5m "
                "FROM signal_observations"
            ).fetchone()
    assert {"long_timing_score_5m", "short_timing_score_5m"} <= columns
    assert tuple(old) == (1.0, 0.0)


def test_live_crypto_rebalances_keep_both_daily_slots():
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)
    with TemporaryDirectory() as folder:
        first = archive_cross_sectional_scan(
            folder,
            payload(start.isoformat(), 100.0, "LIVE_REBALANCE", "2026-09-07T00"),
        )
        second = archive_cross_sectional_scan(
            folder,
            payload(
                (start + timedelta(hours=12)).isoformat(),
                105.0,
                "LIVE_REBALANCE",
                "2026-09-07T12",
            ),
        )
        summary = research_summary(folder)
    assert first["new_shadow_legs"] == 1
    assert second["new_shadow_legs"] == 1
    assert summary["rebalances"] == 2
    assert summary["legs"] == 2


def test_archive_tracks_6h_24h_72h_and_path_excursions():
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)
    with TemporaryDirectory() as folder:
        archive_cross_sectional_scan(folder, payload(start.isoformat(), 100.0))
        six = archive_cross_sectional_scan(
            folder,
            payload((start + timedelta(hours=7)).isoformat(), 110.0, "SHADOW_HOLD"),
        )
        day = archive_cross_sectional_scan(
            folder,
            payload((start + timedelta(hours=25)).isoformat(), 105.0, "SHADOW_HOLD"),
        )
        three_days = archive_cross_sectional_scan(
            folder,
            payload((start + timedelta(hours=73)).isoformat(), 120.0, "SHADOW_HOLD"),
        )
        with closing(sqlite3.connect(Path(folder) / ARCHIVE_FILE)) as connection:
            row = connection.execute(
                """
                SELECT outcome_6h, outcome_24h, outcome_72h,
                       max_favorable_72h, max_adverse_72h
                FROM shadow_legs
                """
            ).fetchone()
    assert (six["settled_6h_outcomes"], day["settled_24h_outcomes"],
            three_days["settled_72h_outcomes"]) == (1, 1, 1)
    assert all(abs(actual - expected) < 1e-12 for actual, expected in zip(
        row, (0.10, 0.05, 0.20, 0.20, 0.0)
    ))
