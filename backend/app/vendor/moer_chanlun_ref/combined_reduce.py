#!/usr/bin/env python3
"""
摩尔缠论 - 综合减仓系统
结合: 分批止盈 + 技术信号
"""

import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

sys.path.insert(0, '/root/.openclaw/workspace/moer-chanlun')

from config import OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
from auto_trading import OKXTrader, OrderSide, OrderType
from moer_quant_pure import MoerChanlun
from realtime_data import RealtimeDataManager

# ========== 综合减仓规则 ==========

# A. 分批止盈规则 (价格变动百分比)
PARTIAL_PROFIT_RULES = [
    {"pnl_pct": 10, "reduce_pct": 0.20},  # 盈利10%减仓20%
    {"pnl_pct": 15, "reduce_pct": 0.20},  # 盈利15%再减仓20%
]

# B. 技术信号减仓
TECHNICAL_RULES = {
    "divergence_5m": {"reduce_pct": 0.30},   # 5分钟顶背离减仓30%
    "divergence_30m": {"reduce_pct": 0.30},  # 30分钟顶背离减仓30%
    "ma5_turn_black": {"reduce_pct": 1.0},   # MA5转折+黑K清仓
}

# C. 风控规则
STOP_LOSS = -5               # 固定止损-5%
TRAILING_STOP = {"activation": 5, "callback": 3, "reduce_pct": 0.60}  # 移动止损:回撤3%减仓60%

CHECK_INTERVAL = 30          # 30秒检查
# ==================================

@dataclass
class Position:
    """持仓信息"""
    symbol: str
    side: str
    entry_price: float
    size: float
    entry_time: datetime
    order_id: str
    
    # 减仓记录
    total_reduced_pct: float = 0.0
    
    # 分批止盈记录
    profit_levels_hit: List[float] = None
    
    # 技术信号记录
    div_5m_done: bool = False
    div_30m_done: bool = False
    ma5_turn_done: bool = False
    
    # 状态
    highest_price: float = 0.0
    lowest_price: float = float('inf')
    
    def __post_init__(self):
        if self.profit_levels_hit is None:
            self.profit_levels_hit = []
        if self.highest_price == 0:
            self.highest_price = self.entry_price


class CombinedReduceManager:
    """综合减仓管理器"""
    
    def __init__(self):
        self.trader = OKXTrader(OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE, is_demo=False)
        self.data_mgr = RealtimeDataManager("okx")
        self.moer = MoerChanlun()
        self.positions: Dict[str, Position] = {}
        
    def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        from realtime_data import OKXAPI
        price = OKXAPI.get_symbol_price(symbol.replace('-SWAP', ''))
        return price if price else 0
    
    def calculate_pnl(self, position: Position, current_price: float) -> float:
        """计算盈亏百分比 (不含杠杆)"""
        if position.side == "buy":
            pnl = (current_price - position.entry_price) / position.entry_price * 100
        else:
            pnl = (position.entry_price - current_price) / position.entry_price * 100
        return pnl
    
    def check_divergence(self, symbol: str, timeframe: str, direction: str) -> bool:
        """检查背离"""
        highs, lows, closes, _ = self.data_mgr.fetch_data(symbol, timeframe, 100)
        if not closes:
            return False
        
        macd = self.moer.calculate_macd(closes)
        
        if direction == "bearish":
            return self.moer.detect_bearish_divergence(highs, macd['histogram'], 20)
        else:
            return self.moer.detect_bullish_divergence(lows, macd['histogram'], 20)
    
    def check_ma5_turn_black(self, symbol: str) -> bool:
        """检查MA5转折+黑K (摩尔缠论标准: 非正向跳空MA5) - 多头清仓"""
        highs, lows, closes, _ = self.data_mgr.fetch_data(symbol, "5m", 20)
        if len(closes) < 10:
            return False
        
        # 计算MA5
        ma5_now = sum(closes[-5:]) / 5
        ma5_prev = sum(closes[-6:-1]) / 5 if len(closes) >= 6 else ma5_now
        
        # 判断转折: MA5由向上转为向下
        was_up = ma5_prev > (sum(closes[-7:-2]) / 5 if len(closes) >= 7 else ma5_prev)
        is_down = ma5_now < ma5_prev
        
        # 黑K确认 (摩尔缠论: 非正向跳空MA5)
        # 正向跳空 = 最低价 > MA5 (向上跳空突破)
        # 非正向跳空 = 不是正向跳空 (包括向下跳空、粘合)
        current_low = lows[-1]
        is_positive_gap = current_low > ma5_now  # 正向跳空
        is_black = not is_positive_gap  # 非正向跳空 = 黑K
        
        return was_up and is_down and is_black
    
    def check_ma5_turn_white(self, symbol: str) -> bool:
        """检查MA5转折+白K (摩尔缠论: 正向跳空MA5) - 空头清仓"""
        highs, lows, closes, _ = self.data_mgr.fetch_data(symbol, "5m", 20)
        if len(closes) < 10:
            return False
        
        # 计算MA5
        ma5_now = sum(closes[-5:]) / 5
        ma5_prev = sum(closes[-6:-1]) / 5 if len(closes) >= 6 else ma5_now
        
        # 判断转折: MA5由向下转为向上
        was_down = ma5_prev < (sum(closes[-7:-2]) / 5 if len(closes) >= 7 else ma5_prev)
        is_up = ma5_now > ma5_prev
        
        # 白K确认 (摩尔缠论: 正向跳空MA5)
        # 正向跳空 = 最低价 > MA5 (向上跳空突破)
        current_low = lows[-1]
        is_white = current_low > ma5_now  # 正向跳空 = 白K
        
        return was_down and is_up and is_white
    
    def reduce_position(self, position: Position, reduce_pct: float, reason: str) -> bool:
        """执行减仓"""
        if reduce_pct <= 0 or position.total_reduced_pct >= 0.99:
            return False
        
        # 计算减仓数量
        remaining = 1 - position.total_reduced_pct
        actual_reduce = min(reduce_pct, remaining)
        reduce_size = position.size * remaining * actual_reduce
        
        if reduce_size <= 0:
            return False
        
        print(f"\n{'='*70}")
        print(f"📤 减仓 - {position.symbol}")
        print(f"触发: {reason}")
        print(f"{'='*70}")
        print(f"   减仓比例: {actual_reduce*100:.0f}%")
        print(f"   减仓数量: {reduce_size:.6f}")
        
        # 下单
        side = OrderSide.SELL if position.side == "buy" else OrderSide.BUY
        order = self.trader.place_order(symbol=position.symbol, side=side, 
                                       size=reduce_size, order_type=OrderType.MARKET)
        
        if order:
            position.total_reduced_pct += actual_reduce * remaining
            print(f"   ✅ 成功! 累计减仓: {position.total_reduced_pct*100:.1f}%")
            self.save_record(position, actual_reduce, reason)
            return True
        else:
            print(f"   ❌ 失败")
            return False
    
    def monitor_position(self, position: Position):
        """监控持仓"""
        symbol = position.symbol
        current_price = self.get_current_price(symbol)
        if current_price == 0:
            return
        
        pnl_pct = self.calculate_pnl(position, current_price)
        
        # 更新极值
        if position.side == "buy":
            position.highest_price = max(position.highest_price, current_price)
        else:
            position.lowest_price = min(position.lowest_price, current_price)
        
        print(f"\n{'='*70}")
        print(f"🔍 {symbol} - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*70}")
        print(f"   当前价格: ${current_price:,.2f}")
        print(f"   入场价格: ${position.entry_price:,.2f}")
        print(f"   当前盈亏: {pnl_pct:+.2f}%")
        print(f"   已减仓: {position.total_reduced_pct*100:.1f}%")
        
        reduced = False
        
        # ===== A. 分批止盈 =====
        if pnl_pct > 0:
            for rule in PARTIAL_PROFIT_RULES:
                target = rule["pnl_pct"]
                pct = rule["reduce_pct"]
                
                if pnl_pct >= target and target not in position.profit_levels_hit:
                    position.profit_levels_hit.append(target)
                    if self.reduce_position(position, pct, f"分批止盈 {target}%"):
                        reduced = True
                    break
        
        # ===== B. 技术信号 =====
        if not reduced:
            if position.side == "buy":
                # 多头持仓: 检测顶背离 (看跌信号)
                # 5分钟顶背离
                if not position.div_5m_done:
                    if self.check_divergence(symbol, "5m", "bearish"):
                        position.div_5m_done = True
                        if self.reduce_position(position, TECHNICAL_RULES["divergence_5m"]["reduce_pct"], 
                                              "5分钟顶背离"):
                            reduced = True
                
                # 30分钟顶背离
                if not reduced and not position.div_30m_done:
                    if self.check_divergence(symbol, "30m", "bearish"):
                        position.div_30m_done = True
                        if self.reduce_position(position, TECHNICAL_RULES["divergence_30m"]["reduce_pct"],
                                              "30分钟顶背离"):
                            reduced = True
                
                # MA5+黑K (多头清仓)
                if not reduced and not position.ma5_turn_done:
                    if self.check_ma5_turn_black(symbol):
                        position.ma5_turn_done = True
                        remaining = 1 - position.total_reduced_pct
                        if self.reduce_position(position, remaining, "MA5转折+黑K"):
                            reduced = True
            
            else:  # side == "sell"
                # 空头持仓: 检测底背离 (看涨信号)
                # 5分钟底背离
                if not position.div_5m_done:
                    if self.check_divergence(symbol, "5m", "bullish"):
                        position.div_5m_done = True
                        if self.reduce_position(position, TECHNICAL_RULES["divergence_5m"]["reduce_pct"], 
                                              "5分钟底背离(做空信号)"):
                            reduced = True
                
                # 30分钟底背离
                if not reduced and not position.div_30m_done:
                    if self.check_divergence(symbol, "30m", "bullish"):
                        position.div_30m_done = True
                        if self.reduce_position(position, TECHNICAL_RULES["divergence_30m"]["reduce_pct"],
                                              "30分钟底背离(做空信号)"):
                            reduced = True
                
                # MA5+白K (空头清仓)
                if not reduced and not position.ma5_turn_done:
                    if self.check_ma5_turn_white(symbol):
                        position.ma5_turn_done = True
                        remaining = 1 - position.total_reduced_pct
                        if self.reduce_position(position, remaining, "MA5转折+白K(做空信号)"):
                            reduced = True
        
        # ===== C. 止损 =====
        if not reduced and pnl_pct <= STOP_LOSS:
            remaining = 1 - position.total_reduced_pct
            self.reduce_position(position, remaining, f"止损 {pnl_pct:.1f}%")
            reduced = True
        
        # ===== D. 移动止损 (回撤3%减仓40%) =====
        if not reduced and pnl_pct >= TRAILING_STOP["activation"]:
            peak = position.highest_price if position.side == "buy" else position.lowest_price
            drawdown = abs(current_price - peak) / peak * 100
            
            if drawdown >= TRAILING_STOP["callback"]:
                # 回撤3%减仓60% (不是清仓!)
                reduce_pct = TRAILING_STOP.get("reduce_pct", 0.60)
                if self.reduce_position(position, reduce_pct, f"移动止损 回撤{drawdown:.1f}%减仓{reduce_pct*100:.0f}%"):
                    reduced = True
        
        if not reduced:
            print(f"   ⏳ 无减仓信号")
    
    def add_position(self, symbol: str, side: str, entry_price: float, 
                     size: float, order_id: str):
        """添加持仓"""
        self.positions[symbol] = Position(
            symbol=symbol, side=side, entry_price=entry_price,
            size=size, entry_time=datetime.now(), order_id=order_id
        )
        print(f"\n📥 新增持仓: {symbol} {'做多' if side=='buy' else '做空'} {size:.6f}")
    
    def save_record(self, position: Position, reduce_pct: float, reason: str):
        """保存记录"""
        try:
            import json
            filename = f"/root/.openclaw/workspace/moer-chanlun/combined_reduce_{datetime.now().strftime('%Y%m%d')}.json"
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
        """打印状态"""
        print(f"\n{'='*70}")
        print("📊 持仓状态")
        print(f"{'='*70}")
        if not self.positions:
            print("   无持仓")
            return
        for s, p in self.positions.items():
            print(f"\n   {s}:")
            print(f"      方向: {'多' if p.side=='buy' else '空'}")
            print(f"      入场: ${p.entry_price:,.2f}")
            print(f"      已减: {p.total_reduced_pct*100:.1f}%")
    
    def run(self):
        """主循环"""
        print("\n" + "="*70)
        print("📤 综合减仓系统 (分批止盈 + 技术信号)")
        print("="*70)
        print("A. 分批止盈:")
        print("   盈利10%→20%  15%→20%")
        print("\nB. 技术信号:")
        print("   5分钟顶背离→30%  30分钟顶背离→30%")
        print("   MA5转折+黑K→清仓")
        print("\nC. 风控:")
        print("   固定止损-5%  移动止损回撤3%减仓60%")
        print("="*70)
        print("\n⏹️  按 Ctrl+C 停止\n")
        
        while True:
            try:
                if not self.positions:
                    print(f"\n⏳ 等待持仓...")
                    time.sleep(CHECK_INTERVAL)
                    continue
                
                for symbol in list(self.positions.keys()):
                    self.monitor_position(self.positions[symbol])
                    if self.positions[symbol].total_reduced_pct >= 0.99:
                        del self.positions[symbol]
                
                self.print_status()
                print(f"\n⏰ 下次检查: {CHECK_INTERVAL}秒后...")
                print(f"{'='*70}")
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n\n👋 已停止")
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                time.sleep(10)


if __name__ == "__main__":
    manager = CombinedReduceManager()
    manager.run()
