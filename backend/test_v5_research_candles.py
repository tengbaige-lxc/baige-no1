from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from v5_research_candles import collect_candles


NOW_MS = int(datetime(2026, 10, 12, tzinfo=timezone.utc).timestamp() * 1000)
BAR_MS = 4 * 3600_000


def _row(ts_ms, confirm="1"):
    return [str(ts_ms), "100", "102", "99", "101", "10", "1", "1000", confirm]


def test_collects_only_completed_and_paginates_older_with_after():
    start = NOW_MS - 4 * BAR_MS
    pages = {
        None: [_row(start + 3 * BAR_MS, "0"), _row(start + 2 * BAR_MS)],
        start + 2 * BAR_MS: [_row(start + BAR_MS), _row(start)],
        start: [],
    }
    calls = []

    def fetch(symbol, bar, after):
        calls.append(after)
        return pages[after]

    with TemporaryDirectory() as folder:
        output = Path(folder) / "candles.sqlite"
        report = collect_candles(
            output, days=2, symbols=("BTC-USDT-SWAP",), bars=("4H",),
            fetch_page=fetch, now_ms=NOW_MS, page_pause_seconds=0,
        )
        with closing(sqlite3.connect(output)) as db:
            first = db.execute("SELECT ts_ms FROM closed_candles ORDER BY ts_ms").fetchall()
        assert report["series"][0]["fetched"] == 3
        assert len(first) == 3
        assert calls == [None, start + 2 * BAR_MS, start]
        collect_candles(
            output, days=2, symbols=("BTC-USDT-SWAP",), bars=("4H",),
            fetch_page=fetch, now_ms=NOW_MS, page_pause_seconds=0,
        )
        with closing(sqlite3.connect(output)) as db:
            assert db.execute("SELECT COUNT(*) FROM closed_candles").fetchone()[0] == 3


def test_bad_page_does_not_create_database():
    with TemporaryDirectory() as folder:
        output = Path(folder) / "candles.sqlite"
        def fetch(symbol, bar, after):
            return [_row(NOW_MS - 2 * BAR_MS), _row(NOW_MS - BAR_MS)]
        try:
            collect_candles(
                output, days=2, symbols=("BTC-USDT-SWAP",), bars=("4H",),
                fetch_page=fetch, now_ms=NOW_MS, page_pause_seconds=0,
            )
        except ValueError as exc:
            assert "newest-first" in str(exc)
        else:
            raise AssertionError("unsorted page was accepted")
        assert not output.exists()


def test_rejects_future_confirmed_candle():
    with TemporaryDirectory() as folder:
        output = Path(folder) / "candles.sqlite"
        try:
            collect_candles(
                output, days=2, symbols=("BTC-USDT-SWAP",), bars=("4H",),
                fetch_page=lambda *_: [_row(NOW_MS)],
                now_ms=NOW_MS, page_pause_seconds=0,
            )
        except ValueError as exc:
            assert "unclosed" in str(exc)
        else:
            raise AssertionError("future confirmed candle was accepted")
        assert not output.exists()
