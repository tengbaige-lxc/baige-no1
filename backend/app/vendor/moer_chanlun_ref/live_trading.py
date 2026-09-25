"""
摩尔缠论 - 实盘测试系统
实时获取数据，分析信号，输出交易建议
"""

import sys
import time
from datetime import datetime
from typing import List, Dict, Optional

# 导入自定义模块
from moer_quant_pure import MoerChanlun, SignalType
from realtime_data import RealtimeDataManager, OKXAPI
from alert_manager import AlertManager

class LiveTradingSystem:
    """实盘交易系统"""
    
    def __init__(self, symbol: str = "BTC-USDT", interval: str = "1H"):
        self.symbol = symbol
        self.interval = interval
        self.data_mgr = RealtimeDataManager("okx")
        self.moer = MoerChanlun(ma_period=34, center_min_height=0.002)
        self.alert_mgr = AlertManager()
        
        # 记录已报警的信号，避免重复
        self.alerted_signals = set()
        
    def fetch_and_analyze(self) -> Dict:
        """获取数据并分析"""
        print(f"\n{'='*70}")
        print(f"📡 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 获取 {self.symbol} 数据")
        print(f"{'='*70}")
        
        # 获取K线数据
        highs, lows, closes, volumes = self.data_mgr.fetch_data(
            self.symbol, self.interval, limit=150
        )
        
        if not closes:
            print("❌ 数据获取失败")
            return {"error": "数据获取失败"}
        
        # 当前价格
        current_price = closes[-1]
        price_change_24h = (closes[-1] / closes[-24] - 1) * 100 if len(closes) >= 24 else 0
        
        print(f"\n💰 当前行情")
        print(f"   价格: ${current_price:,.2f}")
        print(f"   24h涨跌: {price_change_24h:+.2f}%")
        print(f"   24h最高: ${max(closes[-24:]):,.2f}")
        print(f"   24h最低: ${min(closes[-24:]):,.2f}")
        
        # 摩尔缠论分析
        print(f"\n🔍 技术分析")
        
        # 识别中枢
        centers = self.moer.identify_centers(highs, lows, closes)
        print(f"   中枢数量: {len(centers)}")
        
        if centers:
            latest = centers[-1]
            print(f"   最新中枢: ZG=${latest['zg']:,.0f} ZD=${latest['zd']:,.0f}")
            
            # 判断当前位置
            if current_price > latest['zg']:
                position = "📈 中枢上方 (强势)"
            elif current_price < latest['zd']:
                position = "📉 中枢下方 (弱势)"
            else:
                position = "🔄 中枢区间内 (震荡)"
            print(f"   当前位置: {position}")
        
        # 扫描信号
        signals = self.moer.scan(highs, lows, closes, volumes)
        
        # 过滤强信号
        strong_signals = [s for s in signals if s.strength >= 60]
        
        print(f"\n🔔 交易信号")
        print(f"   总信号: {len(signals)} 个")
        print(f"   强信号(≥60): {len(strong_signals)} 个")
        
        result = {
            "symbol": self.symbol,
            "price": current_price,
            "change_24h": price_change_24h,
            "centers": len(centers),
            "signals": signals,
            "strong_signals": strong_signals,
            "timestamp": datetime.now()
        }
        
        # 显示信号详情
        if strong_signals:
            print(f"\n🎯 强信号详情:")
            for i, sig in enumerate(strong_signals[:5], 1):
                emoji = "🟢" if "买" in sig.signal_type.value else "🔴"
                print(f"\n   {emoji} {sig.signal_type.value}")
                print(f"      价格: ${sig.price:,.2f}")
                print(f"      强度: {sig.strength}/100")
                print(f"      止损: ${sig.stop_loss:,.2f} ({(sig.stop_loss/sig.price-1)*100:+.1f}%)")
                print(f"      止盈: ${sig.take_profit:,.2f} ({(sig.take_profit/sig.price-1)*100:+.1f}%)")
                print(f"      原因: {sig.reason}")
                
                # 检查是否已报警
                signal_id = f"{self.symbol}_{sig.signal_type.value}_{sig.index}"
                if signal_id not in self.alerted_signals:
                    self.alerted_signals.add(signal_id)
                    result["new_signal"] = sig
        else:
            print("\n   ⏳ 暂无强信号，继续观察...")
        
        # 交易建议
        print(f"\n💡 交易建议")
        if strong_signals:
            best = max(strong_signals, key=lambda s: s.strength)
            print(f"   推荐: {best.signal_type.value}")
            print(f"   入场: ${best.price:,.2f}")
            print(f"   止损: ${best.stop_loss:,.2f}")
            print(f"   止盈: ${best.take_profit:,.2f}")
            print(f"   仓位: 建议5-10% (测试阶段)")
        else:
            print("   建议: 观望，等待明确信号")
        
        return result
    
    def run_once(self):
        """运行一次分析"""
        try:
            result = self.fetch_and_analyze()
            return result
        except Exception as e:
            print(f"❌ 错误: {e}")
            import traceback
            traceback.print_exc()
            return {"error": str(e)}
    
    def run_loop(self, interval_minutes: int = 60):
        """循环运行"""
        print(f"\n{'='*70}")
        print(f"🚀 摩尔缠论实盘测试 - 循环模式")
        print(f"   交易对: {self.symbol}")
        print(f"   周期: {self.interval}")
        print(f"   检查间隔: {interval_minutes} 分钟")
        print(f"{'='*70}")
        print(f"\n按 Ctrl+C 停止\n")
        
        while True:
            try:
                self.run_once()
                
                # 等待下次检查
                next_check = datetime.now().timestamp() + interval_minutes * 60
                print(f"\n⏰ 下次检查: {datetime.fromtimestamp(next_check).strftime('%H:%M:%S')}")
                print(f"{'='*70}")
                
                time.sleep(interval_minutes * 60)
                
            except KeyboardInterrupt:
                print("\n\n👋 已停止")
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                time.sleep(60)  # 出错后1分钟重试


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='摩尔缠论实盘测试')
    parser.add_argument('--symbol', default='BTC-USDT', help='交易对 (默认: BTC-USDT)')
    parser.add_argument('--interval', default='1H', help='时间周期 (默认: 1H)')
    parser.add_argument('--loop', action='store_true', help='循环模式')
    parser.add_argument('--interval-minutes', type=int, default=60, help='检查间隔分钟')
    
    args = parser.parse_args()
    
    # 创建系统
    system = LiveTradingSystem(args.symbol, args.interval)
    
    if args.loop:
        # 循环模式
        system.run_loop(args.interval_minutes)
    else:
        # 单次模式
        system.run_once()


if __name__ == "__main__":
    # 如果没有参数，默认运行一次
    if len(sys.argv) == 1:
        print("="*70)
        print("🚀 摩尔缠论 - 实盘测试")
        print("="*70)
        print("\n使用说明:")
        print("  python3 live_trading.py              # 单次分析")
        print("  python3 live_trading.py --loop       # 循环监控")
        print("  python3 live_trading.py --symbol ETH-USDT  # 分析ETH")
        print("\n")
        
        # 默认运行一次
        system = LiveTradingSystem("BTC-USDT", "1H")
        system.run_once()
    else:
        main()
