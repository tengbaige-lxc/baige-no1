from v5_execution import (
    V5ExecutionManager,
    blocks_reentry_after_exit,
    cross_sectional_selected_by_pool,
    dynamic_exposure_plan,
    execution_params,
    factor_margin_remaining,
    loss_bounded_margin_cap,
    prioritize_factor_recovery,
    restrict_legs_for_exposure_recovery,
    select_legs,
    select_pairs,
)


def leg(symbol, direction, quality):
    return {"symbol": symbol, "direction": direction, "quality": quality, "score": 6.5}


def test_select_pairs_merges_pools_and_limits_account_book():
    selected = {
        "crypto": {"LONG": [leg("BTC", "LONG", 9), leg("ETH", "LONG", 8)],
                   "SHORT": [leg("DOGE", "SHORT", 8), leg("XRP", "SHORT", 7)]},
        "tradfi": {"LONG": [leg("NVDA", "LONG", 10), leg("AMD", "LONG", 7)],
                   "SHORT": [leg("AAOI", "SHORT", 9), leg("ORCL", "SHORT", 6)]},
    }
    pairs = select_pairs(selected, 3)
    assert len(pairs) == 3
    assert pairs[0]["LONG"]["symbol"] == "NVDA"
    assert len({p["LONG"]["symbol"] for p in pairs}) == 3
    assert len({p["SHORT"]["symbol"] for p in pairs}) == 3


def test_execution_params_always_has_hard_and_native_stop():
    cfg = {"requested_leverage": 30, "minimum_leg_margin_usdt": 2,
           "hard_stop_price_pct": 0.015, "native_stop_price_pct": 0.02}
    params = execution_params(cfg, ["BTC-USDT-SWAP"])
    assert params["leverage"] == 30
    assert params["margin_mode"] == "cross"
    assert params["native_stop_enabled"] is True
    assert params["native_stop_pct"] == 0.02
    assert params["max_open_symbols"] == 14
    assert params["exit_factors"]["hard_stop"]["reduce_ratio"] == 1.0
    assert params["exit_factors"]["time_stop"]["enabled"] is False


def test_cross_sectional_live_adapter_accepts_only_neutral_executable_targets():
    leg_rows = [
        {"symbol": "BTC-USDT-SWAP", "direction": "LONG", "notional_weight": .5,
         "pair_spread": 2.0, "risk_factor": "CRYPTO"},
        {"symbol": "XRP-USDT-SWAP", "direction": "SHORT", "notional_weight": .5,
         "pair_spread": 2.0, "risk_factor": "CRYPTO"},
    ]
    selected = cross_sectional_selected_by_pool({
        "crypto": {"executable": True, "legs": leg_rows},
        "tradfi": {"executable": False, "legs": leg_rows},
    }, 6.5)
    assert [row["symbol"] for row in selected["crypto"]["LONG"]] == [
        "BTC-USDT-SWAP"]
    assert [row["symbol"] for row in selected["crypto"]["SHORT"]] == [
        "XRP-USDT-SWAP"]
    assert selected["crypto"]["LONG"][0]["score"] == 7.5
    assert selected["tradfi"] == {"LONG": [], "SHORT": []}


def test_cross_sectional_live_adapter_preserves_verified_directional_score():
    legs = [
        {"symbol": "BTC-USDT-SWAP", "direction": "LONG",
         "notional_weight": .5, "pair_spread": 4.0,
         "directional_score": 3.5, "factor_gate_passed": True},
        {"symbol": "XRP-USDT-SWAP", "direction": "SHORT",
         "notional_weight": .5, "pair_spread": 4.0,
         "directional_score": 3.0, "factor_gate_passed": True},
    ]
    selected = cross_sectional_selected_by_pool(
        {"crypto": {"executable": True, "legs": legs}},
        6.5,
        require_factor_gate=True,
    )
    assert selected["crypto"]["LONG"][0]["score"] == 3.5
    assert selected["crypto"]["SHORT"][0]["score"] == 3.0


def test_cross_sectional_live_adapter_rejects_unverified_legacy_target():
    legs = [
        {"symbol": "BTC-USDT-SWAP", "direction": "LONG",
         "notional_weight": .5, "pair_spread": 4.0},
        {"symbol": "XRP-USDT-SWAP", "direction": "SHORT",
         "notional_weight": .5, "pair_spread": 4.0},
    ]
    selected = cross_sectional_selected_by_pool(
        {"crypto": {"executable": True, "legs": legs}},
        6.5,
        require_factor_gate=True,
    )
    assert selected["crypto"] == {"LONG": [], "SHORT": []}


def test_cross_sectional_live_adapter_allows_verified_single_side_target():
    selected = cross_sectional_selected_by_pool(
        {"crypto": {"executable": True, "legs": [
            {"symbol": "BTC-USDT-SWAP", "direction": "LONG",
             "notional_weight": .3, "pair_spread": 3.5,
             "directional_score": 3.5, "factor_gate_passed": True},
        ]}},
        6.5,
        require_factor_gate=True,
    )
    assert [row["symbol"] for row in selected["crypto"]["LONG"]] == [
        "BTC-USDT-SWAP"
    ]
    assert selected["crypto"]["SHORT"] == []


def test_cross_sectional_live_adapter_rejects_unbalanced_target():
    selected = cross_sectional_selected_by_pool({
        "crypto": {"executable": True, "legs": [
            {"symbol": "BTC-USDT-SWAP", "direction": "LONG",
             "notional_weight": .7, "pair_spread": 2.0},
            {"symbol": "XRP-USDT-SWAP", "direction": "SHORT",
             "notional_weight": .3, "pair_spread": 2.0},
        ]},
    }, 6.5)
    assert selected["crypto"] == {"LONG": [], "SHORT": []}


def test_pair_selection_never_reuses_a_symbol_across_sides():
    selected = {
        "crypto": {"LONG": [leg("BTC", "LONG", 9)],
                   "SHORT": [leg("ETH", "SHORT", 8)]},
        "tradfi": {"LONG": [leg("ETH", "LONG", 12)],
                   "SHORT": [leg("NVDA", "SHORT", 11)]},
    }
    pairs = select_pairs(selected, 4)
    flattened = [row[side]["symbol"] for row in pairs for side in ("LONG", "SHORT")]
    assert len(flattened) == len(set(flattened))


def test_used_margin_prefers_exchange_imr_and_has_notional_fallback():
    positions = [
        {"pos": "1", "imr": "3.5", "notionalUsd": "100", "lever": "20"},
        {"pos": "2", "imr": "", "notionalUsd": "120", "lever": "30"},
        {"pos": "0", "imr": "99"},
    ]
    assert V5ExecutionManager._used_initial_margin(positions) == 7.5


def test_liquidation_distance_is_direction_aware():
    long_position = {"markPx": "100", "liqPx": "96"}
    short_position = {"markPx": "100", "liqPx": "104"}
    assert V5ExecutionManager._liquidation_distance(long_position, "LONG") == 0.04
    assert V5ExecutionManager._liquidation_distance(short_position, "SHORT") == 0.04
    assert V5ExecutionManager._liquidation_distance(
        {"markPx": "100", "liqPx": "101"}, "LONG") is None


def test_independent_legs_do_not_require_an_opposite_side():
    selected = {"crypto": {"LONG": [leg("BTC", "LONG", 9)], "SHORT": []}}
    chosen = select_legs(selected, set(), {"LONG": 0, "SHORT": 0}, 4)
    assert [(row["symbol"], row["direction"]) for row in chosen] == [("BTC", "LONG")]


def test_independent_legs_respect_side_cap_and_existing_symbols():
    selected = {"crypto": {
        "LONG": [leg(f"L{i}", "LONG", 10 - i) for i in range(5)],
        "SHORT": [leg("OLD", "SHORT", 12), leg("S1", "SHORT", 8)],
    }}
    chosen = select_legs(selected, {("OLD", "LONG")}, {"LONG": 3, "SHORT": 0}, 4)
    assert sum(row["direction"] == "LONG" for row in chosen) == 1
    assert all(row["symbol"] != "OLD" for row in chosen)


def test_combined_crypto_and_tradfi_book_never_exceeds_total_cap():
    selected = {
        "crypto": {
            "LONG": [leg(f"CL{i}", "LONG", 30 - i) for i in range(8)],
            "SHORT": [leg(f"CS{i}", "SHORT", 20 - i) for i in range(8)],
        },
        "tradfi": {
            "LONG": [leg(f"TL{i}", "LONG", 10 - i) for i in range(8)],
            "SHORT": [leg(f"TS{i}", "SHORT", 5 - i) for i in range(8)],
        },
    }
    live = {(f"OLD{i}", "LONG" if i % 2 == 0 else "SHORT") for i in range(12)}
    chosen = select_legs(
        selected, live, {"LONG": 6, "SHORT": 6}, max_per_side=10, max_total=14)
    assert len(chosen) == 2
    assert len({symbol for symbol, _ in live} | {row["symbol"] for row in chosen}) == 14


def test_existing_oil_group_position_blocks_same_side_energy_candidate():
    selected = {"tradfi": {
        "LONG": [leg("USO-USDT-SWAP", "LONG", 9), leg("NVDA-USDT-SWAP", "LONG", 8)],
        "SHORT": [],
    }}
    chosen = select_legs(
        selected, {("CL-USDT-SWAP", "LONG")}, {"LONG": 1, "SHORT": 0}, 4)
    assert [row["symbol"] for row in chosen] == ["NVDA-USDT-SWAP"]


def test_oil_group_is_direction_scoped_for_existing_positions():
    selected = {"tradfi": {
        "LONG": [],
        "SHORT": [leg("USO-USDT-SWAP", "SHORT", 9)],
    }}
    chosen = select_legs(
        selected, {("CL-USDT-SWAP", "LONG")}, {"LONG": 1, "SHORT": 0}, 4)
    assert [row["symbol"] for row in chosen] == ["USO-USDT-SWAP"]
    assert chosen[0]["risk_group"] == "OIL_ENERGY"


def exposure_config():
    return {
        "account_margin_limit": .90,
        "normal_max_side_risk_share": .60,
        "strong_max_side_risk_share": .70,
        "single_side_margin_limit": .30,
        "strong_single_side_margin_limit": .45,
        "strong_trend_min_score": 7.5,
    }


def test_single_side_uses_30_percent_margin_cap():
    plan = dynamic_exposure_plan(
        [leg("BTC", "LONG", 7)], {"LONG": 0, "SHORT": 0}, exposure_config())
    assert plan["single_side"] is True
    assert plan["margin_limit"] == .30
    assert plan["side_share"] == {"LONG": 1.0, "SHORT": 0.0}


def test_verified_strong_single_side_can_use_45_percent_margin_cap():
    strong = leg("BTC", "LONG", 8)
    strong.update(score=7.5, strong_trend=True)
    plan = dynamic_exposure_plan(
        [strong], {"LONG": 0, "SHORT": 0}, exposure_config())
    assert plan["single_side"] is True
    assert plan["strong"] is True
    assert plan["margin_limit"] == .45


def test_two_sided_book_is_60_40_normally_and_70_30_only_when_strong():
    normal = [leg("BTC", "LONG", 10), leg("ETH", "SHORT", 6)]
    plan = dynamic_exposure_plan(normal, {"LONG": 0, "SHORT": 0}, exposure_config())
    assert abs(plan["side_share"]["LONG"] - .60) < 1e-9
    assert abs(plan["side_share"]["SHORT"] - .40) < 1e-9
    strong = leg("BTC", "LONG", 10)
    strong.update(score=8, strong_trend=True)
    plan = dynamic_exposure_plan(
        [strong, leg("ETH", "SHORT", 6)],
        {"LONG": 0, "SHORT": 0}, exposure_config())
    assert abs(plan["side_share"]["LONG"] - .70) < 1e-9
    assert abs(plan["side_share"]["SHORT"] - .30) < 1e-9


def test_inverse_long_and_equity_short_are_one_economic_side():
    plan = dynamic_exposure_plan(
        [leg("SQQQ-USDT-SWAP", "LONG", 8),
         leg("NVDA-USDT-SWAP", "SHORT", 7)],
        {"LONG": 0, "SHORT": 0}, exposure_config())
    assert plan["single_side"] is True
    assert plan["side_share"] == {"SHORT": 1.0, "LONG": 0.0}
    assert plan["margin_limit"] == .30


def test_inverse_long_respects_existing_economic_short_cap():
    selected = {"tradfi": {
        "LONG": [leg("SQQQ-USDT-SWAP", "LONG", 9)],
        "SHORT": [],
    }}
    chosen = select_legs(
        selected, set(), {"LONG": 0, "SHORT": 4}, max_per_side=4)
    assert chosen == []


def test_loss_bounded_margin_cap_limits_worst_case_to_three_percent():
    cap = loss_bounded_margin_cap(
        equity=100, leverage=20, stop_price_pct=.02, max_loss_fraction=.03)
    assert abs(cap - 7.5) < 1e-9
    assert loss_bounded_margin_cap(100, 20, 0, .03) == 0


def test_exposure_recovery_filters_majority_before_top_n_cutoff():
    legs = [
        leg("TOP-SHORT", "SHORT", 10),
        leg("SECOND-SHORT", "SHORT", 9),
        leg("HEDGE-LONG", "LONG", 8),
    ]
    filtered, recovery = restrict_legs_for_exposure_recovery(
        legs, {"LONG": 10, "SHORT": 90}, .70)
    assert [row["symbol"] for row in filtered] == ["HEDGE-LONG"]
    assert recovery["dominant_side"] == "SHORT"
    assert recovery["recovery_side"] == "LONG"
    assert recovery["dominant_share"] == .9


def test_balanced_book_keeps_all_qualified_legs():
    legs = [leg("L", "LONG", 8), leg("S", "SHORT", 8)]
    filtered, recovery = restrict_legs_for_exposure_recovery(
        legs, {"LONG": 45, "SHORT": 55}, .70)
    assert filtered == legs
    assert recovery is None


def test_factor_recovery_does_not_treat_energy_as_equity_hedge():
    energy_long = leg("USO-USDT-SWAP", "LONG", 10) | {"pool": "tradfi"}
    equity_short = leg("NVDA-USDT-SWAP", "SHORT", 9) | {"pool": "tradfi"}
    equity_long = leg("AAPL-USDT-SWAP", "LONG", 8) | {"pool": "tradfi"}
    filtered, recovery = prioritize_factor_recovery(
        [energy_long, equity_short, equity_long],
        {"EQUITY": {"LONG": 10, "SHORT": 90}},
        .70,
    )
    assert [row["symbol"] for row in filtered] == [
        "AAPL-USDT-SWAP", "USO-USDT-SWAP"]
    assert recovery["factors"]["EQUITY"]["recovery_side"] == "LONG"
    assert recovery["blocked_symbols"] == ["NVDA-USDT-SWAP"]


def test_factor_margin_cap_is_independent_per_asset_class():
    config = {"risk_factor_margin_limits": {
        "EQUITY": .45, "CRYPTO": .30, "ENERGY": .15, "DEFAULT": .10}}
    assert factor_margin_remaining(100, "EQUITY", 20, config) == 25
    assert factor_margin_remaining(100, "CRYPTO", 20, config) == 10
    assert factor_margin_remaining(100, "ENERGY", 20, config) == 0
    assert factor_margin_remaining(100, "UNKNOWN", 2, config) == 8


def test_account_result_key_does_not_expose_credentials():
    class Account:
        id = 7
        name = "second"
        api_key = "never-show-this"

    key = f"{Account.id}:{Account.name}"
    assert key == "7:second"
    assert Account.api_key not in key


def test_fail_closed_cooldown_is_account_scoped_and_persistent(tmp_path):
    manager = V5ExecutionManager(None, {"fail_closed_cooldown_seconds": 7200}, {}, tmp_path)
    manager._set_fail_closed_block(1, "NEAR-USDT-SWAP", "LONG", "unsafe buffer")

    reloaded = V5ExecutionManager(None, {"fail_closed_cooldown_seconds": 7200}, {}, tmp_path)
    assert ("NEAR-USDT-SWAP", "LONG") in reloaded._blocked_keys(1)
    assert ("NEAR-USDT-SWAP", "LONG") not in reloaded._blocked_keys(2)
    assert reloaded.fail_closed_blocks[
        "1|NEAR-USDT-SWAP|LONG"
    ]["reason"] == "unsafe buffer"


def test_expired_fail_closed_cooldown_is_pruned(tmp_path):
    state = tmp_path / "execution_state.json"
    state.write_text('{"last_processed":"x","fail_closed_blocks":'
                     '{"1|NEAR-USDT-SWAP|LONG":{"until":0}}}')
    manager = V5ExecutionManager(None, {"fail_closed_cooldown_seconds": 7200}, {}, tmp_path)
    assert manager._blocked_keys(1) == set()
    assert manager.fail_closed_blocks == {}


def test_exit_reason_classifier_blocks_only_invalidated_targets():
    assert blocks_reentry_after_exit("hard stop (price change 1.7% >= 1.5%)")
    assert blocks_reentry_after_exit("trendline_break (30m closed candle)")
    assert blocks_reentry_after_exit("structure invalid")
    assert not blocks_reentry_after_exit("trailing stop peak=80%")
    assert not blocks_reentry_after_exit("5m divergence partial reduce")


def test_exit_reentry_block_is_account_scoped_and_expires_next_slot(tmp_path):
    manager = V5ExecutionManager(None, {}, {"crypto": ["NEAR-USDT-SWAP"]}, tmp_path)
    manager.active_target_slots = {"crypto": "2026-09-25T00"}
    manager._set_exit_reentry_block(
        1,
        "NEAR-USDT-SWAP",
        "LONG",
        "crypto",
        "2026-09-25T00",
        "hard stop",
    )

    reloaded = V5ExecutionManager(None, {}, {"crypto": ["NEAR-USDT-SWAP"]}, tmp_path)
    assert ("NEAR-USDT-SWAP", "LONG") in reloaded._exit_reentry_blocked_keys(1)
    assert ("NEAR-USDT-SWAP", "LONG") not in reloaded._exit_reentry_blocked_keys(2)

    reloaded.active_target_slots["crypto"] = "2026-09-25T12"
    assert reloaded._exit_reentry_blocked_keys(1) == set()
    assert reloaded.exit_reentry_blocks == {}
