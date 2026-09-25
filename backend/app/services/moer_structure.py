"""Lightweight Moer-style long structure confirmation for white-dove.

This module intentionally stays dependency-free.  It implements the live
decision subset used by white-dove: 4H MA170 environment, a 30m MA34 centre,
and confirmed B2/B3-style pullback states.  It is a quality factor, not a
standalone trading engine.
"""

from __future__ import annotations

from typing import Any


def _sma(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    total = sum(values[:period])
    result[period - 1] = total / period
    for index in range(period, len(values)):
        total += values[index] - values[index - period]
        result[index] = total / period
    return result


def _parse_klines(klines: list[Any]) -> tuple[list[float], list[float], list[float]]:
    lows: list[float] = []
    highs: list[float] = []
    closes: list[float] = []
    for row in klines:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
        except (TypeError, ValueError):
            continue
    return lows, highs, closes


def _latest_ma34_centre(ma5: list[float | None], ma34: list[float | None]) -> dict[str, Any]:
    """Build a current MA34 centre from the latest three MA5/MA34 legs."""
    start = next((index for index, value in enumerate(ma34) if value is not None), None)
    if start is None:
        return {"available": False}

    legs: list[tuple[int, int, int]] = []
    direction = 1 if (ma5[start] or 0.0) >= (ma34[start] or 0.0) else -1
    leg_start = start
    for index in range(start + 1, len(ma34)):
        if ma5[index] is None or ma34[index] is None:
            continue
        next_direction = 1 if ma5[index] >= ma34[index] else -1
        if next_direction != direction:
            legs.append((direction, leg_start, index - 1))
            direction, leg_start = next_direction, index
    legs.append((direction, leg_start, len(ma34) - 1))
    if len(legs) < 3:
        return {"available": False, "leg_count": len(legs)}

    selected = legs[-3:]
    ranges = []
    for _, left, right in selected:
        values = [value for value in ma34[left:right + 1] if value is not None]
        if not values:
            return {"available": False, "leg_count": len(legs)}
        ranges.append((min(values), max(values)))
    zd = max(low for low, _ in ranges)
    zg = min(high for _, high in ranges)
    return {
        "available": zg > zd,
        "zd": zd,
        "zg": zg,
        "leg_count": len(legs),
        "legs": selected,
    }


def evaluate_moer_long_structure(
    higher_klines: list[Any],
    entry_klines: list[Any],
    *,
    centre_tolerance: float = 0.0015,
    b3_lookback: int = 48,
) -> dict[str, Any]:
    """Return a causal 4H/30m Moer-style long quality state.

    B2 is a confirmed 30m pullback low that holds MA34 and turns MA5 upward.
    B3 is the same confirmed pullback after a recent break above an MA34 centre,
    while the pullback remains above the centre's upper boundary.
    """
    _, _, higher_closes = _parse_klines(higher_klines)
    lows, _, closes = _parse_klines(entry_klines)
    if len(higher_closes) < 170 or len(closes) < 40 or len(lows) != len(closes):
        return {
            "available": False,
            "state": "insufficient_data",
            "score": 0.0,
            "detail": f"need 4H>=170 and 30m>=40; got 4H={len(higher_closes)} 30m={len(closes)}",
        }

    higher_ma170 = _sma(higher_closes, 170)[-1]
    ma5 = _sma(closes, 5)
    ma34 = _sma(closes, 34)
    current_ma5 = ma5[-1]
    previous_ma5 = ma5[-2]
    current_ma34 = ma34[-1]
    pivot = len(closes) - 2
    pivot_ma34 = ma34[pivot]
    if None in (higher_ma170, current_ma5, previous_ma5, current_ma34, pivot_ma34):
        return {"available": False, "state": "warming_up", "score": 0.0, "detail": "MA warmup incomplete"}

    big_regime_bull = higher_closes[-1] > higher_ma170
    ma5_turns_up = current_ma5 > previous_ma5
    local_bottom = lows[pivot] <= lows[pivot - 1] and lows[pivot] < lows[pivot + 1]
    entry_bullish = closes[-1] > current_ma34 and current_ma5 >= current_ma34 and ma5_turns_up
    b2 = bool(
        big_regime_bull
        and entry_bullish
        and local_bottom
        and lows[pivot] >= pivot_ma34 * (1.0 - centre_tolerance)
    )

    centre = _latest_ma34_centre(ma5, ma34)
    breakout_index: int | None = None
    b3 = False
    if centre.get("available"):
        zg = float(centre["zg"])
        start = max(1, len(closes) - max(8, b3_lookback))
        for index in range(start, pivot):
            if closes[index - 1] <= zg < closes[index]:
                breakout_index = index
        if breakout_index is not None:
            b3 = bool(
                big_regime_bull
                and entry_bullish
                and local_bottom
                and pivot > breakout_index
                and lows[pivot] >= zg * (1.0 - centre_tolerance)
                and closes[-1] > zg
            )

    state = "B3" if b3 else "B2" if b2 else "none"
    return {
        "available": True,
        "state": state,
        "score": 1.4 if b3 else 0.8 if b2 else 0.0,
        "detail": (
            f"4H close={higher_closes[-1]:.6g} MA170={higher_ma170:.6g} bull={big_regime_bull} | "
            f"30m close={closes[-1]:.6g} MA5={current_ma5:.6g} MA34={current_ma34:.6g} "
            f"pivot_bottom={local_bottom} | centre={centre.get('zd')}..{centre.get('zg')} | "
            f"breakout_index={breakout_index} | state={state}"
        ),
        "raw": {
            "big_regime_bull": big_regime_bull,
            "entry_bullish": entry_bullish,
            "local_bottom": local_bottom,
            "ma5_turns_up": ma5_turns_up,
            "centre": centre,
            "breakout_index": breakout_index,
            "b2": b2,
            "b3": b3,
        },
    }


def evaluate_moer_short_structure(
    higher_klines: list[Any],
    entry_klines: list[Any],
    *,
    centre_tolerance: float = 0.0015,
    s3_lookback: int = 48,
) -> dict[str, Any]:
    """Return the short-side mirror of the live Moer long quality state.

    This is an explicitly symmetric futures adaptation, not a claim that the
    original long-only Moer research independently validated short alpha.  S2
    is a MA34-rejected rebound and S3 is a failed rebound after a centre break.
    """
    _, higher_highs, higher_closes = _parse_klines(higher_klines)
    lows, highs, closes = _parse_klines(entry_klines)
    if len(higher_closes) < 170 or len(closes) < 40 or len(highs) != len(closes):
        return {
            "available": False,
            "state": "insufficient_data",
            "score": 0.0,
            "detail": f"need 4H>=170 and 30m>=40; got 4H={len(higher_closes)} 30m={len(closes)}",
        }

    higher_ma170 = _sma(higher_closes, 170)[-1]
    ma5 = _sma(closes, 5)
    ma34 = _sma(closes, 34)
    current_ma5 = ma5[-1]
    previous_ma5 = ma5[-2]
    current_ma34 = ma34[-1]
    pivot = len(closes) - 2
    pivot_ma34 = ma34[pivot]
    if None in (higher_ma170, current_ma5, previous_ma5, current_ma34, pivot_ma34):
        return {"available": False, "state": "warming_up", "score": 0.0, "detail": "MA warmup incomplete"}

    big_regime_bear = higher_closes[-1] < higher_ma170
    ma5_turns_down = current_ma5 < previous_ma5
    local_top = highs[pivot] >= highs[pivot - 1] and highs[pivot] > highs[pivot + 1]
    entry_bearish = closes[-1] < current_ma34 and current_ma5 <= current_ma34 and ma5_turns_down
    s2 = bool(
        big_regime_bear
        and entry_bearish
        and local_top
        and highs[pivot] <= pivot_ma34 * (1.0 + centre_tolerance)
    )

    centre = _latest_ma34_centre(ma5, ma34)
    breakdown_index: int | None = None
    s3 = False
    if centre.get("available"):
        zd = float(centre["zd"])
        start = max(1, len(closes) - max(8, s3_lookback))
        for index in range(start, pivot):
            if closes[index - 1] >= zd > closes[index]:
                breakdown_index = index
        if breakdown_index is not None:
            s3 = bool(
                big_regime_bear
                and entry_bearish
                and local_top
                and pivot > breakdown_index
                and highs[pivot] <= zd * (1.0 + centre_tolerance)
                and closes[-1] < zd
            )

    state = "S3" if s3 else "S2" if s2 else "none"
    return {
        "available": True,
        "state": state,
        "score": 1.4 if s3 else 0.8 if s2 else 0.0,
        "detail": (
            f"4H close={higher_closes[-1]:.6g} MA170={higher_ma170:.6g} bear={big_regime_bear} | "
            f"30m close={closes[-1]:.6g} MA5={current_ma5:.6g} MA34={current_ma34:.6g} "
            f"pivot_top={local_top} | centre={centre.get('zd')}..{centre.get('zg')} | "
            f"breakdown_index={breakdown_index} | state={state}"
        ),
        "raw": {
            "big_regime_bear": big_regime_bear,
            "entry_bearish": entry_bearish,
            "local_top": local_top,
            "ma5_turns_down": ma5_turns_down,
            "centre": centre,
            "breakdown_index": breakdown_index,
            "s2": s2,
            "s3": s3,
        },
    }
