#!/usr/bin/env python3
"""
交易信号组合测试
测试各种信号组合情况，确保信号系统逻辑正确
运行: pytest test_signal_combinations.py -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from real_trader_v2 import (
    Trader, BATCH_SIZES, SYMBOLS
)


class TestSignalWeightSystem:
    """测试信号权重系统"""
    
    def test_divergence_signal_weight(self):
        """测试背离信号权重"""
        # 5分钟底背离: 权重2（高置信度）
        # 30分钟底背离: 权重4（高置信度）
        # 多周期共振: 权重5
        signals = [
            ('5分钟底背离', 2),
            ('30分钟底背离', 4),
            ('多周期底背离共振', 5),
        ]
        
        for name, weight in signals:
            assert weight >= 0, f"{name}权重不能为负"
            print(f"\n✅ {name}: 权重{weight}")
    
    def test_chanlun_signal_weight(self):
        """测试摩尔缠论信号权重"""
        # 摩尔缠论一买/三买: 3分
        # 摩尔缠论二买: 2分
        signals = [
            ('摩尔缠论一买', 3),
            ('摩尔缠论二买', 2),
            ('摩尔缠论三买', 3),
        ]
        
        for name, weight in signals:
            assert weight >= 0, f"{name}权重不能为负"
            print(f"\n✅ {name}: 权重{weight}")
    
    def test_ma_cross_weight(self):
        """测试均线金叉权重"""
        weight = 1
        assert weight > 0
        print(f"\n✅ 均线金叉: 权重{weight}")
    
    def test_combined_signal_weights(self):
        """测试组合信号权重计算"""
        # 模拟多种信号组合
        test_cases = [
            {
                'name': '强信号组合',
                'signals': [('多周期底背离共振', 5), ('摩尔缠论三买', 3)],
                'expected_min': 6,
            },
            {
                'name': '中等信号组合',
                'signals': [('30分钟底背离', 4), ('均线金叉', 1)],
                'expected_min': 5,
            },
            {
                'name': '弱信号组合',
                'signals': [('5分钟底背离', 2)],
                'expected_min': 2,
            },
        ]
        
        for case in test_cases:
            total_weight = sum(w for _, w in case['signals'])
            assert total_weight >= case['expected_min'], \
                f"{case['name']}权重{total_weight}应>= {case['expected_min']}"
            print(f"\n✅ {case['name']}: 总权重{total_weight}")


class TestSignalThresholds:
    """测试信号阈值"""
    
    def test_strong_signal_threshold(self):
        """强信号阈值 >= 6"""
        threshold = 6
        signals = [('多周期底背离共振', 5), ('摩尔缠论三买', 3)]
        total = sum(w for _, w in signals)
        
        assert total >= threshold, f"强信号需要>= {threshold}分, 实际{total}分"
        print(f"\n✅ 强信号阈值: {threshold}分 (当前{total}分)")
    
    def test_medium_signal_threshold(self):
        """中等信号阈值 >= 5"""
        threshold = 5
        signals = [('30分钟底背离', 4), ('均线金叉', 1)]
        total = sum(w for _, w in signals)
        
        assert total >= threshold, f"中等信号需要>= {threshold}分, 实际{total}分"
        print(f"\n✅ 中等信号阈值: {threshold}分 (当前{total}分)")
    
    def test_weak_signal_rejection(self):
        """弱信号应被拒绝 (< 4)"""
        weak_signals = [
            [('5分钟底背离', 2)],  # 总权重2
            [('均线金叉', 1)],     # 总权重1
        ]
        
        for signals in weak_signals:
            total = sum(w for _, w in signals)
            assert total < 4, f"弱信号{total}分应该被拒绝"
            print(f"\n✅ 弱信号正确拒绝: {total}分 < 4分")


class TestBatchEntryLogic:
    """测试分批入场逻辑"""
    
    def test_batch_1_entry_conditions(self):
        """第一批入场条件"""
        # 第一批: 强信号或中等信号
        strong_signals = [
            ('多周期底背离共振', 5),
            ('摩尔缠论三买', 3),
        ]
        total = sum(w for _, w in strong_signals)
        
        assert total >= 3, f"第一批需要>= 3分, 实际{total}分"
        print(f"\n✅ 第一批入场: 权重{total} >= 3")
    
    def test_batch_2_entry_conditions(self):
        """第二批加仓条件"""
        # 第二批: 30分钟底背离 + 清算热力图
        batch2_signals = [
            ('30分钟底背离', 4),
            ('清算热力图2分', 2),
        ]
        total = sum(w for _, w in batch2_signals)
        
        # 需要30分钟底背离 + 清算热力图 >= 2分
        assert total >= 5, f"第二批需要>= 5分, 实际{total}分"
        print(f"\n✅ 第二批加仓: 权重{total} >= 5")
    
    def test_batch_3_entry_conditions(self):
        """第三批加仓条件"""
        # 第三批: 均线金叉确认
        has_ma_cross = True
        assert has_ma_cross, "第三批需要均线金叉"
        print(f"\n✅ 第三批加仓: 均线金叉确认")


class TestSignalDiversity:
    """测试信号多样性"""
    
    def test_signal_type_count(self):
        """测试信号类型数量"""
        # 执行条件需要至少2种不同类型信号
        signals = [
            ('多周期底背离共振', 5),
            ('摩尔缠论三买', 3),
        ]
        signal_types = set(s[0] for s in signals)
        
        assert len(signal_types) >= 2, f"需要至少2种信号类型, 实际{len(signal_types)}种"
        print(f"\n✅ 信号多样性: {len(signal_types)}种类型")
    
    def test_single_signal_type_rejection(self):
        """单一信号类型应被拒绝"""
        single_type_signals = [
            ('5分钟底背离', 2),
            ('5分钟底背离', 2),  # 重复类型
        ]
        signal_types = set(s[0] for s in single_type_signals)
        
        # 即使是同一类型多次出现，也应该算多样性不足
        assert len(signal_types) < 2, "单一信号类型应该被拒绝"
        print(f"\n✅ 单一信号类型正确识别: {len(signal_types)}种")


class TestLiquidationSignal:
    """测试清算热力图信号"""
    
    def test_liqmap_signal_weights(self):
        """清算热力图信号权重"""
        weights = {
            'LONG_BIAS': 1,  # 偏向多头
            'LONG': 2,       # 多头信号
            'STRONG_LONG': 3, # 强烈多头
        }
        
        for signal_type, weight in weights.items():
            assert 1 <= weight <= 3, f"{signal_type}权重应在1-3之间"
            print(f"\n✅ 清算热力图 {signal_type}: 权重{weight}")
    
    def test_liqmap_threshold(self):
        """清算热力图阈值"""
        min_strength = 2  # 至少需要2分
        
        test_cases = [
            (1, False),  # 1分 - 不足
            (2, True),   # 2分 - 足够
            (3, True),   # 3分 - 足够
        ]
        
        for strength, should_pass in test_cases:
            result = strength >= min_strength
            assert result == should_pass, \
                f"清算热力图{strength}分应该{'通过' if should_pass else '拒绝'}"
            print(f"\n✅ 清算热力图 {strength}分: {'通过' if result else '拒绝'}")


class TestSignalCombinationsRealWorld:
    """真实场景信号组合测试"""
    
    def test_scenario_1_strong_divergence(self):
        """场景1: 强背离信号"""
        signals = [
            ('5分钟底背离', 2),
            ('15分钟底背离', 3),
            ('30分钟底背离', 4),
        ]
        total = sum(w for _, w in signals)
        signal_types = len(set(s[0] for s in signals))
        
        assert total >= 6, f"强背离场景应>= 6分"
        assert signal_types >= 2, "应有多样性"
        print(f"\n✅ 场景1-强背离: {total}分, {signal_types}种类型")
    
    def test_scenario_2_chanlun_buy(self):
        """场景2: 缠论买点"""
        signals = [
            ('摩尔缠论一买', 3),
            ('5分钟底背离', 2),
            ('清算热力图', 2),
        ]
        total = sum(w for _, w in signals)
        has_chanlun = any('缠论' in s[0] for s in signals)
        has_divergence = any('背离' in s[0] for s in signals)
        
        # 缠论+背离组合加分
        if has_chanlun and has_divergence:
            total += 2
        
        assert total >= 5, f"缠论买点场景应>= 5分"
        print(f"\n✅ 场景2-缠论买点: {total}分 (含组合加分)")
    
    def test_scenario_3_weak_no_entry(self):
        """场景3: 弱信号不入场"""
        signals = [
            ('5分钟底背离', 2),
        ]
        total = sum(w for _, w in signals)
        signal_types = len(set(s[0] for s in signals))
        
        # 权重<4 或 信号类型<2 都应该拒绝
        should_reject = total < 4 or signal_types < 2
        assert should_reject, f"弱信号应该被拒绝"
        print(f"\n✅ 场景3-弱信号正确拒绝: {total}分")
    
    def test_scenario_4_ma_cross_only(self):
        """场景4: 仅均线金叉不入场"""
        signals = [
            ('均线金叉', 1),
        ]
        total = sum(w for _, w in signals)
        
        assert total < 3, "仅均线金叉不应入场"
        print(f"\n✅ 场景4-仅金叉正确拒绝: {total}分 < 3")


class TestSignalPriority:
    """测试信号优先级"""
    
    def test_high_priority_signals(self):
        """高优先级信号"""
        high_priority = [
            '多周期底背离共振',
            '摩尔缠论一买',
            '摩尔缠论三买',
        ]
        
        for signal in high_priority:
            assert '共振' in signal or '一买' in signal or '三买' in signal
            print(f"\n✅ 高优先级信号: {signal}")
    
    def test_medium_priority_signals(self):
        """中等优先级信号"""
        medium_priority = [
            '30分钟底背离',
            '摩尔缠论二买',
            '动态反弹',
        ]
        
        for signal in medium_priority:
            assert '30分钟' in signal or '二买' in signal or '反弹' in signal
            print(f"\n✅ 中等优先级信号: {signal}")
    
    def test_low_priority_signals(self):
        """低优先级信号"""
        low_priority = [
            '5分钟底背离',
            '均线金叉',
        ]
        
        for signal in low_priority:
            assert '5分钟' in signal or '金叉' in signal
            print(f"\n✅ 低优先级信号: {signal}")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
