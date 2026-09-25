"""
摩尔缠论量化交易系统 - 完整版
包含：中枢识别、背离判断、三类买卖点
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

class SignalType(Enum):
    FIRST_BUY = "一买"      # 第一类买点
    SECOND_BUY = "二买"     # 第二类买点
    THIRD_BUY = "三买"      # 第三类买点
    FIRST_SELL = "一卖"     # 第一类卖点
    SECOND_SELL = "二卖"    # 第二类卖点
    THIRD_SELL = "三卖"     # 第三类卖点

@dataclass
class TradeSignal:
    signal_type: SignalType
    price: float
    strength: int           # 0-100
    stop_loss: float
    take_profit: float
    reason: str
    timestamp: pd.Timestamp

class MoerChanlun:
    """摩尔缠论量化核心类"""
    
    def __init__(self, ma_period: int = 34, center_min_height: float = 0.01):
        self.ma_period = ma_period
        self.center_min_height = center_min_height  # 中枢最小高度 1%
        self.centers = []        # 识别的中枢列表
        self.signals = []        # 信号列表
        
    def calculate_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算MA均线"""
        df = df.copy()
        df['ma'] = df['close'].rolling(window=self.ma_period).mean()
        return df
    
    def find_extremes(self, df: pd.DataFrame, order: int = 3) -> pd.DataFrame:
        """
        识别波峰波谷（局部极值点）
        order: 左右各order根K线确认极值
        """
        df = df.copy()
        
        # 波峰：比左右各order根K线都高
        df['is_peak'] = df['high'] == df['high'].rolling(window=order*2+1, center=True).max()
        
        # 波谷：比左右各order根K线都低  
        df['is_valley'] = df['low'] == df['low'].rolling(window=order*2+1, center=True).min()
        
        # 极值点价格
        df['extreme_price'] = np.where(
            df['is_peak'], df['high'],
            np.where(df['is_valley'], df['low'], np.nan)
        )
        
        df['extreme_type'] = np.where(
            df['is_peak'], 'peak',
            np.where(df['is_valley'], 'valley', None)
        )
        
        return df
    
    def identify_centers(self, df: pd.DataFrame) -> List[Dict]:
        """
        识别中枢
        中枢 = 连续4个极值点（2峰+2谷）形成的价格重叠区域
        """
        df = self.calculate_ma(df)
        df = self.find_extremes(df)
        
        # 获取所有极值点
        extremes = df[df['extreme_price'].notna()].copy()
        
        centers = []
        
        for i in range(len(extremes) - 3):
            # 取连续4个极值点
            window = extremes.iloc[i:i+4]
            
            # 必须有2峰+2谷
            peaks = window[window['extreme_type'] == 'peak']
            valleys = window[window['extreme_type'] == 'valley']
            
            if len(peaks) != 2 or len(valleys) != 2:
                continue
            
            # 计算中枢区间
            zg = peaks['extreme_price'].min()  # 最低的高点
            zd = valleys['extreme_price'].max()  # 最高的低点
            
            # 验证中枢有效性
            if zg <= zd:
                continue
            
            # 验证高度
            height = (zg - zd) / zd
            if height < self.center_min_height:
                continue
            
            center = {
                'start_idx': window.index[0],
                'end_idx': window.index[-1],
                'zg': zg,
                'zd': zd,
                'height': height,
                'ma_points': len(window)
            }
            centers.append(center)
        
        self.centers = centers
        return centers
    
    def calculate_macd(self, df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """计算MACD指标"""
        df = df.copy()
        
        ema_fast = df['close'].ewm(span=fast).mean()
        ema_slow = df['close'].ewm(span=slow).mean()
        
        df['macd'] = ema_fast - ema_slow
        df['macd_signal'] = df['macd'].ewm(span=signal).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']
        
        return df
    
    def detect_bullish_divergence(self, df: pd.DataFrame, lookback: int = 20) -> bool:
        """
        检测底背离（看涨信号）
        价格创新低，MACD不创新低
        """
        df = df.tail(lookback)
        
        if len(df) < 5:
            return False
        
        # 找价格低点
        price_low_idx = df['low'].idxmin()
        price_low = df.loc[price_low_idx, 'low']
        
        # 找之前的低点（用于比较）
        prev_df = df[df.index < price_low_idx]
        if len(prev_df) < 5:
            return False
        
        prev_low = prev_df['low'].min()
        
        # 价格创新低
        price_makes_lower_low = price_low < prev_low * 0.99
        
        # 找对应的MACD值
        macd_at_price_low = df.loc[price_low_idx, 'macd_histogram']
        macd_prev_low = prev_df.loc[prev_df['low'].idxmin(), 'macd_histogram']
        
        # MACD不创新低（抬高）
        macd_higher = macd_at_price_low > macd_prev_low * 1.05
        
        return price_makes_lower_low and macd_higher
    
    def detect_bearish_divergence(self, df: pd.DataFrame, lookback: int = 20) -> bool:
        """
        检测顶背离（看跌信号）
        价格创新高，MACD不创新高
        """
        df = df.tail(lookback)
        
        if len(df) < 5:
            return False
        
        # 找价格高点
        price_high_idx = df['high'].idxmax()
        price_high = df.loc[price_high_idx, 'high']
        
        # 找之前的高点
        prev_df = df[df.index < price_high_idx]
        if len(prev_df) < 5:
            return False
        
        prev_high = prev_df['high'].max()
        
        # 价格创新高
        price_makes_higher_high = price_high > prev_high * 1.01
        
        # 找对应的MACD值
        macd_at_price_high = df.loc[price_high_idx, 'macd_histogram']
        macd_prev_high = prev_df.loc[prev_df['high'].idxmax(), 'macd_histogram']
        
        # MACD不创新高（降低）
        macd_lower = macd_at_price_high < macd_prev_high * 0.95
        
        return price_makes_higher_high and macd_lower
    
    def calculate_signal_strength(self, df: pd.DataFrame, signal_type: SignalType) -> int:
        """计算信号强度 0-100"""
        score = 50  # 基础分
        
        # MACD背离强度 (30分)
        if 'macd_histogram' in df.columns:
            macd_change = abs(df['macd_histogram'].iloc[-1] - df['macd_histogram'].iloc[-5])
            score += min(30, int(macd_change * 50))
        
        # 量能配合 (20分)
        if 'volume' in df.columns:
            avg_vol = df['volume'].tail(20).mean()
            recent_vol = df['volume'].tail(3).mean()
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1
            
            if signal_type in [SignalType.FIRST_BUY, SignalType.SECOND_BUY, SignalType.THIRD_BUY]:
                # 买点需要放量
                if vol_ratio > 1.3:
                    score += 20
                elif vol_ratio > 1.0:
                    score += 10
            else:
                # 卖点需要缩量（顶背离时）
                if vol_ratio < 0.8:
                    score += 20
                elif vol_ratio < 1.0:
                    score += 10
        
        return min(100, score)
    
    def detect_first_buy(self, df: pd.DataFrame, center: Dict) -> Optional[TradeSignal]:
        """
        检测一买点（第一类买点）
        条件：跌破中枢 + 创新低 + 底背离 + MACD<0 + 量能配合
        """
        current_price = df['close'].iloc[-1]
        zd = center['zd']
        zg = center['zg']
        
        # 条件1：价格跌破中枢下轨
        cond1 = current_price < zd
        
        # 条件2：价格创近期新低
        recent_lows = df['low'].tail(20)
        cond2 = df['low'].iloc[-1] <= recent_lows.min() * 1.001
        
        # 条件3：MACD底背离
        cond3 = self.detect_bullish_divergence(df, lookback=20)
        
        # 条件4：MACD在零轴下方
        df = self.calculate_macd(df)
        cond4 = df['macd'].iloc[-1] < 0
        
        # 条件5：量能不再萎缩
        if 'volume' in df.columns:
            avg_vol = df['volume'].tail(20).mean()
            recent_vol = df['volume'].tail(3).mean()
            cond5 = recent_vol >= avg_vol * 0.7
        else:
            cond5 = True
        
        if all([cond1, cond2, cond3, cond4, cond5]):
            strength = self.calculate_signal_strength(df, SignalType.FIRST_BUY)
            
            return TradeSignal(
                signal_type=SignalType.FIRST_BUY,
                price=current_price,
                strength=strength,
                stop_loss=df['low'].tail(5).min() * 0.98,
                take_profit=zg,  # 目标：回到中枢
                reason=f"跌破中枢{zd:.2f}，底背离确认，MACD<0",
                timestamp=df.index[-1]
            )
        
        return None
    
    def detect_first_sell(self, df: pd.DataFrame, center: Dict) -> Optional[TradeSignal]:
        """
        检测一卖点（第一类卖点）
        条件：突破中枢 + 创新高 + 顶背离 + MACD>0 + 量能萎缩
        """
        current_price = df['close'].iloc[-1]
        zd = center['zd']
        zg = center['zg']
        
        # 条件1：价格突破中枢上轨
        cond1 = current_price > zg
        
        # 条件2：价格创近期新高
        recent_highs = df['high'].tail(20)
        cond2 = df['high'].iloc[-1] >= recent_highs.max() * 0.999
        
        # 条件3：MACD顶背离
        cond3 = self.detect_bearish_divergence(df, lookback=20)
        
        # 条件4：MACD在零轴上方
        df = self.calculate_macd(df)
        cond4 = df['macd'].iloc[-1] > 0
        
        # 条件5：价升量缩（背离确认）
        if 'volume' in df.columns:
            avg_vol = df['volume'].tail(20).mean()
            recent_vol = df['volume'].tail(3).mean()
            cond5 = recent_vol < avg_vol * 0.9
        else:
            cond5 = True
        
        if all([cond1, cond2, cond3, cond4, cond5]):
            strength = self.calculate_signal_strength(df, SignalType.FIRST_SELL)
            
            return TradeSignal(
                signal_type=SignalType.FIRST_SELL,
                price=current_price,
                strength=strength,
                stop_loss=df['high'].tail(5).max() * 1.02,
                take_profit=zd,  # 目标：回到中枢
                reason=f"突破中枢{zg:.2f}，顶背离确认，MACD>0",
                timestamp=df.index[-1]
            )
        
        return None
    
    def detect_second_buy(self, df: pd.DataFrame, first_buy_price: float, center: Dict) -> Optional[TradeSignal]:
        """
        检测二买点（第二类买点）
        条件：一买后回抽，不破一买低点，回到中枢附近
        """
        current_price = df['close'].iloc[-1]
        zg = center['zg']
        zd = center['zd']
        
        # 条件1：价格在一买后回落
        cond1 = current_price > first_buy_price  # 当前价高于一买价
        
        # 条件2：不破一买低点
        recent_low = df['low'].tail(10).min()
        cond2 = recent_low > first_buy_price * 0.99
        
        # 条件3：回到中枢区间附近
        cond3 = zd <= current_price <= zg * 1.02
        
        # 条件4：MACD回抽零轴附近
        df = self.calculate_macd(df)
        cond4 = abs(df['macd'].iloc[-1]) < abs(df['macd'].max()) * 0.3
        
        if all([cond1, cond2, cond3, cond4]):
            strength = self.calculate_signal_strength(df, SignalType.SECOND_BUY)
            
            return TradeSignal(
                signal_type=SignalType.SECOND_BUY,
                price=current_price,
                strength=strength,
                stop_loss=first_buy_price * 0.98,
                take_profit=zg * 1.05,  # 目标：突破中枢
                reason=f"回抽确认，不破一买{first_buy_price:.2f}",
                timestamp=df.index[-1]
            )
        
        return None
    
    def detect_third_buy(self, df: pd.DataFrame, center: Dict) -> Optional[TradeSignal]:
        """
        检测三买点（第三类买点）
        条件：突破中枢后回抽，不跌回中枢
        """
        current_price = df['close'].iloc[-1]
        zg = center['zg']
        
        # 条件1：之前有过突破（简单判断：当前价在ZG上方一定距离）
        recent_high = df['high'].tail(10).max()
        cond1 = recent_high > zg * 1.01
        
        # 条件2：回抽不跌回中枢
        recent_low = df['low'].tail(5).min()
        cond2 = recent_low > zg
        
        # 条件3：当前价格在ZG附近（刚刚回抽完毕）
        cond3 = current_price >= zg * 0.98
        
        # 条件4：MACD在零轴上方金叉
        df = self.calculate_macd(df)
        cond4 = df['macd'].iloc[-1] > 0 and df['macd'].iloc[-1] > df['macd'].iloc[-2]
        
        if all([cond1, cond2, cond3, cond4]):
            strength = self.calculate_signal_strength(df, SignalType.THIRD_BUY)
            strength = min(100, strength + 10)  # 三买加分
            
            return TradeSignal(
                signal_type=SignalType.THIRD_BUY,
                price=current_price,
                strength=strength,
                stop_loss=zg * 0.98,
                take_profit=current_price * 1.08,  # 目标：主升浪
                reason=f"突破回抽确认，不跌回中枢{zg:.2f}",
                timestamp=df.index[-1]
            )
        
        return None
    
    def scan(self, df: pd.DataFrame) -> List[TradeSignal]:
        """
        扫描所有买卖点信号
        """
        signals = []
        
        # 1. 识别中枢
        centers = self.identify_centers(df)
        
        if not centers:
            return signals
        
        # 使用最近的中枢
        center = centers[-1]
        
        # 2. 检测一买/一卖
        first_buy = self.detect_first_buy(df, center)
        if first_buy:
            signals.append(first_buy)
        
        first_sell = self.detect_first_sell(df, center)
        if first_sell:
            signals.append(first_sell)
        
        # 3. 如果有历史信号，检测二买
        # (这里简化处理，实际应该读取历史信号)
        
        # 4. 检测三买
        third_buy = self.detect_third_buy(df, center)
        if third_buy:
            signals.append(third_buy)
        
        self.signals = signals
        return signals


# ==================== 使用示例 ====================

if __name__ == "__main__":
    # 示例：创建模拟数据测试
    print("=" * 50)
    print("摩尔缠论量化交易系统 - 测试")
    print("=" * 50)
    
    # 创建模拟K线数据
    np.random.seed(42)
    n = 100
    
    dates = pd.date_range('2024-01-01', periods=n, freq='1h')
    
    # 生成带趋势的价格数据
    trend = np.sin(np.linspace(0, 4*np.pi, n)) * 10
    noise = np.random.randn(n) * 2
    close = 100 + trend + noise
    
    df = pd.DataFrame({
        'open': close + np.random.randn(n) * 0.5,
        'high': close + abs(np.random.randn(n)) * 1.5,
        'low': close - abs(np.random.randn(n)) * 1.5,
        'close': close,
        'volume': np.random.randint(1000, 10000, n)
    }, index=dates)
    
    # 初始化系统
    moer = MoerChanlun(ma_period=34, center_min_height=0.01)
    
    # 识别中枢
    centers = moer.identify_centers(df)
    print(f"\n📊 识别到 {len(centers)} 个中枢")
    
    for i, c in enumerate(centers[-3:], 1):
        print(f"  中枢{i}: ZG={c['zg']:.2f}, ZD={c['zd']:.2f}, 高度={c['height']*100:.1f}%")
    
    # 扫描信号
    signals = moer.scan(df)
    print(f"\n🔔 检测到 {len(signals)} 个信号")
    
    for sig in signals:
        emoji = "🟢" if "买" in sig.signal_type.value else "🔴"
        print(f"\n{emoji} {sig.signal_type.value}")
        print(f"   价格: {sig.price:.2f}")
        print(f"   强度: {sig.strength}/100")
        print(f"   止损: {sig.stop_loss:.2f}")
        print(f"   止盈: {sig.take_profit:.2f}")
        print(f"   原因: {sig.reason}")
    
    print("\n" + "=" * 50)
    print("测试完成！")
    print("=" * 50)
