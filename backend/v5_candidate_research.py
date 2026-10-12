"""Read-only preliminary screening for external V5 research candidates.

This is an event study, not a portfolio backtest or a trading signal source.
It never changes the archive, production configuration, or orders.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from statistics import mean, median, stdev

from v5_research_archive import ARCHIVE_FILE
from v5_research_candles import BAR_MS


DEFAULT_PAIR = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
BUCKET_SECONDS = 4 * 3600
HORIZON_SECONDS = 24 * 3600


def _pending(reason):
    return {
        "stage": "collecting", "reason": reason,
        "review_recommended": False, "promotion_eligible": False,
        "auto_apply": False,
    }


def _metrics(events):
    values = [event["net_stressed"] for event in events]
    return {
        "events": len(values),
        "mean_net_stressed": mean(values) if values else None,
        "median_net_stressed": median(values) if values else None,
        "win_share_stressed": (
            sum(value > 0 for value in values) / len(values) if values else None
        ),
        "worst_net_stressed": min(values) if values else None,
    }


def _paired_snapshots(connection, pool, symbol_a, symbol_b):
    rows = connection.execute(
        """
        SELECT a.scan_ts, a.scan_at, a.price, b.price
        FROM signal_observations AS a
        JOIN signal_observations AS b
          ON b.scan_at = a.scan_at AND b.pool = a.pool AND b.symbol = ?
        WHERE a.pool = ? AND a.symbol = ? AND a.price > 0 AND b.price > 0
        ORDER BY a.scan_ts
        """,
        (symbol_b, pool, symbol_a),
    )
    snapshots = []
    last_bucket = None
    for scan_ts, scan_at, price_a, price_b in rows:
        values = (scan_ts, price_a, price_b)
        if any(value is None or not math.isfinite(float(value)) for value in values):
            continue
        bucket = int(float(scan_ts) // BUCKET_SECONDS)
        if bucket == last_bucket:
            continue
        snapshots.append((float(scan_ts), str(scan_at), float(price_a), float(price_b)))
        last_bucket = bucket
    return snapshots


def _paired_candles(connection, symbol_a, symbol_b):
    rows = connection.execute(
        """SELECT a.ts_ms, a.open, a.close, b.open, b.close
        FROM closed_candles AS a
        JOIN closed_candles AS b ON b.ts_ms = a.ts_ms
          AND b.bar = a.bar AND b.symbol = ?
        WHERE a.symbol = ? AND a.bar = '4H'
        ORDER BY a.ts_ms""",
        (symbol_b, symbol_a),
    )
    return [tuple(row) for row in rows]


def _pair_events(snapshots, *, lookback, entry_z, cost_bps_per_side):
    """Select non-overlapping 24h events using only pre-signal ratio history."""
    times = [row[0] for row in snapshots]
    ratios = [row[2] / row[3] for row in snapshots]
    events = []
    last_signal_ts = float("-inf")
    stressed_round_trip_cost = 4 * cost_bps_per_side / 10000
    for index in range(lookback, len(snapshots)):
        now_ts, scan_at, price_a, price_b = snapshots[index]
        if now_ts - last_signal_ts < HORIZON_SECONDS:
            continue
        if any(
            snapshots[pos + 1][0] - snapshots[pos][0] > 2 * BUCKET_SECONDS
            for pos in range(index - lookback, index)
        ):
            continue
        history = ratios[index - lookback:index]
        deviation = stdev(history)
        if deviation <= 0:
            continue
        zscore = (ratios[index] - mean(history)) / deviation
        if abs(zscore) < entry_z:
            continue
        direction = 1 if zscore < 0 else -1
        future_index = bisect_left(times, now_ts + HORIZON_SECONDS, index + 1)
        if future_index >= len(snapshots):
            continue
        future_ts, _, future_a, future_b = snapshots[future_index]
        if future_ts > now_ts + HORIZON_SECONDS + BUCKET_SECONDS:
            continue
        last_signal_ts = now_ts
        gross = direction * 0.5 * (
            (future_a / price_a - 1) - (future_b / price_b - 1)
        )
        events.append({
            "scan_at": scan_at,
            "direction_a": "LONG" if direction == 1 else "SHORT",
            "zscore": zscore,
            "gross_24h": gross,
            "net_stressed": gross - stressed_round_trip_cost,
        })
    return events


def _candle_pair_events(candles, *, lookback, entry_z, cost_bps_per_side):
    """Signal at 4H close; model entry at next open and exit 24h later."""
    ratios = [row[2] / row[4] for row in candles]
    events = []
    last_exit_ms = -1
    stressed_round_trip_cost = 4 * cost_bps_per_side / 10000
    for index in range(lookback, len(candles) - 7):
        if any(
            candles[pos + 1][0] - candles[pos][0] != BAR_MS["4H"]
            for pos in range(index - lookback, index)
        ):
            continue
        history = ratios[index - lookback:index]
        deviation = stdev(history)
        if deviation <= 0:
            continue
        zscore = (ratios[index] - mean(history)) / deviation
        if abs(zscore) < entry_z:
            continue
        if any(
            candles[pos + 1][0] - candles[pos][0] != BAR_MS["4H"]
            for pos in range(index, index + 7)
        ):
            continue
        entry = candles[index + 1]
        exit_row = candles[index + 7]
        if entry[0] < last_exit_ms:
            continue
        direction = 1 if zscore < 0 else -1
        gross = direction * 0.5 * (
            (exit_row[1] / entry[1] - 1)
            - (exit_row[3] / entry[3] - 1)
        )
        last_exit_ms = exit_row[0]
        events.append({
            "scan_at": datetime.fromtimestamp(
                (candles[index][0] + BAR_MS["4H"]) / 1000,
                timezone.utc,
            ).isoformat(),
            "entry_at": datetime.fromtimestamp(entry[0] / 1000, timezone.utc).isoformat(),
            "direction_a": "LONG" if direction == 1 else "SHORT",
            "zscore": zscore,
            "gross_24h": gross,
            "net_stressed": gross - stressed_round_trip_cost,
        })
    return events


def pair_event_report(
    state_dir, *, pool="crypto", pair=DEFAULT_PAIR,
    lookback=60, entry_z=2.0, cost_bps_per_side=3.0,
    candle_db=None, now_ms=None,
):
    """Screen one preregistered pair; never infer a live hedge from this report."""
    if len(pair) != 2 or pair[0] == pair[1]:
        raise ValueError("pair must contain two distinct symbols")
    if lookback < 3 or entry_z <= 0 or cost_bps_per_side < 0:
        raise ValueError("invalid lookback, entry threshold, or cost")
    report = {
        "candidate": "price_ratio_pair_v1", "pool": pool,
        "pair": list(pair), "lookback_4h_buckets": lookback,
        "entry_z": entry_z, "horizon_hours": 24,
        "cost_bps_per_side": cost_bps_per_side,
        "review_recommended": False, "promotion_eligible": False,
        "auto_apply": False,
    }
    candle_path = Path(candle_db) if candle_db else None
    if candle_path and candle_path.exists():
        with closing(sqlite3.connect(candle_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            snapshots = _paired_candles(db, *pair)
        if not snapshots:
            return {**report, **_pending("paired_closed_candles_missing")}
        source = "okx_confirmed_4h_candles"
        events = _candle_pair_events(
            snapshots, lookback=lookback, entry_z=entry_z,
            cost_bps_per_side=cost_bps_per_side,
        )
        times = [row[0] / 1000 for row in snapshots]
        now_ms = int(now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000)
        freshness_hours = max(0.0, (now_ms - snapshots[-1][0] - BAR_MS["4H"]) / 3_600_000)
        fresh = freshness_hours <= 8
        minimum_coverage = 0.98
        limitations = [
            "Next 4H open entry and 24h open exit are price proxies, not fills.",
            "Equal-notional legs; cointegration and hedge ratio not tested.",
            "Funding, borrow costs, and market-impact slippage omitted.",
            "Cost is stressed to twice the configured two-sided round trip.",
        ]
    else:
        path = Path(state_dir) / ARCHIVE_FILE
        if not path.exists():
            return {**report, **_pending("research_archive_missing")}
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            snapshots = _paired_snapshots(db, pool, *pair)
        if not snapshots:
            return {**report, **_pending("paired_price_snapshots_missing")}
        source = "event_driven_scan_archive"
        events = _pair_events(
            snapshots, lookback=lookback, entry_z=entry_z,
            cost_bps_per_side=cost_bps_per_side,
        )
        times = [row[0] for row in snapshots]
        freshness_hours = None
        fresh = True
        minimum_coverage = 0.8
        limitations = [
            "Event-driven target-slot archive is not regular 4H candle history.",
            "Fixed 24h mark-to-mark study, not stateful pair execution.",
            "Equal-notional legs; cointegration and hedge ratio not tested.",
            "Scan prices are not fills; funding and borrow costs omitted.",
        ]
    expected_buckets = (
        int(times[-1] // BUCKET_SECONDS)
        - int(times[0] // BUCKET_SECONDS) + 1
    )
    coverage = len(snapshots) / expected_buckets
    max_gap_hours = max(
        ((right - left) / 3600 for left, right in zip(times, times[1:])),
        default=0.0,
    )
    cut = max(1, int(len(events) * 0.6))
    train, holdout = events[:cut], events[cut:]
    span_days = (times[-1] - times[0]) / 86400
    enough = (
        fresh and coverage >= minimum_coverage and span_days >= 14
        and len(train) >= 10 and len(holdout) >= 8
    )
    return {
        **report,
        "data_source": source,
        "stage": "exploratory_only" if enough else "collecting",
        "reason": (
            "cointegration_hedge_ratio_funding_and_fill_replay_unverified"
            if enough else "stale_closed_candles"
            if not fresh else "sparse_paired_observations"
            if coverage < minimum_coverage else "insufficient_nonoverlapping_pair_events"
        ),
        "price_snapshots": len(snapshots),
        "expected_4h_buckets": expected_buckets,
        "snapshot_coverage": round(coverage, 4),
        "max_gap_hours": round(max_gap_hours, 2),
        "freshness_hours": round(freshness_hours, 2) if freshness_hours is not None else None,
        "calendar_span_days": round(span_days, 2),
        "train": _metrics(train), "holdout": _metrics(holdout),
        "recent_events": events[-3:],
        "limitations": limitations,
    }


def research_pool_report(state_dir, **pair_options):
    """Keep missing external evidence visible instead of fabricating scores."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": {
            "pair": pair_event_report(state_dir, **pair_options),
            "smc": _pending("causal_smc_adapter_and_event_labels_missing"
                            if pair_options.get("candle_db") and Path(pair_options["candle_db"]).exists()
                            else "closed_candle_history_and_causal_adapter_missing"),
            "social": _pending("timestamped_licensed_social_feed_missing"),
        },
        "auto_apply": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--pool", default="crypto")
    parser.add_argument("--pair", nargs=2, default=DEFAULT_PAIR)
    parser.add_argument("--lookback", type=int, default=60)
    parser.add_argument("--entry-z", type=float, default=2.0)
    parser.add_argument("--cost-bps-per-side", type=float, default=3.0)
    parser.add_argument("--candle-db", help="Optional independent closed-candle research DB")
    parser.add_argument("--output", help="Optional JSON report path; never the archive")
    args = parser.parse_args()
    report = research_pool_report(
        args.state_dir, pool=args.pool, pair=args.pair,
        lookback=args.lookback, entry_z=args.entry_z,
        cost_bps_per_side=args.cost_bps_per_side, candle_db=args.candle_db,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        if output.resolve() in {
            (Path(args.state_dir) / ARCHIVE_FILE).resolve(),
            Path(args.candle_db).resolve() if args.candle_db else None,
        }:
            parser.error("report output must not overwrite a research database")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(output)
    print(rendered, end="")


if __name__ == "__main__":
    main()
