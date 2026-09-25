"""
价格位置上下文：判断币价当前处于高位/低位/中性区间
用于修正新闻因子的强度和方向
"""
from app.services.okx_client import okx_manager


async def get_price_position(symbol: str, bar: str = "4H", lookback: int = 50):
    """
    获取币种当前价格位置（0~1）
    0 = 区间最低, 1 = 区间最高
    返回: (position_ratio, current_price, high, low, trend)
    """
    try:
        klines = await okx_manager.get_candles(symbol, bar, lookback)
        if len(klines) < 20:
            return 0.5, 0, 0, 0, "unknown"

        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]

        high = max(highs)
        low = min(lows)
        current = closes[-1]

        if high == low:
            return 0.5, current, high, low, "flat"

        position = (current - low) / (high - low)

        # 简单趋势判断
        ma20 = sum(closes[-20:]) / 20
        trend = "up" if current > ma20 * 1.02 else ("down" if current < ma20 * 0.98 else "flat")

        return position, current, high, low, trend
    except Exception as e:
        print(f"[price-context] 获取 {symbol} 价格位置失败: {e}")
        return 0.5, 0, 0, 0, "unknown"


def get_zone_label(position: float) -> str:
    """根据价格位置给出区间标签"""
    if position >= 0.85:
        return "high"
    if position <= 0.15:
        return "low"
    if position >= 0.65:
        return "mid-high"
    if position <= 0.35:
        return "mid-low"
    return "mid"


def adjust_news_by_price(news_direction: str, news_strength: int, position: float) -> tuple:
    """
    根据价格位置修正新闻因子的方向和强度

    规则：
    - 高位 + 利多 → 力度打折（利好出尽风险）
    - 高位 + 利空 → 力度放大（高位崩盘更狠）
    - 低位 + 利多 → 力度放大（低位出利好，反弹猛）
    - 低位 + 利空 → 力度打折（低位利空可能是最后一跌/利空出尽）
    """
    zone = get_zone_label(position)

    # 基础系数
    multiplier = 1.0
    note = ""

    if zone == "high":
        if news_direction == "bullish":
            multiplier = 0.5
            note = f"高位出利好，力度减半(位置{position:.0%})"
        elif news_direction == "bearish":
            multiplier = 1.5
            note = f"高位出利空，力度增强(位置{position:.0%})"
    elif zone == "low":
        if news_direction == "bullish":
            multiplier = 1.5
            note = f"低位出利好，力度增强(位置{position:.0%})"
        elif news_direction == "bearish":
            multiplier = 0.5
            note = f"低位出利空，力度减半(位置{position:.0%})"
    elif zone in ("mid-high", "mid-low"):
        # 中高位/中低位，轻微修正
        if news_direction == "bullish" and zone == "mid-high":
            multiplier = 0.8
            note = f"中高位利好，轻微打折(位置{position:.0%})"
        elif news_direction == "bearish" and zone == "mid-low":
            multiplier = 0.8
            note = f"中低位利空，轻微打折(位置{position:.0%})"

    adjusted_strength = int(news_strength * multiplier)
    if adjusted_strength < 1 and news_strength >= 1:
        adjusted_strength = 1

    # 极端情况：高位强利好可能反转标记为中性（利好出尽）
    if zone == "high" and news_direction == "bullish" and position > 0.95 and news_strength >= 4:
        return "neutral", 0, note + "|利好出尽，标记中性"

    # 极端情况：低位强利空可能反转（利空出尽）
    if zone == "low" and news_direction == "bearish" and position < 0.05 and news_strength >= 4:
        return "neutral", 0, note + "|利空出尽，标记中性"

    return news_direction, adjusted_strength, note
