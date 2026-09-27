from v5_execution import V5ExecutionManager
from v5_rotation import (
    choose_rotation_replacement,
    classify_leg,
    marginal_hedge_contributions,
)


def market(values):
    return {
        "returns": values,
        "return_timestamps": [index * 1_800_000 for index in range(len(values))],
    }


def test_correlated_opposite_leg_is_an_effective_hedge():
    values = [((index % 7) - 3) * 0.001 for index in range(96)]
    legs = [
        {"symbol": "LONG", "direction": "LONG", "notional_weight": 100,
         "market": market(values)},
        {"symbol": "SHORT", "direction": "SHORT", "notional_weight": 80,
         "market": market([value * 0.9 for value in values])},
    ]
    contributions = marginal_hedge_contributions(legs)
    short = contributions[("SHORT", "SHORT")]
    assert short["relative_volatility_reduction"] > 0.5
    assert classify_leg(
        in_target=False,
        contribution=short,
        minimum_relative_volatility_reduction=0.03,
        minimum_drawdown_reduction=0.0025,
    ) == "HEDGE"


def test_wrong_way_leg_is_dead_capital_not_a_hedge():
    values = [((index % 7) - 3) * 0.001 for index in range(96)]
    legs = [
        {"symbol": "LONG", "direction": "LONG", "notional_weight": 100,
         "market": market(values)},
        {"symbol": "WRONG", "direction": "SHORT", "notional_weight": 80,
         "market": market([-value for value in values])},
    ]
    contribution = marginal_hedge_contributions(legs)[("WRONG", "SHORT")]
    assert contribution["relative_volatility_reduction"] <= 0
    assert classify_leg(
        in_target=False,
        contribution=contribution,
        minimum_relative_volatility_reduction=0.03,
        minimum_drawdown_reduction=0.0025,
    ) == "DEAD"


def test_missing_history_fails_closed_instead_of_rotating():
    assert classify_leg(
        in_target=False,
        contribution=None,
        minimum_relative_volatility_reduction=0.03,
        minimum_drawdown_reduction=0.0025,
    ) == "UNKNOWN"


def test_replacement_requires_same_bucket_material_alpha_and_cost_edge():
    stale = {
        "pool": "crypto", "risk_factor": "CRYPTO", "risk_direction": "LONG",
        "directional_alpha": 1.0,
    }
    candidates = [
        {"symbol": "BEST", "direction": "LONG", "pool": "crypto",
         "risk_factor": "CRYPTO", "risk_direction": "LONG",
         "directional_alpha": 2.2, "expected_edge_fraction": 0.002},
        {"symbol": "TOO-CLOSE", "direction": "LONG", "pool": "crypto",
         "risk_factor": "CRYPTO", "risk_direction": "LONG",
         "directional_alpha": 1.5, "expected_edge_fraction": 0.01},
        {"symbol": "WRONG-SIDE", "direction": "SHORT", "pool": "crypto",
         "risk_factor": "CRYPTO", "risk_direction": "SHORT",
         "directional_alpha": 4.0, "expected_edge_fraction": 0.02},
    ]
    replacement = choose_rotation_replacement(
        stale, candidates, live_keys=set(), minimum_alpha_improvement=1.0,
        minimum_expected_edge_fraction=0.0012,
    )
    assert replacement["symbol"] == "BEST"
    assert abs(replacement["alpha_improvement"] - 1.2) < 1e-9


def test_dead_capital_requires_two_distinct_rebalance_slots_and_persists(tmp_path):
    manager = V5ExecutionManager(None, {}, {}, tmp_path)
    first = manager._record_rotation_classification(
        1, "OLD", "LONG", "crypto", "2026-09-27T00", "DEAD")
    repeated = manager._record_rotation_classification(
        1, "OLD", "LONG", "crypto", "2026-09-27T00", "DEAD")
    manager._save_state()
    reloaded = V5ExecutionManager(None, {}, {}, tmp_path)
    second = reloaded._record_rotation_classification(
        1, "OLD", "LONG", "crypto", "2026-09-27T12", "DEAD")
    assert (first, repeated, second) == (1, 1, 2)
    assert reloaded._record_rotation_classification(
        1, "OLD", "LONG", "crypto", "2026-09-28T00", "HEDGE") == 0
    assert reloaded.rotation_dead_counts == {}
