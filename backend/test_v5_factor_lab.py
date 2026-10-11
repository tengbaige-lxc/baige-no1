from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from test_v5_research_archive import payload
from v5_factor_lab import factor_lab_report
from v5_research_archive import ARCHIVE_FILE, archive_cross_sectional_scan


CONFIG = {
    "factor_entry_gate_enabled": True,
    "directional_entry_score_min": 3.0,
    "directional_entry_score_max": 4.0,
    "minimum_directional_score_edge": 2.0,
    "require_trend_4h_aligned": True,
    "require_structure_or_adx": True,
    "short_require_strong_adx_4h": True,
    "minimum_alpha_spread": 1.0,
    "momentum_weight": 0.5,
}


def _run_series(folder, daily_change):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    price = 100.0
    for index in range(20):
        row = payload(
            (start + timedelta(days=index)).isoformat(), price,
            "LIVE_HOLD", f"2026-09-{index + 1:02d}T00",
        )
        row["cross_sectional_shadow_by_pool"]["crypto"]["legs"] = []
        for observation in row["observations_by_pool"]["crypto"]:
            is_long = observation["direction"] == "LONG"
            observation.update(
                score=4.5 if is_long else 1.0,
                trigger_5m=is_long,
                trigger_score_5m=1.0 if is_long else 0.0,
            )
        archive_cross_sectional_scan(folder, row)
        price *= 1 + daily_change
    return factor_lab_report(folder, CONFIG)


def test_lab_requires_new_features_and_never_auto_applies():
    with TemporaryDirectory() as folder:
        missing = factor_lab_report(folder, CONFIG)
        archive_cross_sectional_scan(
            folder, payload(datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat(), 100),
        )
        incomplete = factor_lab_report(folder, CONFIG)
    assert missing["stage"] == "collecting"
    assert incomplete["reason"] == "awaiting_complete_five_minute_and_24h_outcomes"
    assert incomplete["auto_apply"] is False


def test_two_week_positive_holdout_recommends_review_only():
    with TemporaryDirectory() as folder:
        report = _run_series(folder, 0.01)
    assert report["stage"] == "review_candidate"
    assert report["review_recommended"] is True
    assert report["promotion_eligible"] is False
    assert report["auto_apply"] is False
    assert report["independent_slots"] == 19
    assert report["sections"]["holdout"]["candidate"]["selected_legs"] == 8
    assert report["sections"]["holdout"]["delta_mean_slot_net_return"] > 0


def test_two_week_negative_holdout_is_not_recommended():
    with TemporaryDirectory() as folder:
        report = _run_series(folder, -0.01)
    assert report["stage"] == "screened_no_improvement"
    assert report["review_recommended"] is False
    assert report["auto_apply"] is False


def test_hold_slot_is_archived_once_without_rebalance():
    with TemporaryDirectory() as folder:
        row = payload(
            datetime(2026, 9, 1, tzinfo=timezone.utc).isoformat(),
            100.0, "LIVE_HOLD", "2026-09-01T00",
        )
        row["cross_sectional_shadow_by_pool"]["crypto"]["legs"] = []
        first = archive_cross_sectional_scan(folder, row)
        second = archive_cross_sectional_scan(folder, row)
        with closing(sqlite3.connect(Path(folder) / ARCHIVE_FILE)) as db:
            slots = db.execute("SELECT COUNT(*) FROM factor_lab_slots").fetchone()[0]
            observations = db.execute(
                "SELECT COUNT(*) FROM signal_observations"
            ).fetchone()[0]
            rebalances = db.execute(
                "SELECT COUNT(*) FROM shadow_rebalances"
            ).fetchone()[0]
    assert first["factor_lab_slots"] == 1
    assert second["factor_lab_slots"] == 0
    assert slots == 1
    assert observations == 1
    assert rebalances == 0
