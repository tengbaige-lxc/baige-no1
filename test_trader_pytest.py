#!/usr/bin/env python3
"""
pytest 格式的白鸽一号交易系统测试
运行方式:
  pytest test_trader_pytest.py -v
  pytest test_trader_pytest.py -v -s    # 显示print输出
  pytest test_trader_pytest.py --cov=.  # 显示覆盖率
"""

import sys
import os
import pytest
import sqlite3

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from real_trader_v2 import (
    Trader, OKXAPI, MAX_POSITIONS, LEVERAGE, 
    POSITION_PERCENT, TIME_STOP_LOSS, ATR_STOP_LOSS,
    SYMBOLS, BATCH_SIZES, ACTIVATION_PERCENT, CALLBACK_RATIO
)


# ============== Fixtures ==============

@pytest.fixture
def btc_trader():
    """BTC交易实例"""
    return Trader('BTC-USDT-SWAP')

@pytest.fixture
def eth_trader():
    """ETH交易实例"""
    return Trader('ETH-USDT-SWAP')


# ============== 配置测试 ==============

class TestConfiguration:
    """测试配置参数"""
    
    def test_max_positions_is_positive(self):
        """最大持仓数必须为正整数"""
        assert isinstance(MAX_POSITIONS, int)
        assert MAX_POSITIONS > 0
        print(f"\n✅ MAX_POSITIONS = {MAX_POSITIONS}")
    
    def test_max_positions_is_3(self):
        """验证最大持仓数为3"""
        assert MAX_POSITIONS == 3, f"期望MAX_POSITIONS=3, 实际={MAX_POSITIONS}"
    
    def test_leverage_reasonable(self):
        """杠杆倍数应该在合理范围内"""
        assert LEVERAGE > 1
        assert LEVERAGE <= 125
        print(f"\n✅ LEVERAGE = {LEVERAGE}x")
    
    def test_position_percent_valid(self):
        """仓位比例应该在0-1之间"""
        assert 0 < POSITION_PERCENT <= 1
        print(f"\n✅ POSITION_PERCENT = {POSITION_PERCENT*100}%")
    
    def test_batch_sizes_sum_to_1(self):
        """批次比例总和应该为1"""
        assert sum(BATCH_SIZES) == 1.0
        print(f"\n✅ BATCH_SIZES = {BATCH_SIZES}")


# ============== 风控测试 ==============

class TestRiskControls:
    """测试风控配置"""
    
    def test_time_stop_loss_enabled(self):
        """时间止损配置检查"""
        assert 'enabled' in TIME_STOP_LOSS
        assert 'max_hold_hours' in TIME_STOP_LOSS
        assert TIME_STOP_LOSS['max_hold_hours'] > 0
        print(f"\n✅ 时间止损: {'启用' if TIME_STOP_LOSS['enabled'] else '禁用'}")
    
    def test_atr_stop_loss_config(self):
        """ATR止损配置检查"""
        assert 'enabled' in ATR_STOP_LOSS
        assert 'atr_multiplier' in ATR_STOP_LOSS
        assert ATR_STOP_LOSS['atr_multiplier'] > 0
        print(f"\n✅ ATR止损: {'启用' if ATR_STOP_LOSS['enabled'] else '禁用'}")
    
    def test_trailing_stop_params(self):
        """移动止损参数检查"""
        assert 0 < ACTIVATION_PERCENT < 1
        assert 0 < CALLBACK_RATIO < 1
        print(f"\n✅ 移动止损: +{ACTIVATION_PERCENT*100:.0f}%激活, 回撤{CALLBACK_RATIO*100:.0f}%")


# ============== 仓位计算测试 ==============

class TestPositionCalculations:
    """测试仓位计算逻辑"""
    
    def test_contract_value_btc(self, btc_trader):
        """BTC合约面值检查"""
        ct_val = btc_trader._get_contract_value()
        assert ct_val == 0.01
        print(f"\n✅ BTC合约面值 = {ct_val}")
    
    def test_contract_value_eth(self, eth_trader):
        """ETH合约面值检查"""
        ct_val = eth_trader._get_contract_value()
        assert ct_val == 0.1
        print(f"\n✅ ETH合约面值 = {ct_val}")
    
    def test_contract_value_sol(self):
        """SOL合约面值检查"""
        trader = Trader('SOL-USDT-SWAP')
        ct_val = trader._get_contract_value()
        assert ct_val == 1.0
        print(f"\n✅ SOL合约面值 = {ct_val}")
    
    def test_position_size_calculation(self, btc_trader):
        """仓位大小计算检查"""
        sz = btc_trader.calculate_position_size(batch=1)
        assert sz is not None
        sz_float = float(sz)
        assert sz_float > 0
        print(f"\n✅ 仓位计算结果 = {sz} 张")
    
    def test_batch_1_larger_than_batch_3(self, btc_trader):
        """第一批仓位应该大于等于第三批"""
        sz1 = float(btc_trader.calculate_position_size(batch=1))
        sz3 = float(btc_trader.calculate_position_size(batch=3))
        assert sz1 >= sz3, f"第一批({sz1})应该大于等于第三批({sz3})"
        print(f"\n✅ 批次验证: 第一批({sz1}) >= 第三批({sz3})")


# ============== 持仓计数测试（核心） ==============

class TestPositionCounting:
    """测试持仓计数逻辑 - 针对分号bug"""
    
    def test_position_counting_with_3_positions(self):
        """模拟3个持仓的计数"""
        test_positions = [
            {'symbol': 'BTC-USDT-SWAP', 'pos': 0.38},
            {'symbol': 'ETH-USDT-SWAP', 'pos': 0.2},
            {'symbol': 'TRX-USDT-SWAP', 'pos': 2.7},
        ]
        
        positions_count = 0
        for pos in test_positions:
            try:
                pos_value = float(pos.get('pos', 0))
            except (ValueError, TypeError):
                pos_value = 0
            
            if pos_value > 0.01:
                positions_count += 1
        
        assert positions_count == 3
        assert positions_count <= MAX_POSITIONS
        print(f"\n✅ 持仓计数正确: {positions_count} / {MAX_POSITIONS}")
    
    def test_position_counting_with_zero_positions(self):
        """测试无持仓情况"""
        test_positions = [
            {'symbol': 'BTC-USDT-SWAP', 'pos': 0},
            {'symbol': 'ETH-USDT-SWAP', 'pos': 0},
        ]
        
        positions_count = sum(1 for p in test_positions if float(p.get('pos', 0)) > 0.01)
        assert positions_count == 0
        print(f"\n✅ 无持仓计数正确: {positions_count}")
    
    def test_position_counting_at_limit(self):
        """测试持仓达到上限"""
        test_positions = [
            {'symbol': 'BTC-USDT-SWAP', 'pos': 0.38},
            {'symbol': 'ETH-USDT-SWAP', 'pos': 0.2},
            {'symbol': 'TRX-USDT-SWAP', 'pos': 2.7},
            {'symbol': 'SOL-USDT-SWAP', 'pos': 1.5},  # 第4个 - 应该被拒绝
        ]
        
        positions_count = sum(1 for p in test_positions if float(p.get('pos', 0)) > 0.01)
        assert positions_count == 4  # 实际有4个
        assert positions_count > MAX_POSITIONS  # 超过上限
        print(f"\n✅ 超限检测: {positions_count} > {MAX_POSITIONS}")


# ============== 数据库测试 ==============

class TestDatabase:
    """测试数据库操作"""
    
    def test_database_connection(self):
        """数据库连接测试"""
        db_path = '/root/.openclaw/workspace/trading.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        conn.close()
        
        table_names = [t[0] for t in tables]
        assert 'trades' in table_names
        print(f"\n✅ 数据库连接成功，表: {table_names}")


# ============== 代码风格测试 ==============

class TestCodeQuality:
    """测试代码质量"""
    
    def test_no_semicolon_print_statements(self):
        """检查是否有分号连接print语句"""
        with open('real_trader_v2.py', 'r') as f:
            content = f.read()
        
        lines = content.split('\n')
        bad_lines = []
        for i, line in enumerate(lines, 1):
            if '; print(' in line or ';print(' in line:
                bad_lines.append((i, line.strip()))
        
        assert len(bad_lines) == 0, f"发现分号连接print: {bad_lines}"
        print("\n✅ 未发现分号连接print语句")
    
    def test_no_semicolon_assignment(self):
        """检查是否有分号连接赋值语句"""
        with open('real_trader_v2.py', 'r') as f:
            content = f.read()
        
        lines = content.split('\n')
        bad_lines = []
        for i, line in enumerate(lines, 1):
            if '; positions_count' in line or ';positions_count' in line:
                bad_lines.append((i, line.strip()))
        
        assert len(bad_lines) == 0, f"发现分号连接赋值: {bad_lines}"
        print("\n✅ 未发现分号连接赋值语句")


# ============== 集成测试 ==============

class TestIntegration:
    """集成测试"""
    
    def test_trader_initialization(self, btc_trader):
        """交易器初始化测试"""
        assert btc_trader.symbol == 'BTC-USDT-SWAP'
        assert btc_trader.coin == 'BTC'
        assert btc_trader.api is not None
        print("\n✅ 交易器初始化成功")
    
    def test_symbols_list_not_empty(self):
        """交易对列表不为空"""
        assert len(SYMBOLS) > 0
        print(f"\n✅ 监控 {len(SYMBOLS)} 个交易对")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
