"""
白鸽一号内部的缠论桥接层。

缠论/MA34 买点核心算法已收编为内部包 app.vendor.chanlun_core，
不再依赖服务器 /root/.openclaw/workspace 路径。
"""
from __future__ import annotations

from app.vendor.chanlun_core import (
    KLine,
    analyze_chanlun_structure,
    calc_ma,
    calc_macd,
    detect_buy_signals,
)

LONG_SIGNAL_TYPES = {
    "jin1_std",
    "jin1_ext",
    "jin2_approx",
    "jin3_approx",
    "shou1",
    "shou2",
}

LONG_SIGNAL_LABELS = {
    "jin1_std": "进攻买一",
    "jin1_ext": "进攻买一扩展",
    "jin2_approx": "进攻买二",
    "jin3_approx": "进攻买三",
    "shou1": "防守买一",
    "shou2": "防守买二",
}

LONG_SIGNAL_PRIORITY = {
    "jin1_ext": 6,
    "jin1_std": 5,
    "jin2_approx": 4,
    "jin3_approx": 3,
    "shou1": 2,
    "shou2": 1,
}


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_date(value) -> str:
    text = str(value or "").strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    return text


def _normalize_confidence(raw_confidence) -> float:
    """Buy-signal confidence from chanlun_core.py is 0-1; every downstream consumer
    (strategy_engine.py, signal_service.py) compares it against a 0-100 threshold, so
    it must be rescaled exactly once, here, at the bridge boundary. See docs/02 §1.2 /
    docs/03 §一 / docs/04 §三 for why this was silently zeroing out long entries."""
    confidence = round(float(raw_confidence or 0) * 100, 2)
    assert 0 <= confidence <= 100, f"confidence out of 0-100 range after rescale: {confidence}"
    return confidence


def parse_klines(rows) -> list[KLine]:
    klines: list[KLine] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        date = _normalize_date(row.get("trade_date") or row.get("date"))
        if not date:
            continue
        klines.append(
            KLine(
                date=date,
                open=_to_float(row.get("open")),
                high=_to_float(row.get("high")),
                low=_to_float(row.get("low")),
                close=_to_float(row.get("close")),
                vol=_to_float(row.get("vol") or row.get("volume")),
            )
        )
    return klines


def analyze_klines(rows, big_yang_threshold: float = 0.05) -> dict:
    klines = parse_klines(rows)
    if not klines:
        return {"signals": [], "structure": None}

    klines.sort(key=lambda item: item.date)
    closes = [k.close for k in klines]
    ma5 = calc_ma(closes, 5)
    ma34 = calc_ma(closes, 34)
    ma170 = calc_ma(closes, 170)
    signals = detect_buy_signals(
        klines,
        ma5,
        ma34,
        ma170,
        big_yang_threshold=float(big_yang_threshold or 0.05),
    )
    structure = analyze_chanlun_structure(klines, level="day")

    return {
        "signals": [
            {
                "id": signal.id,
                "stype": signal.stype,
                "date": signal.date,
                "price": round(signal.price, 2),
                "confidence": _normalize_confidence(signal.confidence),
                "reason": signal.reason,
                "meta": signal.meta,
            }
            for signal in signals
        ],
        "structure": structure,
    }


def _pick_best_long_signal(signals: list[dict]) -> dict | None:
    candidates = [signal for signal in signals if signal.get("stype") in LONG_SIGNAL_TYPES]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda signal: (
            LONG_SIGNAL_PRIORITY.get(signal.get("stype"), 0),
            float(signal.get("confidence", 0) or 0),
        ),
    )


def detect_black_candle(rows: list) -> dict:
    """
    黑K检测：MA5由升转降的确认K线
    条件：
    1. 当前是阴线（收盘 < 开盘）
    2. MA5[当前] < MA5[前一根]（均线开始走低）
    3. MA5[前一根] >= MA5[前两根]（之前均线是上升或水平）
    """
    if len(rows) < 7:
        return {"triggered": False, "reason": "K线不足"}

    closes = [float(r.get("close", 0)) for r in rows]
    opens = [float(r.get("open", 0)) for r in rows]

    ma5_series = calc_ma(closes, 5)
    if len(ma5_series) < 3:
        return {"triggered": False, "reason": "MA5计算不足"}

    curr_close = closes[-1]
    curr_open = opens[-1]
    ma5_curr = ma5_series[-1]
    ma5_prev = ma5_series[-2]
    ma5_prev2 = ma5_series[-3]

    is_bearish = curr_close < curr_open                           # 阴线
    body_pct = (curr_open - curr_close) / curr_open if curr_open > 0 else 0
    ma5_drop_pct = (ma5_prev - ma5_curr) / ma5_prev if ma5_prev > 0 else 0
    ma5_turning_down = ma5_drop_pct >= 0.001                      # MA5下降至少0.1%
    ma5_was_rising = ma5_prev >= ma5_prev2 * 0.9995               # 之前 MA5 是上升或水平
    body_valid = body_pct >= 0.001                                 # 阴线实体至少0.1%

    if is_bearish and body_valid and ma5_turning_down and ma5_was_rising:
        confidence = min(100, int(60 + ma5_drop_pct * 10000))
        return {
            "triggered": True,
            "confidence": confidence,
            "reason": f"黑K确认MA5转折: MA5 {ma5_prev:.4f}→{ma5_curr:.4f}(下降{ma5_drop_pct*100:.3f}%) 阴线实体{body_pct*100:.2f}%",
            "ma5_curr": round(ma5_curr, 4),
            "ma5_prev": round(ma5_prev, 4),
        }

    return {"triggered": False, "reason": f"MA5未转折(当前{ma5_curr:.4f} 前{ma5_prev:.4f}) 或非阴线"}


def summarize_chanlun_factors(rows, big_yang_threshold: float = 0.05) -> dict:
    result = analyze_klines(rows, big_yang_threshold=big_yang_threshold)
    signals = result.get("signals", [])
    structure = result.get("structure") or {}
    closes = [float(row.get("close", 0)) for row in rows]

    best_long_signal = _pick_best_long_signal(signals)
    if best_long_signal:
        best_long_signal = {
            **best_long_signal,
            "signal": "BULLISH",
            "label": LONG_SIGNAL_LABELS.get(best_long_signal["stype"], best_long_signal["stype"]),
        }

    bullish_divergence = None
    bearish_divergence = None
    if len(closes) >= 5:
        _, _, hist = calc_macd(closes)
        price_lows = []
        macd_lows = []
        price_highs = []
        macd_highs = []
        for i in range(2, len(rows) - 2):
            row = rows[i]
            if (
                row["low"] < rows[i - 1]["low"]
                and row["low"] < rows[i - 2]["low"]
                and row["low"] < rows[i + 1]["low"]
                and row["low"] < rows[i + 2]["low"]
            ):
                price_lows.append((i, row["low"]))
                macd_lows.append((i, hist[i]))
            if (
                row["high"] > rows[i - 1]["high"]
                and row["high"] > rows[i - 2]["high"]
                and row["high"] > rows[i + 1]["high"]
                and row["high"] > rows[i + 2]["high"]
            ):
                price_highs.append((i, row["high"]))
                macd_highs.append((i, hist[i]))

        if len(price_lows) >= 2 and len(macd_lows) >= 2:
            prev_p = price_lows[-2]
            curr_p = price_lows[-1]
            prev_m = macd_lows[-2]
            curr_m = macd_lows[-1]
            price_lower = curr_p[1] < prev_p[1] * 0.995
            macd_higher = curr_m[1] > prev_m[1] * 1.05 if prev_m[1] != 0 else curr_m[1] > prev_m[1]
            if price_lower and macd_higher and hist[curr_p[0]] < 0:
                confidence = min(100, int(60 + abs(curr_m[1] - prev_m[1]) / max(abs(prev_m[1]), 1e-9) * 30))
                bullish_divergence = {
                    "signal": "BULLISH",
                    "stype": "bottom_divergence",
                    "label": "底背驰",
                    "confidence": confidence,
                    "reason": f"底背驰: 价格新低 {curr_p[1]:.2f} < {prev_p[1]:.2f}, MACD抬高",
                }

        if len(price_highs) >= 2 and len(macd_highs) >= 2:
            prev_p = price_highs[-2]
            curr_p = price_highs[-1]
            prev_m = macd_highs[-2]
            curr_m = macd_highs[-1]
            price_higher = curr_p[1] > prev_p[1] * 1.005
            macd_lower = curr_m[1] < prev_m[1] * 0.95 if prev_m[1] != 0 else curr_m[1] < prev_m[1]
            if price_higher and macd_lower and hist[curr_p[0]] > 0:
                confidence = min(100, int(60 + abs(prev_m[1] - curr_m[1]) / max(abs(prev_m[1]), 1e-9) * 30))
                bearish_divergence = {
                    "signal": "BEARISH",
                    "stype": "top_divergence",
                    "label": "顶背驰",
                    "confidence": confidence,
                    "reason": f"顶背驰: 价格新高 {curr_p[1]:.2f} > {prev_p[1]:.2f}, MACD降低",
                }

    # 三卖检测：跌破MA34中枢后回抽不回中枢
    third_sell = None
    ma34 = calc_ma(closes, 34)
    if len(ma34) >= 10 and ma34[-1] > 0:
        zd = ma34[-1]  # 用MA34作为中枢下沿参考
        recent_lows = [float(r.get("low", 0)) for r in rows[-20:]]
        recent_highs = [float(r.get("high", 0)) for r in rows[-5:]]
        current_price = closes[-1]
        _, _, hist_arr = calc_macd(closes)

        breakdown = min(recent_lows) < zd * 0.98          # 条件1: 有效跌破MA34
        no_recovery = max(recent_highs) < zd              # 条件2: 回抽未回到MA34
        near_zd = current_price < zd * 1.02               # 条件3: 当前在回抽确认区
        macd_weak = hist_arr[-1] < 0                       # 条件4: MACD零轴下方

        if breakdown and no_recovery and near_zd and macd_weak:
            gap = abs(zd - min(recent_lows)) / zd
            confidence = min(100, int(60 + gap * 300))
            third_sell = {
                "signal": "BEARISH",
                "stype": "third_sell",
                "label": "三卖",
                "confidence": confidence,
                "reason": f"三卖: 跌破MA34({zd:.2f})回抽确认，MACD零轴下方",
            }

    # best_short_signal: 优先顶背驰，其次三卖
    best_short = bearish_divergence or third_sell
    # 若两者都有，取置信度更高的
    if bearish_divergence and third_sell:
        best_short = bearish_divergence if bearish_divergence["confidence"] >= third_sell["confidence"] else third_sell

    return {
        "signals": signals,
        "structure": structure,
        "best_long_signal": best_long_signal,
        "best_short_signal": best_short,
        "bullish_divergence": bullish_divergence,
        "bearish_divergence": bearish_divergence,
        "third_sell": third_sell,
    }


__all__ = [
    "LONG_SIGNAL_LABELS",
    "LONG_SIGNAL_TYPES",
    "analyze_klines",
    "calc_macd",
    "detect_black_candle",
    "parse_klines",
    "summarize_chanlun_factors",
]
