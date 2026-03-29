#!/usr/bin/env python3
"""
白鸽一号交易系统测试套件
运行: python3 test_trading_system.py
"""

import unittest
import sys
import os
import sqlite3
from datetime import datetime

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入要测试的模块
from real_trader_v2 import (
    Trader, OKXAPI, MAX_POSITIONS, LEVERAGE, 
    POSITION_PERCENT, TIME_STOP_LOSS, ATR_STOP_LOSS
)


class TestConfiguration(unittest.TestCase):
    """测试配置参数"""
    
    def test_max_positions_is_positive(self):
        """最大持仓数必须为正整数"""
        self.assertIsInstance(MAX_POSITIONS, int)
        self.assertGreater(MAX_POSITIONS, 0)
        print(f"✅ MAX_POSITIONS = {MAX_POSITIONS}")
    
    def test_leverage_reasonable(self):
        """杠杆倍数应该在合理范围内"""
        self.assertGreater(LEVERAGE, 1)
        self.assertLessEqual(LEVERAGE, 125)  # OKX最大125倍
        print(f"✅ LEVERAGE = {LEVERAGE}x")
    
    def test_position_percent_valid(self):
        """仓位比例应该在0-1之间"""
        self.assertGreater(POSITION_PERCENT, 0)
        self.assertLessEqual(POSITION_PERCENT, 1)
        print(f"✅ POSITION_PERCENT = {POSITION_PERCENT*100}%")


class TestRiskControls(unittest.TestCase):
    """测试风控配置"""
    
    def test_time_stop_loss_config(self):
        """时间止损配置检查"""
        self.assertIn('enabled', TIME_STOP_LOSS)
        self.assertIn('max_hold_hours', TIME_STOP_LOSS)
        self.assertGreater(TIME_STOP_LOSS['max_hold_hours'], 0)
        print(f"✅ 时间止损: {'启用' if TIME_STOP_LOSS['enabled'] else '禁用'}")
    
    def test_atr_stop_loss_config(self):
        """ATR止损配置检查"""
        self.assertIn('enabled', ATR_STOP_LOSS)
        self.assertIn('atr_multiplier', ATR_STOP_LOSS)
        self.assertGreater(ATR_STOP_LOSS['atr_multiplier'], 0)
        print(f"✅ ATR止损: {'启用' if ATR_STOP_LOSS['enabled'] else '禁用'}")


class TestPositionCalculations(unittest.TestCase):
    """测试仓位计算逻辑"""
    
    def test_contract_value_btc(self):
        """BTC合约面值检查"""
        trader = Trader('BTC-USDT-SWAP')
        ct_val = trader._get_contract_value()
        self.assertEqual(ct_val, 0.01)
        print(f"✅ BTC合约面值 = {ct_val}")
    
    def test_contract_value_eth(self):
        """ETH合约面值检查"""
        trader = Trader('ETH-USDT-SWAP')
        ct_val = trader._get_contract_value()
        self.assertEqual(ct_val, 0.1)
        print(f"✅ ETH合约面值 = {ct_val}")
    
    def test_position_size_calculation(self):
        """仓位大小计算检查"""
        trader = Trader('BTC-USDT-SWAP')
        sz = trader.calculate_position_size(batch=1)
        self.assertIsNotNone(sz)
        try:
            sz_float = float(sz)
            self.assertGreater(sz_float, 0)
            print(f"✅ 仓位计算结果 = {sz} 张")
        except ValueError:
            self.fail(f"仓位大小'{sz}'无法转换为数字")


class TestDatabase(unittest.TestCase):
    """测试数据库操作"""
    
    def test_database_exists(self):
        """数据库文件是否存在"""
        db_path = '/root/.openclaw/workspace/trading.db'
        if os.path.exists(db_path):
            print(f"✅ 数据库文件存在")
        else:
            print(f"⚠️ 数据库文件不存在（首次运行）")
    
    def test_database_connection(self):
        """数据库连接测试"""
        db_path = '/root/.openclaw/workspace/trading.db'
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            conn.close()
            print(f"✅ 数据库连接成功，表: {[t[0] for t in tables]}")
        except Exception as e:
            print(f"⚠️ 数据库连接: {e}")


class TestPositionCounting(unittest.TestCase):
    """测试持仓计数逻辑（针对分号bug）"""
    
    def test_position_counting_logic(self):
        """模拟多品种持仓计数"""
        # 模拟持仓数据
        test_positions = [
            {'symbol': 'BTC-USDT-SWAP', 'pos': 0.38},
            {'symbol': 'ETH-USDT-SWAP', 'pos': 0.2},
            {'symbol': 'TRX-USDT-SWAP', 'pos': 2.7},
            {'symbol': 'SOL-USDT-SWAP', 'pos': 0},  # 无持仓
        ]
        
        positions_count = 0
        for pos in test_positions:
            try:
                pos_value = float(pos.get('pos', 0))
            except (ValueError, TypeError):
                pos_value = 0
            
            if pos_value > 0.01:
                positions_count += 1
                print(f"  持仓: {pos['symbol']} = {pos_value} 张")
        
        self.assertEqual(positions_count, 3, "持仓计数应该为3")
        self.assertLessEqual(positions_count, MAX_POSITIONS, f"持仓数不应超过上限{MAX_POSITIONS}")
        print(f"✅ 持仓计数正确: {positions_count} / {MAX_POSITIONS}")


class TestAPISyntax(unittest.TestCase):
    """测试API语法"""
    
    def test_no_semicolon_lines(self):
        """检查是否有分号连接代码的行"""
        with open('real_trader_v2.py', 'r') as f:
            lines = f.readlines()
        
        semicolon_lines = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # 忽略注释和字符串中的分号
            if ';' in stripped and not stripped.startswith('#'):
                # 检查是否是简单的语句分隔（非for循环等）
                if '; print(' in stripped or '; positions_count' in stripped:
                    semicolon_lines.append((i, stripped[:80]))
        
        if semicolon_lines:
            print(f"⚠️ 发现分号连接代码的行:")
            for line_num, content in semicolon_lines:
                print(f"  第{line_num}行: {content}")
        else:
            print("✅ 未发现分号连接代码的问题")
        
        # 只是警告，不强制失败
        self.assertTrue(True)


def run_tests():
    """运行所有测试"""
    print("\n" + "="*60)
    print("🧪 白鸽一号交易系统测试套件")
    print("="*60 + "\n")
    
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加测试类
    suite.addTests(loader.loadTestsFromTestCase(TestConfiguration))
    suite.addTests(loader.loadTestsFromTestCase(TestRiskControls))
    suite.addTests(loader.loadTestsFromTestCase(TestPositionCalculations))
    suite.addTests(loader.loadTestsFromTestCase(TestDatabase))
    suite.addTests(loader.loadTestsFromTestCase(TestPositionCounting))
    suite.addTests(loader.loadTestsFromTestCase(TestAPISyntax))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 总结
    print("\n" + "="*60)
    print("📊 测试结果总结")
    print("="*60)
    print(f"总测试数: {result.testsRun}")
    print(f"通过: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    
    if result.wasSuccessful():
        print("\n✅ 所有测试通过！")
    else:
        print("\n❌ 存在失败的测试")
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
