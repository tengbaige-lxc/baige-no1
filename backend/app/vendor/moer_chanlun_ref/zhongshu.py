"""
摩尔缠论核心模块 - 中枢识别（基于34均线+波峰波谷）
Moer ChanLun - ZhongShu (Central Pivot) Detection

摩尔缠论特点：
- 不使用笔的概念，直接用线段或波峰波谷
- 结合34均线判断趋势
- 中枢 = 价格密集成交区域
"""
from typing import List, Tuple, Optional
from dataclasses import dataclass
import numpy as np
import pandas as pd

@dataclass
class ZhongShu:
    """中枢数据结构"""
    start_idx: int        # 起始K线索引
    end_idx: int          # 结束K线索引
    zg: float            # 中枢高点（ZG）
    zd: float            # 中枢低点（ZD）
    gg: float            # 区间最高（GG）
    dd: float            # 区间最低（DD）
    ma34: float          # 34均线位置
    
    @property
    def center(self) -> float:
        """中枢中心 = (ZG + ZD) / 2"""
        return (self.zg + self.zd) / 2
    
    @property
    def height(self) -> float:
        """中枢高度 = ZG - ZD"""
        return self.zg - self.zd
    
    @property
    def height_pct(self) -> float:
        """中枢高度百分比"""
        return self.height / self.center * 100
    
    @property
    def kline_count(self) -> int:
        """中枢包含的K线数量"""
        return self.end_idx - self.start_idx + 1
    
    def contains_price(self, price: float) -> bool:
        """价格是否在中枢区间内"""
        return self.zd <= price <= self.zg
    
    def is_above(self, price: float) -> bool:
        """价格是否在中枢上方"""
        return price > self.zg
    
    def is_below(self, price: float) -> bool:
        """价格是否在中枢下方"""
        return price < self.zd


def calculate_ma34(klines: pd.DataFrame) -> pd.Series:
    """计算34日均线"""
    return klines['close'].rolling(window=34, min_periods=1).mean()


def find_peaks_troughs(klines: pd.DataFrame, 
                       window: int = 3) -> Tuple[List[int], List[int]]:
    """
    寻找波峰和波谷（简化版分型）
    
    Args:
        klines: K线数据
        window: 左右窗口大小
    
    Returns:
        peaks: 波峰索引列表
        troughs: 波谷索引列表
    """
    highs = klines['high'].values
    lows = klines['low'].values
    
    peaks = []
    troughs = []
    
    for i in range(window, len(klines) - window):
        # 波峰：窗口内最高点
        if highs[i] == max(highs[i-window:i+window+1]):
            peaks.append(i)
        
        # 波谷：窗口内最低点
        if lows[i] == min(lows[i-window:i+window+1]):
            troughs.append(i)
    
    return peaks, troughs


def detect_zhongshu(klines: pd.DataFrame,
                   min_klines: int = 10,
                   max_klines: int = 100,
                   overlap_threshold: float = 0.6) -> List[ZhongShu]:
    """
    识别中枢（基于波峰波谷+34均线）
    
    摩尔缠论中枢识别逻辑：
    1. 找到波峰波谷
    2. 寻找价格重叠区域（至少2个波峰+2个波谷重叠）
    3. 结合34均线位置确认中枢
    
    Args:
        klines: DataFrame with ['high', 'low', 'close']
        min_klines: 中枢最小K线数
        max_klines: 中枢最大K线数
        overlap_threshold: 重叠度阈值
    
    Returns:
        zhongshu_list: 中枢列表
    """
    if len(klines) < min_klines:
        return []
    
    # 计算34均线
    ma34 = calculate_ma34(klines)
    
    # 寻找波峰波谷
    peaks, troughs = find_peaks_troughs(klines)
    
    if len(peaks) < 2 or len(troughs) < 2:
        return []
    
    zhongshu_list = []
    
    # 滑动窗口寻找中枢
    i = 0
    while i < len(klines) - min_klines:
        window = klines.iloc[i:i+max_klines]
        
        # 检查窗口内的波峰波谷
        window_peaks = [p for p in peaks if i <= p < i + max_klines]
        window_troughs = [t for t in troughs if i <= t < i + max_klines]
        
        if len(window_peaks) < 2 or len(window_troughs) < 2:
            i += 1
            continue
        
        # 计算重叠区域
        peak_prices = [klines['high'].iloc[p] for p in window_peaks]
        trough_prices = [klines['low'].iloc[t] for t in window_troughs]
        
        # ZG = 最低的高点（波峰最低点）
        # ZD = 最高的低点（波谷最高点）
        zg = min(peak_prices)
        zd = max(trough_prices)
        
        # 检查是否形成中枢（ZG > ZD）
        if zg > zd:
            # 计算GG和DD
            gg = max(peak_prices)
            dd = min(trough_prices)
            
            # 找到中枢的起止索引
            start_idx = min(window_peaks + window_troughs)
            end_idx = max(window_peaks + window_troughs)
            
            # 检查K线数量
            if end_idx - start_idx + 1 >= min_klines:
                zhongshu = ZhongShu(
                    start_idx=start_idx,
                    end_idx=end_idx,
                    zg=zg,
                    zd=zd,
                    gg=gg,
                    dd=dd,
                    ma34=ma34.iloc[end_idx]
                )
                zhongshu_list.append(zhongshu)
                i = end_idx + 1  # 跳过已识别的中枢
                continue
        
        i += 1
    
    return zhongshu_list


def detect_zhongshu_simple(klines: pd.DataFrame,
                           window: int = 20,
                           overlap_ratio: float = 0.5) -> List[ZhongShu]:
    """
    简化版中枢识别（基于价格密集区）
    
    直接在滑动窗口内找价格重叠最多的区域
    
    Args:
        klines: K线数据
        window: 滑动窗口大小
        overlap_ratio: 重叠比例阈值
    """
    if len(klines) < window:
        return []
    
    ma34 = calculate_ma34(klines)
    zhongshu_list = []
    
    for i in range(0, len(klines) - window, window // 2):
        window_data = klines.iloc[i:i+window]
        
        # 计算窗口内的价格分布
        highs = window_data['high'].values
        lows = window_data['low'].values
        
        # 找重叠区域（使用20-80分位数作为中枢区间）
        zg = np.percentile(highs, 20)  # 低20%的高点
        zd = np.percentile(lows, 80)   # 高20%的低点
        
        if zg > zd:
            # 检查重叠度
            overlap = (zg - zd) / (max(highs) - min(lows))
            
            if overlap > overlap_ratio:
                zhongshu = ZhongShu(
                    start_idx=i,
                    end_idx=i + window - 1,
                    zg=zg,
                    zd=zd,
                    gg=max(highs),
                    dd=min(lows),
                    ma34=ma34.iloc[i + window - 1]
                )
                zhongshu_list.append(zhongshu)
    
    return zhongshu_list


if __name__ == "__main__":
    # 测试
    print("✅ 摩尔缠论中枢识别模块加载完成")
    print("使用方法：")
    print("  from core.zhongshu import detect_zhongshu, calculate_ma34")
    print("  zhongshu_list = detect_zhongshu(klines)")
