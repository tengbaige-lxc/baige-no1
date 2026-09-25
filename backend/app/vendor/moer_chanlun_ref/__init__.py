"""
缠论核心模块入口 (已替换为 OpenClaw K线分析 迁移版)
ChanLun - Core Module Entry (Migrated from OpenClaw K-line Analysis)
"""

# ===== 新包核心导出 =====
from .chanlun_core import (
    # 数据模型
    KLine,
    Bi,
    Xianduan,
    Zhongshu,
    BuySignal,
    # K线处理
    merge_klines,
    find_fenxing,
    # 结构构建
    build_bi,
    build_xianduan,
    build_zhongshu,
    build_ma34_zhongshu_items,
    # 辅助计算
    calc_ma,
    calc_macd,
    calc_macd_area,
    # 买卖点 & 结构分析
    detect_buy_signals,
    analyze_chanlun_structure,
)

from .chanlun_api_adapter import (
    chanlun_bp,
    analyze_klines,
    parse_klines,
)

# 兼容旧接口：提供适配函数（基于新包实现）
import pandas as pd
import math
from typing import List, Tuple, Optional
from .chanlun_core import KLine, find_fenxing, build_ma34_zhongshu_items, calc_ma


class FenXingType:
    """兼容旧版分型类型"""
    TOP = "top"
    BOTTOM = "bottom"


class FenXing:
    """兼容旧版分型数据类"""
    def __init__(self, idx, type, price, timestamp=None):
        self.idx = idx
        self.type = type
        self.price = price
        self.timestamp = timestamp


class ZhongShu:
    """兼容旧版中枢数据类"""
    def __init__(self, start_idx, end_idx, zg, zd, gg=None, dd=None, ma34=None):
        self.start_idx = start_idx
        self.end_idx = end_idx
        self.zg = zg
        self.zd = zd
        self.gg = gg or zg
        self.dd = dd or zd
        self.ma34 = ma34 or (zg + zd) / 2

    @property
    def center(self) -> float:
        return (self.zg + self.zd) / 2

    @property
    def height(self) -> float:
        return self.zg - self.zd

    @property
    def height_pct(self) -> float:
        return self.height / self.center * 100 if self.center else 0

    @property
    def kline_count(self) -> int:
        return self.end_idx - self.start_idx + 1

    def contains_price(self, price: float) -> bool:
        return self.zd <= price <= self.zg

    def is_above(self, price: float) -> bool:
        return price > self.zg

    def is_below(self, price: float) -> bool:
        return price < self.zd


def detect_fenxing(klines: pd.DataFrame, use_body: bool = False) -> Tuple[List[FenXing], List[FenXing]]:
    """
    兼容旧版：基于 pandas DataFrame 的分型识别
    底层已替换为新包实现
    """
    kline_list = [
        KLine(
            date=str(klines.index[i]) if hasattr(klines, 'index') else str(i),
            open=float(row.get('open', 0)),
            high=float(row.get('high', 0)),
            low=float(row.get('low', 0)),
            close=float(row.get('close', 0)),
            vol=float(row.get('volume', row.get('vol', 0))),
        )
        for i, row in klines.iterrows()
    ]

    merged = merge_klines(kline_list)
    fx_klines = find_fenxing(merged)

    tops = []
    bottoms = []
    for idx, k in enumerate(fx_klines):
        ts = klines.index[idx] if hasattr(klines, 'index') and idx < len(klines) else None
        if k.fenxing == 'top':
            tops.append(FenXing(idx=idx, type=FenXingType.TOP, price=k.high, timestamp=ts))
        elif k.fenxing == 'bottom':
            bottoms.append(FenXing(idx=idx, type=FenXingType.BOTTOM, price=k.low, timestamp=ts))

    return tops, bottoms


def calculate_ma34(klines: pd.DataFrame) -> pd.Series:
    """兼容旧版：计算34日均线"""
    closes = klines['close'].values.tolist()
    ma34 = calc_ma(closes, 34)
    return pd.Series(ma34, index=klines.index if hasattr(klines, 'index') else None)


def detect_zhongshu(klines: pd.DataFrame,
                   min_klines: int = 10,
                   max_klines: int = 100,
                   overlap_threshold: float = 0.6) -> List[ZhongShu]:
    """
    兼容旧版：基于 pandas DataFrame 的中枢识别
    底层已替换为新包 MA34 标准中枢实现
    """
    kline_list = [
        KLine(
            date=str(klines.index[i]) if hasattr(klines, 'index') else str(i),
            open=float(row.get('open', 0)),
            high=float(row.get('high', 0)),
            low=float(row.get('low', 0)),
            close=float(row.get('close', 0)),
            vol=float(row.get('volume', row.get('vol', 0))),
        )
        for i, row in klines.iterrows()
    ]

    items = build_ma34_zhongshu_items(kline_list, min_distance=6)
    result = []
    for item in items:
        result.append(ZhongShu(
            start_idx=item['startIndex'],
            end_idx=item['endIndex'],
            zg=item['upper'],
            zd=item['lower'],
            gg=item['upper'],
            dd=item['lower'],
            ma34=(item['upper'] + item['lower']) / 2,
        ))
    return result


def detect_zhongshu_simple(klines: pd.DataFrame,
                           window: int = 20,
                           overlap_ratio: float = 0.5) -> List[ZhongShu]:
    """兼容旧版：简化版中枢识别（委托给标准版）"""
    return detect_zhongshu(klines)


def find_peaks_troughs(klines: pd.DataFrame, window: int = 3) -> Tuple[List[int], List[int]]:
    """兼容旧版：寻找波峰波谷"""
    highs = klines['high'].values
    lows = klines['low'].values
    peaks = []
    troughs = []
    for i in range(window, len(klines) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            peaks.append(i)
        if lows[i] == min(lows[i - window:i + window + 1]):
            troughs.append(i)
    return peaks, troughs


__all__ = [
    # 新包核心
    'KLine', 'Bi', 'Xianduan', 'Zhongshu', 'BuySignal',
    'merge_klines', 'find_fenxing',
    'build_bi', 'build_xianduan', 'build_zhongshu', 'build_ma34_zhongshu_items',
    'calc_ma', 'calc_macd', 'calc_macd_area',
    'detect_buy_signals', 'analyze_chanlun_structure',
    'chanlun_bp', 'analyze_klines', 'parse_klines',
    # 兼容旧接口
    'FenXing', 'FenXingType', 'ZhongShu',
    'detect_fenxing', 'detect_zhongshu', 'detect_zhongshu_simple',
    'calculate_ma34', 'find_peaks_troughs',
]

__version__ = "1.0.0-migrated"
