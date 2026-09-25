#!/usr/bin/env python3
"""
摩尔缠论 - 金字塔加仓交易系统 (MA55趋势 + 多时间周期)
规则:
1. MA55上方只做多，下方只做空
2. 一买+5分钟底背离 → 建仓30%
3. 二买+30分钟底背离 → 加仓30%
4. 三买 → 满仓(100%)
5. 每个币种最大25%保证金
"""

import sys
import time
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, '/root/.openclaw/workspace/moer-chanlun')

from config import OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
from moer_quant_pure import MoerChanlun, SignalType
from realtime_data import RealtimeDataManager
from auto_trading import OKXTrader, OrderSide, OrderType

# ========== 交易配置 ==========
SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]
MA_PERIOD_TREND = 55          # 趋势判断MA
MAX_MARGIN_PER_SYMBOL = 0.25  # 单个币种最大25%保证金

# 加仓比例
POSITION_FIRST = 0.30         # 一买30%
POSITION_SECOND = 0.30        # 二买30%
POSITION_THIRD = 0.40         # 三买40% (满仓)

# 时间周期
TIMEFRAME_TREND = "1H"        # 趋势判断
TIMEFRAME_ENTRY = "5m"        # 入场信号
TIMEFRAME_ADD = "30m"         # 加仓确认

CHECK_INTERVAL = 60           # 1分钟检查
# =============================

class PyramidTrader:
    """金字塔加仓交易器"""
    
    def __init__(self):
        self.data_mgr = RealtimeDataManager("okx")
        self.trader = OKXTrader(OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE, is_demo=False)
        self.positions = {}  # 持仓状态
        
    def get_ma(self, closes: List[float], period: int) -> float:
        """计算MA"""
        if len(closes) < period:
            return sum(closes) / len(closes)
        return sum(closes[-period:]) / period
    
    def get_trend(self, symbol: str) -> str:
        """
        判断趋势方向
        Returns: "UP" | "DOWN" | "NONE"
        """
        highs, lows, closes, _ = self.data_mgr.fetch_data(symbol, TIMEFRAME_TREND, 100)
        if not closes:
            return "NONE"
        
        ma55 = self.get_ma(closes, MA_PERIOD_TREND)
        current_price = closes[-1]
        
        # MA55上方 = 多头趋势，只做多
        if current_price > ma55 * 1.01:
            return "UP"
        # MA55下方 = 空头趋势，只做空
        elif current_price < ma55 * 0.99:
            return "DOWN"
        else:
            return "NONE"
    
    def check_divergence(self, symbol: str, timeframe: str, direction: str) -> bool:
        """
        检查背离
        direction: "bullish" (底背离) | "bearish" (顶背离)
        """
        highs, lows, closes, volumes = self.data_mgr.fetch_data(symbol, timeframe, 100)
        if not closes:
            return False
        
        moer = MoerChanlun()
        macd = moer.calculate_macd(closes)
        
        if direction == "bullish":
            return moer.detect_bullish_divergence(lows, macd['histogram'], 20)
        else:
            return moer.detect_bearish_divergence(highs, macd['histogram'], 20)
    
    def get_signal(self, symbol: str, timeframe: str) -> Optional[Dict]:
        """获取缠论信号"""
        highs, lows, closes, volumes = self.data_mgr.fetch_data(symbol, timeframe, 100)
        if not closes:
            return None
        
        moer = MoerChanlun(ma_period=21, center_min_height=0.01)
        signals = moer.scan(highs, lows, closes, volumes)
        
        if not signals:
            return None
        
        # 返回最强信号
        best = max(signals, key=lambda x: x.strength)
        return {
            "type": best.signal_type.value,
            "price": best.price,
            "strength": best.strength,
            "stop_loss": best.stop_loss,
            "take_profit": best.take_profit
        }
    
    def calculate_position_size(self, symbol: str, position_pct: float) -> Tuple[float, float]:
        """
        计算仓位大小
        Returns: (数量, 金额)
        """
        # 获取账户余额
        balance_result = self.trader.get_balance()
        if balance_result.get('code') != '0':
            return 0, 0
        
        usdt_balance = 0
        for detail in balance_result.get('data', [{}])[0].get('details', []):
            if detail.get('ccy') == 'USDT':
                usdt_balance = float(detail.get('availBal', 0))
                break
        
        # 计算该币种已用保证金
        used_margin = self.positions.get(symbol, {}).get("used_margin", 0)
        
        # 可用保证金 (最大25% - 已用)
        max_margin = usdt_balance * MAX_MARGIN_PER_SYMBOL
        available_margin = max_margin - used_margin
        
        if available_margin <= 0:
            return 0, 0
        
        # 本次开仓金额
        trade_amount = min(available_margin, usdt_balance * position_pct)
        
        # 获取当前价格
        _, _, closes, _ = self.data_mgr.fetch_data(symbol, "1m", 10)
        if not closes:
            return 0, 0
        
        current_price = closes[-1]
        size = trade_amount / current_price
        
        return size, trade_amount
    
    def open_position(self, symbol: str, side: str, signal: Dict, reason: str):
        """开仓"""
        print(f"\n{'='*70}")
        print(f"🚀 开仓 - {symbol} {side.upper()}")
        print(f"原因: {reason}")
        print(f"{'='*70}")
        
        # 确定加仓比例
        if "一买" in signal['type'] or "一卖" in signal['type']:
            position_pct = POSITION_FIRST
        elif "二买" in signal['type'] or "二卖" in signal['type']:
            position_pct = POSITION_SECOND
        elif "三买" in signal['type'] or "三卖" in signal['type']:
            position_pct = POSITION_THIRD
        else:
            return False
        
        # 计算仓位
        size, amount = self.calculate_position_size(symbol, position_pct)
        
        if size <= 0:
            print(f"   ❌ 保证金不足或已达上限")
            return False
        
        print(f"   保证金使用: {amount:.2f} USDT ({position_pct*100}%)")
        print(f"   开仓数量: {size:.6f}")
        print(f"   开仓价格: ${signal['price']:,.2f}")
        
        # 执行下单
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        order = self.trader.place_order(
            symbol=symbol,
            side=order_side,
            size=size,
            price=signal['price'],
            order_type=OrderType.LIMIT
        )
        
        if order:
            print(f"   ✅ 开仓成功! 订单: {order.order_id}")
            
            # 更新持仓
            if symbol not in self.positions:
                self.positions[symbol] = {
                    "side": side,
                    "entry_price": signal['price'],
                    "total_size": 0,
                    "used_margin": 0,
                    "orders": []
                }
            
            self.positions[symbol]["total_size"] += size
            self.positions[symbol]["used_margin"] += amount
            self.positions[symbol]["orders"].append({
                "time": datetime.now().isoformat(),
                "type": signal['type'],
                "size": size,
                "amount": amount,
                "price": signal['price'],
                "order_id": order.order_id
            })
            
            return True
        else:
            print(f"   ❌ 开仓失败")
            return False
    
    def scan_symbol(self, symbol: str):
        """扫描单个币种"""
        print(f"\n{'='*70}")
        print(f"🔍 {symbol} - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*70}")
        
        # 1. 判断趋势 (MA55)
        trend = self.get_trend(symbol)
        print(f"   趋势: {trend} (MA55)")
        
        if trend == "NONE":
            print(f"   ⏭️ 趋势不明，跳过")
            return
        
        # 2. 获取当前持仓
        position = self.positions.get(symbol, {})
        current_side = position.get("side")
        
        # 3. 做多逻辑
        if trend == "UP":
            print(f"   📈 多头趋势，寻找买点")
            
            # 检查是否已有空单 (需要平仓)
            if current_side == "sell":
                print(f"   ⚠️  持有空单，趋势反转应平仓")
                return
            
            # 一买 + 5分钟底背离 → 建仓30%
            if not position or position.get("total_size", 0) == 0:
                signal_5m = self.get_signal(symbol, TIMEFRAME_ENTRY)
                if signal_5m and "一买" in signal_5m['type']:
                    has_divergence = self.check_divergence(symbol, TIMEFRAME_ENTRY, "bullish")
                    if has_divergence:
                        print(f"   ✅ 一买 + 5分钟底背离")
                        self.open_position(symbol, "buy", signal_5m, "一买+5m底背离")
                    else:
                        print(f"   ⏭️ 有一买但无底背离")
                else:
                    print(f"   ⏭️ 无一买信号")
            
            # 二买 + 30分钟底背离 → 加仓30%
            elif position.get("used_margin", 0) < MAX_MARGIN_PER_SYMBOL * 0.6:
                signal_30m = self.get_signal(symbol, TIMEFRAME_ADD)
                if signal_30m and "二买" in signal_30m['type']:
                    has_divergence = self.check_divergence(symbol, TIMEFRAME_ADD, "bullish")
                    if has_divergence:
                        print(f"   ✅ 二买 + 30分钟底背离")
                        self.open_position(symbol, "buy", signal_30m, "二买+30m底背离")
                    else:
                        print(f"   ⏭️ 有二买但无底背离")
            
            # 三买 → 满仓
            elif position.get("used_margin", 0) < MAX_MARGIN_PER_SYMBOL * 0.9:
                signal_5m = self.get_signal(symbol, TIMEFRAME_ENTRY)
                if signal_5m and "三买" in signal_5m['type']:
                    print(f"   ✅ 三买信号")
                    self.open_position(symbol, "buy", signal_5m, "三买满仓")
            
            else:
                print(f"   ⏭️ 已达最大仓位")
        
        # 4. 做空逻辑 (镜像)
        elif trend == "DOWN":
            print(f"   📉 空头趋势，寻找卖点")
            
            # 检查是否已有多单
            if current_side == "buy":
                print(f"   ⚠️  持有多单，趋势反转应平仓")
                return
            
            # 一卖 + 5分钟顶背离 → 建仓30%
            if not position or position.get("total_size", 0) == 0:
                signal_5m = self.get_signal(symbol, TIMEFRAME_ENTRY)
                if signal_5m and "一卖" in signal_5m['type']:
                    has_divergence = self.check_divergence(symbol, TIMEFRAME_ENTRY, "bearish")
                    if has_divergence:
                        print(f"   ✅ 一卖 + 5分钟顶背离")
                        self.open_position(symbol, "sell", signal_5m, "一卖+5m顶背离")
                    else:
                        print(f"   ⏭️ 有一卖但无顶背离")
            
            # 二卖 + 30分钟顶背离 → 加仓30%
            elif position.get("used_margin", 0) < MAX_MARGIN_PER_SYMBOL * 0.6:
                signal_30m = self.get_signal(symbol, TIMEFRAME_ADD)
                if signal_30m and "二卖" in signal_30m['type']:
                    has_divergence = self.check_divergence(symbol, TIMEFRAME_ADD, "bearish")
                    if has_divergence:
                        print(f"   ✅ 二卖 + 30分钟顶背离")
                        self.open_position(symbol, "sell", signal_30m, "二卖+30m顶背离")
            
            # 三卖 → 满仓
            elif position.get("used_margin", 0) < MAX_MARGIN_PER_SYMBOL * 0.9:
                signal_5m = self.get_signal(symbol, TIMEFRAME_ENTRY)
                if signal_5m and "三卖" in signal_5m['type']:
                    print(f"   ✅ 三卖信号")
                    self.open_position(symbol, "sell", signal_5m, "三卖满仓")
            
            else:
                print(f"   ⏭️ 已达最大仓位")
    
    def print_status(self):
        """打印持仓状态"""
        print(f"\n{'='*70}")
        print("📊 当前持仓")
        print(f"{'='*70}")
        
        if not self.positions:
            print("   无持仓")
            return
        
        for symbol, pos in self.positions.items():
            print(f"\n   {symbol}:")
            print(f"      方向: {pos['side']}")
            print(f"      总数量: {pos['total_size']:.6f}")
            print(f"      已用保证金: {pos['used_margin']:.2f} USDT")
            print(f"      开仓次数: {len(pos['orders'])}")
    
    def run(self):
        """主循环"""
        print("\n" + "="*70)
        print("🚀 金字塔加仓交易系统")
        print("="*70)
        print("规则:")
        print("  1. MA55趋势过滤 (上方做多/下方做空)")
        print("  2. 一买+5m底背离 → 30%")
        print("  3. 二买+30m底背离 → 30%")
        print("  4. 三买 → 40% (满仓)")
        print("  5. 单币种最大25%保证金")
        print("="*70)
        print(f"\n监控: {', '.join(SYMBOLS)}")
        print("\n⏹️  按 Ctrl+C 停止\n")
        
        while True:
            try:
                for symbol in SYMBOLS:
                    self.scan_symbol(symbol)
                
                self.print_status()
                
                print(f"\n⏰ 下次扫描: {CHECK_INTERVAL}秒后...")
                print(f"{'='*70}")
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n\n👋 交易已停止")
                self.print_status()
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                time.sleep(10)


if __name__ == "__main__":
    trader = PyramidTrader()
    trader.run()
