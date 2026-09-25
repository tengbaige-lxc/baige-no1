#!/usr/bin/env python3
"""
摩尔缠论 - 自动开仓交易系统
发现信号立即执行交易
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
from auto_trading import OKXTrader, OrderSide, OrderType

# ========== 交易配置 ==========
MONITOR_SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "DOGE-USDT", "XRP-USDT",
    "TRX-USDT", "HYPE-USDT", "SPACE-USDT"
]
INTERVAL = "1H"
MIN_STRENGTH = 70
CHECK_INTERVAL = 300  # 5分钟
POSITION_PCT = 0.30   # 30%仓位
# =============================

class AutoOpenTrader:
    """自动开仓交易器"""
    
    def __init__(self):
        self.data_mgr = RealtimeDataManager("okx")
        self.moer = MoerChanlun(ma_period=21, center_min_height=0.01)
        self.trader = OKXTrader(OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE, is_demo=False)
        self.executed_signals = set()  # 已执行的信号
        
    def print_banner(self):
        print("\n" + "="*70)
        print("🚀 摩尔缠论 - 自动开仓交易系统")
        print("="*70)
        print(f"监控币种: {len(MONITOR_SYMBOLS)} 个")
        print(f"信号阈值: ≥{MIN_STRENGTH}")
        print(f"仓位比例: {POSITION_PCT*100}%")
        print(f"检查间隔: {CHECK_INTERVAL//60} 分钟")
        print("="*70)
        print("\n⚠️  自动开仓模式已启用")
        print("   发现信号将立即执行交易！")
        print("\n⏹️  按 Ctrl+C 停止\n")
        
    def get_balance(self) -> float:
        """获取USDT余额"""
        result = self.trader.get_balance()
        if result.get('code') == '0':
            for detail in result.get('data', [{}])[0].get('details', []):
                if detail.get('ccy') == 'USDT':
                    return float(detail.get('availBal', 0))
        return 0
    
    def execute_trade(self, symbol: str, signal) -> bool:
        """执行交易"""
        print(f"\n{'='*70}")
        print(f"🚀 执行自动交易 - {symbol}")
        print(f"{'='*70}")
        
        # 检查余额
        balance = self.get_balance()
        if balance < 10:
            print(f"❌ USDT余额不足: {balance:.2f} (最少需要10 USDT)")
            return False
        
        # 计算下单金额 - 至少10 USDT
        trade_amount = max(balance * POSITION_PCT, 10)
        
        # 确定方向
        if '买' in signal.signal_type.value:
            side = OrderSide.BUY
            # 检查是否有足够资金
            if balance < trade_amount:
                print(f"❌ 余额不足: {balance:.2f} < {trade_amount:.2f}")
                return False
        else:
            side = OrderSide.SELL
            # 检查是否有该币种余额
            # 获取持仓
            positions = self.trader.get_positions(symbol)
            has_position = False
            pos_size = 0
            if positions.get('code') == '0':
                for pos in positions.get('data', []):
                    if float(pos.get('pos', 0)) > 0:
                        has_position = True
                        pos_size = float(pos.get('pos', 0))
                        break
            
            if not has_position:
                print(f"⚠️  没有{symbol}持仓，无法卖出")
                return False
            
            # 使用持仓数量
            size = pos_size
            print(f"   持仓数量: {size}")
        
        # 买入时计算数量
        if '买' in signal.signal_type.value:
            current_price = signal.price
            size = trade_amount / current_price
        
        # 最小数量检查
        if size <= 0:
            print(f"❌ 下单数量无效: {size}")
            return False
        
        print(f"\n📊 交易信息:")
        print(f"   交易对: {symbol}")
        print(f"   方向: {side.value}")
        print(f"   金额: {trade_amount:.2f} USDT")
        print(f"   数量: {size:.6f}")
        print(f"   价格: ${signal.price:,.2f}")
        print(f"   止损: ${signal.stop_loss:,.2f}")
        print(f"   止盈: ${signal.take_profit:,.2f}")
        
        # 执行下单
        print(f"\n📡 正在下单...")
        order = self.trader.place_order(
            symbol=symbol,
            side=side,
            size=size,
            price=current_price,
            order_type=OrderType.LIMIT
        )
        
        if order:
            print(f"✅ 下单成功!")
            print(f"   订单ID: {order.order_id}")
            print(f"   状态: {order.status}")
            
            # 保存记录
            self.save_trade_record({
                "time": datetime.now().isoformat(),
                "symbol": symbol,
                "signal": signal.signal_type.value,
                "side": side.value,
                "price": current_price,
                "size": size,
                "amount": trade_amount,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "order_id": order.order_id,
                "strength": signal.strength
            })
            return True
        else:
            print(f"❌ 下单失败")
            return False
    
    def save_trade_record(self, record: Dict):
        """保存交易记录"""
        try:
            filename = f"/root/.openclaw/workspace/moer-chanlun/trades_{datetime.now().strftime('%Y%m%d')}.json"
            try:
                with open(filename, 'r') as f:
                    history = json.load(f)
            except:
                history = []
            history.append(record)
            with open(filename, 'w') as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            print(f"⚠️  保存记录失败: {e}")
    
    def scan_and_trade(self):
        """扫描并交易"""
        print(f"\n{'='*70}")
        print(f"🔍 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 扫描交易机会")
        print(f"{'='*70}")
        
        for symbol in MONITOR_SYMBOLS:
            try:
                # 获取数据
                highs, lows, closes, volumes = self.data_mgr.fetch_data(symbol, INTERVAL, 100)
                if not closes:
                    continue
                
                current_price = closes[-1]
                signals = self.moer.scan(highs, lows, closes, volumes)
                strong_signals = [s for s in signals if s.strength >= MIN_STRENGTH]
                
                if strong_signals:
                    best = max(strong_signals, key=lambda x: x.strength)
                    signal_id = f"{symbol}_{best.signal_type.value}_{best.index}"
                    
                    # 检查是否已执行
                    if signal_id in self.executed_signals:
                        print(f"   {symbol}: {best.signal_type.value} 已执行，跳过")
                        continue
                    
                    emoji = "🟢" if "买" in best.signal_type.value else "🔴"
                    print(f"\n   {emoji} {symbol}: 发现{best.signal_type.value}信号!")
                    print(f"      价格: ${current_price:,.2f}")
                    print(f"      强度: {best.strength}")
                    
                    # 执行交易
                    if self.execute_trade(symbol, best):
                        self.executed_signals.add(signal_id)
                else:
                    print(f"   {symbol}: 无信号 (${current_price:,.2f})")
                    
            except Exception as e:
                print(f"   {symbol}: 错误 - {e}")
    
    def run(self):
        """主循环"""
        self.print_banner()
        
        while True:
            try:
                self.scan_and_trade()
                
                next_time = datetime.now().timestamp() + CHECK_INTERVAL
                print(f"\n⏰ 下次扫描: {datetime.fromtimestamp(next_time).strftime('%H:%M:%S')}")
                print(f"{'='*70}")
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n\n👋 自动交易已停止")
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                time.sleep(60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='只运行一次')
    args = parser.parse_args()
    
    trader = AutoOpenTrader()
    
    if args.once:
        trader.scan_and_trade()
    else:
        trader.run()
