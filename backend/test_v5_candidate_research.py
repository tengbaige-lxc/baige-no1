from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import math
import json
import subprocess
import sys

from v5_candidate_research import pair_event_report, research_pool_report
from v5_research_archive import ARCHIVE_FILE


def _archive(folder, *, move_future=False, missing_b=False, every_other=False):
    path = Path(folder) / ARCHIVE_FILE
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE signal_observations (scan_ts REAL, scan_at TEXT, "
            "pool TEXT, symbol TEXT, price REAL)"
        )
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for index in range(90):
            if every_other and index % 2:
                continue
            when = start + timedelta(hours=4 * index)
            ratio = 1 + 0.005 * math.sin(index)
            if index == 60:
                ratio = 0.90
            if move_future and index >= 66:
                ratio += 0.02
            for symbol, price in (
                ("BTC-USDT-SWAP", ratio * 100), ("ETH-USDT-SWAP", 100),
            ):
                if missing_b and symbol == "ETH-USDT-SWAP":
                    continue
                db.execute(
                    "INSERT INTO signal_observations VALUES (?, ?, ?, ?, ?)",
                    (when.timestamp(), when.isoformat(), "crypto", symbol, price),
                )
        db.commit()
    return path


def _candles(folder, *, gap=False):
    path = Path(folder) / "candles.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE closed_candles (
            symbol TEXT, bar TEXT, ts_ms INTEGER, open REAL, high REAL,
            low REAL, close REAL, volume_quote REAL,
            PRIMARY KEY (symbol, bar, ts_ms))""")
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        for index in range(120):
            if gap and index == 10:
                continue
            ts_ms = int((start + timedelta(hours=4 * index)).timestamp() * 1000)
            ratio = 0.9 if index == 60 else 1 + 0.005 * math.sin(index)
            open_a = 95 if index == 61 else 105 if index == 67 else ratio * 100
            for symbol, opened, closed in (
                ("BTC-USDT-SWAP", open_a, ratio * 100),
                ("ETH-USDT-SWAP", 100, 100),
            ):
                db.execute(
                    "INSERT INTO closed_candles VALUES (?, '4H', ?, ?, ?, ?, ?, ?)",
                    (symbol, ts_ms, opened, max(opened, closed),
                     min(opened, closed), closed, 1000),
                )
        db.commit()
    last_close_ms = int((start + timedelta(hours=4 * 120)).timestamp() * 1000)
    return path, last_close_ms


def test_missing_archive_fails_closed():
    with TemporaryDirectory() as folder:
        report = research_pool_report(folder)
    assert report["auto_apply"] is False
    assert report["candidates"]["pair"]["stage"] == "collecting"
    assert report["candidates"]["smc"]["review_recommended"] is False
    assert report["candidates"]["social"]["review_recommended"] is False


def test_pair_event_is_read_only_and_not_promoted():
    with TemporaryDirectory() as folder:
        path = _archive(folder)
        before = path.read_bytes()
        report = pair_event_report(folder)
        after = path.read_bytes()
    assert before == after
    assert report["price_snapshots"] == 90
    assert report["snapshot_coverage"] == 1.0
    assert report["train"]["events"] + report["holdout"]["events"] >= 1
    assert report["recent_events"][0]["direction_a"] == "LONG"
    assert report["auto_apply"] is False
    assert report["promotion_eligible"] is False


def test_pair_signal_does_not_use_future_price():
    with TemporaryDirectory() as original, TemporaryDirectory() as changed:
        _archive(original)
        _archive(changed, move_future=True)
        first = pair_event_report(original)["recent_events"][0]
        second = pair_event_report(changed)["recent_events"][0]
    assert first["scan_at"] == second["scan_at"]
    assert first["zscore"] == second["zscore"]
    assert first["gross_24h"] != second["gross_24h"]


def test_pair_requires_both_sides_and_valid_parameters():
    with TemporaryDirectory() as folder:
        _archive(folder, missing_b=True)
        report = pair_event_report(folder)
        assert report["reason"] == "paired_price_snapshots_missing"
        try:
            pair_event_report(folder, pair=("BTC-USDT-SWAP", "BTC-USDT-SWAP"))
        except ValueError:
            pass
        else:
            raise AssertionError("duplicate symbols were accepted")


def test_sparse_snapshots_fail_closed():
    with TemporaryDirectory() as folder:
        _archive(folder, every_other=True)
        report = pair_event_report(folder, lookback=10)
    assert report["snapshot_coverage"] < 0.8
    assert report["reason"] == "sparse_paired_observations"
    assert report["review_recommended"] is False


def test_cli_writes_only_report_file():
    with TemporaryDirectory() as folder:
        archive = _archive(folder)
        before = archive.read_bytes()
        output = Path(folder) / "reports" / "latest.json"
        script = Path(__file__).with_name("v5_candidate_research.py")
        run = subprocess.run(
            [sys.executable, str(script), "--state-dir", folder,
             "--output", str(output)],
            capture_output=True, text=True, check=True,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        assert json.loads(run.stdout) == report
        assert not output.with_name(output.name + ".tmp").exists()
        assert archive.read_bytes() == before


def test_cli_rejects_archive_output():
    with TemporaryDirectory() as folder:
        archive = _archive(folder)
        before = archive.read_bytes()
        script = Path(__file__).with_name("v5_candidate_research.py")
        run = subprocess.run(
            [sys.executable, str(script), "--state-dir", folder,
             "--output", str(archive)],
            capture_output=True, text=True,
        )
        assert run.returncode != 0
        assert "must not overwrite" in run.stderr
        assert archive.read_bytes() == before


def test_completed_candles_use_next_open_and_do_not_change_archive():
    with TemporaryDirectory() as folder:
        candle_db, last_close_ms = _candles(folder)
        before = candle_db.read_bytes()
        report = pair_event_report(
            folder, candle_db=candle_db, now_ms=last_close_ms + 3600_000,
        )
        assert candle_db.read_bytes() == before
    assert report["data_source"] == "okx_confirmed_4h_candles"
    assert report["snapshot_coverage"] == 1.0
    event = report["recent_events"][0]
    assert event["direction_a"] == "LONG"
    assert abs(event["gross_24h"] - 0.5 * (105 / 95 - 1)) < 1e-10
    assert report["auto_apply"] is False
    assert report["promotion_eligible"] is False


def test_candle_gap_and_staleness_fail_closed():
    with TemporaryDirectory() as folder:
        candle_db, last_close_ms = _candles(folder, gap=True)
        gap_report = pair_event_report(
            folder, candle_db=candle_db, now_ms=last_close_ms,
        )
        assert gap_report["train"]["events"] == 0
        assert gap_report["stage"] == "collecting"
        stale_report = pair_event_report(
            folder, candle_db=candle_db, now_ms=last_close_ms + 2 * 86_400_000,
        )
        assert stale_report["reason"] == "stale_closed_candles"
