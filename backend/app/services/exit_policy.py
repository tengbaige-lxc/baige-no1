"""Pure exit-policy rules for Baige strategies.

This module has no database, exchange, or service lifecycle dependencies. It
is safe to reuse from live execution, backtests, and regression tests.
"""

from dataclasses import dataclass


def filter_confirmed_klines(klines: list) -> list:
    """Exclude an exchange's still-forming candle from exit decisions."""
    return [
        row for row in (klines or [])
        if len(row) <= 8 or str(row[8]) == "1"
    ]


def is_confirmed_third_sell_exit(
    base_state: dict | None,
    confirm_state: dict | None,
) -> bool:
    """Require third-sell structure on both timing and confirmation bars."""
    return bool(
        base_state
        and base_state.get("third_sell")
        and confirm_state
        and confirm_state.get("third_sell")
    )


def resolve_trailing_callback(peak_metric: float, trailing_config: dict) -> float:
    """Select the callback that applies to the current high-water mark."""
    fallback = float(trailing_config.get("callback", trailing_config.get("callback_pct", 0.30)))
    selected = fallback
    tiers = trailing_config.get("callback_tiers", [])
    if not isinstance(tiers, list):
        return selected
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        try:
            threshold = float(tier.get("min_peak", tier.get("activation", 0)))
            callback = float(tier["callback"])
        except (KeyError, TypeError, ValueError):
            continue
        if peak_metric >= threshold:
            selected = callback
    return max(0.0, min(1.0, selected))


def resolve_trailing_profit_lock_ratio(peak_metric: float, trailing_config: dict) -> float:
    """Return the share of whole-trade peak PnL protected after a trim."""
    fallback = float(trailing_config.get("profit_lock_ratio", 0.30))
    selected = fallback
    tiers = trailing_config.get("profit_lock_tiers") or [
        {"min_peak": 0.40, "lock_ratio": 0.30},
        {"min_peak": 0.80, "lock_ratio": 0.40},
        {"min_peak": 1.50, "lock_ratio": 0.50},
    ]
    if not isinstance(tiers, list):
        return max(0.0, min(1.0, selected))
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        try:
            threshold = float(tier.get("min_peak", tier.get("activation", 0)))
            lock_ratio = float(tier["lock_ratio"])
        except (KeyError, TypeError, ValueError):
            continue
        if peak_metric >= threshold:
            selected = lock_ratio
    return max(0.0, min(1.0, selected))


def resolve_profit_lock_price(
    direction: str,
    entry_price: float,
    remaining_quantity: float,
    contract_value: float,
    realized_pnl: float,
    peak_total_pnl: float,
    lock_ratio: float,
) -> float | None:
    """Translate a whole-trade profit floor into a residual-leg price."""
    try:
        entry = float(entry_price)
        quantity = float(remaining_quantity)
        ct_val = float(contract_value)
        realized = float(realized_pnl)
        peak = float(peak_total_pnl)
        ratio = float(lock_ratio)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or quantity <= 0 or ct_val <= 0 or peak <= 0:
        return None

    target_remaining_pnl = peak * max(0.0, min(1.0, ratio)) - realized
    price_delta = target_remaining_pnl / (quantity * ct_val)
    price = entry + price_delta if str(direction).upper() == "LONG" else entry - price_delta
    return price if price > 0 else None


def is_tighter_profit_floor(direction: str, proposed_price: float, current_price: float) -> bool:
    """A long floor only rises; a short floor only falls."""
    if proposed_price <= 0:
        return False
    if current_price <= 0:
        return True
    if str(direction).upper() == "LONG":
        return proposed_price > current_price
    return proposed_price < current_price


def cap_trend_runner_reduce_quantity(
    position_quantity: float,
    requested_quantity: float,
    core_quantity: float,
) -> float:
    """Only trim the runner; profit exits never sell the protected core."""
    try:
        position = abs(float(position_quantity))
        requested = max(0.0, float(requested_quantity))
        core = max(0.0, float(core_quantity))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(requested, max(0.0, position - core)))


def resolve_runner_add_stop_price(direction: str, entry_price: float, stop_pct: float) -> float:
    """Return the adverse-price stop for a continuation add-on."""
    entry = max(0.0, float(entry_price))
    distance = max(0.0, min(0.10, float(stop_pct)))
    if entry <= 0 or distance <= 0:
        return 0.0
    return entry * (1 - distance) if str(direction).upper() == "LONG" else entry * (1 + distance)


def evaluate_extreme_volume_followthrough(
    direction: str,
    entry_price: float,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    failure_tolerance: float = 0.003,
    max_rejection_wick_ratio: float = 0.45,
) -> str:
    """Classify the first closed 5m candle after an extreme-volume entry."""
    try:
        entry = float(entry_price)
        opened = float(candle_open)
        high = float(candle_high)
        low = float(candle_low)
        closed = float(candle_close)
    except (TypeError, ValueError):
        return "neutral"
    if min(entry, opened, high, low, closed) <= 0 or high <= low:
        return "neutral"
    tolerance = max(0.0, min(0.02, float(failure_tolerance)))
    wick_limit = max(0.0, min(1.0, float(max_rejection_wick_ratio)))
    candle_range = high - low
    side = str(direction).upper()
    if side == "LONG":
        rejection_wick = (high - max(opened, closed)) / candle_range
        if closed < entry * (1 - tolerance) or (
            closed < opened and closed < entry and rejection_wick >= wick_limit
        ):
            return "failed"
        if closed >= entry and closed >= opened and rejection_wick < wick_limit:
            return "confirmed"
    elif side == "SHORT":
        rejection_wick = (min(opened, closed) - low) / candle_range
        if closed > entry * (1 + tolerance) or (
            closed > opened and closed > entry and rejection_wick >= wick_limit
        ):
            return "failed"
        if closed <= entry and closed <= opened and rejection_wick < wick_limit:
            return "confirmed"
    return "neutral"


@dataclass
class TrailingPositionState:
    peak_metric: float = 0.0
    exit_taken: bool = False
    peak_total_pnl: float = 0.0
    last_exit_peak_metric: float = 0.0
    profit_floor_price: float = 0.0
    profit_floor_taken: bool = False
    profit_floor_peak_total_pnl: float = 0.0
    core_quantity: float = 0.0
    runner_add_quantity: float = 0.0
    runner_add_entry_price: float = 0.0
    runner_add_stop_price: float = 0.0
    runner_add_break_even_armed: bool = False
    runner_add_used: bool = False
    extreme_volume_ratio: float = 0.0
    extreme_event_candle_ts: str = ""
    extreme_event_status: str = ""


def resolve_trendline_break_signal(
    candles: list,
    is_long: bool,
    break_pct: float,
    mark_price: float,
    require_closed_candle: bool = False,
) -> bool:
    """Evaluate a structural break, optionally from the last closed candle."""
    if len(candles) < 10:
        return False

    evaluation_price = mark_price
    history = candles[-10:-1]
    if require_closed_candle:
        closed = [row for row in candles if len(row) > 8 and str(row[8]) == "1"]
        if len(closed) < 10:
            return False
        evaluation_price = float(closed[-1][4])
        history = closed[-10:-1]

    if is_long:
        recent_low = min(float(row[3]) for row in history)
        return evaluation_price < recent_low * (1 - break_pct)
    recent_high = max(float(row[2]) for row in history)
    return evaluation_price > recent_high * (1 + break_pct)
