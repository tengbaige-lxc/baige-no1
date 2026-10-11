"""Read-only, point-in-time screening for proposed V5 entry factors.

This is a research diagnostic, not a fill-level backtest or a live factor gate.
Candidates remain outside the production factor library until separately reviewed.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3
from statistics import mean, median

from v5_cross_sectional import _directional_entry_allowed
from v5_research_archive import ARCHIVE_FILE


CANDIDATE = {
    "id": "direction_score_5m_timing_v1",
    "status": "research_only",
    "hypothesis": "Rank with 4H/30m direction score; require a separate 5m trigger to enter.",
    "required_features": (
        "long_trigger_5m", "short_trigger_5m",
        "long_trigger_score_5m", "short_trigger_score_5m",
    ),
}
MIN_CALENDAR_DAYS = 14
MIN_INDEPENDENT_SLOTS = 10
MIN_HOLDOUT_SLOTS = 4
MIN_CANDIDATE_TRADES = 8


def _empty(reason):
    return {
        "candidate": CANDIDATE, "stage": "collecting", "reason": reason,
        "review_recommended": False, "promotion_eligible": False,
        "auto_apply": False,
    }


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _signals(row, *, separate_timing):
    signals = {}
    for direction in ("LONG", "SHORT"):
        prefix = direction.lower()
        score = float(row[f"{prefix}_score"] or 0)
        if separate_timing:
            score -= float(row[f"{prefix}_trigger_score_5m"])
        signals[direction] = {
            "score": score,
            "trend_4h_aligned": bool(row[f"{prefix}_trend_4h_aligned"]),
            "structure_30m_aligned": bool(row[f"{prefix}_structure_30m_aligned"]),
            "adx_4h_strong": bool(row[f"{prefix}_adx_4h_strong"]),
        }
    return signals


def _ranked_choices(row, normalized_momentum, config, *, separate_timing):
    signals = _signals(row, separate_timing=separate_timing)
    score_spread = signals["LONG"]["score"] - signals["SHORT"]["score"]
    entry_row = {"signals": signals, "score_spread": score_spread}
    choices = []
    for direction, sign in (("LONG", 1), ("SHORT", -1)):
        if separate_timing and not bool(row[f"{direction.lower()}_trigger_5m"]):
            continue
        alpha = sign * (
            score_spread + float(config.get("momentum_weight", 0.5))
            * normalized_momentum
        )
        if alpha < float(config.get("minimum_alpha_spread", 1.0)):
            continue
        if _directional_entry_allowed(entry_row, direction, config):
            choices.append((direction, alpha))
    return choices


def _summary(slot_ids, pools_by_slot, picks, cost):
    bucket_counts = defaultdict(int)
    slot_returns = []
    for slot in slot_ids:
        values = []
        for pool in pools_by_slot[slot]:
            for direction in ("LONG", "SHORT"):
                pick = picks.get((slot, pool, direction))
                values.append(pick[1] - cost if pick else 0.0)
                if pick:
                    bucket_counts[f"{pool}:{direction}"] += 1
        slot_returns.append(mean(values))
    return {
        "independent_slots": len(slot_ids),
        "selected_legs": sum(bucket_counts.values()),
        "by_pool_direction": dict(sorted(bucket_counts.items())),
        "mean_slot_net_return": mean(slot_returns) if slot_returns else None,
        "positive_slot_share": (
            sum(value > 0 for value in slot_returns) / len(slot_returns)
            if slot_returns else None
        ),
    }


def factor_lab_report(state_dir, config, cost_bps_per_side=3.0):
    """Compare one preregistered candidate with today's gate on matched slots."""
    path = Path(state_dir) / ARCHIVE_FILE
    if not path.exists():
        return _empty("research_archive_missing")
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        columns = {row[1] for row in db.execute("PRAGMA table_info(signal_observations)")}
        if not set(CANDIDATE["required_features"]) <= columns:
            return _empty("five_minute_features_not_recorded")
        rows = db.execute(
            """
            SELECT o.*,
                   (SELECT f.price FROM signal_observations f
                    WHERE f.pool = o.pool AND f.symbol = o.symbol
                      AND f.scan_ts >= o.scan_ts + 86400
                      AND f.scan_ts <= o.scan_ts + 108000
                    ORDER BY f.scan_ts LIMIT 1) AS future_price
            FROM signal_observations o
            JOIN factor_lab_slots s
              ON s.scan_at = o.scan_at AND s.pool = o.pool
            WHERE o.price > 0 AND o.volatility_30m > 0
            ORDER BY o.scan_ts, o.pool, o.symbol
            """
        ).fetchall()

    grouped = defaultdict(list)
    for row in rows:
        required = (row[key] for key in CANDIDATE["required_features"])
        if any(value is None for value in required):
            continue
        bonuses = [
            _finite(row[f"{direction}_trigger_score_5m"])
            for direction in ("long", "short")
        ]
        if any(value is None or not 0 <= value <= 2 for value in bonuses):
            continue
        if any(
            bool(row[f"{direction}_trigger_5m"]) != (bonus > 0)
            for direction, bonus in zip(("long", "short"), bonuses)
        ):
            continue
        if any(_finite(row[key]) is None for key in (
            "future_price", "return_24h", "volatility_30m",
        )):
            continue
        if float(row["future_price"]) <= 0:
            continue
        grouped[(row["scan_at"], row["pool"])].append(row)
    if not grouped:
        return _empty("awaiting_complete_five_minute_and_24h_outcomes")

    baseline_picks = {}
    candidate_picks = {}
    pools_by_slot = defaultdict(set)
    for (slot, pool), values in grouped.items():
        pools_by_slot[slot].add(pool)
        center = median(float(row["return_24h"]) for row in values)
        for row in values:
            normalized = (float(row["return_24h"]) - center) / (
                float(row["volatility_30m"]) * math.sqrt(48)
            )
            normalized = max(-2.0, min(2.0, normalized))
            for picks, separate in ((baseline_picks, False), (candidate_picks, True)):
                for direction, alpha in _ranked_choices(
                    row, normalized, config, separate_timing=separate,
                ):
                    sign = 1 if direction == "LONG" else -1
                    outcome = sign * (
                        float(row["future_price"]) / float(row["price"]) - 1
                    )
                    key = (slot, pool, direction)
                    old = picks.get(key)
                    if old is None or (alpha, row["symbol"]) > (old[0], old[2]):
                        picks[key] = (alpha, outcome, row["symbol"])

    slots = sorted(pools_by_slot)
    cut = max(1, int(len(slots) * 0.6))
    holdout = slots[cut:]
    first_holdout_ts = (
        datetime.fromisoformat(holdout[0].replace("Z", "+00:00")).timestamp()
        if holdout else None
    )
    train = [slot for slot in slots[:cut] if first_holdout_ts is None or (
        first_holdout_ts - datetime.fromisoformat(
            slot.replace("Z", "+00:00")
        ).timestamp() >= 86400
    )]
    cost = max(0.0, float(cost_bps_per_side)) * 4 / 10000
    sections = {}
    for name, section_slots in (("train", train), ("holdout", holdout)):
        base = _summary(section_slots, pools_by_slot, baseline_picks, cost)
        candidate = _summary(section_slots, pools_by_slot, candidate_picks, cost)
        sections[name] = {
            "baseline": base, "candidate": candidate,
            "delta_mean_slot_net_return": (
                candidate["mean_slot_net_return"] - base["mean_slot_net_return"]
                if base["mean_slot_net_return"] is not None else None
            ),
        }
    span_days = (
        datetime.fromisoformat(slots[-1].replace("Z", "+00:00")).timestamp()
        - datetime.fromisoformat(slots[0].replace("Z", "+00:00")).timestamp()
    ) / 86400
    enough = (
        span_days >= MIN_CALENDAR_DAYS
        and len(slots) >= MIN_INDEPENDENT_SLOTS
        and len(holdout) >= MIN_HOLDOUT_SLOTS
        and sections["holdout"]["candidate"]["selected_legs"]
        >= MIN_CANDIDATE_TRADES
    )
    holdout_result = sections["holdout"]
    train_result = sections["train"]
    review_recommended = bool(
        enough
        and train_result["delta_mean_slot_net_return"] is not None
        and train_result["delta_mean_slot_net_return"] > 0
        and train_result["candidate"]["mean_slot_net_return"] > 0
        and holdout_result["delta_mean_slot_net_return"] is not None
        and holdout_result["delta_mean_slot_net_return"] > 0
        and holdout_result["candidate"]["mean_slot_net_return"] > 0
    )
    return {
        "candidate": CANDIDATE,
        "stage": (
            "review_candidate" if review_recommended
            else "screened_no_improvement" if enough else "collecting"
        ),
        "reason": (
            "requires_human_review_and_fill_level_replay"
            if review_recommended else "training_or_holdout_not_better_after_cost"
            if enough else "insufficient_independent_evidence"
        ),
        "calendar_span_days": round(span_days, 2),
        "complete_feature_rows": sum(map(len, grouped.values())),
        "independent_slots": len(slots),
        "stressed_round_trip_cost_fraction": cost,
        "split": "first_60_percent_train_last_40_percent_holdout_24h_embargo",
        "sections": sections,
        "requirements": {
            "calendar_days": MIN_CALENDAR_DAYS,
            "independent_slots": MIN_INDEPENDENT_SLOTS,
            "holdout_slots": MIN_HOLDOUT_SLOTS,
            "holdout_candidate_trades": MIN_CANDIDATE_TRADES,
        },
        "review_recommended": review_recommended,
        "limitations": [
            "Top-one-per-pool-direction screening, not the V5 portfolio execution path.",
            "24h mark prices omit native stops, funding and actual fill slippage.",
            "Only rebalance snapshots are archived; intraday trigger timing needs separate replay.",
            "Historical observations without 5m fields are excluded, not treated as no trigger.",
        ],
        "promotion_eligible": False,
        "auto_apply": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    report = factor_lab_report(
        args.state_dir, config["cross_sectional_shadow"],
        config["cross_sectional_shadow"].get("cost_bps_per_side", 3.0),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
