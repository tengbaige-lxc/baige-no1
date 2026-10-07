"""Fail-closed execution primitives for the independently isolated V5 portfolio."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
import time
from pathlib import Path

from sqlalchemy import select

from app.db.base import AsyncSessionLocal
from app.models.exchange_config import ExchangeConfig
from app.models.trade_record import TradeRecord
from app.models.trading_strategy import TradingStrategy
from app.models.user import User
from app.services.okx_client import decrypt_text, okx_manager
from v5_portfolio import (
    economic_direction_for_symbol,
    risk_factor_for_symbol,
    risk_group_for_symbol,
    market_features,
    correlation,
    _position_market,
    pool_direction_risk_multiplier,
    rotation_min_holding_hours,
)
from app.services.native_stop_validation import (
    native_stop_matches_target,
)
from v5_rotation import (
    choose_rotation_replacement,
    classify_leg,
    marginal_hedge_contributions,
)


STRATEGY_IDS = {
    ("crypto", "LONG"): 401,
    ("crypto", "SHORT"): 402,
    ("tradfi", "LONG"): 403,
    ("tradfi", "SHORT"): 404,
}


def blocks_reentry_after_exit(reason: str) -> bool:
    """Return whether a completed exit invalidates the current target leg."""
    normalized = str(reason or "").strip().lower().replace("_", " ")
    return any(token in normalized for token in (
        "hard stop",
        "trendline break",
        "structure invalid",
    ))


def execution_params(config: dict, symbols: list[str]) -> dict:
    leverage = int(config["requested_leverage"])
    return {
        "symbols": symbols,
        "leverage": leverage,
        "max_auto_leverage": leverage,
        "margin_mode": str(config.get("margin_mode") or "cross"),
        # The account executor owns the combined crypto + TradFi cap. Keep the
        # per-strategy guard aligned so it cannot reject a valid account slot.
        "max_open_symbols": int(config.get("max_positions_total", 14)),
        "allow_add_existing_position": False,
        "allow_hedge_same_symbol": False,
        # The account-level executor owns the 90% book limit. This remaining-
        # balance guard must allow later legs of an already approved pair.
        "position_percent": 0.65,
        "max_auto_position_percent": 0.65,
        "max_symbol_margin_percent": 0.65,
        "max_auto_symbol_margin_percent": 0.65,
        "min_order_margin_usdt": float(config["minimum_leg_margin_usdt"]),
        "native_stop_enabled": True,
        "native_stop_pct": float(config["native_stop_price_pct"]),
        "exit_factors": {
            "hard_stop": {
                "enabled": True,
                "metric": "price_change",
                "stop_pct": float(config["hard_stop_price_pct"]),
                "reduce_ratio": 1.0,
            },
            "trendline_break": {
                "enabled": True,
                "timeframe": "30m",
                "break_pct": 0.02,
                "require_closed_candle": True,
                "reduce_ratio": 1.0,
            },
            "top_divergence": {
                "enabled": True,
                "timeframe": "5m",
                "confirm_timeframe": "30m",
                "reduce_ratio": 0.15,
                "confluence_reduce_ratio": 0.25,
                "third_sell_reduce_ratio": 1.0,
            },
            "trailing_stop": {
                "enabled": True,
                "metric": "upl_ratio",
                "activation": 0.40,
                "callback": 0.38,
                "callback_mode": "peak_profit_ratio",
                "reduce_ratio": 0.25,
                "callback_tiers": [
                    {"min_peak": 0.80, "callback": 0.30},
                    {"min_peak": 1.50, "callback": 0.25},
                ],
            },
            "time_stop": {"enabled": False},
            "residual_clear": {
                "enabled": True,
                "margin_ratio": 0.50,
                "min_age_minutes": 30,
            },
        },
    }


def select_pairs(selected_by_pool: dict, max_pairs: int = 4) -> list[dict]:
    """Merge pool-local pairs into one account-wide 4+4 book."""
    pairs = []
    for pool, selected in selected_by_pool.items():
        longs = list(selected.get("LONG") or [])
        shorts = list(selected.get("SHORT") or [])
        for long_leg, short_leg in zip(longs, shorts):
            quality = float(long_leg.get("quality", 0)) + float(short_leg.get("quality", 0))
            pairs.append({"pool": pool, "LONG": long_leg, "SHORT": short_leg, "quality": quality})
    pairs.sort(key=lambda row: (-row["quality"], row["LONG"]["symbol"], row["SHORT"]["symbol"]))
    chosen, symbols = [], set()
    for pair in pairs:
        pair_symbols = {pair["LONG"]["symbol"], pair["SHORT"]["symbol"]}
        if symbols & pair_symbols:
            continue
        chosen.append(pair)
        symbols.update(pair_symbols)
        if len(chosen) >= max_pairs:
            break
    return chosen


def select_legs(selected_by_pool: dict, live: set[tuple[str, str]],
                counts: dict[str, int], max_per_side: int = 4,
                max_total: int | None = None) -> list[dict]:
    """Merge independently qualified legs without forcing a simultaneous hedge."""
    ranked = []
    for pool, selected in selected_by_pool.items():
        for direction in ("LONG", "SHORT"):
            for leg in selected.get(direction) or []:
                ranked.append({**leg, "pool": pool, "direction": direction})
    ranked.sort(key=lambda row: (-float(row.get("quality", row.get("score", 0))),
                                 -float(row.get("score", 0)), row["symbol"]))
    chosen, symbols = [], {symbol for symbol, _ in live}
    used_risk_groups = {
        side: {
            group for symbol, direction in live
            if economic_direction_for_symbol(symbol, direction) == side
            and (group := risk_group_for_symbol(symbol))
        }
        for side in ("LONG", "SHORT")
    }
    next_counts = dict(counts)
    total_limit = max(0, int(max_total)) if max_total is not None else None
    total_count = len({symbol for symbol, _ in live})
    for leg in ranked:
        if total_limit is not None and total_count >= total_limit:
            break
        key = (leg["symbol"], leg["direction"])
        if key in live or leg["symbol"] in symbols:
            continue
        side = leg.get("risk_direction") or economic_direction_for_symbol(
            leg["symbol"], leg["direction"])
        risk_group = leg.get("risk_group") or risk_group_for_symbol(leg["symbol"])
        if risk_group and risk_group in used_risk_groups[side]:
            continue
        if next_counts.get(side, 0) >= max_per_side:
            continue
        chosen.append({
            **leg,
            "risk_group": risk_group,
            "risk_direction": side,
            "risk_factor": leg.get("risk_factor") or risk_factor_for_symbol(
                leg["symbol"], leg.get("pool")),
        })
        symbols.add(leg["symbol"])
        if risk_group:
            used_risk_groups[side].add(risk_group)
        next_counts[side] = next_counts.get(side, 0) + 1
        total_count += 1
    return chosen


def cross_sectional_selected_by_pool(
    plans: dict,
    minimum_score: float,
    *,
    require_factor_gate: bool = False,
    directional_minimum_score: float | None = None,
) -> dict:
    """Validate cross-sectional targets and adapt them to the live executor."""
    directional_threshold = (
        float(directional_minimum_score)
        if directional_minimum_score is not None
        else (0.0 if require_factor_gate else float(minimum_score))
    )
    selected_by_pool = {}
    for pool, plan in (plans or {}).items():
        selected = {"LONG": [], "SHORT": []}
        legs = list(plan.get("legs") or []) if isinstance(plan, dict) else []
        if not plan.get("executable") or not legs:
            selected_by_pool[pool] = selected
            continue
        long_weight = sum(float(leg.get("notional_weight") or 0) for leg in legs
                          if leg.get("direction") == "LONG")
        short_weight = sum(float(leg.get("notional_weight") or 0) for leg in legs
                           if leg.get("direction") == "SHORT")
        if require_factor_gate:
            if long_weight <= 0 and short_weight <= 0:
                selected_by_pool[pool] = selected
                continue
        elif long_weight <= 0 or short_weight <= 0 or not math.isclose(
            long_weight, short_weight, rel_tol=1e-6, abs_tol=1e-9
        ):
            selected_by_pool[pool] = selected
            continue
        for leg in legs:
            direction = str(leg.get("direction") or "").upper()
            symbol = str(leg.get("symbol") or "").upper()
            weight = float(leg.get("notional_weight") or 0)
            if direction not in selected or not symbol or weight <= 0:
                continue
            if require_factor_gate and not bool(leg.get("factor_gate_passed")):
                continue
            pair_spread = max(0.0, float(leg.get("pair_spread") or 0))
            directional_score = leg.get("directional_score")
            if require_factor_gate:
                score = float(directional_score or 0)
                if score < directional_threshold:
                    continue
                score_scale = "cross_sectional_directional"
                entry_minimum_score = directional_threshold
            else:
                score = max(
                    float(minimum_score),
                    float(minimum_score) + min(2.0, pair_spread / 2.0),
                )
                score_scale = "legacy_trend"
                entry_minimum_score = float(minimum_score)
            selected[direction].append({
                **leg,
                "symbol": symbol,
                "pool": pool,
                "direction": direction,
                "score": score,
                "score_scale": score_scale,
                "entry_minimum_score": entry_minimum_score,
                "quality": pair_spread,
                "risk_direction": economic_direction_for_symbol(symbol, direction),
                "risk_factor": leg.get("risk_factor") or risk_factor_for_symbol(
                    symbol, pool),
                "strong_trend": False,
            })
        selected_by_pool[pool] = selected
    return selected_by_pool


def dynamic_exposure_plan(legs: list[dict], counts: dict[str, int], config: dict) -> dict:
    """Resolve margin utilization and directional tilt from verified signals."""
    active_sides = {
        side for side in ("LONG", "SHORT")
        if counts.get(side, 0) > 0 or any(
            (leg.get("risk_direction") or economic_direction_for_symbol(
                leg["symbol"], leg["direction"])) == side for leg in legs)
    }
    strong_sides = {
        (leg.get("risk_direction") or economic_direction_for_symbol(
            leg["symbol"], leg["direction"])) for leg in legs
        if bool(leg.get("strong_trend"))
        and float(leg.get("score") or 0) >= float(config.get("strong_trend_min_score", 7.5))
    }
    if len(active_sides) == 1:
        side = next(iter(active_sides))
        strong = side in strong_sides
        return {
            "single_side": True,
            "strong": strong,
            "margin_limit": float(config[
                "strong_single_side_margin_limit" if strong else "single_side_margin_limit"
            ]),
            "side_share": {side: 1.0, ("SHORT" if side == "LONG" else "LONG"): 0.0},
        }

    qualities = {
        side: [float(leg.get("quality", leg.get("score", 0)))
               for leg in legs if (
                   leg.get("risk_direction") or economic_direction_for_symbol(
                       leg["symbol"], leg["direction"])) == side]
        for side in ("LONG", "SHORT")
    }
    strong_direction = next(iter(strong_sides)) if len(strong_sides) == 1 else None
    if strong_direction:
        max_share = float(config.get("strong_max_side_risk_share", .70))
        long_share = max_share if strong_direction == "LONG" else 1 - max_share
    elif qualities["LONG"] and qualities["SHORT"]:
        difference = sum(qualities["LONG"]) / len(qualities["LONG"]) - (
            sum(qualities["SHORT"]) / len(qualities["SHORT"]))
        max_share = float(config.get("normal_max_side_risk_share", .60))
        long_share = max(1 - max_share, min(max_share, .5 + .06 * difference))
    else:
        max_share = float(config.get("normal_max_side_risk_share", .60))
        long_share = max_share if qualities["LONG"] else 1 - max_share
    return {
        "single_side": False,
        "strong": bool(strong_direction),
        "margin_limit": float(config["account_margin_limit"]),
        "side_share": {"LONG": long_share, "SHORT": 1 - long_share},
    }


def loss_bounded_margin_cap(equity: float, leverage: float, stop_price_pct: float,
                            max_loss_fraction: float) -> float:
    values = (equity, leverage, stop_price_pct, max_loss_fraction)
    if any(not isinstance(value, (int, float)) or isinstance(value, bool)
           or not math.isfinite(value) or value <= 0 for value in values):
        return 0.0
    return equity * max_loss_fraction / (leverage * stop_price_pct)


def risk_adjusted_leg_margin_cap(base_cap: float, config: dict,
                                 pool: str, direction: str) -> float:
    if not isinstance(base_cap, (int, float)) or isinstance(base_cap, bool) \
            or not math.isfinite(base_cap) or base_cap <= 0:
        return 0.0
    return base_cap * pool_direction_risk_multiplier(config, pool, direction)


def holding_period_satisfied(opened_at_ts, minimum_hours: float,
                             now_ts: float | None = None) -> bool:
    minimum = max(0.0, float(minimum_hours or 0))
    if minimum <= 0:
        return True
    try:
        opened = float(opened_at_ts)
        now = float(time.time() if now_ts is None else now_ts)
    except (TypeError, ValueError):
        return False
    return math.isfinite(opened) and math.isfinite(now) \
        and now >= opened and now - opened >= minimum * 3600


def restrict_legs_for_exposure_recovery(legs: list[dict], used_by_side: dict[str, float],
                                        max_side_share: float) -> tuple[list[dict], dict | None]:
    """When the live book is imbalanced, consider only qualified recovery-side legs."""
    long_margin = max(0.0, float(used_by_side.get("LONG") or 0))
    short_margin = max(0.0, float(used_by_side.get("SHORT") or 0))
    total = long_margin + short_margin
    if total <= 0:
        return legs, None
    limit = max(0.5, min(1.0, float(max_side_share)))
    shares = {"LONG": long_margin / total, "SHORT": short_margin / total}
    dominant = max(shares, key=shares.get)
    if shares[dominant] <= limit:
        return legs, None
    recovery_side = "SHORT" if dominant == "LONG" else "LONG"
    filtered = [leg for leg in legs if (
        leg.get("risk_direction") or economic_direction_for_symbol(
            leg["symbol"], leg["direction"])) == recovery_side]
    return filtered, {
        "dominant_side": dominant,
        "dominant_share": shares[dominant],
        "recovery_side": recovery_side,
        "economic_margin_share": shares,
    }


def prioritize_factor_recovery(
        legs: list[dict], used_by_factor_side: dict[str, dict[str, float]],
        max_side_share: float) -> tuple[list[dict], dict | None]:
    """Block majority-side adds inside an imbalanced factor, without fake cross-factor hedges."""
    limit = max(0.5, min(1.0, float(max_side_share)))
    recoveries = {}
    for factor, margins in used_by_factor_side.items():
        long_margin = max(0.0, float(margins.get("LONG") or 0))
        short_margin = max(0.0, float(margins.get("SHORT") or 0))
        total = long_margin + short_margin
        if total <= 0:
            continue
        shares = {"LONG": long_margin / total, "SHORT": short_margin / total}
        dominant = max(shares, key=shares.get)
        if shares[dominant] > limit:
            recoveries[factor] = {
                "dominant_side": dominant,
                "dominant_share": shares[dominant],
                "recovery_side": "SHORT" if dominant == "LONG" else "LONG",
                "margin_share": shares,
            }
    if not recoveries:
        return legs, None

    recovery_legs, neutral_legs, blocked = [], [], []
    for leg in legs:
        factor = leg.get("risk_factor") or risk_factor_for_symbol(
            leg["symbol"], leg.get("pool"))
        side = leg.get("risk_direction") or economic_direction_for_symbol(
            leg["symbol"], leg["direction"])
        recovery = recoveries.get(factor)
        tagged = {**leg, "risk_factor": factor, "risk_direction": side}
        if recovery is None:
            neutral_legs.append(tagged)
        elif side == recovery["recovery_side"]:
            recovery_legs.append(tagged)
        else:
            blocked.append(leg["symbol"])
    return recovery_legs + neutral_legs, {
        "factors": recoveries,
        "blocked_symbols": blocked,
    }


def factor_margin_remaining(equity: float, factor: str, used_margin: float,
                            config: dict) -> float:
    limits = config.get("risk_factor_margin_limits") or {}
    share = float(limits.get(factor, limits.get("DEFAULT", 0.15)))
    return max(0.0, max(0.0, float(equity)) * max(0.0, share) -
               max(0.0, float(used_margin)))


def is_portfolio_profit_reduce_reason(reason: str) -> bool:
    """Return whether an exit is a discretionary V5 profit trim."""
    normalized = str(reason or "").strip().lower()
    return any(marker in normalized for marker in (
        "trailing stop",
        "dynamic profit lock",
        "profit tier",
        "take profit",
    ))


def _position_initial_margin(position: dict) -> float:
    try:
        margin = float(position.get("imr") or position.get("margin") or 0)
        if margin > 0:
            return margin
        notional = abs(float(position.get("notionalUsd") or 0))
        leverage = float(position.get("lever") or 0)
        return notional / leverage if notional > 0 and leverage > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _reduction_scope_allowed(
    before: dict[str, float],
    after: dict[str, float],
    normal_limit: float,
    hard_limit: float,
) -> tuple[bool, str]:
    """Keep ordinary books within 60:40 and never worsen a stressed book."""
    before_total = sum(before.values())
    after_total = sum(after.values())
    if after_total <= 1e-9:
        return True, "scope_closed"
    if before_total <= 1e-9:
        return False, "missing_pre_reduce_exposure"

    before_share = max(before.values()) / before_total
    after_share = max(after.values()) / after_total
    before_net = abs(before["LONG"] - before["SHORT"])
    after_net = abs(after["LONG"] - after["SHORT"])
    normal = max(0.5, min(1.0, float(normal_limit)))
    hard = max(normal, min(1.0, float(hard_limit)))

    if after_share <= normal + 1e-9:
        return True, "within_normal_band"
    if after_share > hard + 1e-9 and after_net >= before_net - 1e-9:
        return False, "hard_side_share_would_not_recover"
    if before_share > normal + 1e-9 and after_net < before_net - 1e-9:
        return True, "stressed_book_net_exposure_reduced"
    return False, "normal_side_share_would_be_exceeded"


def profit_reduce_exposure_decision(
    positions: list[dict],
    symbol: str,
    direction: str,
    reduce_quantity: float,
    crypto_symbols: set[str] | None = None,
    *,
    normal_max_side_share: float = 0.60,
    strong_max_side_share: float = 0.70,
    factor_max_side_share: float = 0.70,
) -> dict:
    """Evaluate a V5 profit trim against account and factor exposure."""
    target_symbol = str(symbol or "").upper()
    target_direction = str(direction or "").upper()
    crypto = {str(item).upper() for item in (crypto_symbols or set())}
    rows = []
    target = None
    for position in positions or []:
        try:
            size = abs(float(position.get("pos") or 0))
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        position_symbol = str(position.get("instId") or "").upper()
        side = str(position.get("posSide") or "net").lower()
        raw_size = float(position.get("pos") or 0)
        contract_direction = (
            "SHORT" if side == "short" or (side == "net" and raw_size < 0)
            else "LONG"
        )
        margin = _position_initial_margin(position)
        if margin <= 0:
            continue
        pool = "crypto" if position_symbol in crypto else "tradfi"
        row = {
            "symbol": position_symbol,
            "direction": contract_direction,
            "risk_direction": economic_direction_for_symbol(
                position_symbol, contract_direction
            ),
            "risk_factor": risk_factor_for_symbol(position_symbol, pool),
            "margin": margin,
            "size": size,
        }
        rows.append(row)
        if position_symbol == target_symbol and contract_direction == target_direction:
            target = row

    if target is None:
        return {"allowed": False, "reason": "target_position_or_margin_not_found"}
    try:
        fraction = min(1.0, max(0.0, float(reduce_quantity)) / target["size"])
    except (TypeError, ValueError, ZeroDivisionError):
        fraction = 0.0
    reduction_margin = target["margin"] * fraction
    if reduction_margin <= 0:
        return {"allowed": False, "reason": "invalid_reduction_margin"}

    account_before = {"LONG": 0.0, "SHORT": 0.0}
    factor_before = {"LONG": 0.0, "SHORT": 0.0}
    for row in rows:
        account_before[row["risk_direction"]] += row["margin"]
        if row["risk_factor"] == target["risk_factor"]:
            factor_before[row["risk_direction"]] += row["margin"]
    account_after = dict(account_before)
    factor_after = dict(factor_before)
    account_after[target["risk_direction"]] = max(
        0.0, account_after[target["risk_direction"]] - reduction_margin
    )
    factor_after[target["risk_direction"]] = max(
        0.0, factor_after[target["risk_direction"]] - reduction_margin
    )

    account_allowed, account_reason = _reduction_scope_allowed(
        account_before, account_after,
        normal_max_side_share, strong_max_side_share,
    )
    factor_allowed, factor_reason = _reduction_scope_allowed(
        factor_before, factor_after,
        normal_max_side_share, factor_max_side_share,
    )
    exposure_recovery_required = not (account_allowed and factor_allowed)
    return {
        # A reduce-only profit exit always lowers gross and instrument risk.
        # Directional/factor imbalance is repaired by the entry allocator; it
        # must not trap a position after its exit condition has fired.
        "allowed": True,
        "reason": (
            "portfolio_profit_reduce_allowed"
            if not exposure_recovery_required
            else "portfolio_profit_reduce_allowed_rebalance_required"
        ),
        "exposure_recovery_required": exposure_recovery_required,
        "exposure_assessment": (
            f"account={account_reason} factor={factor_reason}"
        ),
        "risk_direction": target["risk_direction"],
        "risk_factor": target["risk_factor"],
        "reduction_margin": round(reduction_margin, 8),
        "account_before": account_before,
        "account_after": account_after,
        "factor_before": factor_before,
        "factor_after": factor_after,
    }


class V5ExecutionManager:
    def __init__(self, engine, config: dict, universes: dict, state_dir: Path):
        self.engine = engine
        self.config = config
        self.universes = universes
        self.state_file = state_dir / "execution_state.json"
        self.lock = asyncio.Lock()
        self._state = self._load_state()
        self.last_processed = self._state.get("last_processed")
        self.fail_closed_blocks = dict(self._state.get("fail_closed_blocks") or {})
        self.exit_reentry_blocks = dict(self._state.get("exit_reentry_blocks") or {})
        self.active_target_slots = dict(self._state.get("active_target_slots") or {})
        self.rotation_dead_counts = dict(self._state.get("rotation_dead_counts") or {})
        self._missing_v5_ledger_since: dict[tuple[int, str, str], float] = {}
        if self.engine is not None:
            self.engine._pre_reduce_callback = self._pre_reduce_callback
            self.engine._post_reduce_callback = self._post_reduce_callback

    def _load_state(self) -> dict:
        try:
            payload = json.loads(self.state_file.read_text())
            return payload if isinstance(payload, dict) else {}
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def _save_state(self) -> None:
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({
            "last_processed": self.last_processed,
            "fail_closed_blocks": self.fail_closed_blocks,
            "exit_reentry_blocks": self.exit_reentry_blocks,
            "active_target_slots": self.active_target_slots,
            "rotation_dead_counts": self.rotation_dead_counts,
        }))
        temporary.replace(self.state_file)

    def _save_last_processed(self, value: str) -> None:
        self.last_processed = value
        self._save_state()

    async def _close_stale_v5_ledger_if_due(self, accounts: list[ExchangeConfig]) -> None:
        """Close missing V5-owned rows without importing external positions."""
        now = time.monotonic()
        if now - getattr(self, "_last_v5_ledger_audit", 0) < 60:
            return
        self._last_v5_ledger_audit = now
        grace_seconds = max(
            120.0, float(self.config.get("ledger_missing_grace_seconds", 120))
        )
        strategies = await self._strategies()
        markers = {
            self.engine._format_strategy_marker(strategy)
            for strategy in strategies.values()
        }
        if not markers:
            return
        closed = 0
        observed_open_keys = set()
        async with AsyncSessionLocal() as db:
            for account in accounts:
                live_positions = await self._live_positions(account)
                live_keys = {
                    key for row in live_positions
                    if (key := self._position_key(row)) is not None
                }
                result = await db.execute(select(TradeRecord).where(
                    TradeRecord.exchange_config_id == account.id,
                    TradeRecord.is_closed == False,
                    TradeRecord.strategy_tag.in_(markers),
                ))
                for record in result.scalars().all():
                    position_key = (
                        str(record.symbol or "").upper(),
                        str(record.direction or "").upper(),
                    )
                    cache_key = (account.id, *position_key)
                    observed_open_keys.add(cache_key)
                    if position_key in live_keys:
                        self._missing_v5_ledger_since.pop(cache_key, None)
                        continue
                    first_seen = self._missing_v5_ledger_since.get(cache_key)
                    if first_seen is None:
                        self._missing_v5_ledger_since[cache_key] = now
                        continue
                    if now - first_seen < grace_seconds:
                        continue
                    record.is_closed = True
                    record.updated_at = datetime.now(timezone.utc)
                    record.notes = (
                        (record.notes or "")
                        + " | V5账本对账关闭: 交易所连续观察无该持仓，PnL未核算"
                    )
                    self._missing_v5_ledger_since.pop(cache_key, None)
                    closed += 1
            for cache_key in list(self._missing_v5_ledger_since):
                if cache_key not in observed_open_keys:
                    self._missing_v5_ledger_since.pop(cache_key, None)
            if closed:
                await db.commit()
        if closed:
            print(f"v5_stale_ledger_closed count={closed}", flush=True)

    @staticmethod
    def _rotation_key(account_id: int, symbol: str, direction: str) -> str:
        return f"{account_id}|{symbol.upper()}|{direction.upper()}"

    def _record_rotation_classification(
        self,
        account_id: int,
        symbol: str,
        direction: str,
        pool: str,
        target_slot: str,
        classification: str,
    ) -> int:
        key = self._rotation_key(account_id, symbol, direction)
        if classification != "DEAD":
            self.rotation_dead_counts.pop(key, None)
            return 0
        previous = self.rotation_dead_counts.get(key) or {}
        if previous.get("target_slot") == target_slot:
            return int(previous.get("count") or 0)
        count = int(previous.get("count") or 0) + 1
        self.rotation_dead_counts[key] = {
            "count": count,
            "pool": pool,
            "target_slot": target_slot,
            "classified_at": datetime.now(timezone.utc).isoformat(),
        }
        return count

    @staticmethod
    def _block_key(account_id: int, symbol: str, direction: str) -> str:
        return f"{account_id}|{symbol.upper()}|{direction.upper()}"

    def _set_fail_closed_block(self, account_id: int, symbol: str,
                               direction: str, reason: str) -> None:
        now = datetime.now(timezone.utc).timestamp()
        cooldown = max(300, int(self.config.get("fail_closed_cooldown_seconds", 7200)))
        self.fail_closed_blocks[self._block_key(account_id, symbol, direction)] = {
            "blocked_at": now,
            "until": now + cooldown,
            "reason": str(reason)[:160],
        }
        self._save_state()

    def _blocked_keys(self, account_id: int) -> set[tuple[str, str]]:
        now = datetime.now(timezone.utc).timestamp()
        active = set()
        changed = False
        for key, value in list(self.fail_closed_blocks.items()):
            try:
                stored_account, symbol, direction = key.split("|", 2)
                stored_account_id = int(stored_account)
                until = float(value.get("until") or 0)
            except (AttributeError, TypeError, ValueError):
                del self.fail_closed_blocks[key]
                changed = True
                continue
            if until <= now:
                del self.fail_closed_blocks[key]
                changed = True
            elif stored_account_id == int(account_id):
                active.add((symbol, direction))
        if changed:
            self._save_state()
        return active

    def _set_exit_reentry_block(
        self,
        account_id: int,
        symbol: str,
        direction: str,
        pool: str,
        target_slot: str,
        reason: str,
    ) -> None:
        self.exit_reentry_blocks[self._block_key(account_id, symbol, direction)] = {
            "pool": pool,
            "target_slot": target_slot,
            "reason": str(reason)[:160],
            "blocked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_state()

    def _exit_reentry_blocked_keys(self, account_id: int) -> set[tuple[str, str]]:
        active = set()
        changed = False
        for key, value in list(self.exit_reentry_blocks.items()):
            try:
                stored_account, symbol, direction = key.split("|", 2)
                stored_account_id = int(stored_account)
                pool = str(value["pool"])
                stored_slot = str(value["target_slot"])
            except (AttributeError, KeyError, TypeError, ValueError):
                del self.exit_reentry_blocks[key]
                changed = True
                continue
            current_slot = str(self.active_target_slots.get(pool) or "")
            if current_slot and current_slot != stored_slot:
                del self.exit_reentry_blocks[key]
                changed = True
            elif stored_account_id == int(account_id):
                active.add((symbol, direction))
        if changed:
            self._save_state()
        return active

    async def _post_reduce_callback(
        self,
        *,
        strategy: TradingStrategy,
        account: ExchangeConfig,
        symbol: str,
        direction: str,
        reason: str,
        remaining_size: float | None,
    ) -> None:
        if strategy.id not in STRATEGY_IDS.values():
            return
        if remaining_size is None or remaining_size > 0:
            return
        if not blocks_reentry_after_exit(reason):
            return
        pool = "crypto" if symbol.upper() in {
            item.upper() for item in self.universes.get("crypto", [])
        } else "tradfi"
        target_slot = str(self.active_target_slots.get(pool) or "")
        if not target_slot:
            return
        self._set_exit_reentry_block(
            account.id, symbol, direction, pool, target_slot, reason
        )
        print(
            f"v5_reentry_blocked account={account.id} symbol={symbol.upper()} "
            f"direction={direction.upper()} pool={pool} target_slot={target_slot}",
            flush=True,
        )

    async def _pre_reduce_callback(
        self,
        *,
        strategy: TradingStrategy,
        account: ExchangeConfig,
        symbol: str,
        direction: str,
        quantity: float,
        reason: str,
        params: dict,
    ) -> dict:
        if strategy.id not in STRATEGY_IDS.values():
            return {"allowed": True, "reason": "not_v5"}
        if not is_portfolio_profit_reduce_reason(reason):
            return {"allowed": True, "reason": "risk_exit_not_gated"}
        positions = await self._live_positions(account)
        decision = profit_reduce_exposure_decision(
            positions,
            symbol,
            direction,
            quantity,
            set(self.universes.get("crypto", [])),
            normal_max_side_share=float(
                self.config.get("normal_max_side_risk_share", 0.60)
            ),
            strong_max_side_share=float(
                self.config.get("strong_max_side_risk_share", 0.70)
            ),
            factor_max_side_share=float(
                self.config.get("max_factor_side_risk_share", 0.70)
            ),
        )
        if decision.get("allowed"):
            return decision
        return {
            **decision,
            "allowed": True,
            "reason": f"profit_reduce_fail_open: {decision.get('reason', 'unknown')}",
            "exposure_recovery_required": True,
        }

    async def bootstrap(self) -> None:
        async with AsyncSessionLocal() as db:
            user = await db.get(User, 1)
            if user is None:
                db.add(User(id=1, username="baige_v5", email="v5@localhost.invalid",
                            full_name="Baige V5", hashed_password="disabled-local-user",
                            is_active=True, is_superuser=True))
            for (pool, direction), strategy_id in STRATEGY_IDS.items():
                strategy = await db.get(TradingStrategy, strategy_id)
                values = {
                    "user_id": 1,
                    "name": f"Baige V5 {pool} {direction}",
                    "strategy_type": "white_dove",
                    "symbol": self.universes[pool][0],
                    "market_type": "SWAP",
                    "side": "BUY" if direction == "LONG" else "SELL",
                    "params": execution_params(self.config, self.universes[pool]),
                    "is_active": True,
                    "interval_seconds": 300,
                    "remark": "V5 entries are controlled only by its isolated portfolio executor",
                }
                if strategy is None:
                    db.add(TradingStrategy(id=strategy_id, **values))
                else:
                    for key, value in values.items():
                        setattr(strategy, key, value)
            await db.commit()
        await self.engine._ensure_account_scoped_ledger_schema()

    async def _accounts(self) -> list[ExchangeConfig]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(ExchangeConfig).where(
                ExchangeConfig.is_active == True).order_by(ExchangeConfig.id))
            return list(result.scalars().all())

    async def _strategies(self) -> dict[tuple[str, str], TradingStrategy]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(TradingStrategy).where(
                TradingStrategy.id.in_(list(STRATEGY_IDS.values()))))
            by_id = {row.id: row for row in result.scalars().all()}
        return {key: by_id[value] for key, value in STRATEGY_IDS.items() if value in by_id}

    async def _live_positions(self, account: ExchangeConfig) -> list[dict]:
        return await okx_manager.get_positions(
            decrypt_text(account.api_key), decrypt_text(account.api_secret),
            decrypt_text(account.api_passphrase or ""), bool(account.is_testnet),
        )

    @staticmethod
    def _position_key(position: dict) -> tuple[str, str] | None:
        try:
            size = float(position.get("pos") or 0)
        except (TypeError, ValueError):
            return None
        if not size:
            return None
        side = str(position.get("posSide") or "net").lower()
        direction = "SHORT" if side == "short" or (side == "net" and size < 0) else "LONG"
        return str(position.get("instId") or "").upper(), direction

    async def _equity(self, account: ExchangeConfig) -> tuple[float, float]:
        rows = await okx_manager.get_balance(
            decrypt_text(account.api_key), decrypt_text(account.api_secret),
            decrypt_text(account.api_passphrase or ""), bool(account.is_testnet),
        )
        equity = sum(float(row.get("eqUsd") or row.get("eq") or 0) for row in rows)
        available = sum(float(row.get("availEq") or row.get("availBal") or 0) for row in rows)
        return equity, available

    @staticmethod
    def _used_initial_margin(positions: list[dict]) -> float:
        total = 0.0
        for position in positions:
            try:
                if not float(position.get("pos") or 0):
                    continue
                margin = float(position.get("imr") or position.get("margin") or 0)
                if margin <= 0:
                    notional = abs(float(position.get("notionalUsd") or 0))
                    leverage = float(position.get("lever") or 0)
                    margin = notional / leverage if notional > 0 and leverage > 0 else 0
                total += max(0.0, margin)
            except (TypeError, ValueError):
                continue
        return total

    async def _pair_margin_split(self, pair: dict, pair_margin: float) -> dict[str, float]:
        raw = {}
        for side in ("LONG", "SHORT"):
            leg = pair[side]
            leverage = min(int(self.config["requested_leverage"]),
                           await self.engine._get_instrument_max_leverage(leg["symbol"], "SWAP"))
            # Portfolio weights describe desired notional risk. Divide by the
            # live leverage to obtain the margin needed for that risk share.
            notional_weight = max(1e-9, float(leg.get("notional_weight") or 0.5))
            raw[side] = notional_weight / max(1, leverage)
        total = sum(raw.values())
        return {side: pair_margin * raw[side] / total for side in raw}

    async def _quantity(self, strategy, symbol: str, price: float, margin: float) -> tuple[float, int]:
        leverage = min(int(self.config["requested_leverage"]),
                       await self.engine._get_instrument_max_leverage(symbol, "SWAP"))
        ct_val = await self.engine._get_contract_value_live(symbol, "SWAP")
        raw = margin * leverage / (price * ct_val)
        quantity = await self.engine._round_contract_quantity_down(symbol, raw, "SWAP")
        return quantity, leverage

    async def _protected(self, account: ExchangeConfig, symbol: str, direction: str) -> bool:
        position = next((row for row in await self._live_positions(account)
                         if self._position_key(row) == (symbol.upper(), direction)), None)
        if position is None:
            return False
        instruments = await okx_manager.get_instruments("SWAP")
        tick = next((row.get("tickSz") for row in instruments
                     if row.get("instId") == symbol), None)
        if not tick:
            return False
        expected = float(self.engine._format_stop_trigger_px(
            self.engine._native_stop_trigger_price(direction, float(position["avgPx"]),
                {"native_stop_pct": self.config["native_stop_price_pct"]}), tick, direction))
        pending = await okx_manager.get_pending_algo_stops(
            decrypt_text(account.api_key), decrypt_text(account.api_secret),
            decrypt_text(account.api_passphrase or ""), inst_id=symbol,
            simulated=bool(account.is_testnet),
        )
        return any(native_stop_matches_target(
            row,
            symbol,
            direction,
            abs(float(position["pos"])),
            expected,
            tick,
        ) for row in pending or [])

    async def reconcile_native_stops(self, account, strategies) -> bool:
        """Only repair V5 ledger-owned positions, never adopt manual holdings."""
        live = {self._position_key(row): row for row in await self._live_positions(account)}
        safe = True
        for strategy in strategies.values():
            keys = await self.engine._get_strategy_live_position_keys(account, strategy)
            for symbol, direction in keys:
                position = live.get((symbol, direction))
                if not position:
                    continue
                if not await self._protected(account, symbol, direction):
                    await self.engine._place_native_stop_backstop(
                        account, symbol, direction, float(position["avgPx"]),
                        abs(float(position["pos"])), strategy.params, market_type="SWAP")
                    if not await self._protected(account, symbol, direction):
                        safe = False
                        print(f"v5_native_stop_unverified account={account.id} symbol={symbol}", flush=True)
        return safe

    async def _filter_existing_correlations(self, legs, live):
        markets = {}
        async def market(symbol):
            if symbol not in markets:
                try:
                    candles = await asyncio.wait_for(
                        okx_manager.get_candles(symbol, "30m", 240), timeout=12)
                    markets[symbol] = market_features(candles, time.time() * 1000)
                except Exception:
                    markets[symbol] = None
            return markets[symbol]
        crypto = {symbol.upper() for symbol in self.universes.get("crypto", [])}
        accepted, blocked = [], []
        for leg in legs:
            reason = None
            for symbol, direction in sorted(live):
                side = economic_direction_for_symbol(symbol, direction)
                factor = risk_factor_for_symbol(symbol, "crypto" if symbol in crypto else "tradfi")
                if side != leg["risk_direction"] or factor != leg["risk_factor"]:
                    continue
                current, existing = await market(leg["symbol"]), await market(symbol)
                value = None if not current or not existing else correlation(
                    _position_market({"market": current, "direction": leg["direction"]}),
                    _position_market({"market": existing, "direction": direction}))
                if value is None or value >= float(self.config.get("max_same_side_correlation", .85)):
                    reason = {"symbol": leg["symbol"], "existing": symbol,
                              "correlation": value, "reason": "correlation_unavailable" if value is None
                              else "same_direction_correlation"}
                    break
            if reason:
                blocked.append(reason)
            else:
                accepted.append(leg)
                live = live | {(leg["symbol"], leg["direction"])}
        return accepted, blocked

    @staticmethod
    def _liquidation_distance(position: dict, direction: str) -> float | None:
        try:
            mark = float(position.get("markPx") or position.get("last") or 0)
            liquidation = float(position.get("liqPx") or 0)
        except (TypeError, ValueError):
            return None
        if mark <= 0 or liquidation <= 0:
            return None
        if direction == "LONG":
            distance = (mark - liquidation) / mark
        else:
            distance = (liquidation - mark) / mark
        return distance if distance > 0 else None

    async def _position_has_liquidation_buffer(
            self, account: ExchangeConfig, symbol: str, direction: str) -> tuple[bool, str]:
        required = float(self.config["native_stop_price_pct"]) + 0.003
        expected_mode = str(self.config.get("margin_mode") or "cross").lower()
        for _ in range(3):
            for position in await self._live_positions(account):
                if self._position_key(position) != (symbol.upper(), direction):
                    continue
                margin_mode = str(position.get("mgnMode") or "").lower()
                if margin_mode != expected_mode:
                    return False, f"position margin mode is {margin_mode or 'unknown'}"
                distance = self._liquidation_distance(position, direction)
                if distance is None:
                    # OKX can omit a per-position liquidation price in cross
                    # mode because liquidation depends on account equity.
                    if expected_mode == "cross":
                        return True, "cross margin liquidation price unavailable"
                    return False, "liquidation price unavailable"
                if distance < required:
                    return False, f"liquidation buffer {distance:.4f} below {required:.4f}"
                return True, "ok"
            await asyncio.sleep(1)
        return False, "position not visible after entry"

    async def _emergency_close(self, strategy, account, symbol: str, direction: str, reason: str) -> None:
        for position in await self._live_positions(account):
            if self._position_key(position) != (symbol.upper(), direction):
                continue
            quantity = abs(float(position.get("pos") or 0))
            price = float(position.get("markPx") or position.get("last") or 0)
            if quantity > 0:
                await self.engine._do_reduce(
                    strategy, account, symbol, quantity,
                    "SELL" if direction == "LONG" else "BUY", price, reason,
                    strategy.params, direction.lower(), set(),
                )
            return

    @staticmethod
    def _rotation_notional(position: dict) -> float:
        try:
            notional = abs(float(position.get("notionalUsd") or 0))
            if notional > 0:
                return notional
            quantity = abs(float(position.get("pos") or 0))
            price = float(position.get("markPx") or position.get("last") or 0)
            return quantity * price if quantity > 0 and price > 0 else 0.0
        except (TypeError, ValueError):
            return 0.0

    async def _rotation_markets(self, symbols: set[str]) -> dict[str, dict | None]:
        semaphore = asyncio.Semaphore(4)

        async def load(symbol: str):
            async with semaphore:
                try:
                    candles = await asyncio.wait_for(
                        okx_manager.get_candles(symbol, "30m", 240), timeout=12)
                    return symbol, market_features(candles, time.time() * 1000)
                except Exception:
                    return symbol, None

        return dict(await asyncio.gather(*(load(symbol) for symbol in sorted(symbols))))

    async def _ledger_owned_rotation_rows(
        self,
        account: ExchangeConfig,
        strategies: dict,
        positions: list[dict],
    ) -> list[dict]:
        live = {
            key: position for position in positions
            if (key := self._position_key(position)) is not None
        }
        strategy_markers = {
            self.engine._format_strategy_marker(strategy)
            for strategy in strategies.values()
        }
        opened_at_by_key = {}
        if strategy_markers:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(
                    TradeRecord.symbol,
                    TradeRecord.direction,
                    TradeRecord.created_at,
                ).where(
                    TradeRecord.exchange_config_id == account.id,
                    TradeRecord.is_closed == False,
                    TradeRecord.strategy_tag.in_(strategy_markers),
                ))
                for symbol, direction, created_at in result.all():
                    if created_at is None:
                        continue
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    key = (str(symbol).upper(), str(direction).upper())
                    opened = created_at.timestamp()
                    if key not in opened_at_by_key or opened < opened_at_by_key[key]:
                        opened_at_by_key[key] = opened
        owned = {}
        for (pool, strategy_direction), strategy in strategies.items():
            for symbol, direction in await self.engine._get_strategy_live_position_keys(
                    account, strategy):
                key = (str(symbol).upper(), str(direction).upper())
                if key not in live or key[1] != strategy_direction:
                    continue
                owned.setdefault(key, {
                    "symbol": key[0],
                    "direction": key[1],
                    "pool": pool,
                    "risk_direction": economic_direction_for_symbol(*key),
                    "risk_factor": risk_factor_for_symbol(key[0], pool),
                    "notional_weight": self._rotation_notional(live[key]),
                    "opened_at_ts": opened_at_by_key.get(key),
                    "strategy": strategy,
                })
        markets = await self._rotation_markets({row["symbol"] for row in owned.values()})
        return [{**row, "market": markets.get(row["symbol"])}
                for row in owned.values()]

    async def _close_for_rotation(
        self,
        strategy: TradingStrategy,
        account: ExchangeConfig,
        symbol: str,
        direction: str,
        reason: str,
    ) -> bool:
        await self._emergency_close(strategy, account, symbol, direction, reason)
        for _ in range(3):
            await asyncio.sleep(1)
            keys = {key for position in await self._live_positions(account)
                    if (key := self._position_key(position)) is not None}
            if (symbol.upper(), direction.upper()) not in keys:
                return True
        return False

    async def _process_account_rotation(
        self,
        account: ExchangeConfig,
        plans: dict,
        selected_by_pool: dict,
        strategies: dict,
        changed_pools: set[str],
    ) -> dict:
        positions = await self._live_positions(account)
        rows = await self._ledger_owned_rotation_rows(account, strategies, positions)
        active_keys = {
            self._rotation_key(account.id, row["symbol"], row["direction"])
            for row in rows
        }
        prefix = f"{account.id}|"
        for key in list(self.rotation_dead_counts):
            if key.startswith(prefix) and key not in active_keys:
                del self.rotation_dead_counts[key]

        targets = {
            pool: {
                (str(leg.get("symbol") or "").upper(),
                 str(leg.get("direction") or "").upper())
                for side in ("LONG", "SHORT")
                for leg in (selected_by_pool.get(pool, {}).get(side) or [])
            }
            for pool in changed_pools
        }
        rankings = {
            pool: {
                (str(row.get("symbol") or "").upper(),
                 str(row.get("direction") or "").upper()): row
                for row in (plans.get(pool, {}).get("rotation_rankings") or [])
            }
            for pool in changed_pools
        }
        groups: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            if row["pool"] in changed_pools and row.get("market") \
                    and row.get("notional_weight", 0) > 0:
                groups.setdefault((row["pool"], row["risk_factor"]), []).append(row)
        rotation_config = self.config.get("cross_sectional_shadow") or {}
        minimum_samples = int(rotation_config.get(
            "rotation_hedge_min_return_samples", 96))
        contributions = {}
        for group_rows in groups.values():
            contributions.update(marginal_hedge_contributions(
                group_rows, min_samples=minimum_samples))

        min_volatility = float(rotation_config.get(
            "rotation_hedge_min_relative_volatility_reduction", 0.03))
        min_drawdown = float(rotation_config.get(
            "rotation_hedge_min_drawdown_reduction", 0.0025))
        required_slots = max(2, int(rotation_config.get("rotation_dead_slots", 2)))
        now_ts = time.time()
        live_keys = {(row["symbol"], row["direction"]) for row in rows}
        target_candidates = []
        for pool in changed_pools:
            for side in ("LONG", "SHORT"):
                for leg in selected_by_pool.get(pool, {}).get(side) or []:
                    target_candidates.append({
                        **leg,
                        "pool": pool,
                        "direction": side,
                        "directional_alpha": float(
                            leg.get("pair_spread", leg.get("quality", 0)) or 0
                        ),
                        "expected_edge_fraction": float(
                            leg.get("expected_edge_fraction") or 0
                        ),
                    })
        cost_bps = max(0.0, float(rotation_config.get("cost_bps_per_side", 3.0)))
        minimum_expected_edge = (
            cost_bps * 2 * float(rotation_config.get("rotation_cost_multiple", 2.0))
            / 10000
        )
        minimum_improvement = float(rotation_config.get(
            "rotation_min_alpha_improvement", 1.0))
        assessments = []
        choices = []
        for row in rows:
            pool = row["pool"]
            if pool not in changed_pools:
                continue
            key = (row["symbol"], row["direction"])
            classification = classify_leg(
                in_target=key in targets.get(pool, set()),
                contribution=contributions.get(key),
                minimum_relative_volatility_reduction=min_volatility,
                minimum_drawdown_reduction=min_drawdown,
            )
            minimum_holding_hours = rotation_min_holding_hours(
                rotation_config, pool, row["direction"]
            )
            holding_satisfied = holding_period_satisfied(
                row.get("opened_at_ts"), minimum_holding_hours, now_ts
            )
            effective_classification = (
                "MINIMUM_HOLD"
                if classification == "DEAD" and not holding_satisfied
                else classification
            )
            slot = str(plans.get(pool, {}).get("target_slot") or "")
            dead_count = self._record_rotation_classification(
                account.id, row["symbol"], row["direction"], pool, slot,
                effective_classification,
            )
            ranking = rankings.get(pool, {}).get(key)
            assessment = {
                "symbol": row["symbol"],
                "direction": row["direction"],
                "pool": pool,
                "risk_factor": row["risk_factor"],
                "classification": classification,
                "effective_classification": effective_classification,
                "minimum_holding_hours": minimum_holding_hours,
                "holding_period_satisfied": holding_satisfied,
                "dead_slots": dead_count,
                "hedge_contribution": contributions.get(key),
            }
            assessments.append(assessment)
            if effective_classification != "DEAD" \
                    or dead_count < required_slots or not ranking:
                continue
            stale = {
                **row,
                "directional_alpha": float(ranking.get("directional_alpha") or 0),
            }
            replacement = choose_rotation_replacement(
                stale,
                target_candidates,
                live_keys=live_keys,
                minimum_alpha_improvement=minimum_improvement,
                minimum_expected_edge_fraction=minimum_expected_edge,
            )
            if replacement:
                choices.append((replacement["alpha_improvement"], row, replacement))
        self._save_state()
        if not choices:
            return {"status": "OBSERVE", "rotated": [], "assessments": assessments}
        choices.sort(key=lambda item: (-item[0], item[1]["symbol"]))
        _, stale, replacement = choices[0]
        reason = (
            f"V5 scheduled dead-capital rotation after {required_slots} slots; "
            f"replacement={replacement['symbol']} alpha_improvement="
            f"{replacement['alpha_improvement']:.3f}"
        )
        closed = await self._close_for_rotation(
            stale["strategy"], account, stale["symbol"], stale["direction"], reason)
        if not closed:
            return {"status": "BLOCKED", "rotated": [],
                    "reason": "rotation_close_unverified", "assessments": assessments}
        self.rotation_dead_counts.pop(
            self._rotation_key(account.id, stale["symbol"], stale["direction"]), None)
        self._save_state()
        return {
            "status": "ROTATED",
            "rotated": [{
                "closed_symbol": stale["symbol"],
                "closed_direction": stale["direction"],
                "replacement_symbol": replacement["symbol"],
                "replacement_direction": replacement["direction"],
                "alpha_improvement": round(replacement["alpha_improvement"], 4),
            }],
            "assessments": assessments,
        }

    async def _process_rebalance_rotations(
        self,
        plans: dict,
        selected_by_pool: dict,
        changed_pools: set[str],
    ) -> dict:
        async with self.lock:
            strategies = await self._strategies()
            results = {}
            for account in await self._accounts():
                key = f"{account.id}:{account.name}"
                try:
                    results[key] = await self._process_account_rotation(
                        account, plans, selected_by_pool, strategies, changed_pools)
                except Exception as exc:
                    results[key] = {"status": "ERROR", "error": type(exc).__name__}
                    print(f"v5_rotation_error account_id={account.id} "
                          f"error={type(exc).__name__}", flush=True)
            rotated = any(result.get("rotated") for result in results.values())
            return {"status": "ROTATED" if rotated else "OBSERVE",
                    "accounts": results}

    async def _open_leg(self, strategy, account, leg: dict, margin: float) -> bool:
        symbol, direction = leg["symbol"], leg["direction"]
        score = float(leg.get("score") or 0)
        entry_minimum_score = float(
            leg.get("entry_minimum_score", self.config.get("minimum_score", 0)) or 0
        )
        if score < entry_minimum_score:
            return False
        ticker = await okx_manager.get_ticker(symbol)
        price = float(ticker.get("last") or 0)
        if not math.isfinite(price) or price <= 0:
            return False
        quantity, leverage = await self._quantity(strategy, symbol, price, margin)
        if quantity <= 0:
            return False
        self.engine._pending_trade_quantity = quantity
        self.engine._pending_trade_context = {
            "entry_score": score,
            "entry_minimum_score": entry_minimum_score,
            "entry_score_scale": str(leg.get("score_scale") or "legacy_trend"),
            "leverage": leverage,
            "v5_portfolio_entry": True,
        }
        async with AsyncSessionLocal() as db:
            fresh_strategy = await db.get(TradingStrategy, strategy.id)
            fresh_account = await db.get(ExchangeConfig, account.id)
            opened = await self.engine._execute_trade(
                db, fresh_strategy, fresh_account,
                "BUY" if direction == "LONG" else "SELL", price, symbol,
            )
        if not opened:
            return False
        await asyncio.sleep(1)
        native_stop_confirmed = await self._protected(account, symbol, direction)
        liquidation_safe, liquidation_reason = await self._position_has_liquidation_buffer(
            account, symbol, direction)
        if native_stop_confirmed and liquidation_safe:
            return True
        failure = "native stop not confirmed" if not native_stop_confirmed else liquidation_reason
        await self._emergency_close(strategy, account, symbol, direction,
                                    f"V5 fail-closed: {failure}")
        self._set_fail_closed_block(account.id, symbol, direction, failure)
        return False

    async def _process_account_scan(self, account: ExchangeConfig, selected_by_pool: dict,
                                    strategies: dict) -> dict:
        if not await self.reconcile_native_stops(account, strategies):
            return {"status": "BLOCKED", "reason": "native_stop_coverage_unverified"}
        positions = await self._live_positions(account)
        live = {key for row in positions if (key := self._position_key(row))}
        counts = {side: sum(
            economic_direction_for_symbol(symbol, direction) == side
            for symbol, direction in live)
                  for side in ("LONG", "SHORT")}
        equity, available = await self._equity(account)
        if equity <= 0 or available <= 0:
            return {"status": "BLOCKED", "reason": "no_available_margin"}
        max_per_side = int(self.config.get(
            "max_positions_per_side", self.config["max_pairs"]))
        max_total = int(self.config.get("max_positions_total", max_per_side * 2))
        fail_closed_blocked = self._blocked_keys(account.id)
        exited_target_blocked = self._exit_reentry_blocked_keys(account.id)
        blocked = fail_closed_blocked | exited_target_blocked
        fail_closed_reentries = sum(
            (str(leg.get("symbol") or "").upper(), side) in fail_closed_blocked
            for selected in selected_by_pool.values()
            for side in ("LONG", "SHORT")
            for leg in (selected.get(side) or [])
        )
        exited_target_reentries = sum(
            (str(leg.get("symbol") or "").upper(), side) in exited_target_blocked
            for selected in selected_by_pool.values()
            for side in ("LONG", "SHORT")
            for leg in (selected.get(side) or [])
        )
        filtered = {
            pool: {
                side: [leg for leg in (selected.get(side) or [])
                       if (str(leg.get("symbol") or "").upper(), side) not in blocked]
                for side in ("LONG", "SHORT")
            }
            for pool, selected in selected_by_pool.items()
        }
        blocked_reentries = sum(
            len(selected.get(side) or []) - len(filtered[pool][side])
            for pool, selected in selected_by_pool.items()
            for side in ("LONG", "SHORT")
        )
        legs = select_legs(
            filtered, live, counts, max_per_side, max_total=max_total)
        legs, correlation_blocks = await self._filter_existing_correlations(legs, live)
        used_by_side = {side: self._used_initial_margin([
            row for row in positions if self._position_key(row)
            and economic_direction_for_symbol(*self._position_key(row)) == side
        ]) for side in ("LONG", "SHORT")}
        used_by_factor_side: dict[str, dict[str, float]] = {}
        crypto_symbols = {symbol.upper() for symbol in self.universes.get("crypto", [])}
        for row in positions:
            key = self._position_key(row)
            if key is None:
                continue
            symbol, direction = key
            pool = "crypto" if symbol in crypto_symbols else "tradfi"
            factor = risk_factor_for_symbol(symbol, pool)
            side = economic_direction_for_symbol(symbol, direction)
            margins = used_by_factor_side.setdefault(factor, {"LONG": 0.0, "SHORT": 0.0})
            margins[side] += self._used_initial_margin([row])
        legs, factor_recovery = prioritize_factor_recovery(
            legs, used_by_factor_side,
            float(self.config.get("max_factor_side_risk_share", .70)),
        )
        legs, exposure_recovery = restrict_legs_for_exposure_recovery(
            legs, used_by_side, float(self.config.get("max_side_risk_share", .70)))
        legs = legs[:int(self.config.get("max_new_legs_per_scan", 2))]
        if not legs:
            at_total_cap = len({symbol for symbol, _ in live}) >= max_total
            return {"status": "WAIT", "opened": [],
                    "correlation_blocks": correlation_blocks,
                    "reason": "account_position_cap" if at_total_cap else (
                        "economic_exposure_recovery_wait" if exposure_recovery else None),
                    "position_count": len({symbol for symbol, _ in live}),
                    "position_limit": max_total,
                    "exposure_recovery": exposure_recovery,
                    "factor_recovery": factor_recovery,
                    "reentries_blocked": blocked_reentries,
                    "fail_closed_reentries_blocked": fail_closed_reentries,
                    "exited_target_reentries_blocked": exited_target_reentries}
        exposure = dynamic_exposure_plan(legs, counts, self.config)
        if exposure["single_side"]:
            side = next(
                leg.get("risk_direction") or economic_direction_for_symbol(
                    leg["symbol"], leg["direction"])
                for leg in legs
            )
            slots = max(0, int(self.config.get("single_side_max_positions", 2)) - counts[side])
            legs = [leg for leg in legs if (
                leg.get("risk_direction") or economic_direction_for_symbol(
                    leg["symbol"], leg["direction"])) == side][:slots]
            if not legs:
                return {"status": "WAIT", "opened": [], "reason": "single_side_position_cap"}
        target_margin = equity * exposure["margin_limit"]
        used_margin = self._used_initial_margin(positions)
        remaining_margin = max(0.0, min(available * 0.98, target_margin - used_margin))
        side_share = exposure["side_share"]
        if remaining_margin < float(self.config["minimum_leg_margin_usdt"]):
            return {"status": "BLOCKED", "reason": "leg_margin_below_minimum"}
        opened = []
        opened_margins = {}
        protective_stop = max(
            float(self.config["hard_stop_price_pct"]),
            float(self.config["native_stop_price_pct"]),
        )
        base_per_leg_margin_cap = loss_bounded_margin_cap(
            equity,
            float(self.config["requested_leverage"]),
            protective_stop,
            float(self.config.get("max_loss_per_leg_equity_fraction", 0.03)),
        )
        used_by_factor = {
            factor: sum(margins.values())
            for factor, margins in used_by_factor_side.items()
        }
        margin_blocks = []
        for leg in legs:
            contract_side = leg["direction"]
            side = leg.get("risk_direction") or economic_direction_for_symbol(
                leg["symbol"], contract_side)
            side_target = target_margin * side_share[side]
            side_remaining = max(0.0, side_target - used_by_side[side])
            divisor = int(self.config.get("single_side_max_positions", 2)) \
                if exposure["single_side"] else max_per_side
            factor = leg.get("risk_factor") or risk_factor_for_symbol(
                leg["symbol"], leg.get("pool"))
            factor_remaining = factor_margin_remaining(
                equity, factor, used_by_factor.get(factor, 0.0), self.config)
            risk_multiplier = pool_direction_risk_multiplier(
                self.config.get("cross_sectional_shadow") or {},
                leg.get("pool"),
                contract_side,
            )
            per_leg_margin_cap = risk_adjusted_leg_margin_cap(
                base_per_leg_margin_cap,
                self.config.get("cross_sectional_shadow") or {},
                leg.get("pool"),
                contract_side,
            )
            margin = min(
                side_target / divisor,
                side_remaining,
                remaining_margin,
                per_leg_margin_cap,
                factor_remaining,
            )
            if margin < float(self.config["minimum_leg_margin_usdt"]):
                margin_blocks.append({
                    "symbol": leg["symbol"],
                    "calculated_margin": round(margin, 4),
                    "minimum_margin": float(self.config["minimum_leg_margin_usdt"]),
                    "risk_cap": round(per_leg_margin_cap, 4),
                    "risk_multiplier": risk_multiplier,
                })
                continue
            strategy = strategies[(leg["pool"], contract_side)]
            if not await self._open_leg(strategy, account, leg, margin):
                continue
            opened.append(leg["symbol"])
            opened_margins[leg["symbol"]] = round(margin, 4)
            counts[side] += 1
            used_by_side[side] += margin
            used_by_factor[factor] = used_by_factor.get(factor, 0.0) + margin
            remaining_margin -= margin
        return {"status": "OPENED" if opened else "WAIT", "opened": opened,
                "reason": (None if opened else (
                    "leg_margin_below_minimum_after_risk_caps"
                    if margin_blocks else "no_leg_opened"
                )),
                "margin_blocks": margin_blocks,
                "correlation_blocks": correlation_blocks,
                "side_margin_share": {key: round(value, 4)
                                      for key, value in side_share.items()},
                "opened_margins": opened_margins,
                "exposure_recovery": exposure_recovery,
                "factor_recovery": factor_recovery,
                "used_factor_margin_after": {
                    key: round(value, 4) for key, value in used_by_factor.items()
                },
                "reentries_blocked": blocked_reentries,
                "fail_closed_reentries_blocked": fail_closed_reentries,
                "exited_target_reentries_blocked": exited_target_reentries,
                "position_count_before": len({symbol for symbol, _ in live}),
                "position_limit": max_total,
                "used_margin_before": round(used_margin, 4),
                "account_margin_target": round(target_margin, 4)}

    async def process_scan(self, completed: str, selected_by_pool: dict) -> dict:
        if not self.config.get("execution_enabled") or not completed or completed == self.last_processed:
            return {"status": "SKIP"}
        async with self.lock:
            if completed == self.last_processed:
                return {"status": "SKIP"}
            self._save_last_processed(completed)
            accounts = await self._accounts()
            if not accounts:
                return {"status": "BLOCKED", "reason": "no_active_account"}
            strategies = await self._strategies()
            results = {}
            for account in accounts:
                key = f"{account.id}:{account.name}"
                try:
                    results[key] = await self._process_account_scan(
                        account, selected_by_pool, strategies)
                except Exception as exc:
                    results[key] = {"status": "ERROR", "error": type(exc).__name__}
                    print(f"v5_account_scan_error account_id={account.id} "
                          f"error={type(exc).__name__}", flush=True)
            statuses = {result["status"] for result in results.values()}
            overall = "OPENED" if "OPENED" in statuses else (
                "ERROR" if statuses == {"ERROR"} else "WAIT")
            return {"status": overall, "accounts": results}

    async def process_cross_sectional_scan(self, completed: str, plans: dict) -> dict:
        slots = {
            pool: str(plan.get("target_slot") or "")
            for pool, plan in (plans or {}).items()
            if isinstance(plan, dict) and plan.get("target_slot")
        }
        changed_pools = {
            pool for pool, slot in slots.items()
            if self.active_target_slots.get(pool) != slot
            and bool((plans.get(pool) or {}).get("rebalance_due"))
            and (plans.get(pool) or {}).get("status") == "LIVE_REBALANCE"
        }
        if slots and any(self.active_target_slots.get(pool) != slot
                         for pool, slot in slots.items()):
            self.active_target_slots.update(slots)
            for account in await self._accounts():
                self._exit_reentry_blocked_keys(account.id)
            self._save_state()
        cross_config = self.config.get("cross_sectional_shadow") or {}
        selected = cross_sectional_selected_by_pool(
            plans,
            float(self.config.get("minimum_score", 6.5)),
            require_factor_gate=bool(
                cross_config.get("factor_entry_gate_enabled")
            ),
            directional_minimum_score=float(
                cross_config.get("directional_entry_score_min", 3.0)
            ),
        )
        rotation = None
        if changed_pools:
            rotation = await self._process_rebalance_rotations(
                plans, selected, changed_pools)
            if rotation.get("status") == "ROTATED":
                # Open the verified replacement on a later scan, after the
                # exchange and ledger both confirm the outgoing leg is gone.
                self._save_last_processed(completed)
                return {"status": "ROTATED", "mode": "cross_sectional_live",
                        "rotation": rotation}
        if not any(rows.get(side) for rows in selected.values()
                   for side in ("LONG", "SHORT")):
            return {"status": "WAIT", "mode": "cross_sectional_live",
                    "reason": "no_valid_executable_factor_target",
                    "rotation": rotation}
        result = await self.process_scan(completed, selected)
        return {**result, "mode": "cross_sectional_live", "rotation": rotation}

    async def risk_loop(self) -> None:
        while True:
            try:
                accounts = await self._accounts()
                if accounts:
                    if time.monotonic() - getattr(self, "_last_stop_audit", 0) >= 60:
                        try:
                            async with self.lock:
                                strategies = await self._strategies()
                                for account in accounts:
                                    await self.reconcile_native_stops(account, strategies)
                        except Exception as exc:
                            print(f"v5_stop_audit_error {type(exc).__name__}", flush=True)
                        self._last_stop_audit = time.monotonic()
                    async with self.lock:
                        async with AsyncSessionLocal() as db:
                            fresh = [await db.get(ExchangeConfig, account.id)
                                     for account in accounts]
                            await self.engine._process_exit_checks(
                                db, [account for account in fresh if account is not None])
                    # Audit after exits so exchange latency cannot delay hard
                    # stops. Only V5-owned stale rows may be closed; external
                    # positions are never imported into V5 ownership.
                    await self._close_stale_v5_ledger_if_due(accounts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"v5_risk_loop_error {type(exc).__name__}", flush=True)
            await asyncio.sleep(10)
