"""Collect public, completed OKX candles into an isolated research database."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen


CANDLE_FILE = "v5_candidate_candles.sqlite"
DEFAULT_SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
DEFAULT_BARS = ("4H", "30m", "5m")
BAR_MS = {"4H": 4 * 3600_000, "30m": 30 * 60_000, "5m": 5 * 60_000}
API_URL = "https://www.okx.com/api/v5/market/history-candles"
PAGE_SIZE = 300


def _fetch_page(symbol, bar, after=None):
    params = {"instId": symbol, "bar": bar, "limit": PAGE_SIZE}
    if after is not None:
        params["after"] = after
    url = API_URL + "?" + urlencode(params)
    for attempt in range(3):
        request = Request(url, headers={
            "User-Agent": "curl/8.0.1", "Accept": "application/json",
        })
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            time.sleep(2 ** attempt)
            continue
        if payload.get("code") == "0":
            data = payload.get("data")
            if not isinstance(data, list):
                raise ValueError("OKX candle data is not a list")
            return data
        if payload.get("code") != "50011" or attempt == 2:
            raise RuntimeError("OKX candle request failed: " + str(payload.get("code")))
        time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _parse_candle(raw, symbol, bar, now_ms, cutoff_ms):
    if not isinstance(raw, (list, tuple)) or len(raw) < 9:
        raise ValueError("incomplete OKX candle row")
    ts_ms = int(raw[0])
    if ts_ms % BAR_MS[bar]:
        raise ValueError("misaligned OKX candle timestamp")
    if raw[8] not in ("0", "1"):
        raise ValueError("unknown OKX candle confirmation state")
    if raw[8] == "0" or ts_ms < cutoff_ms:
        return None
    if ts_ms + BAR_MS[bar] > now_ms:
        raise ValueError("future or unclosed candle marked confirmed")
    prices = tuple(float(value) for value in raw[1:5])
    volume_quote = float(raw[7])
    if (not all(math.isfinite(value) and value > 0 for value in prices)
            or not math.isfinite(volume_quote) or volume_quote < 0):
        raise ValueError("invalid OKX candle price or volume")
    opened, high, low, closed = prices
    if high < max(opened, closed) or low > min(opened, closed) or high < low:
        raise ValueError("inconsistent OKX candle OHLC")
    return (symbol, bar, ts_ms, opened, high, low, closed, volume_quote)


def collect_candles(output, *, days=90, symbols=DEFAULT_SYMBOLS,
                    bars=DEFAULT_BARS, fetch_page=_fetch_page, now_ms=None,
                    page_pause_seconds=0.12):
    """Fetch before writing, so network or validation failure leaves DB untouched."""
    if days < 1 or not symbols or not bars or len(set(symbols)) != len(symbols):
        raise ValueError("invalid collection scope")
    if any(bar not in BAR_MS for bar in bars) or len(set(bars)) != len(bars):
        raise ValueError("unsupported or duplicate bar")
    if any(not symbol.endswith("-USDT-SWAP") for symbol in symbols):
        raise ValueError("research collector only accepts USDT swaps")
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    cutoff_ms = now_ms - days * 86_400_000
    collected = {}
    for symbol in symbols:
        for bar in bars:
            rows = {}
            after = None
            for _ in range(400):
                page = fetch_page(symbol, bar, after)
                if not page:
                    break
                timestamps = [int(row[0]) for row in page]
                if timestamps != sorted(timestamps, reverse=True):
                    raise ValueError("OKX candle page is not newest-first")
                oldest = timestamps[-1]
                if after is not None and oldest >= int(after):
                    raise ValueError("OKX candle pagination did not advance")
                for raw in page:
                    parsed = _parse_candle(raw, symbol, bar, now_ms, cutoff_ms)
                    if parsed is not None:
                        rows[parsed[2]] = parsed
                if oldest < cutoff_ms:
                    break
                after = oldest
                if page_pause_seconds:
                    time.sleep(page_pause_seconds)
            else:
                raise ValueError("OKX candle pagination exceeded page limit")
            collected[(symbol, bar)] = rows

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(output)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS closed_candles (
            symbol TEXT NOT NULL, bar TEXT NOT NULL, ts_ms INTEGER NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume_quote REAL NOT NULL,
            PRIMARY KEY (symbol, bar, ts_ms))""")
        for rows in collected.values():
            db.executemany(
                """INSERT OR REPLACE INTO closed_candles
                (symbol, bar, ts_ms, open, high, low, close, volume_quote)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows.values(),
            )
        db.commit()
    return {
        "generated_at": datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat(),
        "source": "okx_public_history_candles_confirmed_only",
        "database": str(output),
        "series": [
            {"symbol": symbol, "bar": bar, "fetched": len(rows),
             "first_ts_ms": min(rows) if rows else None,
             "last_ts_ms": max(rows) if rows else None}
            for (symbol, bar), rows in collected.items()
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--bars", nargs="+", default=DEFAULT_BARS)
    args = parser.parse_args()
    report = collect_candles(
        args.output, days=args.days, symbols=args.symbols, bars=args.bars,
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
