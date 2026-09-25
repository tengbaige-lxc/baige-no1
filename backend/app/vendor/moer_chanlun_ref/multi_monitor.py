#!/usr/bin/env python3
"""
摩尔缠论 - 多币种自动监控系统
同时监控多个币种，发现强信号时提醒
"""

import sys
import time
import json
from datetime import datetime
from typing import List, Dict, Tuple

sys.path.insert(0, '/root/.openclaw/workspace/moer-chanlun')

from config import OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
from moer_quant_pure import MoerChanlun, SignalType
from realtime_data import RealtimeDataManager
from auto_trading import OKXTrader

# ========== 监控配置 ==========
# 监控的币种列表
MONITOR_SYMBOLS = [
    "BTC-USDT",   # 比特币
    "ETH-USDT",   # 以太坊
    "DOGE-USDT",  # 狗狗币
    "XRP-USDT",   # 瑞波币
    "TRX-USDT",   # 波场
    "HYPE-USDT",  # Hyperliquid
    "SPACE-USDT", # Space ID
]

INTERVAL = "1H"               # 分析周期
MIN_STRENGTH = 70             # 最小信号强度
CHECK_INTERVAL = 300          # 检查间隔(秒) = 5分钟
# =============================

class MultiSymbolMonitor:
    """多币种监控器"""
    
    def __init__(self):
        self.data_mgr = RealtimeDataManager("okx")
        self.moer = MoerChanlun(ma_period=21, center_min_height=0.01)
        self.trader = OKXTrader(OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE, is_demo=False)
        self.alerted_signals = set()  # 已报警的信号，避免重复
        
    def print_banner(self):
        """打印启动信息"""
        print("\n" + "="*70)
        print("🚀 摩尔缠论 - 多币种自动监控系统")
        print("="*70)
        print(f"监控币种: {len(MONITOR_SYMBOLS)} 个")
        print(f"分析周期: {INTERVAL}")
        print(f"信号阈值: ≥{MIN_STRENGTH}")
        print(f"检查间隔: {CHECK_INTERVAL//60} 分钟")
        print("="*70)
        print(f"\n监控列表: {', '.join(MONITOR_SYMBOLS)}")
        print("\n⏹️  按 Ctrl+C 停止\n")
        
    def scan_symbol(self, symbol: str) -> Tuple[float, List]:
        """扫描单个币种"""
        try:
            highs, lows, closes, volumes = self.data_mgr.fetch_data(
                symbol, INTERVAL, limit=100
            )
            
            if not closes:
                return 0, []
            
            current_price = closes[-1]
            signals = self.moer.scan(highs, lows, closes, volumes)
            strong_signals = [s for s in signals if s.strength >= MIN_STRENGTH]
            
            return current_price, strong_signals
            
        except Exception as e:
            print(f"   ❌ {symbol} 扫描失败: {e}")
            return 0, []
    
    def format_signal(self, symbol: str, price: float, signals: List) -> str:
        """格式化信号输出"""
        if not signals:
            return f"{symbol:12s} ${price:>10,.2f} | ⏳ 观望"
        
        best = max(signals, key=lambda x: x.strength)
        emoji = "🟢" if "买" in best.signal_type.value else "🔴"
        return f"{symbol:12s} ${price:>10,.2f} | {emoji} {best.signal_type.value} (强度{best.strength})"
    
    def check_alerts(self, results: Dict):
        """检查并报警新信号"""
        alerts = []
        
        for symbol, (price, signals) in results.items():
            for sig in signals:
                # 生成唯一标识
                signal_id = f"{symbol}_{sig.signal_type.value}_{sig.index}"
                
                if signal_id not in self.alerted_signals:
                    self.alerted_signals.add(signal_id)
                    alerts.append({
                        "time": datetime.now().isoformat(),
                        "symbol": symbol,
                        "price": price,
                        "signal": sig.signal_type.value,
                        "strength": sig.strength,
                        "stop_loss": sig.stop_loss,
                        "take_profit": sig.take_profit
                    })
        
        return alerts
    
    def print_results(self, results: Dict):
        """打印扫描结果"""
        print("\n" + "="*70)
        print(f"📊 {datetime.now().strftime('%H:%M:%S')} - 扫描结果")
        print("="*70)
        
        has_signal = False
        for symbol in MONITOR_SYMBOLS:
            price, signals = results.get(symbol, (0, []))
            if price > 0:
                line = self.format_signal(symbol, price, signals)
                print(line)
                if signals:
                    has_signal = True
        
        if has_signal:
            print("\n" + "🚨"*35)
            print("⚠️  发现交易信号！请查看上方详情")
            print("🚨"*35)
        else:
            print("\n✅ 所有币种正常，暂无强信号")
    
    def save_alerts(self, alerts: List):
        """保存报警记录"""
        if not alerts:
            return
        
        try:
            filename = f"/root/.openclaw/workspace/moer-chanlun/alerts_{datetime.now().strftime('%Y%m%d')}.json"
            
            # 读取已有记录
            try:
                with open(filename, 'r') as f:
                    history = json.load(f)
            except:
                history = []
            
            # 添加新记录
            history.extend(alerts)
            
            # 保存
            with open(filename, 'w') as f:
                json.dump(history, f, indent=2)
                
        except Exception as e:
            print(f"⚠️  保存报警记录失败: {e}")
    
    def run_once(self):
        """运行一次扫描"""
        results = {}
        
        print(f"\n{'='*70}")
        print(f"🔍 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 开始扫描 {len(MONITOR_SYMBOLS)} 个币种")
        print(f"{'='*70}")
        
        for symbol in MONITOR_SYMBOLS:
            print(f"  扫描 {symbol}...", end=" ", flush=True)
            price, signals = self.scan_symbol(symbol)
            results[symbol] = (price, signals)
            
            if signals:
                best = max(signals, key=lambda x: x.strength)
                emoji = "🟢" if "买" in best.signal_type.value else "🔴"
                print(f"{emoji} {best.signal_type.value} (强度{best.strength})")
            else:
                print("⏳")
        
        # 打印汇总
        self.print_results(results)
        
        # 检查新信号
        alerts = self.check_alerts(results)
        if alerts:
            print("\n🔔 新信号报警:")
            for alert in alerts:
                print(f"   {alert['symbol']}: {alert['signal']} @ ${alert['price']:,.2f}")
            self.save_alerts(alerts)
        
        return results
    
    def run_loop(self):
        """循环运行"""
        self.print_banner()
        
        while True:
            try:
                self.run_once()
                
                # 等待下次检查
                next_time = datetime.now().timestamp() + CHECK_INTERVAL
                print(f"\n⏰ 下次扫描: {datetime.fromtimestamp(next_time).strftime('%H:%M:%S')}")
                print(f"{'='*70}")
                
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n\n👋 监控已停止")
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                time.sleep(60)


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='多币种监控')
    parser.add_argument('--once', action='store_true', help='只运行一次')
    args = parser.parse_args()
    
    monitor = MultiSymbolMonitor()
    
    if args.once:
        monitor.run_once()
    else:
        monitor.run_loop()


if __name__ == "__main__":
    main()
