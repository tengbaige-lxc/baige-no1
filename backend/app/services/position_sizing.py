"""Pure position allocation and rotation rules for Baige strategies."""

import math


def resolve_global_entry_allocation(
    open_symbol_count: int,
    allocation_steps: list | tuple | None,
) -> float | None:
    """Return the share of available margin for the next global entry."""
    if not isinstance(open_symbol_count, int) or open_symbol_count < 0:
        return None
    if not isinstance(allocation_steps, (list, tuple)):
        return None
    try:
        steps = [max(0.01, min(1.0, float(step))) for step in allocation_steps]
    except (TypeError, ValueError):
        return None
    if open_symbol_count >= len(steps):
        return None
    return steps[open_symbol_count]


def transition_slot_allows(
    open_symbol_count: int,
    primary_symbol_limit: int,
    entry_score: float,
    minimum_score: float,
) -> bool:
    """Allow the buffer slot only for a materially stronger candidate."""
    try:
        count = int(open_symbol_count)
        primary_limit = int(primary_symbol_limit)
        score = float(entry_score)
        threshold = float(minimum_score)
    except (TypeError, ValueError):
        return False
    return count < primary_limit or score >= threshold


def is_replaceable_transition_tail(
    current_quantity: float,
    protected_core_quantity: float,
    core_ratio: float,
    unrealized_pnl: float,
    maximum_remaining_ratio: float = 0.20,
) -> bool:
    """A profitable residual can rotate out; a core or loser cannot."""
    try:
        current = abs(float(current_quantity))
        core = abs(float(protected_core_quantity))
        ratio = float(core_ratio)
        pnl = float(unrealized_pnl)
        maximum = float(maximum_remaining_ratio)
    except (TypeError, ValueError):
        return False
    if current <= 0 or core <= 0 or ratio <= 0 or pnl < 0:
        return False
    original_quantity = core / ratio
    return current / original_quantity <= max(0.0, min(1.0, maximum))


def classify_transition_replacement(
    unrealized_pnl: float,
    is_profitable_tail: bool,
    regime: str,
    strong_regime: str,
) -> str | None:
    """Prefer a weak losing position, then a weak profitable residual."""
    if str(regime or "").lower() in {"", "unknown", str(strong_regime or "").lower()}:
        return None
    try:
        pnl = float(unrealized_pnl)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pnl):
        return None
    if pnl < 0:
        return "losing"
    if is_profitable_tail:
        return "profitable_tail"
    return None


def rotation_score_gap(new_score, old_score, minimum=7.0, gap=1.5):
    try:
        values = [float(v) for v in (new_score, old_score, minimum, gap)]
    except (TypeError, ValueError):
        return False
    return (
        all(math.isfinite(v) for v in values)
        and values[0] >= values[2]
        and values[0] - values[1] >= values[3]
    )


def rotation_weak_closed_bars(rows, direction, now_ms):
    """Require two closed bars beyond MA34 with adverse MA5 slope."""
    if direction not in {"LONG", "SHORT"}:
        return False
    try:
        candles = sorted(
            {
                int(row[0]): row
                for row in rows
                if len(row) >= 9
                and str(row[8]) == "1"
                and int(row[0]) + 300000 <= now_ms
            }.values(),
            key=lambda row: int(row[0]),
        )
        if len(candles) < 35 or now_ms - (int(candles[-1][0]) + 300000) > 360000:
            return False
        if int(candles[-1][0]) - int(candles[-2][0]) != 300000:
            return False
        closes = [float(row[4]) for row in candles]
        if not all(math.isfinite(value) and value > 0 for value in closes):
            return False
        for end in (len(closes) - 1, len(closes)):
            last = closes[end - 1]
            ma34 = sum(closes[end - 34:end]) / 34
            ma5 = sum(closes[end - 5:end]) / 5
            previous = sum(closes[end - 6:end - 1]) / 5
            weak = (
                last < ma34 and ma5 < previous
                if direction == "LONG"
                else last > ma34 and ma5 > previous
            )
            if not weak:
                return False
        return True
    except (TypeError, ValueError, IndexError):
        return False


def resolve_directional_ma_extension(
    current_price: float,
    ma34: float,
    direction: str,
) -> float:
    """Return price extension past MA34 in the entry direction."""
    try:
        price = float(current_price)
        average = float(ma34)
    except (TypeError, ValueError):
        return 0.0
    if price <= 0 or average <= 0:
        return 0.0
    if str(direction).upper() == "LONG":
        return max(0.0, price / average - 1.0)
    return max(0.0, average / price - 1.0)
