"""
摩尔缠论 - 市场状态判断模块
判断趋势/震荡/单边行情
"""

from typing import List, Dict
from dataclasses import dataclass
from enum import Enum

class MarketState(Enum):
    TREND_UP = "上涨趋势"
    TREND_DOWN = "下跌趋势"
    RANGING = "震荡市"
    BREAKOUT = "突破中"
    UNKNOWN = "未知"

@dataclass
class MarketAnalysis:
    state: MarketState
    confidence: float  # 0-100
    reason: str
    advice: str

class MarketStateDetector:
    """市场状态检测器"""
    
    def __init__(self):
        self.ranging_threshold = 0.02  # 2%范围内视为震荡
    
    def calculate_atr(self, highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        """计算ATR（平均真实波幅）"""
        if len(closes) < period + 1:
            return 0
        
        tr_list = []
        for i in range(1, len(closes)):
            tr1 = highs[i] - lows[i]
            tr2 = abs(highs[i] - closes[i-1])
            tr3 = abs(lows[i] - closes[i-1])
            tr_list.append(max(tr1, tr2, tr3))
        
        # 简单平均
        return sum(tr_list[-period:]) / period if len(tr_list) >= period else sum(tr_list) / len(tr_list)
    
    def detect(self, highs: List[float], lows: List[float], closes: List[float], 
               centers: List[Dict]) -> MarketAnalysis:
        """
        检测市场状态
        """
        if len(closes) < 55:
            return MarketAnalysis(MarketState.UNKNOWN, 0, "数据不足", "等待更多数据")
        
        current_price = closes[-1]
        
        # 1. 计算均线
        ma21 = sum(closes[-21:]) / 21
        ma55 = sum(closes[-55:]) / 55
        ma_deviation = abs(current_price - ma55) / ma55
        
        # 2. 计算ATR
        atr = self.calculate_atr(highs, lows, closes, 14)
        atr_pct = atr / current_price
        
        # 3. 分析中枢
        ranging_score = 0  # 震荡分数
        
        if centers:
            latest = centers[-1]
            zg, zd = latest['zg'], latest['zd']
            center_range = (zg - zd) / zd
            
            # 当前位置
            if zd <= current_price <= zg:
                position = "inside"
                ranging_score += 30
            elif abs(current_price - zg) / zg < 0.01 or abs(current_price - zd) / zd < 0.01:
                position = "boundary"
                ranging_score += 20
            else:
                position = "outside"
                ranging_score -= 20
            
            # 中枢宽度判断
            if center_range < 0.02:  # 窄中枢
                ranging_score += 20
            elif center_range > 0.05:  # 宽中枢
                ranging_score -= 10
        
        # 4. 均线判断
        ma_diff = abs(ma21 - ma55) / ma55
        if ma_diff < 0.005:  # 均线非常接近
            ranging_score += 30
        elif ma_diff < 0.02:
            ranging_score += 15
        else:
            ranging_score -= 20
        
        # 5. ATR判断
        if atr_pct < 0.005:  # 波动率很低
            ranging_score += 20
        elif atr_pct > 0.02:  # 波动率高
            ranging_score -= 20
        
        # 6. 价格近期波动范围
        recent_high = max(closes[-20:])
        recent_low = min(closes[-20:])
        price_range = (recent_high - recent_low) / recent_low
        
        if price_range < 0.05:  # 5%以内波动
            ranging_score += 20
        elif price_range > 0.15:  # 15%以上波动
            ranging_score -= 30
        
        # 判断结果
        if ranging_score >= 60:
            return MarketAnalysis(
                MarketState.RANGING,
                min(ranging_score, 100),
                f"震荡分数: {ranging_score}/100, 均线偏离: {ma_diff*100:.2f}%, ATR: {atr_pct*100:.2f}%",
                "观望或区间操作，等待突破"
            )
        elif ma21 > ma55 and current_price > ma21:
            return MarketAnalysis(
                MarketState.TREND_UP,
                70,
                f"MA21({ma21:.0f}) > MA55({ma55:.0f})",
                "顺势做多，关注回调买点"
            )
        elif ma21 < ma55 and current_price < ma21:
            return MarketAnalysis(
                MarketState.TREND_DOWN,
                70,
                f"MA21({ma21:.0f}) < MA55({ma55:.0f})",
                "顺势做空，关注反弹卖点"
            )
        else:
            return MarketAnalysis(
                MarketState.UNKNOWN,
                50,
                "趋势不明朗",
                "继续观察，等待方向确认"
            )


def demo_market_state():
    """演示市场状态判断"""
    
    import random
    from moer_quant_pure import MoerChanlun
    
    print("="*70)
    print("📊 市场状态判断演示")
    print("="*70)
    
    # 生成震荡市数据
    print("\n🔸 场景1: 震荡市")
    random.seed(42)
    closes = []
    base = 50000
    for i in range(100):
        # 在49000-51000之间震荡
        change = random.uniform(-0.008, 0.008)
        base = base * (1 + change)
        base = max(49000, min(51000, base))  # 限制范围
        closes.append(base)
    
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    
    moer = MoerChanlun()
    centers = moer.identify_centers(highs, lows, closes)
    
    detector = MarketStateDetector()
    result = detector.detect(highs, lows, closes, centers)
    
    print(f"   状态: {result.state.value}")
    print(f"   置信度: {result.confidence}%")
    print(f"   原因: {result.reason}")
    print(f"   建议: {result.advice}")
    
    # 生成上涨趋势数据
    print("\n🔸 场景2: 上涨趋势")
    closes = []
    base = 50000
    for i in range(100):
        # 上涨趋势，偏向上涨
        change = random.uniform(-0.005, 0.015)
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    centers = moer.identify_centers(highs, lows, closes)
    
    result = detector.detect(highs, lows, closes, centers)
    
    print(f"   状态: {result.state.value}")
    print(f"   置信度: {result.confidence}%")
    print(f"   原因: {result.reason}")
    print(f"   建议: {result.advice}")
    
    print("\n" + "="*70)
    print("💡 震荡市判断标准:")
    print("="*70)
    print("""
1. 价格在中枢区间内运行
2. MA21和MA55距离很近 (<2%)
3. ATR波动率下降 (<0.5%)
4. 近期价格波动小 (<5%)
5. 频繁出现假突破信号

震荡市应对策略:
- 降低仓位或观望
- 只做中枢上下轨的高抛低吸
- 等待突破确认后再追趋势
- 减小止盈预期
    """)


if __name__ == "__main__":
    demo_market_state()
