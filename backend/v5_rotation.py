"""Pure portfolio analytics for V5 scheduled rotation decisions."""
from __future__ import annotations

import math
from statistics import pstdev


def _finite(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def portfolio_metrics(legs: list[dict], min_samples: int = 96) -> dict | None:
    """Return volatility and max drawdown for aligned, weighted leg returns."""
    usable = []
    for leg in legs:
        market = leg.get("market") or {}
        timestamps = market.get("return_timestamps") or []
        returns = market.get("returns") or []
        weight = _finite(leg.get("notional_weight"), 0.0)
        direction = str(leg.get("direction") or "").upper()
        if direction not in {"LONG", "SHORT"} or weight <= 0:
            continue
        values = {
            int(timestamp): _finite(value)
            for timestamp, value in zip(timestamps, returns)
        }
        values = {timestamp: value for timestamp, value in values.items()
                  if value is not None}
        if values:
            usable.append((weight, 1.0 if direction == "LONG" else -1.0, values))
    if not usable:
        return None
    common = set(usable[0][2])
    for _, _, values in usable[1:]:
        common &= set(values)
    timestamps = sorted(common)
    if len(timestamps) < max(2, int(min_samples)):
        return None
    total_weight = sum(weight for weight, _, _ in usable)
    if total_weight <= 0:
        return None
    portfolio_returns = [
        sum(weight / total_weight * sign * values[timestamp]
            for weight, sign, values in usable)
        for timestamp in timestamps
    ]
    volatility = pstdev(portfolio_returns)
    wealth = peak = 1.0
    max_drawdown = 0.0
    for value in portfolio_returns:
        wealth *= math.exp(value)
        peak = max(peak, wealth)
        max_drawdown = min(max_drawdown, wealth / peak - 1.0)
    return {
        "samples": len(timestamps),
        "volatility": volatility,
        "max_drawdown": max_drawdown,
    }


def marginal_hedge_contributions(
    legs: list[dict],
    min_samples: int = 96,
) -> dict[tuple[str, str], dict]:
    """Measure how much each leg reduces same-factor volatility or drawdown."""
    full = portfolio_metrics(legs, min_samples=min_samples)
    if full is None:
        return {}
    if len(legs) == 1:
        leg = legs[0]
        key = (str(leg.get("symbol") or "").upper(),
               str(leg.get("direction") or "").upper())
        return {key: {
            "samples": full["samples"],
            "full_volatility": full["volatility"],
            "without_volatility": 0.0,
            "relative_volatility_reduction": 0.0,
            "drawdown_reduction": 0.0,
            "full_max_drawdown": full["max_drawdown"],
            "without_max_drawdown": 0.0,
        }}
    contributions = {}
    for index, leg in enumerate(legs):
        without = portfolio_metrics(
            legs[:index] + legs[index + 1:], min_samples=min_samples)
        if without is None:
            continue
        denominator = max(without["volatility"], 1e-12)
        key = (str(leg.get("symbol") or "").upper(),
               str(leg.get("direction") or "").upper())
        contributions[key] = {
            "samples": full["samples"],
            "full_volatility": full["volatility"],
            "without_volatility": without["volatility"],
            "relative_volatility_reduction": (
                without["volatility"] - full["volatility"]
            ) / denominator,
            "drawdown_reduction": full["max_drawdown"] - without["max_drawdown"],
            "full_max_drawdown": full["max_drawdown"],
            "without_max_drawdown": without["max_drawdown"],
        }
    return contributions


def classify_leg(
    *,
    in_target: bool,
    contribution: dict | None,
    minimum_relative_volatility_reduction: float,
    minimum_drawdown_reduction: float,
) -> str:
    """Classify a live leg as ALPHA, HEDGE, DEAD or UNKNOWN."""
    if in_target:
        return "ALPHA"
    if contribution is None:
        return "UNKNOWN"
    if (
        float(contribution.get("relative_volatility_reduction") or 0)
        >= minimum_relative_volatility_reduction
        or float(contribution.get("drawdown_reduction") or 0)
        >= minimum_drawdown_reduction
    ):
        return "HEDGE"
    return "DEAD"


def choose_rotation_replacement(
    stale: dict,
    candidates: list[dict],
    *,
    live_keys: set[tuple[str, str]],
    minimum_alpha_improvement: float,
    minimum_expected_edge_fraction: float,
) -> dict | None:
    """Choose a materially better same-pool, same-factor, same-risk-side leg."""
    old_alpha = _finite(stale.get("directional_alpha"))
    if old_alpha is None:
        return None
    ranked = []
    live_symbols = {symbol for symbol, _ in live_keys}
    for candidate in candidates:
        key = (str(candidate.get("symbol") or "").upper(),
               str(candidate.get("direction") or "").upper())
        if not key[0] or key in live_keys or key[0] in live_symbols:
            continue
        if candidate.get("pool") != stale.get("pool"):
            continue
        if candidate.get("risk_factor") != stale.get("risk_factor"):
            continue
        if candidate.get("risk_direction") != stale.get("risk_direction"):
            continue
        new_alpha = _finite(candidate.get("directional_alpha"))
        expected_edge = _finite(candidate.get("expected_edge_fraction"), 0.0)
        if new_alpha is None:
            continue
        improvement = new_alpha - old_alpha
        if improvement < minimum_alpha_improvement:
            continue
        if expected_edge < minimum_expected_edge_fraction:
            continue
        ranked.append((improvement, expected_edge, key[0], candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda row: (-row[0], -row[1], row[2]))
    improvement, expected_edge, _, candidate = ranked[0]
    return {
        **candidate,
        "alpha_improvement": improvement,
        "expected_edge_fraction": expected_edge,
    }
