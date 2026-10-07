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


def test_optimizer_compares_short_adx_gate_with_previous_entry_rule():
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)

    def short_payload(completed, price, slot):
        row = payload(completed, price, "LIVE_REBALANCE", slot)
        for observation in row["observations_by_pool"]["crypto"]:
            observation["score"] = (
                3.5 if observation["direction"] == "SHORT" else 0.0
            )
            observation["adx_4h_strong"] = False
        leg = row["cross_sectional_shadow_by_pool"]["crypto"]["legs"][0]
        leg.update(direction="SHORT", directional_score=3.5,
                   opposite_score=0.0, directional_score_edge=3.5,
                   score_spread=-3.5, adx_4h_strong=False)
        return row

    with TemporaryDirectory() as folder:
        archive_cross_sectional_scan(
            folder, short_payload(start.isoformat(), 100.0, "2026-09-07T00"),
        )
        archive_cross_sectional_scan(
            folder, short_payload(
                (start + timedelta(hours=25)).isoformat(), 110.0,
                "2026-09-08T00",
            ),
        )
        report = factor_grid_report(folder)
    assert report["variants"]["current"]["samples"] == 0
    assert report["variants"]["previous_short_structure_or_adx"]["samples"] == 1


def test_optimizer_rejects_short_against_opposite_30m_structure():
    start = datetime(2026, 9, 7, tzinfo=timezone.utc)

    def short_payload(completed, price, slot):
        row = payload(completed, price, "LIVE_REBALANCE", slot)
        for observation in row["observations_by_pool"]["crypto"]:
            observation["score"] = (
                3.5 if observation["direction"] == "SHORT" else 0.0
            )
            observation["structure_30m_aligned"] = (
                observation["direction"] == "LONG"
            )
        leg = row["cross_sectional_shadow_by_pool"]["crypto"]["legs"][0]
        leg.update(direction="SHORT", directional_score=3.5,
                   opposite_score=0.0, directional_score_edge=3.5,
                   score_spread=-3.5, structure_30m_aligned=False)
        return row

    with TemporaryDirectory() as folder:
        archive_cross_sectional_scan(
            folder, short_payload(start.isoformat(), 100.0, "2026-09-07T00"),
        )
        archive_cross_sectional_scan(
            folder, short_payload(
                (start + timedelta(hours=25)).isoformat(), 110.0,
                "2026-09-08T00",
            ),
        )
        report = factor_grid_report(folder)
    assert report["variants"]["current"]["samples"] == 0
    assert report["variants"]["previous_short_structure_or_adx"]["samples"] == 1
