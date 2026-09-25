#!/usr/bin/env python3
"""
摩尔缠论 - 全自动实盘交易系统 V1.0
整合: 数据获取 + 信号分析 + 自动交易 + 风控管理
"""

import sys
import time
import json
from datetime import datetime
from typing import Dict, Optional

# 导入模块
from moer_quant_pure import MoerChanlun, SignalType
from realtime_data import RealtimeDataManager
from auto_trading import AutoTrader

# ========== 用户配置区 ==========
# 从配置文件导入API设置
try:
    sys.path.insert(0, '/root/.openclaw/workspace/moer-chanlun')
    from config import OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
    API_KEY = OKX_API_KEY
    API_SECRET = OKX_API_SECRET  
    PASSPHRASE = OKX_PASSPHRASE
except ImportError:
    # 如果配置文件不存在，使用默认空值
    API_KEY = ""
    API_SECRET = ""
    PASSPHRASE = ""

# 交易配置
SYMBOL = "BTC-USDT"           # 交易对
INTERVAL = "1H"               # 分析周期: 1m, 5m, 15m, 1H, 4H, 1D
POSITION_PCT = 0.30           # 仓位比例 30%
MIN_SIGNAL_STRENGTH = 70      # 最小信号强度
IS_DEMO = False               # True=模拟盘, False=实盘

# 优化参数 (根据之前优化结果)
MA_PERIOD = 21                # MA周期
CENTER_HEIGHT = 0.01          # 中枢高度 1%
# =================================

class MoerTradingBot:
    """全自动交易机器人"""
    
    def __init__(self):
        self.data_mgr = RealtimeDataManager("okx")
        self.moer = MoerChanlun(
            ma_period=MA_PERIOD,
            center_min_height=CENTER_HEIGHT
        )
        
        # 检查API配置
        if not API_KEY or not API_SECRET:
            print("❌ 错误: 请先配置API Key!")
            print("   编辑本文件，填写API_KEY, API_SECRET, PASSPHRASE")
            sys.exit(1)
        
        self.trader = AutoTrader(API_KEY, API_SECRET, PASSPHRASE, IS_DEMO)
        
        # 状态记录
        self.last_signal_type = None
        self.today_trades = 0
        self.daily_report = []
        
    def print_banner(self):
        """打印启动信息"""
        mode = "🟡 模拟盘" if IS_DEMO else "🔴 实盘"
        print("\n" + "="*70)
        print(f"🚀 摩尔缠论全自动交易系统")
        print("="*70)
        print(f"交易模式: {mode}")
        print(f"交易对: {SYMBOL}")
        print(f"分析周期: {INTERVAL}")
        print(f"仓位比例: {POSITION_PCT*100}%")
        print(f"信号阈值: ≥{MIN_SIGNAL_STRENGTH}")
        print(f"MA周期: {MA_PERIOD}")
        print(f"中枢高度: {CENTER_HEIGHT*100}%")
        print("="*70)
        print("\n⏹️  按 Ctrl+C 停止\n")
        
    def analyze(self) -> Optional[Dict]:
        """分析市场"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        print(f"\n{'='*70}")
        print(f"📡 {now} - 市场分析")
        print(f"{'='*70}")
        
        # 获取数据
        highs, lows, closes, volumes = self.data_mgr.fetch_data(
            SYMBOL, INTERVAL, limit=150
        )
        
        if not closes:
            print("❌ 数据获取失败")
            return None
        
        current_price = closes[-1]
        
        # 打印行情
        print(f"\n💰 当前行情")
        print(f"   价格: ${current_price:,.2f}")
        if len(closes) >= 24:
            change_24h = (closes[-1] / closes[-24] - 1) * 100
            print(f"   24h涨跌: {change_24h:+.2f}%")
        
        # 技术分析
        centers = self.moer.identify_centers(highs, lows, closes)
        signals = self.moer.scan(highs, lows, closes, volumes)
        
        print(f"\n🔍 技术分析")
        print(f"   中枢数量: {len(centers)}")
        if centers:
            latest = centers[-1]
            print(f"   最新中枢: ZG=${latest['zg']:,.0f} ZD=${latest['zd']:,.0f}")
            
            # 位置判断
            if current_price > latest['zg']:
                print(f"   当前位置: 📈 中枢上方 (强势)")
            elif current_price < latest['zd']:
                print(f"   当前位置: 📉 中枢下方 (弱势)")
            else:
                print(f"   当前位置: 🔄 中枢区间内")
        
        # 信号分析
        strong_signals = [s for s in signals if s.strength >= MIN_SIGNAL_STRENGTH]
        
        print(f"\n🔔 信号统计")
        print(f"   总信号: {len(signals)}")
        print(f"   强信号: {len(strong_signals)}")
        
        if not strong_signals:
            print("\n   ⏳ 暂无强信号，继续观察...")
            return None
        
        # 最佳信号
        best = max(strong_signals, key=lambda x: x.strength)
        
        print(f"\n🎯 最佳信号: {best.signal_type.value}")
        print(f"   强度: {best.strength}/100")
        print(f"   价格: ${best.price:,.2f}")
        print(f"   止损: ${best.stop_loss:,.2f}")
        print(f"   止盈: ${best.take_profit:,.2f}")
        print(f"   原因: {best.reason}")
        
        return {
            "signal": best,
            "price": current_price,
            "centers": len(centers)
        }
    
    def execute(self, analysis: Dict):
        """执行交易"""
        signal = analysis["signal"]
        
        # 检查是否重复信号
        signal_key = f"{signal.signal_type.value}_{signal.index}"
        if signal_key == self.last_signal_type:
            print("\n⏭️  信号已处理，跳过")
            return
        
        print(f"\n🚀 执行交易")
        print(f"{'='*70}")
        
        # 执行下单
        result = self.trader.execute_signal(
            signal=signal,
            symbol=SYMBOL,
            position_pct=POSITION_PCT
        )
        
        if result:
            self.last_signal_type = signal_key
            self.today_trades += 1
            
            # 记录交易
            trade_record = {
                "time": datetime.now().isoformat(),
                "symbol": SYMBOL,
                "signal": signal.signal_type.value,
                "price": signal.price,
                "strength": signal.strength,
                "status": "executed"
            }
            self.daily_report.append(trade_record)
            
            # 保存记录
            self.save_trade_history(trade_record)
            
            print(f"\n✅ 交易已记录")
            print(f"   今日交易次数: {self.today_trades}")
    
    def save_trade_history(self, record: Dict):
        """保存交易记录"""
        try:
            filename = f"/root/.openclaw/workspace/moer-chanlun/trade_history.json"
            
            # 读取历史
            try:
                with open(filename, 'r') as f:
                    history = json.load(f)
            except:
                history = []
            
            # 添加新记录
            history.append(record)
            
            # 保存
            with open(filename, 'w') as f:
                json.dump(history, f, indent=2)
                
        except Exception as e:
            print(f"⚠️  保存记录失败: {e}")
    
    def run(self):
        """主循环"""
        self.print_banner()
        
        while True:
            try:
                # 分析市场
                analysis = self.analyze()
                
                # 执行交易
                if analysis:
                    self.execute(analysis)
                
                # 等待下次检查
                print(f"\n⏰ 下次检查: 5分钟后...")
                print(f"{'='*70}")
                time.sleep(300)  # 5分钟
                
            except KeyboardInterrupt:
                print("\n\n👋 系统已停止")
                print(f"📊 今日交易: {self.today_trades} 笔")
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                time.sleep(60)


def quick_test():
    """快速测试模式"""
    print("="*70)
    print("🧪 快速测试模式")
    print("="*70)
    
    data_mgr = RealtimeDataManager("okx")
    moer = MoerChanlun(ma_period=21, center_min_height=0.01)
    
    # 测试多个币种
    symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
    
    for symbol in symbols:
        print(f"\n{'='*70}")
        print(f"🔍 {symbol}")
        print(f"{'='*70}")
        
        highs, lows, closes, volumes = data_mgr.fetch_data(symbol, "1H", 150)
        
        if not closes:
            continue
        
        signals = moer.scan(highs, lows, closes, volumes)
        strong = [s for s in signals if s.strength >= 70]
        
        print(f"价格: ${closes[-1]:,.2f}")
        print(f"信号: {len(strong)} 个强信号")
        
        for s in strong[:3]:
            emoji = "🟢" if "买" in s.signal_type.value else "🔴"
            print(f"   {emoji} {s.signal_type.value} @ ${s.price:,.0f} (强度{s.strength})")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='摩尔缠论全自动交易')
    parser.add_argument('--test', action='store_true', help='快速测试模式')
    parser.add_argument('--demo', action='store_true', help='强制模拟盘')
    
    args = parser.parse_args()
    
    if args.test:
        quick_test()
    else:
        # 强制模拟盘安全模式
        if not API_KEY:
            print("⚠️  未配置API Key，进入快速测试模式")
            print("   运行: python3 full_trading_bot.py --test\n")
            quick_test()
        else:
            bot = MoerTradingBot()
            bot.run()
