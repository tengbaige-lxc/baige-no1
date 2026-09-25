"""Durable signal-window helpers for asynchronous long/short pairing."""
from __future__ import annotations

import math


def update_candidate_cache(cache: dict, observations_by_pool: dict, *, now: float,
                           minimum_score: float, ttl_seconds: int,
                           max_per_side: int = 12) -> dict:
    normalized = {}
    for pool in observations_by_pool:
        source_pool = cache.get(pool) or {}
        normalized[pool] = {"LONG": {}, "SHORT": {}}
        for side in ("LONG", "SHORT"):
            for symbol, row in (source_pool.get(side) or {}).items():
                detected_at = float(row.get("detected_at") or 0)
                if 0 <= now - detected_at <= ttl_seconds:
                    normalized[pool][side][symbol] = dict(row)

    for pool, observations in observations_by_pool.items():
        target = normalized.setdefault(pool, {"LONG": {}, "SHORT": {}})
        for row in observations:
            side = row.get("direction")
            score = row.get("score")
            symbol = str(row.get("symbol") or "")
            if side not in {"LONG", "SHORT"} or not symbol or not row.get("eligible"):
                continue
            if not isinstance(score, (int, float)) or not math.isfinite(score) or score < minimum_score:
                continue
            detected_at = float(row.get("observed_ms") or now * 1000) / 1000
            target[side][symbol] = {
                "symbol": symbol,
                "direction": side,
                "score": float(score),
                "detected_at": detected_at,
                "original_reason": str(row.get("reason") or ""),
            }

    for pool in normalized.values():
        for side in ("LONG", "SHORT"):
            ranked = sorted(pool[side].values(),
                            key=lambda row: (-row["score"], -row["detected_at"], row["symbol"]))
            pool[side] = {row["symbol"]: row for row in ranked[:max_per_side]}
    return normalized


def revalidation_allows(reason: str, current_score: float, minimum_score: float) -> bool:
    if not isinstance(current_score, (int, float)) or not math.isfinite(current_score):
        return False
    normalized = str(reason or "").lower()
    hard_failures = (
        "reject",
        "opposes_direction",
        "structure_failed",
        "ma34_extension",
        "overextended",
        "insufficient_confirmed_trend_candles",
        "missing_",
        "not_aligned",
    )
    recognized = "rotation_score_only" in normalized or "await_5m_trigger" in normalized
    return (recognized and current_score >= minimum_score
            and not any(token in normalized for token in hard_failures))
