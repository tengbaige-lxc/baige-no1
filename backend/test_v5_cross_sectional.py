import math

from v5_cross_sectional import build_cross_sectional_shadow
from v5_portfolio import market_features


NOW_MS = 1_788_969_600_000  # 2026-09-07 00:00:00 UTC


def market(seed, *, liquidity=1_000_000, daily_return=0.0, volatility=0.01):
    return {
        "returns": [seed * 0.0001 + ((index % 5) - 2) * 0.0002 for index in range(96)],
        "return_timestamps": [NOW_MS - (96 - index) * 1_800_000 for index in range(96)],
        "volatility": volatility,
        "return_24h": daily_return,
        "quote_volume_24h": liquidity,
        "bar_ts": NOW_MS - 1_800_000,
    }


def observations(symbols, *, pool="crypto"):
    rows = []
    for index, symbol in enumerate(symbols):
        mkt = market(index + 1, liquidity=1_000_000 - index * 10_000,
                     daily_return=(len(symbols) / 2 - index) * 0.01,
                     volatility=0.008 + index * 0.0005)
        long_score = 8.0 - index * 0.6
        short_score = 3.0 + index * 0.6
        for direction, score in (("LONG", long_score), ("SHORT", short_score)):
            rows.append({
                "pool": pool,
                "symbol": symbol,
                "direction": direction,
                "score": score,
                "price": 100 + index,
                "market": mkt,
            })
    return rows


def config(**overrides):
    result = {
        "liquidity_universe_size": {"crypto": 6, "tradfi": 8},
        "universe_method_version": 2,
        "max_legs_per_side": 2,
        "minimum_alpha_spread": 1.0,
        "momentum_weight": 0.5,
        "max_same_side_correlation": 1.01,
        "rebalance_utc_hour": 0,
        "cost_bps_per_side": 3,
    }
    result.update(overrides)
    return result


def test_crypto_book_is_dollar_neutral_and_non_executable():
    plan = build_cross_sectional_shadow(
        observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP",
                      "DDD-USDT-SWAP", "EEE-USDT-SWAP", "FFF-USDT-SWAP"]),
        pool="crypto", now_ms=NOW_MS, config=config(),
    )
    assert plan["status"] == "SHADOW_REBALANCE"
    assert plan["executable"] is False
    assert len([leg for leg in plan["legs"] if leg["direction"] == "LONG"]) == 2
    assert len([leg for leg in plan["legs"] if leg["direction"] == "SHORT"]) == 2
    assert math.isclose(plan["long_weight"], 0.5)
    assert math.isclose(plan["short_weight"], 0.5)
    assert math.isclose(plan["net_weight"], 0.0, abs_tol=1e-12)
    assert math.isclose(plan["gross_weight"], 1.0)


def test_liquidity_universe_is_frozen_for_the_calendar_month():
    rows = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP",
                         "DDD-USDT-SWAP", "EEE-USDT-SWAP", "FFF-USDT-SWAP"])
    first = build_cross_sectional_shadow(rows, pool="crypto", now_ms=NOW_MS,
                                         config=config(liquidity_universe_size=4))
    changed = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP",
                            "DDD-USDT-SWAP", "EEE-USDT-SWAP", "FFF-USDT-SWAP"])
    for row in changed:
        if row["symbol"] == "FFF-USDT-SWAP":
            row["market"]["quote_volume_24h"] = 999_999_999
    second = build_cross_sectional_shadow(
        changed, pool="crypto", now_ms=NOW_MS + 3_600_000,
        state=first["state"], config=config(liquidity_universe_size=4),
    )
    assert first["liquidity_universe"] == second["liquidity_universe"]
    assert second["liquidity_universe_refreshed"] is False
    assert "FFF-USDT-SWAP" not in second["liquidity_universe"]


def test_universe_method_upgrade_forces_a_monthly_pool_refresh():
    rows = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP",
                         "DDD-USDT-SWAP", "EEE-USDT-SWAP", "FFF-USDT-SWAP"])
    first = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=NOW_MS,
        config=config(liquidity_universe_size=4, universe_method_version=1),
    )
    changed = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP",
                            "DDD-USDT-SWAP", "EEE-USDT-SWAP", "FFF-USDT-SWAP"])
    for row in changed:
        if row["symbol"] == "FFF-USDT-SWAP":
            row["market"]["quote_volume_24h"] = 999_999_999
            row["market"]["quote_volume_trailing"] = 999_999_999
    upgraded = build_cross_sectional_shadow(
        changed, pool="crypto", now_ms=NOW_MS + 3_600_000,
        state=first["state"],
        config=config(liquidity_universe_size=4, universe_method_version=2),
    )
    assert upgraded["liquidity_universe_refreshed"] is True
    assert "FFF-USDT-SWAP" in upgraded["liquidity_universe"]
    assert upgraded["state"]["universe_method_version"] == 2


def test_target_rebalances_once_per_utc_day():
    rows = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP",
                         "DDD-USDT-SWAP"])
    first = build_cross_sectional_shadow(rows, pool="crypto", now_ms=NOW_MS,
                                         config=config(max_legs_per_side=1))
    changed = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP",
                            "CCC-USDT-SWAP", "DDD-USDT-SWAP"])
    for row in changed:
        row["score"] = 12.0 if row["symbol"] == "DDD-USDT-SWAP" else 1.0
    same_day = build_cross_sectional_shadow(
        changed, pool="crypto", now_ms=NOW_MS + 3_600_000,
        state=first["state"], config=config(max_legs_per_side=1),
    )
    next_day = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=NOW_MS + 86_400_000,
        state=same_day["state"], config=config(max_legs_per_side=1),
    )
    assert first["status"] == "SHADOW_REBALANCE"
    assert same_day["status"] == "SHADOW_HOLD"
    assert next_day["status"] == "SHADOW_REBALANCE"
    assert same_day["legs"] == first["legs"]
    assert same_day["target_frozen"] is True
    assert next_day["target_day"] != first["target_day"]


def test_crypto_rebalances_twice_while_tradfi_remains_daily():
    midnight_ms = NOW_MS - NOW_MS % 86_400_000
    rows = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP",
                         "CCC-USDT-SWAP", "DDD-USDT-SWAP"])
    schedule = {"crypto": [0, 12], "tradfi": [0]}
    crypto_first = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=midnight_ms,
        config=config(max_legs_per_side=1, rebalance_utc_hours=schedule),
    )
    crypto_before = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=midnight_ms + 11 * 3_600_000,
        state=crypto_first["state"],
        config=config(max_legs_per_side=1, rebalance_utc_hours=schedule),
    )
    crypto_second = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=midnight_ms + 12 * 3_600_000,
        state=crypto_before["state"],
        config=config(max_legs_per_side=1, rebalance_utc_hours=schedule),
    )
    tradfi_first = build_cross_sectional_shadow(
        observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP",
                      "CCC-USDT-SWAP", "DDD-USDT-SWAP"], pool="tradfi"),
        pool="tradfi", now_ms=midnight_ms,
        config=config(max_legs_per_side=1, rebalance_utc_hours=schedule),
    )
    tradfi_noon = build_cross_sectional_shadow(
        observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP",
                      "CCC-USDT-SWAP", "DDD-USDT-SWAP"], pool="tradfi"),
        pool="tradfi", now_ms=midnight_ms + 12 * 3_600_000,
        state=tradfi_first["state"],
        config=config(max_legs_per_side=1, rebalance_utc_hours=schedule),
    )
    assert crypto_first["status"] == "SHADOW_REBALANCE"
    assert crypto_first["target_slot"].endswith("T00")
    assert crypto_before["status"] == "SHADOW_HOLD"
    assert crypto_second["status"] == "SHADOW_REBALANCE"
    assert crypto_second["target_slot"].endswith("T12")
    assert tradfi_noon["status"] == "SHADOW_HOLD"
    assert tradfi_noon["target_slot"].endswith("T00")


def test_existing_state_without_frozen_legs_bootstraps_one_target():
    rows = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP",
                         "CCC-USDT-SWAP", "DDD-USDT-SWAP"])
    migrated = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=NOW_MS + 3_600_000,
        state={"last_rebalance_day": "2026-09-07"},
        config=config(max_legs_per_side=1, execution_enabled=True),
    )
    assert migrated["status"] == "LIVE_REBALANCE"
    assert migrated["executable"] is True
    assert migrated["state"]["target_legs"]["crypto"] == migrated["legs"]


def test_live_target_is_explicitly_executable_and_keeps_daily_cadence():
    rows = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP",
                         "CCC-USDT-SWAP", "DDD-USDT-SWAP"])
    first = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=NOW_MS,
        config=config(max_legs_per_side=1, execution_enabled=True),
    )
    hold = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=NOW_MS + 3_600_000,
        state=first["state"],
        config=config(max_legs_per_side=1, execution_enabled=True),
    )
    assert first["status"] == "LIVE_REBALANCE"
    assert hold["status"] == "LIVE_HOLD"
    assert first["executable"] is True
    assert hold["executable"] is True


def test_tradfi_legs_are_neutral_inside_each_risk_factor():
    symbols = [
        "AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP", "DDD-USDT-SWAP",
        "CL-USDT-SWAP", "USO-USDT-SWAP", "XLE-USDT-SWAP", "EEE-USDT-SWAP",
    ]
    plan = build_cross_sectional_shadow(
        observations(symbols, pool="tradfi"), pool="tradfi", now_ms=NOW_MS,
        config=config(max_legs_per_side=3, minimum_alpha_spread=0.2),
    )
    for factor in {leg["risk_factor"] for leg in plan["legs"]}:
        factor_legs = [leg for leg in plan["legs"] if leg["risk_factor"] == factor]
        long_weight = sum(leg["notional_weight"] for leg in factor_legs
                          if leg["direction"] == "LONG")
        short_weight = sum(leg["notional_weight"] for leg in factor_legs
                           if leg["direction"] == "SHORT")
        assert math.isclose(long_weight, short_weight, abs_tol=1e-12)


def test_no_qualified_pair_leaves_cash_and_never_becomes_executable():
    rows = observations(["AAA-USDT-SWAP", "BBB-USDT-SWAP"])
    for row in rows:
        row["score"] = 5.0
        row["market"]["return_24h"] = 0.0
    plan = build_cross_sectional_shadow(
        rows, pool="crypto", now_ms=NOW_MS,
        config=config(minimum_alpha_spread=1.0),
    )
    assert plan["status"] == "WAIT"
    assert plan["legs"] == []
    assert plan["executable"] is False


def test_inverse_contract_is_not_counted_as_an_economic_long():
    rows = observations([
        "AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP",
        "DDD-USDT-SWAP", "SQQQ-USDT-SWAP",
    ], pool="tradfi")
    plan = build_cross_sectional_shadow(
        rows, pool="tradfi", now_ms=NOW_MS,
        config=config(max_legs_per_side=2, minimum_alpha_spread=0.2),
    )
    assert "SQQQ-USDT-SWAP" not in {leg["symbol"] for leg in plan["legs"]}


def test_energy_is_neutralized_separately_from_equities():
    rows = observations([
        "BZ-USDT-SWAP", "CL-USDT-SWAP",
        "AAA-USDT-SWAP", "BBB-USDT-SWAP",
    ], pool="tradfi")
    plan = build_cross_sectional_shadow(
        rows, pool="tradfi", now_ms=NOW_MS,
        config=config(max_legs_per_side=2, minimum_alpha_spread=0.2),
    )
    energy = [leg for leg in plan["legs"] if leg["risk_factor"] == "ENERGY"]
    assert {leg["symbol"] for leg in energy} == {"BZ-USDT-SWAP", "CL-USDT-SWAP"}
    assert {leg["direction"] for leg in energy} == {"LONG", "SHORT"}
    assert math.isclose(
        sum(leg["notional_weight"] for leg in energy if leg["direction"] == "LONG"),
        sum(leg["notional_weight"] for leg in energy if leg["direction"] == "SHORT"),
    )


def test_liquidity_metric_uses_multiple_days_not_only_last_24h():
    bar_ms = 30 * 60 * 1000
    candles = []
    for index in range(240):
        timestamp = NOW_MS - (240 - index) * bar_ms
        close = 100 + index * 0.01 + ((index % 5) - 2) * 0.2
        quote_volume = 100.0 if index < 192 else 1.0
        candles.append([
            timestamp, close, close, close, close, 1.0, 1.0, quote_volume, "1",
        ])
    features = market_features(candles, NOW_MS)
    assert features["liquidity_lookback_bars"] == 240
    assert features["quote_volume_trailing"] == 192 * 100.0 + 48
