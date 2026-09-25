from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitAction(str, Enum):
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


@dataclass(frozen=True)
class TrendState:
    price: float
    ma34: float
    ma170: float
    ma34_slope: float

    def aligned(self, direction: Direction) -> bool:
        if direction is Direction.LONG:
            return self.price > self.ma170 and self.ma34 > self.ma170 and self.ma34_slope > 0
        return self.price < self.ma170 and self.ma34 < self.ma170 and self.ma34_slope < 0


@dataclass(frozen=True)
class EntryContext:
    direction: Direction
    higher_4h: TrendState
    structure_30m: TrendState
    divergence_1m_at: float | None
    divergence_5m_at: float | None
    now: float
    adx_30m: float
    atr_pct_30m: float
    ma5_slope_5m: float
    structure_signal: str | None = None
    breakout_retest: bool = False
    liquidation_risk: bool = False
    crowded_derivatives: bool = False


@dataclass(frozen=True)
class EntryDecision:
    allowed: bool
    quality: float
    blockers: tuple[str, ...]
    evidence: tuple[str, ...]


@dataclass
class PositionExitState:
    peak_roe: float = 0.0
    fired_stages: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class ExitContext:
    direction: Direction
    roe: float
    price_change: float
    trend_valid: bool
    opposite_5m_divergence: bool
    daily_ma5_slope: float


@dataclass(frozen=True)
class ExitDecision:
    action: ExitAction
    reduce_ratio: float
    stage: str | None
    reason: str


class TrendV2:
    """Small deterministic core for trend entry ranking and one-shot exits."""

    def __init__(
        self,
        *,
        divergence_window_seconds: int = 1800,
        min_adx: float = 18.0,
        min_atr_pct: float = 0.002,
        max_atr_pct: float = 0.05,
        hard_stop_price_pct: float = 0.025,
        trailing_activation_roe: float = 0.25,
        trailing_drawdown_ratio: float = 0.38,
    ) -> None:
        self.divergence_window_seconds = divergence_window_seconds
        self.min_adx = min_adx
        self.min_atr_pct = min_atr_pct
        self.max_atr_pct = max_atr_pct
        self.hard_stop_price_pct = hard_stop_price_pct
        self.trailing_activation_roe = trailing_activation_roe
        self.trailing_drawdown_ratio = trailing_drawdown_ratio

    def evaluate_entry(self, ctx: EntryContext) -> EntryDecision:
        blockers: list[str] = []
        evidence: list[str] = []

        if not ctx.higher_4h.aligned(ctx.direction):
            blockers.append("4h_direction_not_aligned")
        else:
            evidence.append("4h_direction")

        if not ctx.structure_30m.aligned(ctx.direction):
            blockers.append("30m_structure_not_aligned")
        else:
            evidence.append("30m_structure")

        ordered_divergence = (
            ctx.divergence_1m_at is not None
            and ctx.divergence_5m_at is not None
            and 0 <= ctx.divergence_5m_at - ctx.divergence_1m_at <= self.divergence_window_seconds
            and ctx.now - ctx.divergence_5m_at <= self.divergence_window_seconds
        )
        if not ordered_divergence:
            blockers.append("missing_ordered_1m_5m_divergence")
        else:
            evidence.append("ordered_1m_5m_divergence")

        if ctx.adx_30m < self.min_adx:
            blockers.append("adx_too_low")
        elif not self.min_atr_pct <= ctx.atr_pct_30m <= self.max_atr_pct:
            blockers.append("atr_not_tradeable")
        else:
            evidence.append("tradeable_regime")

        expected_ma5_sign = 1 if ctx.direction is Direction.LONG else -1
        if ctx.ma5_slope_5m * expected_ma5_sign <= 0:
            blockers.append("5m_ma5_wrong_slope")
        else:
            evidence.append("5m_ma5_slope")

        if ctx.liquidation_risk:
            blockers.append("liquidation_risk")
        if ctx.crowded_derivatives:
            blockers.append("crowded_derivatives")

        # Quality ranks already-valid candidates; it never overrides a blocker.
        quality = 0.0
        quality += 40.0 if "ordered_1m_5m_divergence" in evidence else 0.0
        quality += 25.0 if "4h_direction" in evidence else 0.0
        quality += 15.0 if "30m_structure" in evidence else 0.0
        quality += 10.0 if "tradeable_regime" in evidence else 0.0
        quality += 5.0 if "5m_ma5_slope" in evidence else 0.0
        quality += 3.0 if ctx.structure_signal in {"B2", "B3", "S2", "S3"} else 0.0
        quality += 2.0 if ctx.breakout_retest else 0.0
        return EntryDecision(not blockers, quality, tuple(blockers), tuple(evidence))

    def evaluate_exit(
        self, ctx: ExitContext, state: PositionExitState
    ) -> ExitDecision:
        state.peak_roe = max(state.peak_roe, ctx.roe)

        if ctx.price_change <= -self.hard_stop_price_pct:
            return ExitDecision(ExitAction.CLOSE, 1.0, "hard_stop", "hard price stop")
        if not ctx.trend_valid:
            return ExitDecision(ExitAction.CLOSE, 1.0, "trend_invalid", "4h/30m trend invalid")

        daily_ma5_still_supports = (
            ctx.daily_ma5_slope >= 0
            if ctx.direction is Direction.LONG
            else ctx.daily_ma5_slope <= 0
        )
        if daily_ma5_still_supports:
            return ExitDecision(
                ExitAction.HOLD,
                0.0,
                None,
                "daily MA5 has not turned against the position; defer profit taking",
            )

        if ctx.opposite_5m_divergence and "opposite_divergence" not in state.fired_stages:
            state.fired_stages.add("opposite_divergence")
            return ExitDecision(
                ExitAction.REDUCE, 0.40, "opposite_divergence", "first opposite 5m divergence"
            )

        trailing_drawdown = (
            (state.peak_roe - ctx.roe) / state.peak_roe if state.peak_roe > 0 else 0.0
        )
        if (
            state.peak_roe >= self.trailing_activation_roe
            and trailing_drawdown >= self.trailing_drawdown_ratio
            and "trailing_profit" not in state.fired_stages
        ):
            state.fired_stages.add("trailing_profit")
            return ExitDecision(
                ExitAction.REDUCE, 0.40, "trailing_profit", "38% drawdown from peak ROE"
            )

        return ExitDecision(ExitAction.HOLD, 0.0, None, "trend remains valid")
