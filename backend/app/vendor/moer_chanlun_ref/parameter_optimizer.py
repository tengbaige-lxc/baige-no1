"""
摩尔缠论 - 参数优化系统
自动寻找最优参数组合
"""

import random
import itertools
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass
from moer_quant_pure import MoerChanlun, SignalType

@dataclass
class BacktestResult:
    """回测结果"""
    params: Dict[str, Any]
    total_trades: int
    win_rate: float
    profit_factor: float
    total_return: float
    max_drawdown: float
    sharpe_ratio: float
    score: float

class ParameterOptimizer:
    """参数优化器"""
    
    def __init__(self):
        self.param_grid = {
            'ma_period': [21, 34, 55, 89],
            'center_min_height': [0.001, 0.002, 0.003, 0.005],
        }
        
    def generate_param_combinations(self) -> List[Dict]:
        """生成参数组合"""
        keys = list(self.param_grid.keys())
        values = list(self.param_grid.values())
        
        combinations = []
        for combo in itertools.product(*values):
            param_dict = dict(zip(keys, combo))
            combinations.append(param_dict)
        
        return combinations
    
    def run_backtest(self, highs, lows, closes, volumes, params: Dict) -> BacktestResult:
        """运行单次回测"""
        moer = MoerChanlun(
            ma_period=params['ma_period'],
            center_min_height=params['center_min_height']
        )
        
        # 收集信号
        signals = []
        for i in range(100, len(closes)-20):
            w_highs = highs[i-80:i+1]
            w_lows = lows[i-80:i+1]
            w_closes = closes[i-80:i+1]
            w_volumes = volumes[i-80:i+1]
            
            sigs = moer.scan(w_highs, w_lows, w_closes, w_volumes)
            for sig in sigs:
                sig.index = i
                signals.append(sig)
        
        # 模拟交易
        trades = []
        equity = [10000]  # 初始资金
        peak = 10000
        max_dd = 0
        
        for sig in signals:
            i = sig.index
            if i >= len(closes) - 10:
                continue
            
            entry = sig.price
            pnl = 0
            
            if '买' in sig.signal_type.value:
                tp = entry * 1.04
                sl = entry * 0.97
                for j in range(i+1, min(i+20, len(closes))):
                    if lows[j] <= sl:
                        pnl = -0.03
                        break
                    elif highs[j] >= tp:
                        pnl = 0.04
                        break
                else:
                    pnl = (closes[min(i+20, len(closes)-1)] - entry) / entry
            else:
                tp = entry * 0.96
                sl = entry * 1.03
                for j in range(i+1, min(i+20, len(closes))):
                    if highs[j] >= sl:
                        pnl = -0.03
                        break
                    elif lows[j] <= tp:
                        pnl = 0.04
                        break
                else:
                    pnl = (entry - closes[min(i+20, len(closes)-1)]) / entry
            
            trades.append(pnl)
            new_equity = equity[-1] * (1 + pnl)
            equity.append(new_equity)
            
            # 计算最大回撤
            if new_equity > peak:
                peak = new_equity
            dd = (peak - new_equity) / peak
            max_dd = max(max_dd, dd)
        
        # 计算指标
        if not trades:
            return BacktestResult(params, 0, 0, 0, 0, 0, 0, 0)
        
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]
        
        total_trades = len(trades)
        win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
        
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 1
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
        
        total_return = (equity[-1] / equity[0] - 1) * 100
        
        # 夏普比率（简化版）
        returns = trades
        avg_return = sum(returns) / len(returns) if returns else 0
        std_return = (sum((r - avg_return)**2 for r in returns) / len(returns))**0.5 if returns else 1
        sharpe = avg_return / std_return if std_return != 0 else 0
        
        # 综合评分
        score = (
            win_rate * 0.3 +          # 胜率权重30%
            profit_factor * 20 +      # 盈亏比权重20%（放大）
            total_return * 0.3 +      # 总收益权重30%
            (1 - max_dd) * 20         # 最大回撤权重20%
        )
        
        return BacktestResult(
            params=params,
            total_trades=total_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_return=total_return,
            max_drawdown=max_dd * 100,
            sharpe_ratio=sharpe,
            score=score
        )
    
    def optimize(self, highs, lows, closes, volumes, top_n: int = 5) -> List[BacktestResult]:
        """优化参数"""
        print("="*70)
        print("🎯 摩尔缠论参数优化")
        print("="*70)
        
        param_combos = self.generate_param_combinations()
        total = len(param_combos)
        
        print(f"\n📊 参数组合数量: {total}")
        print(f"   MA周期: {self.param_grid['ma_period']}")
        print(f"   中枢高度: {self.param_grid['center_min_height']}")
        
        results = []
        
        print(f"\n🔄 开始回测...")
        for i, params in enumerate(param_combos, 1):
            result = self.run_backtest(highs, lows, closes, volumes, params)
            results.append(result)
            
            if i % 4 == 0 or i == total:
                print(f"   进度: {i}/{total} ({i/total*100:.0f}%)")
        
        # 按评分排序
        results.sort(key=lambda x: x.score, reverse=True)
        
        return results[:top_n]


def demo_optimize():
    """演示参数优化"""
    # 生成测试数据
    random.seed(42)
    n = 300
    closes = []
    base = 50000
    
    for i in range(n):
        change = random.uniform(-0.015, 0.02)
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
    volumes = [random.randint(10000, 50000) for _ in range(n)]
    
    # 优化
    optimizer = ParameterOptimizer()
    top_results = optimizer.optimize(highs, lows, closes, volumes, top_n=5)
    
    # 显示结果
    print("\n" + "="*70)
    print("🏆 最优参数组合 TOP 5")
    print("="*70)
    
    for i, r in enumerate(top_results, 1):
        print(f"\n{i}. 综合评分: {r.score:.2f}")
        print(f"   参数: MA={r.params['ma_period']}, 中枢高度={r.params['center_min_height']*100:.1f}%")
        print(f"   交易次数: {r.total_trades}")
        print(f"   胜率: {r.win_rate:.1f}%")
        print(f"   盈亏比: {r.profit_factor:.2f}")
        print(f"   总收益: {r.total_return:+.2f}%")
        print(f"   最大回撤: {r.max_drawdown:.2f}%")
        print(f"   夏普比率: {r.sharpe_ratio:.2f}")
    
    # 推荐参数
    best = top_results[0]
    print("\n" + "="*70)
    print("✅ 推荐参数")
    print("="*70)
    print(f"""
moer = MoerChanlun(
    ma_period={best.params['ma_period']},
    center_min_height={best.params['center_min_height']}
)
""")
    
    return top_results


if __name__ == "__main__":
    demo_optimize()
