from app.services.strategy_engine import (
    StrategyEngine,
    cap_trend_runner_reduce_quantity,
    classify_transition_replacement,
    evaluate_extreme_volume_followthrough,
    filter_confirmed_klines,
    is_replaceable_transition_tail,
    is_tighter_profit_floor,
    resolve_global_entry_allocation,
    resolve_profit_lock_price,
    resolve_runner_add_stop_price,
    resolve_trailing_callback,
    resolve_trailing_profit_lock_ratio,
    resolve_trendline_break_signal,
    transition_slot_allows,
)


def candle(ts, open_, high, low, close, confirmed="1"):
    return [ts, open_, high, low, close, 1, 1, 1, confirmed]


def test_only_confirmed_candles_drive_exit_decisions():
    rows = [
        candle(1, 100, 101, 99, 100, "1"),
        candle(2, 100, 105, 95, 104, "0"),
        [3, 100, 101, 99, 100],
    ]
    assert filter_confirmed_klines(rows) == [rows[0], rows[2]]


def test_global_entry_allocation_is_position_ordered_and_bounded():
    steps = [0.40, 0.60, 0.80]
    assert resolve_global_entry_allocation(0, steps) == 0.40
    assert resolve_global_entry_allocation(2, steps) == 0.80
    assert resolve_global_entry_allocation(3, steps) is None
    assert resolve_global_entry_allocation(-1, steps) is None
    assert resolve_global_entry_allocation(0, [2.0]) == 1.0


def test_transition_slot_requires_a_stronger_candidate_after_primary_slots_fill():
    assert transition_slot_allows(2, 3, 5.0, 7.0)
    assert transition_slot_allows(3, 3, 7.0, 7.0)
    assert not transition_slot_allows(3, 3, 6.99, 7.0)


def test_only_profitable_small_residual_is_replaceable():
    assert is_replaceable_transition_tail(2, 5, 0.5, 1.0, 0.20)
    assert not is_replaceable_transition_tail(2.01, 5, 0.5, 1.0, 0.20)
    assert not is_replaceable_transition_tail(2, 5, 0.5, -0.01, 0.20)
    assert classify_transition_replacement(-1, False, "weak", "strong") == "losing"
    assert classify_transition_replacement(1, True, "weak", "strong") == "profitable_tail"
    assert classify_transition_replacement(-1, False, "strong", "strong") is None


def test_trailing_callback_and_profit_lock_tiers_follow_peak():
    trailing = {
        "callback": 0.38,
        "callback_tiers": [
            {"min_peak": 0.80, "callback": 0.30},
            {"min_peak": 1.50, "callback": 0.25},
        ],
    }
    assert resolve_trailing_callback(0.79, trailing) == 0.38
    assert resolve_trailing_callback(0.80, trailing) == 0.30
    assert resolve_trailing_callback(2.00, trailing) == 0.25
    assert resolve_trailing_profit_lock_ratio(0.79, {}) == 0.30
    assert resolve_trailing_profit_lock_ratio(0.80, {}) == 0.40
    assert resolve_trailing_profit_lock_ratio(1.50, {}) == 0.50


def test_profit_floor_uses_total_trade_pnl_and_only_tightens():
    long_floor = resolve_profit_lock_price("LONG", 100, 2, 1, 20, 100, 0.40)
    short_floor = resolve_profit_lock_price("SHORT", 100, 2, 1, 20, 100, 0.40)
    assert long_floor == 110
    assert short_floor == 90
    assert is_tighter_profit_floor("LONG", 110, 105)
    assert not is_tighter_profit_floor("LONG", 100, 105)
    assert is_tighter_profit_floor("SHORT", 90, 95)
    assert not is_tighter_profit_floor("SHORT", 100, 95)


def test_profit_reduction_never_consumes_protected_core():
    assert cap_trend_runner_reduce_quantity(10, 8, 4) == 6
    assert cap_trend_runner_reduce_quantity(4, 2, 4) == 0
    assert cap_trend_runner_reduce_quantity(10, -1, 4) == 0


def test_runner_add_stop_is_directional_and_capped():
    assert resolve_runner_add_stop_price("LONG", 100, 0.02) == 98
    assert resolve_runner_add_stop_price("SHORT", 100, 0.02) == 102
    assert resolve_runner_add_stop_price("LONG", 100, 0.50) == 90


def test_extreme_volume_followthrough_contract_for_both_directions():
    assert evaluate_extreme_volume_followthrough(
        "LONG", 100, 100, 103, 99, 102
    ) == "confirmed"
    assert evaluate_extreme_volume_followthrough(
        "LONG", 100, 100, 102, 97, 99
    ) == "failed"
    assert evaluate_extreme_volume_followthrough(
        "SHORT", 100, 100, 101, 97, 98
    ) == "confirmed"
    assert evaluate_extreme_volume_followthrough(
        "SHORT", 100, 103, 104, 99, 102
    ) == "failed"


def test_trendline_break_can_require_a_closed_candle():
    rows = [candle(i, 100, 101, 99, 100) for i in range(10)]
    rows.append(candle(10, 100, 101, 90, 90, "0"))
    assert resolve_trendline_break_signal(rows, True, 0.01, 90, False)
    assert not resolve_trendline_break_signal(rows, True, 0.01, 90, True)


def test_native_stop_converts_roe_distance_to_price_distance():
    engine = StrategyEngine()
    params = {
        "leverage": 20,
        "exit_factors": {
            "hard_stop": {"enabled": True, "metric": "roe", "stop_pct": 0.30}
        },
    }
    expected_pct = (0.30 / 20) * engine.NATIVE_STOP_MULTIPLIER
    assert engine._native_stop_trigger_price("LONG", 100, params) == 100 * (1 - expected_pct)
    assert engine._native_stop_trigger_price("SHORT", 100, params) == 100 * (1 + expected_pct)
    assert engine._native_stop_trigger_price(
        "LONG", 100, {"native_stop_enabled": False}
    ) is None


def test_native_stop_tick_rounding_never_moves_toward_the_position():
    assert StrategyEngine._format_stop_trigger_px(98.127, "0.05", "LONG") == "98.1"
    assert StrategyEngine._format_stop_trigger_px(101.873, "0.05", "SHORT") == "101.9"
