"""
缠论核心适配器 - 将新的chanlun_core与real_trader_v2.py对接
适配器模式：转换数据格式 + 包装接口
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chanlun_core_new import (
    KLine, detect_buy_signals, calc_ma, analyze_chanlun_structure,
    merge_klines, find_fenxing, build_bi, build_xianduan, build_zhongshu,
    calc_macd
)
from typing import List, Dict, Tuple, Optional


def candles_to_klines(candles: List[dict]) -> List[KLine]:
    """
    将OKX candles数据转换为chanlun_core的KLine格式
    
    Args:
        candles: OKX API返回的candle数据，每根包含:
            {'timestamp': '...', 'open': ..., 'high': ..., 'low': ..., 'close': ..., 'vol': ...}
    
    Returns:
        KLine列表
    """
    klines = []
    for c in candles:
        # 处理timestamp格式
        ts = c.get('timestamp', '')
        if not ts:
            ts = str(c.get('ts', ''))
        
        klines.append(KLine(
            date=ts,
            open=float(c.get('open', 0)),
            high=float(c.get('high', 0)),
            low=float(c.get('low', 0)),
            close=float(c.get('close', 0)),
            vol=float(c.get('vol', c.get('volume', 0)))
        ))
    return klines


def detect_chanlun_signals(candles: List[dict], 
                           big_yang_threshold: float = 0.05) -> Tuple[bool, Optional[dict], str]:
    """
    检测缠论买卖点 - 适配器接口
    保持与原有check_chan_buy()相同的返回值格式
    
    Args:
        candles: OKX candle数据列表
        big_yang_threshold: 大阳阈值 (默认5%)
    
    Returns:
        (has_signal, info_dict, message)
        info_dict格式: {
            'type': '摩尔缠论一买',  # 或其他类型
            'price': 123.45,
            'confidence': 0.75,
            'reason': '...',
            'weight': 3,
            # 其他元数据
        }
    """
    if len(candles) < 40:
        return False, None, '数据不足(需至少40根K线)'
    
    # 1. 转换数据格式
    klines = candles_to_klines(candles)
    
    # 2. 计算均线
    closes = [k.close for k in klines]
    ma5 = calc_ma(closes, 5)
    ma34 = calc_ma(closes, 34)
    ma170 = calc_ma(closes, 170)
    
    # 3. 检测买卖点
    signals = detect_buy_signals(
        klines, ma5, ma34, ma170,
        big_yang_threshold=big_yang_threshold
    )
    
    if not signals:
        return False, None, '无缠论买点信号'
    
    # 4. 筛选高置信度信号 (≥0.60)
    strong_signals = [s for s in signals if s.confidence >= 0.60]
    if not strong_signals:
        return False, None, '无高置信度买点信号'
    
    # 5. 取最高置信度的信号
    best_signal = max(strong_signals, key=lambda s: s.confidence)
    
    # 6. 映射信号类型到权重
    signal_type_map = {
        'jin1_std': ('摩尔缠论一买(标准)', 3),
        'jin1_ext': ('摩尔缠论一买(扩展)', 4),
        'shou1': ('摩尔缠论防守一买', 2),
        'jin2_approx': ('摩尔缠论二买', 3),
        'jin3_approx': ('摩尔缠论三买', 3),
        'shou2': ('摩尔缠论防守二买', 2),
    }
    
    signal_name, weight = signal_type_map.get(
        best_signal.stype, 
        ('摩尔缠论买点', 2)
    )
    
    # 7. 构建返回信息
    info = {
        'type': signal_name,
        'price': best_signal.price,
        'confidence': best_signal.confidence,
        'reason': best_signal.reason,
        'weight': weight,
        'meta': best_signal.meta,
        'signal_id': best_signal.id,
    }
    
    return True, info, f'{signal_name}: {best_signal.reason} (置信度{best_signal.confidence:.2f})'


def get_chanlun_structure(candles: List[dict]) -> dict:
    """
    获取缠论结构数据 - 用于前端展示
    
    Returns:
        {
            'fenxings': [...],   # 分型列表
            'bis': [...],        # 笔列表
            'xianduans': [...],  # 线段列表
            'zhongshus': [...],  # 中枢列表
            'signals': [...],    # 信号列表
        }
    """
    if len(candles) < 34:
        return {'error': '数据不足'}
    
    klines = candles_to_klines(candles)
    
    # 计算均线和MACD
    closes = [k.close for k in klines]
    ma5 = calc_ma(closes, 5)
    ma34 = calc_ma(closes, 34)
    ma170 = calc_ma(closes, 170)
    
    # 检测信号
    signals = detect_buy_signals(klines, ma5, ma34, ma170)
    
    # 分析结构
    structure = analyze_chanlun_structure(klines)
    
    return {
        'signals': [
            {
                'id': s.id,
                'type': s.stype,
                'date': s.date,
                'price': s.price,
                'confidence': s.confidence,
                'reason': s.reason,
            }
            for s in signals
        ],
        **structure
    }


# 保持向后兼容的别名
check_chanlun_buy = detect_chanlun_signals


if __name__ == '__main__':
    print('✅ 缠论核心适配器加载完成')
    print('使用方法：')
    print('  from core.chanlun_adapter import detect_chanlun_signals')
    print('  has_signal, info, msg = detect_chanlun_signals(candles)')
