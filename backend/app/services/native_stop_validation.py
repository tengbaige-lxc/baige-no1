"""Validate exchange-side coverage, not just the existence of an algo ID."""
import math


def native_stop_covers(row, symbol, direction, quantity, expected_trigger):
    try:
        size = float(row.get("sz") or 0)
        trigger = float(row.get("slTriggerPx") or 0)
        values = (size, trigger, quantity, expected_trigger)
        if any(not math.isfinite(v) or v <= 0 for v in values):
            return False
        if not str(row.get("algoClOrdId") or "").startswith("bg2sl"):
            return False
        if row.get("instId") != symbol or row.get("posSide") != direction.lower():
            return False
        if row.get("side") != ("sell" if direction == "LONG" else "buy"):
            return False
        if row.get("state") != "live" or str(row.get("slOrdPx")) != "-1":
            return False
        if str(row.get("reduceOnly")).lower() != "true":
            return False
        if size + 1e-10 < quantity:
            return False
        # A tighter profit-protection stop already provides at least this coverage.
        return trigger + 1e-10 >= expected_trigger if direction == "LONG" else trigger <= expected_trigger + 1e-10
    except (TypeError, ValueError, OverflowError):
        return False
