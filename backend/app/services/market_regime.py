"""ADX/ATR regime gate for trend entries.  It returns state, never score."""
from __future__ import annotations

from math import isfinite

from app.services.okx_client import okx_manager
from app.services.signal_quality import confirmed_candles


def _wilder(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    result = [sum(values[:period]) / period]
    for value in values[period:]:
        result.append((result[-1] * (period - 1) + value) / period)
    return result


def calculate_adx_atr(candles: list, period: int = 14, percentile_lookback: int = 96) -> dict:
    if len(candles) < period * 3:
        return {"available": False, "reason": "K线不足"}
    highs = [float(row[2]) for row in candles]
    lows = [float(row[3]) for row in candles]
    closes = [float(row[4]) for row in candles]
    trs, plus_dm, minus_dm = [], [], []
    for index in range(1, len(candles)):
        up_move = highs[index] - highs[index - 1]
        down_move = lows[index - 1] - lows[index]
        trs.append(max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1])))
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    atrs = _wilder(trs, period)
    plus_smoothed = _wilder(plus_dm, period)
    minus_smoothed = _wilder(minus_dm, period)
    if not atrs or len(atrs) != len(plus_smoothed) or len(atrs) != len(minus_smoothed):
        return {"available": False, "reason": "ADX计算不足"}
    plus_di = [100 * value / atr if atr else 0.0 for value, atr in zip(plus_smoothed, atrs)]
    minus_di = [100 * value / atr if atr else 0.0 for value, atr in zip(minus_smoothed, atrs)]
    dx = [100 * abs(p - m) / (p + m) if p + m else 0.0 for p, m in zip(plus_di, minus_di)]
    adx_series = _wilder(dx, period)
    if not adx_series:
        return {"available": False, "reason": "ADX序列不足"}
    atr_pct_series = [atr / close if close else 0.0 for atr, close in zip(atrs, closes[period:])]
    current_atr_pct = atr_pct_series[-1]
    baseline = atr_pct_series[-min(len(atr_pct_series), percentile_lookback):]
    percentile = sum(value <= current_atr_pct for value in baseline) / max(len(baseline), 1)
    return {
        "available": all(isfinite(v) for v in (adx_series[-1], plus_di[-1], minus_di[-1], current_atr_pct)),
        "adx": adx_series[-1],
        "plus_di": plus_di[-1],
        "minus_di": minus_di[-1],
        "atr_pct": current_atr_pct,
        "atr_percentile": percentile,
        "atr_sample_count": len(baseline),
    }


async def evaluate_adx_atr_regime(symbol: str, direction: str, params: dict, *, closed_only: bool = False) -> dict:
    cfg = params.get("adx_atr_filter") or {}
    if not bool(cfg.get("enabled", False)):
        return {"enabled": False, "allowed": True, "state": "disabled", "reason": "未启用"}
    period = max(7, int(cfg.get("adx_period", 14) or 14))
    # Match the existing 4H/30m trend-regime requests so OKX client's candle
    # cache serves both indicators and this gate adds no extra market calls.
    slow_period = int(params.get("trend_regime_ma_slow", 170) or 170)
    higher_slope_bars = int(params.get("trend_regime_higher_slope_bars", 8) or 8)
    entry_slope_bars = int(params.get("trend_regime_entry_slope_bars", 12) or 12)
    higher_bar = str(params.get("trend_regime_higher_timeframe", "4H") or "4H")
    entry_bar = str(params.get("trend_regime_entry_timeframe", "30m") or "30m")
    higher_limit = max(slow_period + higher_slope_bars + 8, 80)
    entry_limit = max(slow_period + entry_slope_bars + 8, 80)
    try:
        candles_4h, candles_30m = await __import__('asyncio').gather(
            okx_manager.get_candles(symbol, higher_bar, higher_limit),
            okx_manager.get_candles(symbol, entry_bar, entry_limit),
        )
        if closed_only:
            candles_4h = confirmed_candles(candles_4h, higher_bar)
            candles_30m = confirmed_candles(candles_30m, entry_bar)
        trend_metrics = calculate_adx_atr(candles_4h, period)
        volatility_metrics = calculate_adx_atr(candles_30m, period, int(cfg.get("atr_percentile_lookback", 96) or 96))
    except Exception as exc:
        return {"enabled": True, "allowed": True, "state": "unavailable", "reason": f"ADX/ATR获取失败，按中性处理: {exc}"}
    if not trend_metrics.get("available") or not volatility_metrics.get("available"):
        return {"enabled": True, "allowed": True, "state": "unavailable", "reason": "ADX/ATR样本不足，按中性处理"}

    adx = float(trend_metrics["adx"])
    plus_di = float(trend_metrics["plus_di"])
    minus_di = float(trend_metrics["minus_di"])
    atr_pct = float(volatility_metrics["atr_pct"])
    atr_percentile = float(volatility_metrics["atr_percentile"])
    min_adx = float(cfg.get("min_adx", 18.0) or 18.0)
    strong_adx = float(cfg.get("strong_adx", 23.0) or 23.0)
    low_atr_percentile = float(cfg.get("low_atr_percentile", 0.20) or 0.20)
    high_atr_percentile = float(cfg.get("high_atr_percentile", 0.95) or 0.95)
    direction_ok = (direction == "LONG" and plus_di > minus_di) or (direction == "SHORT" and minus_di > plus_di)
    allowed = True
    state = "normal"
    reason = "ADX/ATR正常"
    if adx < min_adx:
        allowed, state, reason = False, "range", f"4H ADX {adx:.1f} < {min_adx:.1f}，震荡"
    elif not direction_ok:
        allowed, state, reason = False, "direction_conflict", f"4H DI方向冲突 +DI={plus_di:.1f} -DI={minus_di:.1f}"
    elif atr_percentile <= low_atr_percentile:
        allowed, state, reason = False, "low_volatility", f"30m ATR处于近样本{atr_percentile:.0%}分位，波动不足"
    elif atr_percentile >= high_atr_percentile:
        allowed, state, reason = False, "extreme_volatility", f"30m ATR处于近样本{atr_percentile:.0%}分位，波动过热"
    elif adx < strong_adx:
        allowed, state, reason = False, "weak_trend", f"4H ADX {adx:.1f} < {strong_adx:.1f}，趋势不足"
    return {
        "enabled": True, "allowed": allowed, "state": state, "reason": reason,
        "adx": adx, "plus_di": plus_di, "minus_di": minus_di,
        "atr_pct": atr_pct, "atr_percentile": atr_percentile,
        "atr_sample_count": volatility_metrics.get("atr_sample_count", 0),
    }
