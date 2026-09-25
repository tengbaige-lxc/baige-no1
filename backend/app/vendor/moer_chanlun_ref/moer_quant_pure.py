"""
摩尔缠论量化交易系统 - 纯Python版（无依赖）
包含：中枢识别、背离判断、三类买卖点
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import json

class SignalType(Enum):
    FIRST_BUY = "一买"
    SECOND_BUY = "二买"
    THIRD_BUY = "三买"
    FIRST_SELL = "一卖"
    SECOND_SELL = "二卖"
    THIRD_SELL = "三卖"

@dataclass
class TradeSignal:
    signal_type: SignalType
    price: float
    strength: int
    stop_loss: float
    take_profit: float
    reason: str
    index: int

class MoerChanlun:
    """摩尔缠论量化核心类 - 纯Python版"""
    
    def __init__(self, ma_period: int = 34, center_min_height: float = 0.01):
        self.ma_period = ma_period
        self.center_min_height = center_min_height
        self.centers = []
        self.signals = []
    
    def calculate_ma(self, closes: List[float]) -> List[float]:
        """计算简单移动平均线"""
        ma = []
        for i in range(len(closes)):
            if i < self.ma_period - 1:
                ma.append(sum(closes[:i+1]) / (i+1))
            else:
                ma.append(sum(closes[i-self.ma_period+1:i+1]) / self.ma_period)
        return ma
    
    def find_extremes(self, highs: List[float], lows: List[float], order: int = 3) -> List[Dict]:
        """
        识别波峰波谷（局部极值点）
        返回: [{index, price, type}, ...]
        """
        extremes = []
        n = len(highs)
        
        for i in range(order, n - order):
            # 检查波峰
            is_peak = all(highs[i] >= highs[i-j] for j in range(1, order+1))
            is_peak = is_peak and all(highs[i] >= highs[i+j] for j in range(1, order+1))
            
            if is_peak:
                extremes.append({'index': i, 'price': highs[i], 'type': 'peak'})
                continue
            
            # 检查波谷
            is_valley = all(lows[i] <= lows[i-j] for j in range(1, order+1))
            is_valley = is_valley and all(lows[i] <= lows[i+j] for j in range(1, order+1))
            
            if is_valley:
                extremes.append({'index': i, 'price': lows[i], 'type': 'valley'})
        
        return extremes
    
    def identify_centers(self, highs: List[float], lows: List[float], closes: List[float]) -> List[Dict]:
        """
        识别中枢
        中枢 = 连续4个极值点（2峰+2谷）形成的价格重叠区域
        """
        ma = self.calculate_ma(closes)
        extremes = self.find_extremes(highs, lows)
        
        centers = []
        
        for i in range(len(extremes) - 3):
            window = extremes[i:i+4]
            
            # 必须有2峰+2谷
            peaks = [e for e in window if e['type'] == 'peak']
            valleys = [e for e in window if e['type'] == 'valley']
            
            if len(peaks) != 2 or len(valleys) != 2:
                continue
            
            # 计算中枢区间
            zg = min(p['price'] for p in peaks)  # 最低的高点
            zd = max(v['price'] for v in valleys)  # 最高的低点
            
            # 验证
            if zg <= zd:
                continue
            
            height = (zg - zd) / zd
            if height < self.center_min_height:
                continue
            
            centers.append({
                'start_idx': window[0]['index'],
                'end_idx': window[-1]['index'],
                'zg': zg,
                'zd': zd,
                'height': height,
                'ma_points': len(window)
            })
        
        self.centers = centers
        return centers
    
    def calculate_macd(self, closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, List[float]]:
        """计算MACD指标"""
        # EMA计算
        def ema(data: List[float], period: int) -> List[float]:
            multiplier = 2 / (period + 1)
            result = [data[0]]
            for i in range(1, len(data)):
                result.append(data[i] * multiplier + result[-1] * (1 - multiplier))
            return result
        
        ema_fast = ema(closes, fast)
        ema_slow = ema(closes, slow)
        
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = ema(macd_line, signal)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]
        
        return {
            'macd': macd_line,
            'signal': signal_line,
            'histogram': histogram
        }
    
    def detect_bullish_divergence(self, lows: List[float], histogram: List[float], lookback: int = 20) -> bool:
        """检测底背离（看涨）"""
        if len(lows) < lookback + 5:
            return False
        
        recent_lows = lows[-lookback:]
        recent_hist = histogram[-lookback:]
        
        # 找当前低点
        current_low_idx = recent_lows.index(min(recent_lows))
        current_low = recent_lows[current_low_idx]
        current_hist = recent_hist[current_low_idx]
        
        # 找之前低点
        if current_low_idx < 5:
            return False
        
        prev_lows = recent_lows[:current_low_idx]
        prev_hist = recent_hist[:current_low_idx]
        prev_low_idx = prev_lows.index(min(prev_lows))
        prev_low = prev_lows[prev_low_idx]
        prev_hist_val = prev_hist[prev_low_idx]
        
        # 价格创新低，MACD抬高
        price_lower = current_low < prev_low * 0.99
        macd_higher = current_hist > prev_hist_val * 1.05
        
        return price_lower and macd_higher
    
    def detect_bearish_divergence(self, highs: List[float], histogram: List[float], lookback: int = 20) -> bool:
        """检测顶背离（看跌）"""
        if len(highs) < lookback + 5:
            return False
        
        recent_highs = highs[-lookback:]
        recent_hist = histogram[-lookback:]
        
        # 找当前高点
        current_high_idx = recent_highs.index(max(recent_highs))
        current_high = recent_highs[current_high_idx]
        current_hist = recent_hist[current_high_idx]
        
        # 找之前高点
        if current_high_idx < 5:
            return False
        
        prev_highs = recent_highs[:current_high_idx]
        prev_hist = recent_hist[:current_high_idx]
        prev_high_idx = prev_highs.index(max(prev_highs))
        prev_high = prev_highs[prev_high_idx]
        prev_hist_val = prev_hist[prev_high_idx]
        
        # 价格创新高，MACD降低
        price_higher = current_high > prev_high * 1.01
        macd_lower = current_hist < prev_hist_val * 0.95
        
        return price_higher and macd_lower
    
    def calculate_strength(self, closes: List[float], volumes: List[float], signal_type: SignalType) -> int:
        """计算信号强度"""
        score = 50
        
        # MACD变化
        if len(closes) >= 10:
            # 简化：用价格变化模拟
            price_change = abs(closes[-1] - closes[-5]) / closes[-5]
            score += min(30, int(price_change * 1000))
        
        # 量能配合
        if volumes and len(volumes) >= 20:
            avg_vol = sum(volumes[-20:]) / 20
            recent_vol = sum(volumes[-3:]) / 3
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1
            
            if "买" in signal_type.value and vol_ratio > 1.2:
                score += 20
            elif "卖" in signal_type.value and vol_ratio < 0.9:
                score += 20
        
        return min(100, score)
    
    def detect_first_buy(self, highs: List[float], lows: List[float], 
                         closes: List[float], volumes: List[float], 
                         center: Dict, macd_data: Dict) -> Optional[TradeSignal]:
        """检测一买点"""
        current_price = closes[-1]
        zd = center['zd']
        zg = center['zg']
        histogram = macd_data['histogram']
        macd_line = macd_data['macd']
        
        cond1 = current_price < zd
        cond2 = lows[-1] <= min(lows[-20:]) * 1.001
        cond3 = self.detect_bullish_divergence(lows, histogram, 20)
        cond4 = macd_line[-1] < 0
        
        if volumes and len(volumes) >= 20:
            avg_vol = sum(volumes[-20:]) / 20
            recent_vol = sum(volumes[-3:]) / 3
            cond5 = recent_vol >= avg_vol * 0.7
        else:
            cond5 = True
        
        if all([cond1, cond2, cond3, cond4, cond5]):
            strength = self.calculate_strength(closes, volumes, SignalType.FIRST_BUY)
            return TradeSignal(
                signal_type=SignalType.FIRST_BUY,
                price=current_price,
                strength=strength,
                stop_loss=min(lows[-5:]) * 0.98,
                take_profit=zg,
                reason=f"跌破中枢{zd:.2f}，底背离确认",
                index=len(closes)-1
            )
        return None
    
    def detect_first_sell(self, highs: List[float], lows: List[float],
                          closes: List[float], volumes: List[float],
                          center: Dict, macd_data: Dict) -> Optional[TradeSignal]:
        """检测一卖点"""
        current_price = closes[-1]
        zd = center['zd']
        zg = center['zg']
        histogram = macd_data['histogram']
        macd_line = macd_data['macd']
        
        cond1 = current_price > zg
        cond2 = highs[-1] >= max(highs[-20:]) * 0.999
        cond3 = self.detect_bearish_divergence(highs, histogram, 20)
        cond4 = macd_line[-1] > 0
        
        if volumes and len(volumes) >= 20:
            avg_vol = sum(volumes[-20:]) / 20
            recent_vol = sum(volumes[-3:]) / 3
            cond5 = recent_vol < avg_vol * 0.9
        else:
            cond5 = True
        
        if all([cond1, cond2, cond3, cond4, cond5]):
            strength = self.calculate_strength(closes, volumes, SignalType.FIRST_SELL)
            return TradeSignal(
                signal_type=SignalType.FIRST_SELL,
                price=current_price,
                strength=strength,
                stop_loss=max(highs[-5:]) * 1.02,
                take_profit=zd,
                reason=f"突破中枢{zg:.2f}，顶背离确认",
                index=len(closes)-1
            )
        return None
    
    def scan(self, highs: List[float], lows: List[float], 
             closes: List[float], volumes: List[float] = None) -> List[TradeSignal]:
        """扫描所有信号"""
        signals = []
        
        # 识别中枢
        centers = self.identify_centers(highs, lows, closes)
        if not centers:
            return signals
        
        center = centers[-1]
        
        # 计算MACD
        macd_data = self.calculate_macd(closes)
        
        # 检测一买
        first_buy = self.detect_first_buy(highs, lows, closes, volumes, center, macd_data)
        if first_buy:
            signals.append(first_buy)
        
        # 检测一卖
        first_sell = self.detect_first_sell(highs, lows, closes, volumes, center, macd_data)
        if first_sell:
            signals.append(first_sell)
        
        # 检测二买/二卖 (需要历史信号)
        if hasattr(self, 'last_first_buy') and self.last_first_buy:
            second_buy = self.detect_second_buy(highs, lows, closes, volumes, center, macd_data, self.last_first_buy)
            if second_buy:
                signals.append(second_buy)
        
        if hasattr(self, 'last_first_sell') and self.last_first_sell:
            second_sell = self.detect_second_sell(highs, lows, closes, volumes, center, macd_data, self.last_first_sell)
            if second_sell:
                signals.append(second_sell)
        
        # 保存一买/一卖用于下次检测二买/二卖
        if first_buy:
            self.last_first_buy = first_buy
        if first_sell:
            self.last_first_sell = first_sell
        
        # 检测三买
        third_buy = self.detect_third_buy(highs, lows, closes, volumes, centers, macd_data)
        if third_buy:
            signals.append(third_buy)
        
        # 检测三卖
        third_sell = self.detect_third_sell(highs, lows, closes, volumes, centers, macd_data)
        if third_sell:
            signals.append(third_sell)
        
        self.signals = signals
        return signals
    
    def detect_second_buy(self, highs: List[float], lows: List[float],
                          closes: List[float], volumes: List[float],
                          center: Dict, macd_data: Dict, first_buy: TradeSignal) -> Optional[TradeSignal]:
        """
        检测二买点（第二类买点）
        条件：一买后回抽，不破一买低点，回到中枢区间
        """
        current_price = closes[-1]
        zg = center['zg']
        zd = center['zd']
        first_buy_price = first_buy.price
        
        # 检查是否已触发二买（防止重复）
        if hasattr(self, 'second_buy_triggered') and self.second_buy_triggered:
            return None
        
        # 条件1：价格在一买后上涨（确认一买有效）
        if current_price <= first_buy_price:
            return None
        
        # 条件2：回抽不破一买低点
        recent_low = min(lows[-10:])
        if recent_low <= first_buy_price * 0.99:
            return None
        
        # 条件3：回抽到中枢区间附近
        if not (zd * 0.98 <= current_price <= zg * 1.05):
            return None
        
        # 条件4：MACD在零轴上方或回抽零轴
        macd_line = macd_data['macd']
        if macd_line[-1] < -0.1:  # MACD不能太负
            return None
        
        # 条件5：量能配合（缩量后放量）
        if volumes and len(volumes) >= 20:
            recent_vol = sum(volumes[-3:]) / 3
            avg_vol = sum(volumes[-20:]) / 20
            if recent_vol < avg_vol * 0.7:
                return None
        
        self.second_buy_triggered = True
        strength = self.calculate_strength(closes, volumes, SignalType.SECOND_BUY)
        strength = min(100, strength + 10)
        
        return TradeSignal(
            signal_type=SignalType.SECOND_BUY,
            price=current_price,
            strength=strength,
            stop_loss=first_buy_price * 0.98,
            take_profit=zg * 1.05,
            reason=f"二买：一买{first_buy_price:.0f}后回抽确认",
            index=len(closes)-1
        )
    
    def detect_second_sell(self, highs: List[float], lows: List[float],
                           closes: List[float], volumes: List[float],
                           center: Dict, macd_data: Dict, first_sell: TradeSignal) -> Optional[TradeSignal]:
        """
        检测二卖点（第二类卖点）
        条件：一卖后反弹，不破一卖高点，回到中枢区间
        """
        current_price = closes[-1]
        zg = center['zg']
        zd = center['zd']
        first_sell_price = first_sell.price
        
        if hasattr(self, 'second_sell_triggered') and self.second_sell_triggered:
            return None
        
        # 条件1：价格在一卖后下跌
        if current_price >= first_sell_price:
            return None
        
        # 条件2：反弹不破一卖高点
        recent_high = max(highs[-10:])
        if recent_high >= first_sell_price * 1.01:
            return None
        
        # 条件3：反弹到中枢区间附近
        if not (zd * 0.95 <= current_price <= zg * 1.02):
            return None
        
        # 条件4：MACD在零轴下方或回抽零轴
        macd_line = macd_data['macd']
        if macd_line[-1] > 0.1:
            return None
        
        self.second_sell_triggered = True
        strength = self.calculate_strength(closes, volumes, SignalType.SECOND_SELL)
        strength = min(100, strength + 10)
        
        return TradeSignal(
            signal_type=SignalType.SECOND_SELL,
            price=current_price,
            strength=strength,
            stop_loss=first_sell_price * 1.02,
            take_profit=zd * 0.95,
            reason=f"二卖：一卖{first_sell_price:.0f}后反弹确认",
            index=len(closes)-1
        )
    
    def detect_third_buy(self, highs: List[float], lows: List[float],
                         closes: List[float], volumes: List[float],
                         centers: List[Dict], macd_data: Dict) -> Optional[TradeSignal]:
        """
        检测三买点（第三类买点）
        条件：突破中枢后回抽不跌回中枢
        """
        if len(centers) < 1:
            return None
        
        current_price = closes[-1]
        recent_high = max(highs[-10:])  # 最近10根K线高点
        recent_low = min(lows[-5:])     # 最近5根K线低点
        
        # 遍历所有中枢，寻找突破回抽机会
        for center in centers[-3:]:  # 只看最近3个中枢
            zg = center['zg']
            zd = center['zd']
            center_end = center['end_idx']
            
            # 条件1: 之前有过有效突破（价格曾大幅高于ZG）
            breakout_threshold = zg * 1.02  # 突破2%以上才算有效突破
            
            # 检查突破后是否一直在中枢上方运行
            if recent_high < breakout_threshold:
                continue  # 没有有效突破
            
            # 条件2: 回抽不跌回中枢（ZG上方）
            if recent_low <= zg:
                continue  # 跌回中枢，不是三买
            
            # 条件3: 当前价格在回抽确认区域（ZG附近）
            if current_price < zg * 0.98:
                continue  # 回抽太深
            
            # 条件4: MACD在零轴上方（强势）
            macd_line = macd_data['macd']
            if macd_line[-1] < 0:
                continue  # MACD在零轴下方，不够强势
            
            # 条件5: 量能配合
            if volumes and len(volumes) >= 20:
                avg_vol = sum(volumes[-20:]) / 20
                recent_vol = sum(volumes[-3:]) / 3
                if recent_vol < avg_vol * 0.8:
                    continue  # 量能不足
            
            # 三买确认！
            strength = self.calculate_strength(closes, volumes, SignalType.THIRD_BUY)
            strength = min(100, strength + 15)  # 三买加分
            
            return TradeSignal(
                signal_type=SignalType.THIRD_BUY,
                price=current_price,
                strength=strength,
                stop_loss=zg * 0.97,  # 止损设在中枢上轨下方
                take_profit=current_price * 1.06,  # 主升浪目标6%
                reason=f"三买：突破中枢{zg:.0f}回抽确认",
                index=len(closes)-1
            )
        
        return None
    
    def detect_third_sell(self, highs: List[float], lows: List[float],
                          closes: List[float], volumes: List[float],
                          centers: List[Dict], macd_data: Dict) -> Optional[TradeSignal]:
        """
        检测三卖点（第三类卖点）
        条件：跌破中枢后回抽不回到中枢
        """
        if len(centers) < 1:
            return None
        
        current_price = closes[-1]
        recent_low = min(lows[-10:])   # 最近10根K线低点
        recent_high = max(highs[-5:])  # 最近5根K线高点
        
        for center in centers[-3:]:
            zg = center['zg']
            zd = center['zd']
            
            # 条件1: 有效跌破（价格曾大幅低于ZD）
            breakdown_threshold = zd * 0.98
            
            if recent_low > breakdown_threshold:
                continue  # 没有有效跌破
            
            # 条件2: 回抽不回到中枢（ZD下方）
            if recent_high >= zd:
                continue  # 回到中枢，不是三卖
            
            # 条件3: 当前价格在回抽确认区域
            if current_price > zd * 1.02:
                continue  # 回抽太高
            
            # 条件4: MACD在零轴下方（弱势）
            macd_line = macd_data['macd']
            if macd_line[-1] > 0:
                continue  # MACD还在零轴上方
            
            strength = self.calculate_strength(closes, volumes, SignalType.THIRD_SELL)
            strength = min(100, strength + 15)
            
            return TradeSignal(
                signal_type=SignalType.THIRD_SELL,
                price=current_price,
                strength=strength,
                stop_loss=zd * 1.03,
                take_profit=current_price * 0.94,
                reason=f"三卖：跌破中枢{zd:.0f}回抽确认",
                index=len(closes)-1
            )
        
        return None


# ==================== 测试 ====================

def generate_test_data(n: int = 100) -> Tuple[List[float], List[float], List[float], List[float]]:
    """生成测试数据"""
    import random
    random.seed(42)
    
    closes = []
    base = 100
    
    for i in range(n):
        # 生成带趋势的波动
        trend = (i // 20) % 2 * 10 - 5  # 周期性趋势
        noise = random.uniform(-3, 3)
        close = base + trend + noise
        closes.append(close)
        base = close
    
    highs = [c + random.uniform(0, 2) for c in closes]
    lows = [c - random.uniform(0, 2) for c in closes]
    volumes = [random.randint(1000, 10000) for _ in range(n)]
    
    return highs, lows, closes, volumes


if __name__ == "__main__":
    print("=" * 60)
    print("  摩尔缠论量化交易系统 - 纯Python版")
    print("=" * 60)
    
    # 生成测试数据
    highs, lows, closes, volumes = generate_test_data(200)
    
    print(f"\n📊 数据: {len(closes)} 根K线")
    print(f"   价格范围: {min(closes):.2f} - {max(closes):.2f}")
    
    # 初始化系统
    moer = MoerChanlun(ma_period=34, center_min_height=0.01)
    
    # 识别中枢
    centers = moer.identify_centers(highs, lows, closes)
    print(f"\n📍 识别到 {len(centers)} 个中枢")
    
    for i, c in enumerate(centers[-3:], 1):
        print(f"   中枢{i}: ZG={c['zg']:.2f}, ZD={c['zd']:.2f}, 高度={c['height']*100:.1f}%")
    
    # 扫描信号
    signals = moer.scan(highs, lows, closes, volumes)
    print(f"\n🔔 检测到 {len(signals)} 个信号")
    
    for sig in signals:
        emoji = "🟢" if "买" in sig.signal_type.value else "🔴"
        print(f"\n{emoji} {sig.signal_type.value}")
        print(f"   价格: {sig.price:.2f}")
        print(f"   强度: {'█' * (sig.strength // 10)}{'░' * (10 - sig.strength // 10)} {sig.strength}/100")
        print(f"   止损: {sig.stop_loss:.2f}")
        print(f"   止盈: {sig.take_profit:.2f}")
        print(f"   原因: {sig.reason}")
    
    if not signals:
        print("   (当前无信号，等待市场机会)")
    
    print("\n" + "=" * 60)
    print("测试完成！代码可以直接运行。")
    print("=" * 60)
