from tempfile import TemporaryDirectory
from datetime import datetime, timedelta, timezone

from v5_research_optimizer import factor_grid_report
from test_v5_research_archive import archive_cross_sectional_scan, payload


def test_optimizer_fails_closed_without_an_archive():
    with TemporaryDirectory() as folder:
        report = factor_grid_report(folder)
    assert report["stage"] == "collecting"
    assert report["auto_apply"] is False
    assert report["variants"] == {}


def test_optimizer_reads_rebalance_snapshots_but_does_not_auto_apply():
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)
    with TemporaryDirectory() as folder:
        archive_cross_sectional_scan(
            folder,
            payload(start.isoformat(), 100.0, "LIVE_REBALANCE", "2026-09-07T00"),
        )
        archive_cross_sectional_scan(
            folder,
            payload(
                (start + timedelta(hours=25)).isoformat(),
                110.0,
                "LIVE_REBALANCE",
                "2026-09-08T00",
            ),
        )
        report = factor_grid_report(folder)
    assert report["stage"] == "collecting"
    assert report["evidence_rows"] == 2
    assert report["variants"]["current"]["samples"] == 1
    assert report["variants"]["current"]["samples_by_pool_direction"] == {
        "crypto:LONG": 1
    }
    assert report["requirements"]["per_pool_direction"] == 20
    assert report["auto_apply"] is False
