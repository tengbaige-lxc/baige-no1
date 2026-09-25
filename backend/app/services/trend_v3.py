from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Action(str, Enum):
    WATCH = "WATCH"
    OPEN = "OPEN"
    ADD = "ADD"
    REJECT = "REJECT"


def oil_fundamental_applies_to(symbol: str) -> bool:
    """WTI reports are directional input for the WTI perpetual only."""
    return symbol.strip().upper() == "CL-USDT-SWAP"


def has_structural_confirmation(
    moer_reentry: bool,
    ma_cross: bool,
    divergence: bool,
) -> bool:
    """Require one non-volume timing confirmation for high-conviction entries."""
    return bool(moer_reentry or ma_cross or divergence)


@dataclass(frozen=True)
class TrendContext:
    """Point-in-time inputs for the V3 trend state machine.

    Values are normalized by adapters. Positive values mean support for the
    proposed direction, negative values mean opposition, and zero is neutral.
    """

    symbol: str
    direction: Direction
    trend_4h: int
    adx_4h: float
    structure_30m: int
    moer_reentry_30m: bool
    trigger_5m: bool
    divergence_5m: bool
    volume_ratio_5m: float
    speed_ratio_5m: float
    derivatives_crowded: bool = False
    derivatives_extreme: bool = False
    already_open: bool = False
    add_on_confirmed: bool = False
    fundamental_direction: Direction | None = None
    fundamental_valid_until: str = ""
    macro_event_score: float = 0.0
    macro_event_label: str = ""


@dataclass(frozen=True)
class TrendDecision:
    action: Action
    score: float
    reasons: tuple[str, ...]


class TrendV3:
    """Trend-first entry and add-on policy.

    The higher timeframe defines direction. The 30m structure defines whether
    the move is actionable. A 5m event times the order. This intentionally
    avoids V2's all-factors-must-pass design, which frequently produced no
    trade even during valid trend starts.
    """

    def __init__(
        self,
        *,
        min_open_score: float = 5.0,
        min_add_score: float = 6.0,
        strong_adx: float = 23.0,
        min_volume_ratio: float = 1.8,
        min_speed_ratio: float = 1.5,
    ) -> None:
        self.min_open_score = min_open_score
        self.min_add_score = min_add_score
        self.strong_adx = strong_adx
        self.min_volume_ratio = min_volume_ratio
        self.min_speed_ratio = min_speed_ratio

    def evaluate(self, ctx: TrendContext) -> TrendDecision:
        reasons: list[str] = []
        if ctx.fundamental_direction is not None:
            if not self._fundamental_is_current(ctx.fundamental_valid_until):
                return TrendDecision(Action.REJECT, 0.0, ("fundamental_report_expired",))
            if ctx.direction is not ctx.fundamental_direction:
                return TrendDecision(Action.REJECT, 0.0, ("fundamental_direction_opposes",))
            reasons.append("fundamental_direction_aligned")
        if ctx.trend_4h < 0:
            return TrendDecision(Action.REJECT, 0.0, ("4h_trend_opposes_direction",))
        if ctx.derivatives_extreme:
            return TrendDecision(Action.REJECT, 0.0, ("derivatives_extremely_crowded",))

        score = 0.0
        if ctx.trend_4h > 0:
            score += 2.0
            reasons.append("4h_trend_aligned")
        else:
            reasons.append("4h_trend_neutral")

        if ctx.adx_4h >= self.strong_adx:
            score += 1.0
            reasons.append("4h_adx_confirms")

        if ctx.structure_30m > 0:
            score += 1.5
            reasons.append("30m_structure_aligned")
        elif ctx.structure_30m < 0:
            return TrendDecision(Action.REJECT, score, tuple(reasons + ["30m_structure_failed"]))

        if ctx.moer_reentry_30m:
            score += 1.0
            reasons.append("30m_moer_reentry")

        volume_speed = (
            ctx.volume_ratio_5m >= self.min_volume_ratio
            and ctx.speed_ratio_5m >= self.min_speed_ratio
        )
        if ctx.trigger_5m and volume_speed:
            score += 2.0
            reasons.append("5m_volume_speed_trigger")
        elif ctx.trigger_5m:
            score += 1.0
            reasons.append("5m_structure_trigger")
        else:
            return TrendDecision(Action.WATCH, score, tuple(reasons + ["await_5m_trigger"]))

        if ctx.divergence_5m:
            score += 0.5
            reasons.append("5m_divergence_confirms")
        if ctx.derivatives_crowded:
            score -= 1.0
            reasons.append("crowded_derivatives_penalty")
        if not ctx.already_open and ctx.macro_event_score > 0:
            macro_event_score = min(2.0, float(ctx.macro_event_score))
            score += macro_event_score
            reasons.append(ctx.macro_event_label or f"macro_event_bonus={macro_event_score:g}")

        if ctx.already_open:
            # The caller defines add_on_confirmed.  It can require a fresh
            # 30m Moer re-entry for a conservative add, or a confirmed 1m+5m
            # continuation while the 30m trend is still intact.
            if ctx.add_on_confirmed and score >= self.min_add_score:
                return TrendDecision(Action.ADD, score, tuple(reasons + ["add_on_confirmed"]))
            return TrendDecision(Action.WATCH, score, tuple(reasons + ["no_add_on_confirmation"]))

        if score >= self.min_open_score:
            return TrendDecision(Action.OPEN, score, tuple(reasons))
        return TrendDecision(Action.WATCH, score, tuple(reasons + ["score_below_open_threshold"]))

    @staticmethod
    def _fundamental_is_current(valid_until: str) -> bool:
        if not valid_until:
            return False
        try:
            deadline = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        except ValueError:
            return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) <= deadline.astimezone(timezone.utc)
