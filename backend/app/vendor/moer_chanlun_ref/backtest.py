"""
摩尔缠论回测系统
验证三类买卖点的胜率和收益率
"""

from moer_quant_pure import MoerChanlun, SignalType, TradeSignal
from typing import List, Dict, Tuple
from dataclasses import dataclass
from enum import Enum
import random

class TradeStatus(Enum):
    OPEN = "持仓中"
    CLOSED_PROFIT = "盈利平仓"
    CLOSED_LOSS = "亏损平仓"
    CLOSED_TIMEOUT = "超时平仓"

@dataclass
class Trade:
    """单笔交易记录"""
    entry_signal: TradeSignal
    entry_price: float
    entry_time: int
    exit_price: float = 0
    exit_time: int = 0
    exit_reason: str = ""
    pnl: float = 0  # 盈亏金额
    pnl_pct: float = 0  # 盈亏百分比
    status: TradeStatus = TradeStatus.OPEN
    
    @property
    def holding_bars(self) -> int:
        return self.exit_time - self.entry_time if self.exit_time > 0 else 0

class BacktestEngine:
    """摩尔缠论回测引擎"""
    
    def __init__(self, initial_capital: float = 100000, 
                 position_size: float = 0.1,  # 每笔仓位 10%
                 max_holding_bars: int = 50):  # 最大持仓周期
        self.initial_capital = initial_capital
        self.position_size = position_size
        self.max_holding_bars = max_holding_bars
        
        self.capital = initial_capital
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = []
        
    def run(self, highs: List[float], lows: List[float], 
            closes: List[float], volumes: List[float]) -> Dict:
        """
        执行回测
        """
        print("\n📊 开始回测...")
        
        moer = MoerChanlun(ma_period=34, center_min_height=0.005)
        
        # 滑动窗口扫描
        window_size = 100
        current_trade: Trade = None
        
        for i in range(window_size, len(closes)):
            # 获取当前窗口数据
            w_highs = highs[i-window_size:i+1]
            w_lows = lows[i-window_size:i+1]
            w_closes = closes[i-window_size:i+1]
            w_volumes = volumes[i-window_size:i+1] if volumes else None
            
            current_price = closes[i]
            
            # 检查持仓中的交易
            if current_trade:
                trade = current_trade
                
                # 检查止盈
                if trade.entry_signal.signal_type in [SignalType.FIRST_BUY, SignalType.SECOND_BUY, SignalType.THIRD_BUY]:
                    # 多单止盈/止损
                    if current_price >= trade.entry_signal.take_profit:
                        self.close_trade(trade, current_price, i, "止盈")
                        current_trade = None
                    elif current_price <= trade.entry_signal.stop_loss:
                        self.close_trade(trade, current_price, i, "止损")
                        current_trade = None
                    elif i - trade.entry_time >= self.max_holding_bars:
                        self.close_trade(trade, current_price, i, "超时")
                        current_trade = None
                else:
                    # 空单止盈/止损
                    if current_price <= trade.entry_signal.take_profit:
                        self.close_trade(trade, current_price, i, "止盈")
                        current_trade = None
                    elif current_price >= trade.entry_signal.stop_loss:
                        self.close_trade(trade, current_price, i, "止损")
                        current_trade = None
                    elif i - trade.entry_time >= self.max_holding_bars:
                        self.close_trade(trade, current_price, i, "超时")
                        current_trade = None
            
            # 寻找新信号（只在无持仓时）
            if not current_trade:
                signals = moer.scan(w_highs, w_lows, w_closes, w_volumes)
                
                for sig in signals:
                    # 只交易信号强度 >= 60 的
                    if sig.strength >= 60:
                        trade = self.open_trade(sig, closes[i], i)
                        if trade:
                            current_trade = trade
                            break
            
            # 记录权益
            self.equity_curve.append(self.capital)
        
        # 强制平仓最后持仓
        if current_trade:
            self.close_trade(current_trade, closes[-1], len(closes)-1, "回测结束")
        
        return self.generate_report()
    
    def open_trade(self, signal: TradeSignal, price: float, time: int) -> Trade:
        """开仓"""
        trade = Trade(
            entry_signal=signal,
            entry_price=price,
            entry_time=time
        )
        self.trades.append(trade)
        return trade
    
    def close_trade(self, trade: Trade, price: float, time: int, reason: str):
        """平仓"""
        trade.exit_price = price
        trade.exit_time = time
        trade.exit_reason = reason
        
        # 计算盈亏
        if trade.entry_signal.signal_type in [SignalType.FIRST_BUY, SignalType.SECOND_BUY, SignalType.THIRD_BUY]:
            # 多单
            trade.pnl_pct = (price - trade.entry_price) / trade.entry_price
        else:
            # 空单
            trade.pnl_pct = (trade.entry_price - price) / trade.entry_price
        
        position_value = self.capital * self.position_size
        trade.pnl = position_value * trade.pnl_pct
        self.capital += trade.pnl
        
        # 状态
        if trade.pnl > 0:
            trade.status = TradeStatus.CLOSED_PROFIT
        elif trade.pnl < 0:
            trade.status = TradeStatus.CLOSED_LOSS
        else:
            trade.status = TradeStatus.CLOSED_TIMEOUT
    
    def generate_report(self) -> Dict:
        """生成回测报告"""
        if not self.trades:
            return {"error": "无交易记录"}
        
        total_trades = len(self.trades)
        winning_trades = [t for t in self.trades if t.pnl > 0]
        losing_trades = [t for t in self.trades if t.pnl < 0]
        
        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        
        win_rate = win_count / total_trades * 100 if total_trades > 0 else 0
        
        avg_win = sum(t.pnl_pct for t in winning_trades) / win_count * 100 if win_count > 0 else 0
        avg_loss = sum(t.pnl_pct for t in losing_trades) / loss_count * 100 if loss_count > 0 else 0
        
        total_return = (self.capital - self.initial_capital) / self.initial_capital * 100
        
        # 按信号类型统计
        signal_stats = {}
        for signal_type in SignalType:
            type_trades = [t for t in self.trades if t.entry_signal.signal_type == signal_type]
            if type_trades:
                type_wins = len([t for t in type_trades if t.pnl > 0])
                signal_stats[signal_type.value] = {
                    "count": len(type_trades),
                    "win_rate": type_wins / len(type_trades) * 100,
                    "avg_pnl": sum(t.pnl_pct for t in type_trades) / len(type_trades) * 100
                }
        
        return {
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(avg_win / avg_loss) if avg_loss != 0 else float('inf'),
            "initial_capital": self.initial_capital,
            "final_capital": self.capital,
            "total_return": total_return,
            "signal_stats": signal_stats,
            "trades": self.trades
        }


def generate_trending_data(n: int = 300) -> Tuple[List[float], List[float], List[float], List[float]]:
    """生成带明显趋势的数据（更容易产生信号）"""
    random.seed(123)
    closes = []
    base = 50000
    
    trend_phase = 0
    for i in range(n):
        # 周期性地改变趋势
        if i % 75 == 0:
            trend_phase = (trend_phase + 1) % 4
        
        if trend_phase == 0:  # 上涨
            change = random.uniform(-0.005, 0.02)
        elif trend_phase == 1:  # 顶部震荡
            change = random.uniform(-0.015, 0.015)
        elif trend_phase == 2:  # 下跌
            change = random.uniform(-0.02, 0.005)
        else:  # 底部震荡
            change = random.uniform(-0.015, 0.015)
        
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.005)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.005)) for c in closes]
    volumes = [random.randint(10000, 50000) for _ in range(n)]
    
    return highs, lows, closes, volumes


def print_report(report: Dict):
    """打印回测报告"""
    print("\n" + "=" * 70)
    print("📊 摩尔缠论回测报告")
    print("=" * 70)
    
    print(f"\n💰 资金表现")
    print(f"   初始资金: {report['initial_capital']:,.2f} USDT")
    print(f"   最终资金: {report['final_capital']:,.2f} USDT")
    print(f"   总收益率: {report['total_return']:+.2f}%")
    
    print(f"\n📈 交易统计")
    print(f"   总交易次数: {report['total_trades']}")
    print(f"   盈利次数: {report['win_count']} 🟢")
    print(f"   亏损次数: {report['loss_count']} 🔴")
    print(f"   胜率: {report['win_rate']:.1f}%")
    
    print(f"\n📉 盈亏分析")
    print(f"   平均盈利: +{report['avg_win']:.2f}%")
    print(f"   平均亏损: {report['avg_loss']:.2f}%")
    print(f"   盈亏比: {report['profit_factor']:.2f}")
    
    if report['signal_stats']:
        print(f"\n🎯 按信号类型统计")
        for signal_name, stats in report['signal_stats'].items():
            bar = "█" * int(stats['win_rate'] / 5)
            print(f"   {signal_name:8s}: 胜率 {stats['win_rate']:5.1f}% [{bar:<20s}] ({stats['count']}笔) 平均盈亏: {stats['avg_pnl']:+.2f}%")
    
    # 最近5笔交易
    print(f"\n📝 最近5笔交易")
    recent_trades = report['trades'][-5:]
    for i, t in enumerate(reversed(recent_trades), 1):
        emoji = "🟢" if t.pnl > 0 else "🔴"
        print(f"   {emoji} {t.entry_signal.signal_type.value:6s} | 盈亏: {t.pnl_pct*100:+.2f}% | 持仓: {t.holding_bars}根K线 | 原因: {t.exit_reason}")
    
    print("\n" + "=" * 70)
    
    # 评估
    print("\n💡 策略评估")
    if report['win_rate'] >= 60 and report['total_return'] > 0:
        print("   ✅ 策略表现优秀，可考虑实盘")
    elif report['win_rate'] >= 50 and report['total_return'] > 0:
        print("   ⚠️ 策略表现一般，建议优化参数")
    else:
        print("   ❌ 策略表现不佳，需要改进")
    
    print("=" * 70)


if __name__ == "__main__":
    print("=" * 70)
    print("  摩尔缠论回测系统")
    print("=" * 70)
    
    # 生成测试数据
    print("\n📈 生成测试数据（含趋势周期）...")
    highs, lows, closes, volumes = generate_trending_data(300)
    print(f"   数据量: {len(closes)} 根K线")
    print(f"   价格范围: {min(closes):,.2f} - {max(closes):,.2f}")
    
    # 运行回测
    engine = BacktestEngine(
        initial_capital=100000,
        position_size=0.2,  # 每笔20%仓位
        max_holding_bars=30  # 最多持仓30根K线
    )
    
    report = engine.run(highs, lows, closes, volumes)
    
    # 打印报告
    print_report(report)
