"""
摩尔缠论回测系统 - 优化版
改进信号检测和参数
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
    entry_signal: TradeSignal
    entry_price: float
    entry_time: int
    exit_price: float = 0
    exit_time: int = 0
    exit_reason: str = ""
    pnl: float = 0
    pnl_pct: float = 0
    status: TradeStatus = TradeStatus.OPEN
    
    @property
    def holding_bars(self) -> int:
        return self.exit_time - self.entry_time if self.exit_time > 0 else 0

class BacktestEngine:
    def __init__(self, initial_capital: float = 100000, 
                 position_size: float = 0.2,
                 max_holding_bars: int = 20,
                 signal_strength_threshold: int = 40):  # 降低信号强度阈值
        self.initial_capital = initial_capital
        self.position_size = position_size
        self.max_holding_bars = max_holding_bars
        self.signal_strength_threshold = signal_strength_threshold
        
        self.capital = initial_capital
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = []
        
    def run(self, highs: List[float], lows: List[float], 
            closes: List[float], volumes: List[float]) -> Dict:
        """执行回测"""
        print("\n📊 开始回测...")
        
        moer = MoerChanlun(ma_period=34, center_min_height=0.003)  # 降低中枢高度要求
        
        window_size = 80
        current_trade: Trade = None
        
        for i in range(window_size, len(closes)):
            w_highs = highs[i-window_size:i+1]
            w_lows = lows[i-window_size:i+1]
            w_closes = closes[i-window_size:i+1]
            w_volumes = volumes[i-window_size:i+1] if volumes else None
            
            current_price = closes[i]
            
            # 检查持仓
            if current_trade:
                trade = current_trade
                
                if trade.entry_signal.signal_type in [SignalType.FIRST_BUY, SignalType.SECOND_BUY, SignalType.THIRD_BUY]:
                    # 多单 - 使用更灵活的止盈
                    profit_target = trade.entry_price * 1.03  # 3%止盈
                    if current_price >= profit_target:
                        self.close_trade(trade, current_price, i, "止盈")
                        current_trade = None
                    elif current_price <= trade.entry_signal.stop_loss:
                        self.close_trade(trade, current_price, i, "止损")
                        current_trade = None
                    elif i - trade.entry_time >= self.max_holding_bars:
                        self.close_trade(trade, current_price, i, "超时")
                        current_trade = None
                else:
                    # 空单
                    profit_target = trade.entry_price * 0.97
                    if current_price <= profit_target:
                        self.close_trade(trade, current_price, i, "止盈")
                        current_trade = None
                    elif current_price >= trade.entry_signal.stop_loss:
                        self.close_trade(trade, current_price, i, "止损")
                        current_trade = None
                    elif i - trade.entry_time >= self.max_holding_bars:
                        self.close_trade(trade, current_price, i, "超时")
                        current_trade = None
            
            # 寻找新信号
            if not current_trade:
                signals = moer.scan(w_highs, w_lows, w_closes, w_volumes)
                
                # 按强度排序，优先交易强信号
                signals.sort(key=lambda s: s.strength, reverse=True)
                
                for sig in signals:
                    if sig.strength >= self.signal_strength_threshold:
                        trade = self.open_trade(sig, closes[i], i)
                        if trade:
                            current_trade = trade
                            break
            
            self.equity_curve.append(self.capital)
        
        if current_trade:
            self.close_trade(current_trade, closes[-1], len(closes)-1, "回测结束")
        
        return self.generate_report()
    
    def open_trade(self, signal: TradeSignal, price: float, time: int) -> Trade:
        trade = Trade(
            entry_signal=signal,
            entry_price=price,
            entry_time=time
        )
        self.trades.append(trade)
        return trade
    
    def close_trade(self, trade: Trade, price: float, time: int, reason: str):
        trade.exit_price = price
        trade.exit_time = time
        trade.exit_reason = reason
        
        if trade.entry_signal.signal_type in [SignalType.FIRST_BUY, SignalType.SECOND_BUY, SignalType.THIRD_BUY]:
            trade.pnl_pct = (price - trade.entry_price) / trade.entry_price
        else:
            trade.pnl_pct = (trade.entry_price - price) / trade.entry_price
        
        position_value = self.capital * self.position_size
        trade.pnl = position_value * trade.pnl_pct
        self.capital += trade.pnl
        
        if trade.pnl > 0:
            trade.status = TradeStatus.CLOSED_PROFIT
        elif trade.pnl < 0:
            trade.status = TradeStatus.CLOSED_LOSS
        else:
            trade.status = TradeStatus.CLOSED_TIMEOUT
    
    def generate_report(self) -> Dict:
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
        
        # 最大回撤
        max_drawdown = 0
        peak = self.initial_capital
        for equity in self.equity_curve:
            if equity > peak:
                peak = equity
            drawdown = (peak - equity) / peak * 100
            max_drawdown = max(max_drawdown, drawdown)
        
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
            "max_drawdown": max_drawdown,
            "initial_capital": self.initial_capital,
            "final_capital": self.capital,
            "total_return": total_return,
            "signal_stats": signal_stats,
            "trades": self.trades
        }


def generate_btc_like_data(n: int = 500) -> Tuple[List[float], List[float], List[float], List[float]]:
    """生成类似BTC的震荡+趋势数据"""
    random.seed(42)
    closes = []
    base = 45000
    
    for i in range(n):
        # 模拟不同市场阶段
        phase = (i // 100) % 5
        
        if phase == 0:  # 横盘震荡
            change = random.uniform(-0.008, 0.008)
        elif phase == 1:  # 缓慢上涨
            change = random.uniform(-0.005, 0.015)
        elif phase == 2:  # 快速下跌
            change = random.uniform(-0.02, 0.008)
        elif phase == 3:  # V型反转底部
            if i % 100 < 50:
                change = random.uniform(-0.015, 0.005)
            else:
                change = random.uniform(-0.005, 0.02)
        else:  # 主升浪
            change = random.uniform(-0.008, 0.025)
        
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
    volumes = [random.randint(8000, 60000) for _ in range(n)]
    
    return highs, lows, closes, volumes


def print_report(report: Dict):
    print("\n" + "=" * 70)
    print("📊 摩尔缠论回测报告 - 优化版")
    print("=" * 70)
    
    # 收益曲线图（简化版）
    print("\n📈 收益曲线")
    if len(report.get('equity_curve', [])) > 20:
        print("   (简化显示)")
    
    print(f"\n💰 资金表现")
    print(f"   初始资金: {report['initial_capital']:,.2f} USDT")
    print(f"   最终资金: {report['final_capital']:,.2f} USDT")
    
    ret_color = "🟢" if report['total_return'] >= 0 else "🔴"
    print(f"   总收益率: {ret_color} {report['total_return']:+.2f}%")
    print(f"   最大回撤: {report.get('max_drawdown', 0):.2f}%")
    
    print(f"\n📊 交易统计")
    print(f"   总交易次数: {report['total_trades']}")
    print(f"   盈利次数: {report['win_count']} 🟢")
    print(f"   亏损次数: {report['loss_count']} 🔴")
    
    win_bar = "█" * int(report['win_rate'] / 5)
    print(f"   胜率: {report['win_rate']:.1f}% [{win_bar:<20s}]")
    
    print(f"\n📉 盈亏分析")
    print(f"   平均盈利: +{report['avg_win']:.2f}%")
    print(f"   平均亏损: {report['avg_loss']:.2f}%")
    pf = report['profit_factor']
    print(f"   盈亏比: {pf:.2f}x {'✅' if pf >= 1.5 else '⚠️'}")
    
    if report['signal_stats']:
        print(f"\n🎯 信号类型分析")
        for signal_name, stats in sorted(report['signal_stats'].items(), 
                                          key=lambda x: x[1]['count'], reverse=True):
            bar_len = int(stats['win_rate'] / 5)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            emoji = "🟢" if stats['win_rate'] >= 50 else "🔴"
            print(f"   {emoji} {signal_name:8s}: 胜率{stats['win_rate']:5.1f}% [{bar}]  ({stats['count']:2d}笔) 均盈{stats['avg_pnl']:+6.2f}%")
    
    # 所有交易明细
    print(f"\n📝 全部交易记录")
    print(f"   {'信号':8s} {'入场价':>12s} {'出场价':>12s} {'盈亏%':>8s} {'持仓':>4s} {'结果':6s}")
    print(f"   {'-'*60}")
    for t in report['trades']:
        emoji = "🟢" if t.pnl > 0 else "🔴"
        print(f"   {emoji} {t.entry_signal.signal_type.value:6s} {t.entry_price:>12,.2f} {t.exit_price:>12,.2f} {t.pnl_pct*100:>+7.2f}% {t.holding_bars:>3d}K {t.exit_reason:6s}")
    
    print("\n" + "=" * 70)
    print("💡 策略评估")
    if report['win_rate'] >= 55 and report['total_return'] > 0:
        print("   ✅ 策略表现良好，可考虑小资金实盘测试")
    elif report['win_rate'] >= 45 and report['profit_factor'] >= 1.2:
        print("   ⚠️ 策略有潜力，建议优化止损止盈参数")
    elif report['total_trades'] < 5:
        print("   ⏳ 交易次数过少，数据不足评估")
    else:
        print("   ❌ 策略需要改进，建议调整入场条件或过滤假信号")
    print("=" * 70)


if __name__ == "__main__":
    print("=" * 70)
    print("  摩尔缠论回测系统 - 优化版")
    print("  参数: 中枢高度≥0.3% | 信号强度≥40 | 止盈3% | 止损按信号")
    print("=" * 70)
    
    print("\n📈 生成BTC风格测试数据（5个市场周期）...")
    highs, lows, closes, volumes = generate_btc_like_data(500)
    print(f"   K线数量: {len(closes)}")
    print(f"   价格范围: {min(closes):,.2f} - {max(closes):,.2f}")
    print(f"   波动率: {(max(closes)-min(closes))/min(closes)*100:.1f}%")
    
    engine = BacktestEngine(
        initial_capital=100000,
        position_size=0.25,
        max_holding_bars=25,
        signal_strength_threshold=40
    )
    
    report = engine.run(highs, lows, closes, volumes)
    print_report(report)
