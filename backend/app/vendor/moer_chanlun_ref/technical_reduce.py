#!/usr/bin/env python3
"""
摩尔缠论 - 技术信号减仓系统
规则:
1. 5分钟顶背离 → 减仓30%
2. 30分钟顶背离 → 减仓30%
3. MA5由上向下转折 + 黑K确认 → 清仓
"""

import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

sys.path.insert(0, '/root/.openclaw/workspace/moer-chanlun')

from config import OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
from auto_trading import OKXTrader, OrderSide, OrderType
from moer_quant_pure import MoerChanlun
from realtime_data import RealtimeDataManager

# ========== 减仓规则配置 ==========
# 1. 5分钟顶背离减仓
DIVERGENCE_5M_REDUCE = 0.30   # 减仓30%

# 2. 30分钟顶背离减仓
DIVERGENCE_30M_REDUCE = 0.30  # 减仓30%

# 3. MA5转折 + 黑K清仓
MA5_TURN_CLEAR = True         # 清仓

CHECK_INTERVAL = 60           # 1分钟检查
# ==================================

@dataclass
class Position:
    """持仓信息"""
    symbol: str
    side: str                   # buy(多) / sell(空)
    entry_price: float
    size: float
    entry_time: datetime
    order_id: str
    
    # 减仓记录
    reduced_times: int = 0
    total_reduced_pct: float = 0.0
    
    # 背离记录 (避免重复减仓)
    divergence_5m_done: bool = False
    divergence_30m_done: bool = False
    
    # MA5状态
    ma5_prev: float = 0.0
    ma5_direction: str = "flat"  # up, down, flat


class TechnicalReduceManager:
    """技术信号减仓管理器"""
    
    def __init__(self):
        self.trader = OKXTrader(OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE, is_demo=False)
        self.data_mgr = RealtimeDataManager("okx")
        self.moer = MoerChanlun()
        self.positions: Dict[str, Position] = {}
        
    def get_ma(self, closes: List[float], period: int) -> float:
        """计算MA"""
        if len(closes) < period:
            return sum(closes) / len(closes) if closes else 0
        return sum(closes[-period:]) / period
    
    def is_black_candle(self, open_p: float, close_p: float) -> bool:
        """判断是否为黑K (阴线)"""
        return close_p < open_p
    
    def check_divergence(self, symbol: str, timeframe: str, direction: str) -> bool:
        """检查背离信号"""
        highs, lows, closes, volumes = self.data_mgr.fetch_data(symbol, timeframe, 100)
        if not closes:
            return False
        
        macd = self.moer.calculate_macd(closes)
        
        if direction == "bearish":  # 顶背离
            return self.moer.detect_bearish_divergence(highs, macd['histogram'], 20)
        else:  # 底背离
            return self.moer.detect_bullish_divergence(lows, macd['histogram'], 20)
    
    def check_ma5_turn(self, symbol: str) -> Tuple[bool, str]:
        """
        检查MA5转折
        Returns: (是否转折, 方向)
        """
        # 获取5分钟数据
        highs, lows, closes, _ = self.data_mgr.fetch_data(symbol, "5m", 20)
        if len(closes) < 10:
            return False, ""
        
        # 计算MA5
        ma5_current = self.get_ma(closes, 5)
        ma5_prev = self.get_ma(closes[:-1], 5) if len(closes) > 5 else ma5_current
        
        # 当前K线
        current_open = closes[-1] * 0.999  # 简化估算
        current_close = closes[-1]
        
        # 判断MA5方向变化
        prev_direction = "up" if ma5_prev > self.get_ma(closes[:-2], 5) else "down"
        current_direction = "up" if ma5_current > ma5_prev else "down"
        
        # MA5由上向下转折 + 黑K确认
        if prev_direction == "up" and current_direction == "down":
            is_black = self.is_black_candle(current_open, current_close)
            if is_black:
                return True, "down"
        
        return False, current_direction
    
    def reduce_position(self, position: Position, reduce_pct: float, reason: str) -> bool:
        """执行减仓"""
        if reduce_pct <= 0 or position.total_reduced_pct >= 0.99:
            return False
        
        # 计算减仓数量
        current_size = position.size * (1 - position.total_reduced_pct)
        reduce_size = current_size * reduce_pct
        
        # 如果减仓后剩余太少，直接清仓
        if reduce_size < 0.0001:
            reduce_size = current_size
            reduce_pct = 1.0
        
        print(f"\n{'='*70}")
        print(f"📤 减仓 - {position.symbol}")
        print(f"原因: {reason}")
        print(f"{'='*70}")
        print(f"   减仓比例: {reduce_pct*100:.0f}%")
        print(f"   减仓数量: {reduce_size:.6f}")
        print(f"   当前持仓: {current_size:.6f}")
        
        # 确定方向
        if position.side == "buy":
            order_side = OrderSide.SELL
        else:
            order_side = OrderSide.BUY
        
        # 执行减仓
        order = self.trader.place_order(
            symbol=position.symbol,
            side=order_side,
            size=reduce_size,
            order_type=OrderType.MARKET
        )
        
        if order:
            position.total_reduced_pct += reduce_pct * (1 - position.total_reduced_pct)
            position.reduced_times += 1
            
            print(f"   ✅ 减仓成功!")
            print(f"   累计减仓: {position.total_reduced_pct*100:.1f}%")
            
            self.save_record(position, reduce_pct, reason)
            return True
        else:
            print(f"   ❌ 减仓失败")
            return False
    
    def monitor_position(self, position: Position):
        """监控持仓并检查减仓信号"""
        symbol = position.symbol
        
        print(f"\n{'='*70}")
        print(f"🔍 {symbol} - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*70}")
        print(f"   方向: {'做多' if position.side == 'buy' else '做空'}")
        print(f"   入场价: ${position.entry_price:,.2f}")
        print(f"   已减仓: {position.total_reduced_pct*100:.1f}%")
        
        # ===== 减仓逻辑 =====
        reduced = False
        
        # 1. 5分钟顶背离减仓30%
        if not position.divergence_5m_done and not reduced:
            print(f"\n   检查5分钟顶背离...")
            has_divergence = self.check_divergence(symbol, "5m", "bearish")
            
            if has_divergence:
                print(f"   ✅ 检测到5分钟顶背离!")
                if self.reduce_position(position, DIVERGENCE_5M_REDUCE, "5分钟顶背离"):
                    position.divergence_5m_done = True
                    reduced = True
            else:
                print(f"   ⏭️ 无顶背离")
        
        # 2. 30分钟顶背离减仓30%
        if not position.divergence_30m_done and not reduced:
            print(f"\n   检查30分钟顶背离...")
            has_divergence = self.check_divergence(symbol, "30m", "bearish")
            
            if has_divergence:
                print(f"   ✅ 检测到30分钟顶背离!")
                if self.reduce_position(position, DIVERGENCE_30M_REDUCE, "30分钟顶背离"):
                    position.divergence_30m_done = True
                    reduced = True
            else:
                print(f"   ⏭️ 无顶背离")
        
        # 3. MA5转折 + 黑K清仓
        if not reduced and MA5_TURN_CLEAR:
            print(f"\n   检查MA5转折...")
            turn_down, direction = self.check_ma5_turn(symbol)
            
            if turn_down:
                print(f"   ✅ MA5由上向下转折 + 黑K确认!")
                remaining = 1 - position.total_reduced_pct
                if self.reduce_position(position, remaining, "MA5转折+黑K确认"):
                    reduced = True
            else:
                print(f"   ⏭️ MA5方向: {direction}")
        
        if not reduced:
            print(f"\n   ⏳ 无减仓信号，继续持仓")
    
    def add_position(self, symbol: str, side: str, entry_price: float, 
                     size: float, order_id: str):
        """添加新持仓"""
        self.positions[symbol] = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            size=size,
            entry_time=datetime.now(),
            order_id=order_id
        )
        print(f"\n📥 新增持仓: {symbol} {'做多' if side=='buy' else '做空'} {size:.6f}")
    
    def save_record(self, position: Position, reduce_pct: float, reason: str):
        """保存减仓记录"""
        try:
            import json
            filename = f"/root/.openclaw/workspace/moer-chanlun/reduce_records_{datetime.now().strftime('%Y%m%d')}.json"
            
            record = {
                "time": datetime.now().isoformat(),
                "symbol": position.symbol,
                "reason": reason,
                "reduce_pct": reduce_pct,
                "total_reduced": position.total_reduced_pct
            }
            
            try:
                with open(filename, 'r') as f:
                    history = json.load(f)
            except:
                history = []
            
            history.append(record)
            
            with open(filename, 'w') as f:
                json.dump(history, f, indent=2)
        except:
            pass
    
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
            print(f"      方向: {'做多' if pos.side=='buy' else '做空'}")
            print(f"      入场价: ${pos.entry_price:,.2f}")
            print(f"      数量: {pos.size:.6f}")
            print(f"      已减仓: {pos.total_reduced_pct*100:.1f}%")
            print(f"      5分顶背离: {'已减' if pos.divergence_5m_done else '未减'}")
            print(f"      30分顶背离: {'已减' if pos.divergence_30m_done else '未减'}")
    
    def run(self):
        """主循环"""
        print("\n" + "="*70)
        print("📤 技术信号减仓系统")
        print("="*70)
        print("减仓规则:")
        print("  1. 5分钟顶背离 → 减仓30%")
        print("  2. 30分钟顶背离 → 减仓30%")
        print("  3. MA5由上向下转折 + 黑K确认 → 清仓")
        print("="*70)
        print("\n⏹️  按 Ctrl+C 停止\n")
        
        # 演示: 添加测试持仓
        # self.add_position("BTC-USDT", "buy", 67000, 0.001, "test")
        
        while True:
            try:
                if not self.positions:
                    print(f"\n⏳ 暂无持仓，等待新仓位...")
                    print(f"{'='*70}")
                    time.sleep(CHECK_INTERVAL)
                    continue
                
                for symbol in list(self.positions.keys()):
                    pos = self.positions[symbol]
                    self.monitor_position(pos)
                    
                    # 清仓后移除
                    if pos.total_reduced_pct >= 0.99:
                        print(f"\n   ✅ {symbol} 已清仓")
                        del self.positions[symbol]
                
                self.print_status()
                
                print(f"\n⏰ 下次检查: {CHECK_INTERVAL}秒后...")
                print(f"{'='*70}")
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n\n👋 减仓管理已停止")
                self.print_status()
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(10)


if __name__ == "__main__":
    manager = TechnicalReduceManager()
    manager.run()
