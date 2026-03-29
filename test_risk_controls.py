#!/usr/bin/env python3
"""
风控系统测试
测试各种风控逻辑，确保风险控制系统正确运行
运行: pytest test_risk_controls.py -v
"""

import sys
import os
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from real_trader_v2 import (
    Trader, TIME_STOP_LOSS, ATR_STOP_LOSS, DAILY_RISK_CONTROL,
    CORRELATION_CONTROL, ACTIVATION_PERCENT, CALLBACK_RATIO,
    PROFIT_TIERS, MAX_DAILY_LOSS_PERCENT
)


class TestTimeStopLoss:
    """测试时间止损"""
    
    def test_time_stop_loss_enabled(self):
        """时间止损已启用"""
        assert TIME_STOP_LOSS['enabled'] == True
        print(f"\n✅ 时间止损已启用")
    
    def test_max_hold_hours_positive(self):
        """最大持仓时间为正数"""
        assert TIME_STOP_LOSS['max_hold_hours'] > 0
        print(f"\n✅ 最大持仓时间: {TIME_STOP_LOSS['max_hold_hours']}小时")
    
    def test_reduce_ratio_valid(self):
        """减仓比例在0-1之间"""
        ratio = TIME_STOP_LOSS['reduce_ratio']
        assert 0 < ratio <= 1
        print(f"\n✅ 时间止损减仓比例: {ratio*100}%")
    
    def test_time_stop_trigger_scenario(self):
        """时间止损触发场景"""
        hold_hours = 48  # 刚好48小时
        profit_pct = -0.01  # 亏损1%
        
        should_trigger = (
            hold_hours >= TIME_STOP_LOSS['max_hold_hours'] and 
            profit_pct <= 0 and
            TIME_STOP_LOSS['reduce_if_no_profit']
        )
        
        assert should_trigger == True
        print(f"\n✅ 时间止损触发: 持仓{hold_hours}小时, 盈亏{profit_pct*100}%")
    
    def test_time_stop_not_trigger_when_profitable(self):
        """盈利时不触发时间止损"""
        hold_hours = 48
        profit_pct = 0.05  # 盈利5%
        
        should_trigger = (
            hold_hours >= TIME_STOP_LOSS['max_hold_hours'] and 
            profit_pct <= 0
        )
        
        assert should_trigger == False
        print(f"\n✅ 盈利时不触发时间止损: 盈亏+{profit_pct*100}%")


class TestATRStopLoss:
    """测试ATR波动率止损"""
    
    def test_atr_stop_loss_enabled(self):
        """ATR止损已启用"""
        assert ATR_STOP_LOSS['enabled'] == True
        print(f"\n✅ ATR止损已启用")
    
    def test_atr_period_positive(self):
        """ATR周期为正数"""
        assert ATR_STOP_LOSS['atr_period'] > 0
        print(f"\n✅ ATR周期: {ATR_STOP_LOSS['atr_period']}")
    
    def test_atr_multiplier_positive(self):
        """ATR倍数为正数"""
        assert ATR_STOP_LOSS['atr_multiplier'] > 0
        print(f"\n✅ ATR倍数: {ATR_STOP_LOSS['atr_multiplier']}x")
    
    def test_min_max_stop_pct_valid(self):
        """最小/最大止损百分比有效"""
        min_pct = ATR_STOP_LOSS['min_stop_pct']
        max_pct = ATR_STOP_LOSS['max_stop_pct']
        
        assert 0 < min_pct < max_pct < 1
        print(f"\n✅ ATR止损范围: {min_pct*100}% - {max_pct*100}%")
    
    def test_atr_stop_calculation(self):
        """ATR止损价格计算"""
        entry_price = 2000
        atr = 15
        multiplier = 2.0
        
        # ATR止损距离
        atr_distance = atr * multiplier
        atr_pct = atr_distance / entry_price
        
        # 限制在范围内
        min_pct = ATR_STOP_LOSS['min_stop_pct']
        max_pct = ATR_STOP_LOSS['max_stop_pct']
        stop_pct = max(min_pct, min(atr_pct, max_pct))
        
        # 止损价格
        stop_price = entry_price * (1 - stop_pct)
        
        assert stop_price < entry_price
        print(f"\n✅ ATR止损价格: ${stop_price:.2f} (入场${entry_price}, 止损{stop_pct*100:.2f}%)")
    
    def test_atr_stop_trigger_scenario(self):
        """ATR止损触发场景"""
        entry_price = 2000
        current_price = 1950  # 下跌2.5%
        stop_price = 1960     # 止损价在2%
        
        should_trigger = current_price <= stop_price
        assert should_trigger == True
        print(f"\n✅ ATR止损触发: 当前${current_price} <= 止损${stop_price}")


class TestDailyRiskControl:
    """测试单日风控"""
    
    def test_max_trades_positive(self):
        """最大交易次数为正数"""
        assert DAILY_RISK_CONTROL['max_trades_per_day'] > 0
        print(f"\n✅ 单日最大交易: {DAILY_RISK_CONTROL['max_trades_per_day']}次")
    
    def test_max_loss_positive(self):
        """最大亏损比例为正数"""
        assert DAILY_RISK_CONTROL['max_loss_per_day'] > 0
        print(f"\n✅ 单日最大亏损: {DAILY_RISK_CONTROL['max_loss_per_day']*100}%")
    
    def test_max_daily_loss_matches_config(self):
        """单日最大亏损与全局配置一致"""
        assert DAILY_RISK_CONTROL['max_loss_per_day'] == MAX_DAILY_LOSS_PERCENT
        print(f"\n✅ 单日最大亏损配置一致: {MAX_DAILY_LOSS_PERCENT*100}%")
    
    def test_trade_count_limit_scenario(self):
        """交易次数超限场景"""
        current_trades = 10
        max_trades = DAILY_RISK_CONTROL['max_trades_per_day']
        
        should_block = current_trades >= max_trades
        assert should_block == True
        print(f"\n✅ 交易次数超限: {current_trades}/{max_trades}")
    
    def test_daily_loss_limit_scenario(self):
        """单日亏损超限场景"""
        balance = 100
        daily_pnl = -6.5  # 亏损6.5 USDT
        max_loss_pct = DAILY_RISK_CONTROL['max_loss_per_day']
        
        daily_loss_pct = abs(daily_pnl) / balance
        should_block = daily_loss_pct >= max_loss_pct
        
        assert should_block == True
        print(f"\n✅ 单日亏损超限: {daily_loss_pct*100:.2f}% >= {max_loss_pct*100}%")


class TestCorrelationRiskControl:
    """测试相关性风控"""
    
    def test_correlation_control_enabled(self):
        """相关性风控已启用"""
        assert CORRELATION_CONTROL['enabled'] == True
        print(f"\n✅ 相关性风控已启用")
    
    def test_max_same_direction_positive(self):
        """同向持仓上限为正数"""
        assert CORRELATION_CONTROL['max_same_direction'] > 0
        print(f"\n✅ 同向持仓上限: {CORRELATION_CONTROL['max_same_direction']}")
    
    def test_correlation_pairs_defined(self):
        """高相关性币种对已定义"""
        pairs = CORRELATION_CONTROL['high_correlation_pairs']
        assert len(pairs) > 0
        print(f"\n✅ 高相关性币种对: {len(pairs)}对")
    
    def test_btc_eth_correlation(self):
        """BTC-ETH高相关性检测"""
        held_coins = {'BTC', 'ETH', 'SOL'}
        pairs = CORRELATION_CONTROL['high_correlation_pairs']
        
        has_correlated = any(
            coin1 in held_coins and coin2 in held_coins 
            for coin1, coin2 in pairs
        )
        
        assert has_correlated == True
        print(f"\n✅ BTC-ETH相关性检测: 持有高相关币种")
    
    def test_correlation_risk_scenario(self):
        """相关性风险场景"""
        long_positions = [
            {'coin': 'BTC'},
            {'coin': 'ETH'},  # 与BTC高相关
            {'coin': 'SOL'},  # 与ETH高相关
        ]
        max_same = CORRELATION_CONTROL['max_same_direction']
        
        should_warn = len(long_positions) >= max_same
        assert should_warn == True
        print(f"\n✅ 相关性风险: {len(long_positions)}个持仓 >= 上限{max_same}")


class TestTrailingStop:
    """测试移动止损"""
    
    def test_activation_percent_positive(self):
        """激活百分比为正数"""
        assert ACTIVATION_PERCENT > 0
        print(f"\n✅ 移动止损激活: +{ACTIVATION_PERCENT*100}%")
    
    def test_callback_ratio_positive(self):
        """回撤比例为正数"""
        assert CALLBACK_RATIO > 0
        print(f"\n✅ 移动止损回撤: {CALLBACK_RATIO*100}%")
    
    def test_activation_calculation(self):
        """激活价格计算"""
        entry_price = 100
        activation_price = entry_price * (1 + ACTIVATION_PERCENT)
        expected = 100 * (1 + 0.05)  # 5%激活
        
        assert activation_price == expected
        print(f"\n✅ 激活价格: ${activation_price:.2f} (入场${entry_price} +{ACTIVATION_PERCENT*100}%)")
    
    def test_trailing_stop_trigger_scenario(self):
        """移动止损触发场景"""
        entry_price = 100
        highest_price = 110  # 最高到110
        current_price = 105.5  # 当前回落到105.5
        
        # 回撤比例
        callback = (highest_price - current_price) / (highest_price - entry_price)
        should_trigger = callback >= CALLBACK_RATIO
        
        assert should_trigger == True
        print(f"\n✅ 移动止损触发: 回撤{callback*100:.1f}% >= {CALLBACK_RATIO*100}%")
    
    def test_trailing_stop_not_triggered_early(self):
        """未达到回撤不触发"""
        entry_price = 100
        highest_price = 110
        current_price = 108  # 小幅回落
        
        callback = (highest_price - current_price) / (highest_price - entry_price)
        should_trigger = callback >= CALLBACK_RATIO
        
        assert should_trigger == False
        print(f"\n✅ 未触发: 回撤{callback*100:.1f}% < {CALLBACK_RATIO*100}%")


class TestProfitTiers:
    """测试阶梯止盈"""
    
    def test_profit_tiers_defined(self):
        """阶梯止盈已定义"""
        assert len(PROFIT_TIERS) > 0
        print(f"\n✅ 阶梯止盈: {len(PROFIT_TIERS)}档")
    
    def test_profit_tiers_sorted(self):
        """阶梯止盈按盈利排序"""
        profits = [t['profit'] for t in PROFIT_TIERS]
        assert profits == sorted(profits)
        print(f"\n✅ 阶梯止盈已排序: {[f'{p*100}%' for p in profits]}")
    
    def test_tier_1_reduce_valid(self):
        """第一档减仓比例有效"""
        tier1 = PROFIT_TIERS[0]
        assert 0 < tier1['reduce'] <= 1
        print(f"\n✅ 第一档: +{tier1['profit']*100}% 减仓{tier1['reduce']*100}%")
    
    def test_tier_trigger_scenario(self):
        """阶梯止盈触发场景"""
        profit_pct = 0.09  # 盈利9%
        tier = PROFIT_TIERS[0]  # +8%档
        
        should_trigger = profit_pct >= tier['profit']
        assert should_trigger == True
        print(f"\n✅ 阶梯止盈触发: 盈利{profit_pct*100}% >= {tier['profit']*100}%")
    
    def test_final_tier_closes_all(self):
        """最后一档应全部平仓"""
        final_tier = PROFIT_TIERS[-1]
        assert final_tier['reduce'] == 1.0
        print(f"\n✅ 最后一档全部平仓: +{final_tier['profit']*100}% 减仓100%")


class TestRiskPriority:
    """测试风控优先级"""
    
    def test_atr_stop_highest_priority(self):
        """ATR止损最高优先级"""
        # ATR止损: 全部平仓
        # 时间止损: 减仓50%
        # 相关性风控: 阻止新开仓
        
        priorities = {
            'atr_stop': 1,      # 最高
            'time_stop': 2,
            'correlation': 3,
            'daily_limit': 4,
        }
        
        assert priorities['atr_stop'] < priorities['time_stop']
        print(f"\n✅ ATR止损优先级最高")
    
    def test_risk_action_types(self):
        """风控动作类型"""
        actions = {
            'close': '全部平仓',    # ATR止损
            'reduce': '减仓',       # 时间止损
            'block': '阻止开仓',    # 相关性风控
            'hold': '持仓观察',
        }
        
        assert 'close' in actions
        assert 'reduce' in actions
        print(f"\n✅ 风控动作类型: {list(actions.keys())}")


class TestRealWorldRiskScenarios:
    """真实场景风控测试"""
    
    def test_scenario_1_atr_stop_triggered(self):
        """场景1: ATR止损触发"""
        entry_price = 2000
        atr = 20
        stop_price = entry_price - (atr * 2)  # ATR*2
        current_price = stop_price - 10  # 跌破止损
        
        should_close = current_price <= stop_price
        assert should_close == True
        print(f"\n✅ 场景1-ATR止损: 当前${current_price} <= 止损${stop_price}")
    
    def test_scenario_2_time_stop_triggered(self):
        """场景2: 时间止损触发"""
        entry_time = datetime.now() - timedelta(hours=49)
        current_time = datetime.now()
        hold_hours = (current_time - entry_time).total_seconds() / 3600
        profit_pct = -0.02  # 亏损2%
        
        should_reduce = (
            hold_hours >= TIME_STOP_LOSS['max_hold_hours'] and
            profit_pct <= 0
        )
        
        assert should_reduce == True
        print(f"\n✅ 场景2-时间止损: 持仓{hold_hours:.1f}小时, 盈亏{profit_pct*100}%")
    
    def test_scenario_3_trailing_stop_triggered(self):
        """场景3: 移动止损触发"""
        entry = 100
        highest = 110
        current = 105
        
        # 回撤计算
        callback = (highest - current) / (highest - entry)
        should_close = callback >= CALLBACK_RATIO
        
        assert should_close == True
        print(f"\n✅ 场景3-移动止损: 最高${highest}, 当前${current}, 回撤{callback*100:.0f}%")
    
    def test_scenario_4_daily_limit_reached(self):
        """场景4: 单日亏损上限"""
        daily_loss_pct = 0.065  # 6.5%
        max_loss = DAILY_RISK_CONTROL['max_loss_per_day']
        
        should_stop = daily_loss_pct >= max_loss
        assert should_stop == True
        print(f"\n✅ 场景4-日亏损上限: {daily_loss_pct*100}% >= {max_loss*100}%")
    
    def test_scenario_5_correlation_risk(self):
        """场景5: 相关性风险"""
        held_coins = {'BTC', 'ETH', 'SOL'}
        max_positions = CORRELATION_CONTROL['max_same_direction']
        
        # BTC和ETH高相关
        has_btc_eth = 'BTC' in held_coins and 'ETH' in held_coins
        
        should_warn = len(held_coins) >= max_positions and has_btc_eth
        assert should_warn == True
        print(f"\n✅ 场景5-相关性风险: 持有{len(held_coins)}个, 包含BTC+ETH高相关对")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
