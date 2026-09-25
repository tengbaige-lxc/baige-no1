#!/usr/bin/env python3
"""
摩尔缠论 - 减仓管理系统
包含: 分批止盈、移动止损、时间减仓、回撤减仓
"""

import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass

sys.path.insert(0, '/root/.openclaw/workspace/moer-chanlun')

from config import OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
from auto_trading import OKXTrader, OrderSide, OrderType

# ========== 减仓规则配置 ==========
# 1. 分批止盈
PARTIAL_PROFIT_RULES = [
    {"pnl_pct": 5, "reduce_pct": 0.30},   # 盈利5%减仓30%
    {"pnl_pct": 10, "reduce_pct": 0.30},  # 盈利10%再减仓30%
    {"pnl_pct": 15, "reduce_pct": 0.20},  # 盈利15%再减仓20%
    {"pnl_pct": 20, "reduce_pct": 0.20},  # 盈利20%清仓剩余20%
]

# 2. 移动止损 (跟踪止损)
TRAILING_STOP = {
    "activation": 5,      # 盈利5%后启动
    "callback": 3,        # 回撤3%触发
}

# 3. 固定止损
FIXED_STOP_LOSS = -5    # -5%固定止损

# 4. 时间减仓
TIME_BASED_REDUCE = {
    "hours": 24,          # 持仓24小时
    "reduce_pct": 0.50,   # 减仓50%
}

# 5. 最大回撤减仓
MAX_DRAWDOWN = {
    "from_peak": 5,       # 从最高点回撤5%
    "reduce_pct": 0.50,   # 减仓50%
}

CHECK_INTERVAL = 30     # 30秒检查一次
# ==================================

@dataclass
class Position:
    """持仓信息"""
    symbol: str
    side: str           # buy/sell
    entry_price: float
    size: float
    entry_time: datetime
    order_id: str
    
    # 减仓记录
    reduced_times: int = 0
    total_reduced_pct: float = 0
    highest_price: float = 0
    lowest_price: float = float('inf')
    
    # 分批止盈记录
    profit_levels_hit: List[float] = None
    
    def __post_init__(self):
        if self.profit_levels_hit is None:
            self.profit_levels_hit = []
        if self.highest_price == 0:
            self.highest_price = self.entry_price
        if self.lowest_price == float('inf'):
            self.lowest_price = self.entry_price


class ReducePositionManager:
    """减仓管理器"""
    
    def __init__(self):
        self.trader = OKXTrader(OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE, is_demo=False)
        self.positions: Dict[str, Position] = {}
        
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
        print(f"\n📥 新增持仓: {symbol}")
        print(f"   方向: {side}, 价格: ${entry_price:,.2f}, 数量: {size:.6f}")
    
    def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        from realtime_data import OKXAPI
        price = OKXAPI.get_symbol_price(symbol.replace('-SWAP', ''))
        return price if price else 0
    
    def calculate_pnl(self, position: Position, current_price: float) -> float:
        """计算盈亏百分比"""
        if position.side == "buy":
            return (current_price - position.entry_price) / position.entry_price * 100
        else:  # sell (做空)
            return (position.entry_price - current_price) / position.entry_price * 100
    
    def reduce_position(self, position: Position, reduce_pct: float, reason: str) -> bool:
        """执行减仓"""
        if reduce_pct <= 0 or position.total_reduced_pct >= 0.99:
            return False
        
        reduce_size = position.size * reduce_pct
        
        print(f"\n{'='*70}")
        print(f"📤 减仓 - {position.symbol}")
        print(f"原因: {reason}")
        print(f"{'='*70}")
        print(f"   减仓比例: {reduce_pct*100:.0f}%")
        print(f"   减仓数量: {reduce_size:.6f}")
        
        # 下单减仓
        side = OrderSide.SELL if position.side == "buy" else OrderSide.BUY
        
        order = self.trader.place_order(
            symbol=position.symbol,
            side=side,
            size=reduce_size,
            order_type=OrderType.MARKET
        )
        
        if order:
            position.size -= reduce_size
            position.total_reduced_pct += reduce_pct
            position.reduced_times += 1
            
            print(f"   ✅ 减仓成功!")
            print(f"   剩余仓位: {position.size:.6f} ({(1-position.total_reduced_pct)*100:.0f}%)")
            
            # 记录
            self.save_reduce_record(position, reduce_pct, reason)
            
            return True
        else:
            print(f"   ❌ 减仓失败")
            return False
    
    def check_partial_profit(self, position: Position, pnl_pct: float):
        """检查分批止盈"""
        for rule in PARTIAL_PROFIT_RULES:
            target_pnl = rule["pnl_pct"]
            reduce_pct = rule["reduce_pct"]
            
            # 检查是否达到该级别且未执行过
            if pnl_pct >= target_pnl and target_pnl not in position.profit_levels_hit:
                position.profit_levels_hit.append(target_pnl)
                
                # 检查是否还有仓位可减
                if position.total_reduced_pct + reduce_pct <= 1:
                    self.reduce_position(
                        position, 
                        reduce_pct, 
                        f"分批止盈: 盈利{target_pnl}%减仓{reduce_pct*100:.0f}%"
                    )
                    return True
        return False
    
    def check_trailing_stop(self, position: Position, current_price: float, pnl_pct: float):
        """检查移动止损"""
        # 更新最高/最低价
        if position.side == "buy":
            if current_price > position.highest_price:
                position.highest_price = current_price
            
            # 盈利超过激活点后，计算回撤
            if pnl_pct >= TRAILING_STOP["activation"]:
                peak_price = position.highest_price
                drawdown_pct = (peak_price - current_price) / peak_price * 100
                
                if drawdown_pct >= TRAILING_STOP["callback"]:
                    # 触发移动止损，清仓
                    remaining_pct = 1 - position.total_reduced_pct
                    self.reduce_position(
                        position,
                        remaining_pct,
                        f"移动止损: 从最高点回撤{drawdown_pct:.1f}%"
                    )
                    return True
        
        else:  # 做空
            if current_price < position.lowest_price:
                position.lowest_price = current_price
            
            if pnl_pct >= TRAILING_STOP["activation"]:
                peak_price = position.lowest_price
                drawdown_pct = (current_price - peak_price) / peak_price * 100
                
                if drawdown_pct >= TRAILING_STOP["callback"]:
                    remaining_pct = 1 - position.total_reduced_pct
                    self.reduce_position(
                        position,
                        remaining_pct,
                        f"移动止损: 从最低点反弹{drawdown_pct:.1f}%"
                    )
                    return True
        
        return False
    
    def check_fixed_stop(self, position: Position, pnl_pct: float):
        """检查固定止损"""
        if pnl_pct <= FIXED_STOP_LOSS:
            remaining_pct = 1 - position.total_reduced_pct
            self.reduce_position(
                position,
                remaining_pct,
                f"固定止损: 亏损{pnl_pct:.1f}%"
            )
            return True
        return False
    
    def check_time_based(self, position: Position):
        """检查时间减仓"""
        hold_time = datetime.now() - position.entry_time
        
        if hold_time >= timedelta(hours=TIME_BASED_REDUCE["hours"]):
            # 检查是否已执行过时间减仓
            if not hasattr(position, 'time_reduced'):
                position.time_reduced = True
                reduce_pct = min(TIME_BASED_REDUCE["reduce_pct"], 
                               1 - position.total_reduced_pct)
                
                self.reduce_position(
                    position,
                    reduce_pct,
                    f"时间减仓: 持仓超过{TIME_BASED_REDUCE['hours']}小时"
                )
                return True
        return False
    
    def check_max_drawdown(self, position: Position, current_price: float):
        """检查最大回撤"""
        if position.side == "buy":
            drawdown = (position.highest_price - current_price) / position.highest_price * 100
        else:
            drawdown = (current_price - position.lowest_price) / position.lowest_price * 100
        
        if drawdown >= MAX_DRAWDOWN["from_peak"]:
            if not hasattr(position, 'drawdown_reduced'):
                position.drawdown_reduced = True
                reduce_pct = min(MAX_DRAWDOWN["reduce_pct"],
                               1 - position.total_reduced_pct)
                
                self.reduce_position(
                    position,
                    reduce_pct,
                    f"回撤减仓: 从极值回撤{drawdown:.1f}%"
                )
                return True
        return False
    
    def monitor_position(self, position: Position):
        """监控单个持仓"""
        current_price = self.get_current_price(position.symbol)
        if current_price == 0:
            return
        
        pnl_pct = self.calculate_pnl(position, current_price)
        
        # 更新极值
        if position.side == "buy":
            position.highest_price = max(position.highest_price, current_price)
        else:
            position.lowest_price = min(position.lowest_price, current_price)
        
        print(f"\n{'='*70}")
        print(f"📊 {position.symbol} - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*70}")
        print(f"   当前价格: ${current_price:,.2f}")
        print(f"   入场价格: ${position.entry_price:,.2f}")
        print(f"   盈亏: {pnl_pct:+.2f}%")
        print(f"   持仓数量: {position.size:.6f}")
        print(f"   已减仓: {position.total_reduced_pct*100:.0f}%")
        
        # 检查各种减仓条件
        reduced = False
        
        # 1. 固定止损 (最高优先级)
        if not reduced:
            reduced = self.check_fixed_stop(position, pnl_pct)
        
        # 2. 分批止盈
        if not reduced and pnl_pct > 0:
            reduced = self.check_partial_profit(position, pnl_pct)
        
        # 3. 移动止损
        if not reduced and pnl_pct > TRAILING_STOP["activation"]:
            reduced = self.check_trailing_stop(position, current_price, pnl_pct)
        
        # 4. 最大回撤
        if not reduced:
            reduced = self.check_max_drawdown(position, current_price)
        
        # 5. 时间减仓
        if not reduced:
            reduced = self.check_time_based(position)
        
        if not reduced:
            print(f"   ⏳ 继续持仓")
    
    def save_reduce_record(self, position: Position, reduce_pct: float, reason: str):
        """保存减仓记录"""
        try:
            import json
            filename = f"/root/.openclaw/workspace/moer-chanlun/reduce_records_{datetime.now().strftime('%Y%m%d')}.json"
            
            record = {
                "time": datetime.now().isoformat(),
                "symbol": position.symbol,
                "reason": reason,
                "reduce_pct": reduce_pct,
                "remaining_size": position.size,
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
    
    def run(self):
        """主循环"""
        print("\n" + "="*70)
        print("📤 减仓管理系统")
        print("="*70)
        print("规则:")
        print(f"  1. 分批止盈: 5%/10%/15%/20% 逐步减仓")
        print(f"  2. 移动止损: 盈利{TRAILING_STOP['activation']}%后回撤{TRAILING_STOP['callback']}%触发")
        print(f"  3. 固定止损: {FIXED_STOP_LOSS}%")
        print(f"  4. 时间减仓: {TIME_BASED_REDUCE['hours']}小时减仓{TIME_BASED_REDUCE['reduce_pct']*100:.0f}%")
        print(f"  5. 回撤减仓: 从极值回撤{MAX_DRAWDOWN['from_peak']}%减仓{MAX_DRAWDOWN['reduce_pct']*100:.0f}%")
        print("="*70)
        print("\n⏹️  按 Ctrl+C 停止\n")
        
        while True:
            try:
                if not self.positions:
                    print(f"\n⏳ 暂无持仓，等待新仓位...")
                    time.sleep(CHECK_INTERVAL)
                    continue
                
                for symbol, position in list(self.positions.items()):
                    self.monitor_position(position)
                    
                    # 清仓后移除
                    if position.total_reduced_pct >= 0.99:
                        print(f"\n   ✅ {symbol} 已清仓")
                        del self.positions[symbol]
                
                print(f"\n⏰ 下次检查: {CHECK_INTERVAL}秒后...")
                print(f"{'='*70}")
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n\n👋 减仓管理已停止")
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                time.sleep(10)


if __name__ == "__main__":
    manager = ReducePositionManager()
    manager.run()
