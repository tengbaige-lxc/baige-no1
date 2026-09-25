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
)
from app.services.native_stop_validation import native_stop_covers


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
                "reduce_ratio": 0.25,
                "callback_tiers": [
                    {"peak": 0.80, "callback": 0.30},
                    {"peak": 1.50, "callback": 0.25},
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
) -> dict:
    """Validate cross-sectional targets and adapt them to the live executor."""
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
            else:
                score = max(
                    float(minimum_score),
                    float(minimum_score) + min(2.0, pair_spread / 2.0),
                )
            selected[direction].append({
                **leg,
                "symbol": symbol,
                "pool": pool,
                "direction": direction,
                "score": score,
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
        if self.engine is not None:
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
        }))
        temporary.replace(self.state_file)

    def _save_last_processed(self, value: str) -> None:
        self.last_processed = value
        self._save_state()

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
        return any(native_stop_covers(row, symbol, direction,
                   abs(float(position["pos"])), expected) for row in pending or [])

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

    async def _open_leg(self, strategy, account, leg: dict, margin: float) -> bool:
        symbol, direction = leg["symbol"], leg["direction"]
        ticker = await okx_manager.get_ticker(symbol)
        price = float(ticker.get("last") or 0)
        if not math.isfinite(price) or price <= 0:
            return False
        quantity, leverage = await self._quantity(strategy, symbol, price, margin)
        if quantity <= 0:
            return False
        self.engine._pending_trade_quantity = quantity
        self.engine._pending_trade_context = {
            "entry_score": float(leg["score"]),
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
        per_leg_margin_cap = loss_bounded_margin_cap(
            equity,
            float(self.config["requested_leverage"]),
            protective_stop,
            float(self.config.get("max_loss_per_leg_equity_fraction", 0.03)),
        )
        used_by_factor = {
            factor: sum(margins.values())
            for factor, margins in used_by_factor_side.items()
        }
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
            margin = min(
                side_target / divisor,
                side_remaining,
                remaining_margin,
                per_leg_margin_cap,
                factor_remaining,
            )
            if margin < float(self.config["minimum_leg_margin_usdt"]):
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
        if slots and any(self.active_target_slots.get(pool) != slot
                         for pool, slot in slots.items()):
            self.active_target_slots.update(slots)
            for account in await self._accounts():
                self._exit_reentry_blocked_keys(account.id)
            self._save_state()
        selected = cross_sectional_selected_by_pool(
            plans,
            float(self.config.get("minimum_score", 6.5)),
            require_factor_gate=bool(
                (self.config.get("cross_sectional_shadow") or {}).get(
                    "factor_entry_gate_enabled"
                )
            ),
        )
        if not any(rows.get(side) for rows in selected.values()
                   for side in ("LONG", "SHORT")):
            return {"status": "WAIT", "mode": "cross_sectional_live",
                    "reason": "no_valid_executable_factor_target"}
        result = await self.process_scan(completed, selected)
        return {**result, "mode": "cross_sectional_live"}

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
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"v5_risk_loop_error {type(exc).__name__}", flush=True)
            await asyncio.sleep(10)
