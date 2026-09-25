"""Cross-sectional long-short target construction for the V5 shadow book.

This module has no account access and cannot place orders. It converts the
existing per-symbol directional research observations into a scheduled,
liquidity-bounded target book. Legacy mode is factor neutral; the live factor
gate admits independently qualified early-trend legs and leaves cash when the
opposite side has no valid candidate.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
from statistics import median

from v5_portfolio import correlation, economic_direction_for_symbol


ENERGY_SYMBOLS = frozenset({
    "BZ-USDT-SWAP", "CL-USDT-SWAP", "NG-USDT-SWAP", "URNM-USDT-SWAP",
    "USO-USDT-SWAP", "XLE-USDT-SWAP",
})
METALS_SYMBOLS = frozenset({
    "GLD-USDT-SWAP", "GDX-USDT-SWAP", "SIL-USDT-SWAP", "SLV-USDT-SWAP",
    "XAG-USDT-SWAP", "XAU-USDT-SWAP", "XCU-USDT-SWAP",
    "XPD-USDT-SWAP", "XPT-USDT-SWAP",
})
RATES_SYMBOLS = frozenset({
    "IEF-USDT-SWAP", "SHY-USDT-SWAP", "TLT-USDT-SWAP", "TMF-USDT-SWAP",
})
NON_UNIT_BETA_SYMBOLS = frozenset({
    "KORU-USDT-SWAP", "MUU-USDT-SWAP", "NVDL-USDT-SWAP",
    "SOXL-USDT-SWAP", "SOXS-USDT-SWAP", "SQQQ-USDT-SWAP",
    "TQQQ-USDT-SWAP", "TSLL-USDT-SWAP", "UVXY-USDT-SWAP",
})


def _finite(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _calendar_keys(now_ms):
    current = datetime.fromtimestamp(float(now_ms) / 1000, tz=timezone.utc)
    return current.strftime("%Y-%m"), current.strftime("%Y-%m-%d"), current.hour


def _rebalance_slot(now_ms, pool, config):
    value = config.get("rebalance_utc_hours")
    if isinstance(value, dict):
        value = value.get(pool, value.get("default"))
    if not isinstance(value, (list, tuple, set)):
        value = [config.get("rebalance_utc_hour", 0)]
    hours = sorted({max(0, min(23, int(hour))) for hour in value}) or [0]
    current = datetime.fromtimestamp(float(now_ms) / 1000, tz=timezone.utc)
    available = [hour for hour in hours if hour <= current.hour]
    if available:
        slot_day = current.strftime("%Y-%m-%d")
        slot_hour = available[-1]
    else:
        slot_day = (current - timedelta(days=1)).strftime("%Y-%m-%d")
        slot_hour = hours[-1]
    return f"{slot_day}T{slot_hour:02d}", slot_day, slot_hour, hours


def _liquidity_limit(config, pool):
    value = config.get("liquidity_universe_size", 30)
    if isinstance(value, dict):
        value = value.get(pool, value.get("default", 30))
    return max(2, int(value))


def _risk_factor(symbol, pool):
    if pool == "crypto":
        return "CRYPTO"
    if symbol in ENERGY_SYMBOLS:
        return "ENERGY"
    if symbol in METALS_SYMBOLS:
        return "METALS"
    if symbol in RATES_SYMBOLS:
        return "RATES"
    return "EQUITY"


def _paired_rows(observations, pool):
    rows = {}
    for observation in observations:
        if observation.get("pool") != pool:
            continue
        symbol = str(observation.get("symbol") or "").strip().upper()
        direction = str(observation.get("direction") or "").strip().upper()
        market = observation.get("market")
        score = _finite(observation.get("score"))
        price = _finite(observation.get("price"))
        if not symbol or direction not in {"LONG", "SHORT"} or not market:
            continue
        if score is None or price is None or price <= 0:
            continue
        item = rows.setdefault(symbol, {
            "symbol": symbol,
            "pool": pool,
            "market": market,
            "price": price,
            "scores": {},
            "signals": {},
        })
        item["scores"][direction] = score
        item["signals"][direction] = {
            "score": score,
            "eligible": bool(observation.get("eligible")),
            "trend_4h_aligned": bool(observation.get("trend_4h_aligned")),
            "structure_30m_aligned": bool(
                observation.get("structure_30m_aligned")
            ),
            "adx_4h_strong": bool(observation.get("adx_4h_strong")),
            "adx_4h": _finite(observation.get("adx_4h"), 0.0),
        }
    complete = []
    for row in rows.values():
        market = row["market"]
        liquidity = _finite(
            market.get("quote_volume_trailing", market.get("quote_volume_24h")),
            0.0,
        )
        volatility = _finite(market.get("volatility"), 0.0)
        daily_return = _finite(market.get("return_24h"))
        if set(row["scores"]) != {"LONG", "SHORT"}:
            continue
        # Leveraged inverse contracts need a separate beta model. Until that
        # exists, exclude them instead of reporting false dollar neutrality.
        if economic_direction_for_symbol(row["symbol"], "LONG") != "LONG":
            continue
        if row["symbol"] in NON_UNIT_BETA_SYMBOLS:
            continue
        if liquidity <= 0 or volatility <= 0 or daily_return is None:
            continue
        complete.append({
            **row,
            "liquidity": liquidity,
            "volatility": volatility,
            "return_24h": daily_return,
            "risk_factor": _risk_factor(row["symbol"], pool),
        })
    return complete


def _monthly_universe(rows, pool, month, state, config):
    state = state or {}
    previous = state.get("universe") or {}
    method_version = int(config.get("universe_method_version", 1))
    same_month = (
        state.get("universe_month") == month
        and int(state.get("universe_method_version", 0)) == method_version
    )
    configured = [str(value).upper() for value in previous.get(pool, [])]
    available = {row["symbol"] for row in rows}
    if same_month and configured:
        symbols = [symbol for symbol in configured if symbol in available]
        if len(symbols) >= 2:
            return symbols, False
    limit = _liquidity_limit(config, pool)
    ranked = sorted(rows, key=lambda row: (-row["liquidity"], row["symbol"]))
    return [row["symbol"] for row in ranked[:limit]], True


def _alpha_rows(rows, momentum_weight):
    baseline = median(row["return_24h"] for row in rows)
    ranked = []
    for row in rows:
        normalized_momentum = (row["return_24h"] - baseline) / (
            row["volatility"] * math.sqrt(48)
        )
        normalized_momentum = max(-2.0, min(2.0, normalized_momentum))
        score_spread = row["scores"]["LONG"] - row["scores"]["SHORT"]
        ranked.append({
            **row,
            "score_spread": score_spread,
            "normalized_momentum": normalized_momentum,
            "alpha": score_spread + float(momentum_weight) * normalized_momentum,
        })
    return ranked


def _correlation_ok(candidate, selected, maximum):
    for old in selected:
        value = correlation(candidate["market"], old["market"])
        if value is not None and value >= maximum:
            return False
    return True


def _directional_entry_allowed(row, direction, config):
    if not bool(config.get("factor_entry_gate_enabled")):
        return True
    signal = (row.get("signals") or {}).get(direction) or {}
    score = _finite(signal.get("score"), 0.0)
    minimum = float(config.get("directional_entry_score_min", 3.0))
    maximum = float(config.get("directional_entry_score_max", 4.0))
    sign = 1.0 if direction == "LONG" else -1.0
    edge = sign * float(row.get("score_spread") or 0)
    if not minimum <= score <= maximum:
        return False
    if edge < float(config.get("minimum_directional_score_edge", 2.0)):
        return False
    if bool(config.get("require_trend_4h_aligned", True)) and not signal.get(
        "trend_4h_aligned"
    ):
        return False
    if bool(config.get("require_structure_or_adx", True)) and not (
        signal.get("structure_30m_aligned") or signal.get("adx_4h_strong")
    ):
        return False
    return True


def _select_factor_pairs(
    rows,
    max_per_side,
    minimum_spread,
    maximum_correlation,
    config,
):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["risk_factor"]].append(row)
    opportunities = []
    for factor, values in grouped.items():
        longs = sorted(
            [row for row in values if _directional_entry_allowed(row, "LONG", config)],
            key=lambda row: (-row["alpha"], row["symbol"]),
        )
        shorts = sorted(
            [row for row in values if _directional_entry_allowed(row, "SHORT", config)],
            key=lambda row: (row["alpha"], row["symbol"]),
        )
        pair_count = min(len(longs), len(shorts), max_per_side)
        for offset in range(pair_count):
            long = longs[offset]
            short = shorts[offset]
            spread = long["alpha"] - short["alpha"]
            if long["symbol"] == short["symbol"] or spread < minimum_spread:
                continue
            opportunities.append((spread, factor, long, short))
    opportunities.sort(key=lambda item: (-item[0], item[1], item[2]["symbol"]))

    selected = {"LONG": [], "SHORT": []}
    used = set()
    for spread, factor, long, short in opportunities:
        if len(selected["LONG"]) >= max_per_side:
            break
        if long["symbol"] in used or short["symbol"] in used:
            continue
        if not _correlation_ok(long, selected["LONG"], maximum_correlation):
            continue
        if not _correlation_ok(short, selected["SHORT"], maximum_correlation):
            continue
        selected["LONG"].append({**long, "pair_spread": spread, "direction": "LONG"})
        selected["SHORT"].append({**short, "pair_spread": spread, "direction": "SHORT"})
        used.update({long["symbol"], short["symbol"]})
    return selected


def _select_directional_legs(
    rows,
    max_per_side,
    minimum_alpha,
    maximum_correlation,
    config,
):
    selected = {"LONG": [], "SHORT": []}
    used = set()
    for direction in ("LONG", "SHORT"):
        sign = 1.0 if direction == "LONG" else -1.0
        candidates = [
            row for row in rows
            if _directional_entry_allowed(row, direction, config)
            and sign * float(row.get("alpha") or 0) >= minimum_alpha
        ]
        candidates.sort(
            key=lambda row: (-sign * float(row.get("alpha") or 0), row["symbol"])
        )
        for row in candidates:
            if len(selected[direction]) >= max_per_side:
                break
            if row["symbol"] in used:
                continue
            if not _correlation_ok(
                row, selected[direction], maximum_correlation
            ):
                continue
            selected[direction].append({
                **row,
                "pair_spread": sign * float(row.get("alpha") or 0),
                "direction": direction,
            })
            used.add(row["symbol"])
    return selected


def _neutral_weights(selected):
    by_factor = defaultdict(lambda: {"LONG": [], "SHORT": []})
    for side, rows in selected.items():
        for row in rows:
            by_factor[row["risk_factor"]][side].append(row)
    factors = [factor for factor, sides in by_factor.items()
               if sides["LONG"] and sides["SHORT"]]
    if not factors:
        return []
    factor_gross = 1.0 / len(factors)
    legs = []
    for factor in sorted(factors):
        for side in ("LONG", "SHORT"):
            rows = by_factor[factor][side]
            inverses = [1.0 / row["volatility"] for row in rows]
            denominator = sum(inverses)
            for row, inverse in zip(rows, inverses):
                signal = (row.get("signals") or {}).get(side) or {}
                sign = 1.0 if side == "LONG" else -1.0
                legs.append({
                    "symbol": row["symbol"],
                    "pool": row["pool"],
                    "direction": side,
                    "risk_factor": factor,
                    "alpha": row["alpha"],
                    "score_spread": row["score_spread"],
                    "normalized_momentum": row["normalized_momentum"],
                    "pair_spread": row["pair_spread"],
                    "liquidity_trailing": row["liquidity"],
                    "volatility_30m": row["volatility"],
                    "price": row["price"],
                    "notional_weight": factor_gross * 0.5 * inverse / denominator,
                    "directional_score": float(signal.get("score") or 0),
                    "opposite_score": float(
                        row["scores"]["SHORT" if side == "LONG" else "LONG"]
                    ),
                    "directional_score_edge": sign * row["score_spread"],
                    "trend_4h_aligned": bool(signal.get("trend_4h_aligned")),
                    "structure_30m_aligned": bool(
                        signal.get("structure_30m_aligned")
                    ),
                    "adx_4h_strong": bool(signal.get("adx_4h_strong")),
                    "adx_4h": float(signal.get("adx_4h") or 0),
                    "factor_gate_passed": True,
                })
    return legs


def _directional_weights(selected, config):
    active_sides = [side for side in ("LONG", "SHORT") if selected[side]]
    if not active_sides:
        return []
    if len(active_sides) == 1:
        side_gross = {
            active_sides[0]: float(config.get("single_side_target_weight", 0.30))
        }
    else:
        side_gross = {"LONG": 0.5, "SHORT": 0.5}
    legs = []
    for side in active_sides:
        rows = selected[side]
        inverses = [1.0 / row["volatility"] for row in rows]
        denominator = sum(inverses)
        for row, inverse in zip(rows, inverses):
            signal = (row.get("signals") or {}).get(side) or {}
            sign = 1.0 if side == "LONG" else -1.0
            legs.append({
                "symbol": row["symbol"],
                "pool": row["pool"],
                "direction": side,
                "risk_factor": row["risk_factor"],
                "alpha": row["alpha"],
                "score_spread": row["score_spread"],
                "normalized_momentum": row["normalized_momentum"],
                "pair_spread": row["pair_spread"],
                "liquidity_trailing": row["liquidity"],
                "volatility_30m": row["volatility"],
                "price": row["price"],
                "notional_weight": side_gross[side] * inverse / denominator,
                "directional_score": float(signal.get("score") or 0),
                "opposite_score": float(
                    row["scores"]["SHORT" if side == "LONG" else "LONG"]
                ),
                "directional_score_edge": sign * row["score_spread"],
                "trend_4h_aligned": bool(signal.get("trend_4h_aligned")),
                "structure_30m_aligned": bool(
                    signal.get("structure_30m_aligned")
                ),
                "adx_4h_strong": bool(signal.get("adx_4h_strong")),
                "adx_4h": float(signal.get("adx_4h") or 0),
                "factor_gate_passed": True,
            })
    return legs


def _turnover(legs, state, pool):
    old = (state or {}).get("targets", {}).get(pool, {})
    new = {f"{leg['symbol']}:{leg['direction']}": leg["notional_weight"]
           for leg in legs}
    keys = set(old) | set(new)
    return 0.5 * sum(abs(float(new.get(key, 0)) - float(old.get(key, 0)))
                     for key in keys), new


def build_cross_sectional_shadow(observations, *, pool, now_ms, state=None, config=None):
    """Build one daily target book for a single market pool."""
    config = config or {}
    state = dict(state or {})
    month, day, hour = _calendar_keys(now_ms)
    paired = _paired_rows(observations, pool)
    universe, refreshed = _monthly_universe(paired, pool, month, state, config)
    allowed = set(universe)
    ranked = _alpha_rows(
        [row for row in paired if row["symbol"] in allowed],
        config.get("momentum_weight", 0.5),
    ) if allowed else []
    max_per_side = max(1, int(config.get("max_legs_per_side", 4)))
    minimum_alpha = float(config.get("minimum_alpha_spread", 1.0))
    maximum_correlation = float(config.get("max_same_side_correlation", 0.85))
    if config.get("factor_entry_gate_enabled"):
        selected = _select_directional_legs(
            ranked,
            max_per_side,
            minimum_alpha,
            maximum_correlation,
            config,
        )
        proposed_legs = _directional_weights(selected, config)
    else:
        selected = _select_factor_pairs(
            ranked,
            max_per_side,
            minimum_alpha,
            maximum_correlation,
            config,
        )
        proposed_legs = _neutral_weights(selected)
    slot, slot_day, slot_hour, rebalance_hours = _rebalance_slot(
        now_ms, pool, config)
    stored_legs = [dict(leg) for leg in (
        (state.get("target_legs") or {}).get(pool) or [])
    ]
    last_slot = state.get("last_rebalance_slot")
    if not last_slot and state.get("last_rebalance_day"):
        last_slot = f"{state['last_rebalance_day']}T{rebalance_hours[0]:02d}"
    due = last_slot != slot or refreshed or not stored_legs
    legs = proposed_legs if due else stored_legs
    turnover, target = _turnover(legs, state, pool)
    cost_bps = max(0.0, float(config.get("cost_bps_per_side", 3.0)))
    next_state = {
        **state,
        "universe_month": month,
        "universe_method_version": int(config.get("universe_method_version", 1)),
        "universe": {**(state.get("universe") or {}), pool: universe},
    }
    if due and proposed_legs:
        legs = proposed_legs
        next_state["last_rebalance_day"] = slot_day
        next_state["last_rebalance_slot"] = slot
        next_state["targets"] = {**(state.get("targets") or {}), pool: target}
        next_state["target_legs"] = {
            **(state.get("target_legs") or {}),
            pool: proposed_legs,
        }
    elif due:
        legs = []

    long_weight = sum(leg["notional_weight"] for leg in legs
                      if leg["direction"] == "LONG")
    short_weight = sum(leg["notional_weight"] for leg in legs
                       if leg["direction"] == "SHORT")
    execution_enabled = bool(config.get("execution_enabled"))
    if not legs:
        status = "WAIT"
        reason = "no_factor_neutral_cross_sectional_pair"
    elif due:
        status = "LIVE_REBALANCE" if execution_enabled else "SHADOW_REBALANCE"
        reason = "daily_rebalance_due"
    else:
        status = "LIVE_HOLD" if execution_enabled else "SHADOW_HOLD"
        reason = "daily_target_already_recorded"
    return {
        "status": status,
        "reason": reason,
        "pool": pool,
        "legs": legs,
        "long_weight": long_weight,
        "short_weight": short_weight,
        "gross_weight": long_weight + short_weight,
        "net_weight": long_weight - short_weight,
        "liquidity_universe": universe,
        "liquidity_universe_month": month,
        "liquidity_universe_method_version": int(
            config.get("universe_method_version", 1)
        ),
        "liquidity_universe_refreshed": refreshed,
        "target_day": next_state.get("last_rebalance_day"),
        "target_slot": next_state.get("last_rebalance_slot", last_slot),
        "rebalance_utc_hours": rebalance_hours,
        "target_frozen": bool(legs) and not due,
        "estimated_one_way_turnover": turnover,
        "estimated_cost_fraction": turnover * cost_bps / 10000,
        "state": next_state,
        "executable": execution_enabled and bool(legs),
        "methodology": (
            "monthly_liquidity_universe_daily_factor_neutral_inverse_volatility_"
            "directional_entry_gate"
            if config.get("factor_entry_gate_enabled")
            else "monthly_liquidity_universe_daily_factor_neutral_inverse_volatility"
        ),
    }
