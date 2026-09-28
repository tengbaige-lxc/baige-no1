"""Read-only factor grid for V5 research evidence.

The report never edits config or submits orders.  It compares a small set of
explainable entry gates using rebalance snapshots and forward 24-hour prices.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
import math
import sqlite3
from statistics import median

from v5_research_archive import ARCHIVE_FILE


VARIANTS = {
    "current": {"score_min": 3.0, "score_max": 4.0, "edge": 2.0,
                "alpha": 1.0, "momentum_weight": 0.5, "structure_only": False},
    "wider_score_ceiling": {"score_min": 3.0, "score_max": 4.5, "edge": 2.0,
                            "alpha": 1.0, "momentum_weight": 0.5,
                            "structure_only": False},
    "higher_alpha": {"score_min": 3.0, "score_max": 4.0, "edge": 2.0,
                     "alpha": 1.5, "momentum_weight": 0.5,
                     "structure_only": False},
    "stronger_edge": {"score_min": 3.0, "score_max": 4.0, "edge": 2.5,
                      "alpha": 1.0, "momentum_weight": 0.5,
                      "structure_only": False},
    "structure_only": {"score_min": 3.0, "score_max": 4.0, "edge": 2.0,
                       "alpha": 1.0, "momentum_weight": 0.5,
                       "structure_only": True},
    "no_momentum_boost": {"score_min": 3.0, "score_max": 4.0, "edge": 2.0,
                          "alpha": 1.0, "momentum_weight": 0.0,
                          "structure_only": False},
}


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metrics(rows, stressed_cost_fraction):
    if not rows:
        return {"samples": 0, "independent_slots": 0, "net_average": None,
                "win_share": None, "gross_average": None,
                "samples_by_pool_direction": {}}
    gross = [row["outcome"] for row in rows]
    net = [value - stressed_cost_fraction for value in gross]
    buckets = defaultdict(int)
    for row in rows:
        buckets[f"{row['pool']}:{row['direction']}"] += 1
    return {
        "samples": len(rows),
        "independent_slots": len({row["slot"] for row in rows}),
        "gross_average": sum(gross) / len(gross),
        "net_average": sum(net) / len(net),
        "win_share": sum(value > 0 for value in net) / len(net),
        "samples_by_pool_direction": dict(sorted(buckets.items())),
    }


def factor_grid_report(state_dir, cost_bps_per_side=3.0):
    path = Path(state_dir) / ARCHIVE_FILE
    if not path.exists():
        return {"stage": "collecting", "reason": "research_archive_missing",
                "auto_apply": False, "variants": {}}
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT o.scan_at, o.scan_ts, o.pool, o.symbol, o.price,
                   o.long_score, o.short_score, o.return_24h,
                   o.volatility_30m, o.long_trend_4h_aligned,
                   o.short_trend_4h_aligned, o.long_structure_30m_aligned,
                   o.short_structure_30m_aligned, o.long_adx_4h_strong,
                   o.short_adx_4h_strong,
                   (SELECT f.price FROM signal_observations f
                    WHERE f.pool = o.pool AND f.symbol = o.symbol
                      AND f.scan_ts >= o.scan_ts + 86400
                      AND f.scan_ts <= o.scan_ts + 108000
                    ORDER BY f.scan_ts LIMIT 1) AS future_price
            FROM signal_observations o
            JOIN shadow_rebalances r
              ON r.rebalance_at = o.scan_at AND r.pool = o.pool
            WHERE o.price > 0 AND o.volatility_30m > 0
            ORDER BY o.scan_ts, o.pool, o.symbol
            """
        ).fetchall()

    grouped = defaultdict(list)
    for row in rows:
        future = _finite(row["future_price"])
        daily_return = _finite(row["return_24h"])
        volatility = _finite(row["volatility_30m"])
        if future is None or daily_return is None or volatility is None or volatility <= 0:
            continue
        grouped[(row["scan_at"], row["pool"])].append(row)

    evidence = []
    for (slot, pool), values in grouped.items():
        baseline = median(float(row["return_24h"]) for row in values)
        for row in values:
            normalized = (float(row["return_24h"]) - baseline) / (
                float(row["volatility_30m"]) * math.sqrt(48)
            )
            normalized = max(-2.0, min(2.0, normalized))
            score_spread = float(row["long_score"] or 0) - float(row["short_score"] or 0)
            for direction, sign in (("LONG", 1.0), ("SHORT", -1.0)):
                prefix = direction.lower()
                evidence.append({
                    "slot": f"{slot}|{pool}",
                    "pool": pool,
                    "direction": direction,
                    "score": float(row[f"{prefix}_score"] or 0),
                    "edge": sign * score_spread,
                    "score_spread": score_spread,
                    "normalized_momentum": normalized,
                    "trend": bool(row[f"{prefix}_trend_4h_aligned"]),
                    "structure": bool(row[f"{prefix}_structure_30m_aligned"]),
                    "adx": bool(row[f"{prefix}_adx_4h_strong"]),
                    "outcome": sign * (float(row["future_price"]) / float(row["price"]) - 1),
                })

    stressed_cost = max(0.0, float(cost_bps_per_side)) * 4 / 10000
    reports = {}
    for name, variant in VARIANTS.items():
        selected = []
        for row in evidence:
            directional_alpha = row["direction"] == "LONG"
            alpha = row["score_spread"] + (
                variant["momentum_weight"] * row["normalized_momentum"]
            )
            if not directional_alpha:
                alpha = -alpha
            confirmation = row["structure"] if variant["structure_only"] else (
                row["structure"] or row["adx"]
            )
            if (
                variant["score_min"] <= row["score"] <= variant["score_max"]
                and row["edge"] >= variant["edge"]
                and alpha >= variant["alpha"]
                and row["trend"]
                and confirmation
            ):
                selected.append(row)
        reports[name] = _metrics(selected, stressed_cost)

    baseline = reports["current"]
    span_days = 0.0
    if rows:
        span_days = (max(float(row["scan_ts"]) for row in rows) -
                     min(float(row["scan_ts"]) for row in rows)) / 86400
    expected_buckets = {
        f"{pool}:{direction}"
        for pool in {row["pool"] for row in evidence}
        for direction in ("LONG", "SHORT")
    }
    baseline_buckets = baseline["samples_by_pool_direction"]
    minimum_baseline_bucket = min(
        (baseline_buckets.get(bucket, 0) for bucket in expected_buckets),
        default=0,
    )
    ready = (
        span_days >= 30
        and baseline["samples"] >= 120
        and baseline["independent_slots"] >= 20
        and minimum_baseline_bucket >= 20
    )
    suggestions = []
    if ready and baseline["net_average"] is not None:
        hurdle = max(0.0, baseline["net_average"]) * 1.15
        for name, result in reports.items():
            if name == "current" or result["net_average"] is None:
                continue
            if (
                result["independent_slots"] >= 20
                and result["samples"] >= max(80, int(baseline["samples"] * 0.6))
                and min(
                    (result["samples_by_pool_direction"].get(bucket, 0)
                     for bucket in expected_buckets),
                    default=0,
                ) >= 20
                and result["net_average"] > hurdle
                and result["net_average"] > 0
            ):
                suggestions.append(name)
    return {
        "stage": "preliminary_analysis" if ready else "collecting",
        "calendar_span_days": round(max(0.0, span_days), 2),
        "stressed_round_trip_cost_fraction": stressed_cost,
        "evidence_rows": len(evidence),
        "variants": reports,
        "suggestions": suggestions,
        "auto_apply": False,
        "requirements": {"calendar_days": 30, "baseline_samples": 120,
                         "independent_slots": 20,
                         "per_pool_direction": 20},
    }
