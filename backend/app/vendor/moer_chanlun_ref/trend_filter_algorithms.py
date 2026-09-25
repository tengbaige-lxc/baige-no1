"""
摩尔缠论 - 趋势过滤算法完整实现
包含多种趋势判断方法
"""

from moer_quant_pure import MoerChanlun, SignalType, TradeSignal
from typing import List, Dict, Tuple
import random

class TrendFilter:
    """趋势过滤器 - 多种算法"""
    
    @staticmethod
    def ma_trend(closes: List[float], period: int = 55, threshold: float = 0.02) -> str:
        """
        均线趋势判断
        
        Args:
            closes: 收盘价列表
            period: MA周期
            threshold: 偏离阈值 (默认2%)
            
        Returns:
            "UP" | "DOWN" | "SIDE"
        """
        if len(closes) < period:
            return "UNKNOWN"
        
        ma = sum(closes[-period:]) / period
        current = closes[-1]
        deviation = (current - ma) / ma
        
        if deviation > threshold:
            return "UP"
        elif deviation < -threshold:
            return "DOWN"
        else:
            return "SIDE"
    
    @staticmethod
    def dual_ma_trend(closes: List[float], fast: int = 34, slow: int = 55) -> str:
        """
        双均线趋势判断 (金叉/死叉)
        
        Returns:
            "UP" | "DOWN" | "SIDE"
        """
        if len(closes) < slow:
            return "UNKNOWN"
        
        ma_fast = sum(closes[-fast:]) / fast
        ma_slow = sum(closes[-slow:]) / slow
        
        # 计算前几根K线的MA状态，判断趋势是否持续
        prev_fast = sum(closes[-fast-5:-5]) / fast if len(closes) >= fast + 5 else ma_fast
        prev_slow = sum(closes[-slow-5:-5]) / slow if len(closes) >= slow + 5 else ma_slow
        
        if ma_fast > ma_slow and prev_fast <= prev_slow:
            return "UP"  # 刚刚金叉
        elif ma_fast > ma_slow:
            return "UP"  # 多头趋势
        elif ma_fast < ma_slow and prev_fast >= prev_slow:
            return "DOWN"  # 刚刚死叉
        elif ma_fast < ma_slow:
            return "DOWN"  # 空头趋势
        else:
            return "SIDE"
    
    @staticmethod
    def macd_trend(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> str:
        """
        MACD趋势判断
        
        Returns:
            "UP" | "DOWN" | "SIDE"
        """
        def ema(data, period):
            multiplier = 2 / (period + 1)
            result = [data[0]]
            for i in range(1, len(data)):
                result.append(data[i] * multiplier + result[-1] * (1 - multiplier))
            return result
        
        if len(closes) < slow + signal:
            return "UNKNOWN"
        
        ema_fast = ema(closes, fast)
        ema_slow = ema(closes, slow)
        
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = ema(macd_line, signal)
        
        current_macd = macd_line[-1]
        current_signal = signal_line[-1]
        
        if current_macd > current_signal and current_macd > 0:
            return "UP"
        elif current_macd < current_signal and current_macd < 0:
            return "DOWN"
        else:
            return "SIDE"
    
    @staticmethod
    def higher_highs_lower_lows(highs: List[float], lows: List[float], lookback: int = 20) -> str:
        """
        高低点趋势判断
        
        Returns:
            "UP" | "DOWN" | "SIDE"
        """
        if len(highs) < lookback * 2 or len(lows) < lookback * 2:
            return "UNKNOWN"
        
        # 最近的高点 vs 之前的高点
        recent_highs = highs[-lookback:]
        prev_highs = highs[-lookback*2:-lookback]
        
        recent_lows = lows[-lookback:]
        prev_lows = lows[-lookback*2:-lookback]
        
        higher_high = max(recent_highs) > max(prev_highs)
        higher_low = min(recent_lows) > min(prev_lows)
        
        lower_high = max(recent_highs) < max(prev_highs)
        lower_low = min(recent_lows) < min(prev_lows)
        
        if higher_high and higher_low:
            return "UP"  # 高点更高，低点也更高 = 上升趋势
        elif lower_high and lower_low:
            return "DOWN"  # 高点更低，低点也更低 = 下降趋势
        else:
            return "SIDE"


class MoerChanlunWithTrend(MoerChanlun):
    """带趋势过滤的摩尔缠论"""
    
    def __init__(self, trend_method: str = "ma", trend_period: int = 55, 
                 trend_threshold: float = 0.02, *args, **kwargs):
        """
        Args:
            trend_method: "ma" | "dual_ma" | "macd" | "hh_ll"
            trend_period: 趋势判断周期
            trend_threshold: 趋势偏离阈值
        """
        super().__init__(*args, **kwargs)
        self.trend_method = trend_method
        self.trend_period = trend_period
        self.trend_threshold = trend_threshold
        self.trend_filter = TrendFilter()
    
    def get_trend(self, highs: List[float], lows: List[float], 
                  closes: List[float]) -> str:
        """获取当前趋势"""
        if self.trend_method == "ma":
            return self.trend_filter.ma_trend(closes, self.trend_period, 
                                               self.trend_threshold)
        elif self.trend_method == "dual_ma":
            return self.trend_filter.dual_ma_trend(closes, 34, self.trend_period)
        elif self.trend_method == "macd":
            return self.trend_filter.macd_trend(closes)
        elif self.trend_method == "hh_ll":
            return self.trend_filter.higher_highs_lower_lows(highs, lows, 20)
        else:
            return "UNKNOWN"
    
    def should_trade_signal(self, signal: TradeSignal, trend: str) -> bool:
        """判断是否应该交易该信号"""
        if '买' in signal.signal_type.value:
            # 买点：只在大趋势向上时交易
            return trend in ["UP", "SIDE"]  # 震荡市也允许
        elif '卖' in signal.signal_type.value:
            # 卖点：只在大趋势向下时交易
            return trend in ["DOWN", "SIDE"]
        return False
    
    def scan_with_trend(self, highs: List[float], lows: List[float],
                        closes: List[float], volumes: List[float] = None) -> Tuple[List[TradeSignal], str]:
        """
        带趋势过滤的扫描
        
        Returns:
            (filtered_signals, current_trend)
        """
        # 获取所有信号
        all_signals = self.scan(highs, lows, closes, volumes)
        
        # 判断趋势
        trend = self.get_trend(highs, lows, closes)
        
        # 过滤信号
        filtered = [sig for sig in all_signals 
                    if self.should_trade_signal(sig, trend)]
        
        return filtered, trend


def demo_all_methods():
    """演示所有趋势判断方法"""
    
    # 生成测试数据
    random.seed(42)
    n = 200
    closes = []
    base = 50000
    
    # 上升趋势数据
    for i in range(n):
        change = random.uniform(-0.01, 0.02)  # 偏向上涨
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
    
    print("=" * 70)
    print("📊 趋势过滤算法对比演示")
    print("=" * 70)
    print(f"\n数据: {n}根K线，整体趋势: 上升")
    print(f"价格: ${closes[0]:,.0f} → ${closes[-1]:,.0f}")
    
    tf = TrendFilter()
    
    print("\n" + "-" * 70)
    print("🔍 各种趋势判断方法结果")
    print("-" * 70)
    
    methods = [
        ("MA55偏离", lambda: tf.ma_trend(closes, 55, 0.02)),
        ("双均线(34/55)", lambda: tf.dual_ma_trend(closes, 34, 55)),
        ("MACD", lambda: tf.macd_trend(closes)),
        ("高低点", lambda: tf.higher_highs_lower_lows(highs, lows, 20)),
    ]
    
    for name, method in methods:
        trend = method()
        emoji = {"UP": "📈", "DOWN": "📉", "SIDE": "↔️", "UNKNOWN": "❓"}.get(trend, "❓")
        print(f"   {name:15s}: {emoji} {trend}")
    
    # 演示带过滤的扫描
    print("\n" + "-" * 70)
    print("🎯 带趋势过滤的信号扫描")
    print("-" * 70)
    
    for method_name in ["ma", "dual_ma", "macd"]:
        moer = MoerChanlunWithTrend(
            trend_method=method_name,
            trend_period=55,
            ma_period=34,
            center_min_height=0.003
        )
        
        signals, trend = moer.scan_with_trend(highs, lows, closes)
        
        print(f"\n   方法: {method_name.upper()}")
        print(f"   趋势: {trend}")
        print(f"   信号: {len(signals)} 个")
        
        for sig in signals[:3]:
            print(f"      • {sig.signal_type.value} @ ${sig.price:,.0f} (强度{sig.strength})")


def practical_example():
    """实战示例：如何在交易中应用"""
    
    print("\n" + "=" * 70)
    print("💡 实战代码示例")
    print("=" * 70)
    
    code = '''
# ========== 方式1: 使用MA趋势过滤 ==========
from moer_quant_pure import MoerChanlun, SignalType

def ma_trend(closes, period=55, threshold=0.02):
    """简单MA趋势判断"""
    ma = sum(closes[-period:]) / period
    current = closes[-1]
    
    if current > ma * (1 + threshold):
        return "UP"
    elif current < ma * (1 - threshold):
        return "DOWN"
    return "SIDE"

# 初始化
moer = MoerChanlun(ma_period=34, center_min_height=0.003)

# 扫描信号
signals = moer.scan(highs, lows, closes, volumes)

# 趋势过滤
trend = ma_trend(closes)

for sig in signals:
    if '买' in sig.signal_type.value and trend == "UP":
        # ✅ 上涨趋势中的买点 - 执行买入
        place_order(sig, side="buy")
        
    elif '买' in sig.signal_type.value and trend == "DOWN":
        # ❌ 下跌趋势中的买点 - 忽略或做空
        continue

# ========== 方式2: 双均线过滤 ==========
def dual_ma_trend(closes, fast=34, slow=55):
    """双均线趋势"""
    ma_fast = sum(closes[-fast:]) / fast
    ma_slow = sum(closes[-slow:]) / slow
    
    if ma_fast > ma_slow:
        return "UP"  # 金叉后多头
    elif ma_fast < ma_slow:
        return "DOWN"  # 死叉后空头
    return "SIDE"

# ========== 方式3: 多周期共振 ==========
def multi_timeframe_confirm(closes_1h, closes_4h, closes_1d):
    """多周期趋势确认"""
    trend_1h = ma_trend(closes_1h, 55)
    trend_4h = ma_trend(closes_4h, 55)
    trend_1d = ma_trend(closes_1d, 55)
    
    # 三周期共振
    if trend_1h == "UP" and trend_4h == "UP" and trend_1d == "UP":
        return "STRONG_UP"  # 极强买入信号
    elif trend_1h == "DOWN" and trend_4h == "DOWN":
        return "STRONG_DOWN"  # 极强卖出信号
    
    return "MIXED"

# ========== 完整交易流程 ==========
def trading_decision(highs, lows, closes, volumes):
    """完整的交易决策流程"""
    
    # 1. 判断趋势
    trend = ma_trend(closes)
    print(f"当前趋势: {trend}")
    
    # 2. 扫描缠论信号
    moer = MoerChanlun()
    signals = moer.scan(highs, lows, closes, volumes)
    
    # 3. 过滤并执行
    for sig in signals:
        # 趋势过滤
        if not should_trade_by_trend(sig, trend):
            continue
        
        # 信号强度过滤
        if sig.strength < 60:
            continue
        
        # 执行交易
        execute_trade(sig)

def should_trade_by_trend(signal, trend):
    """根据趋势判断是否交易"""
    if '买' in signal.signal_type.value:
        return trend in ["UP", "SIDE"]  # 买点只在上升/震荡
    else:
        return trend in ["DOWN", "SIDE"]  # 卖点只在下降/震荡
'''
    
    print(code)


if __name__ == "__main__":
    demo_all_methods()
    practical_example()
    
    print("\n" + "=" * 70)
    print("✅ 趋势过滤算法演示完成！")
    print("=" * 70)
    print("\n核心要点:")
    print("  1. MA趋势: 简单有效，适合大多数情况")
    print("  2. 双均线: 可以捕捉趋势转折")
    print("  3. MACD: 兼顾趋势和动量")
    print("  4. 高低点: 最直观的价格结构判断")
    print("\n推荐组合: MA趋势 + 信号强度过滤")
    print("=" * 70)
