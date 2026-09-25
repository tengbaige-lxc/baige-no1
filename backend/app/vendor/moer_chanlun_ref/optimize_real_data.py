"""
摩尔缠论 - 真实数据参数优化
使用OKX历史数据优化参数
"""

import json
from datetime import datetime
from parameter_optimizer import ParameterOptimizer, BacktestResult
from realtime_data import RealtimeDataManager

def optimize_with_real_data(symbol: str = "BTC-USDT", days: int = 30):
    """使用真实数据优化参数"""
    
    print("="*70)
    print(f"🎯 真实数据参数优化 - {symbol}")
    print("="*70)
    
    # 获取OKX历史数据（1小时K线，约720根 = 30天）
    print(f"\n📡 获取 {symbol} {days} 天历史数据...")
    
    data_mgr = RealtimeDataManager("okx")
    
    # 获取4小时数据（减少噪音）
    highs, lows, closes, volumes = data_mgr.fetch_data(
        symbol, "4H", limit=min(days*6, 500)
    )
    
    if not closes:
        print("❌ 数据获取失败")
        return None
    
    print(f"   数据点: {len(closes)} 根K线")
    print(f"   时间跨度: {days} 天")
    print(f"   价格范围: ${min(closes):,.0f} - ${max(closes):,.0f}")
    
    # 定义优化参数范围
    param_grid = {
        'ma_period': [13, 21, 34],           # 更灵活的周期
        'center_min_height': [0.005, 0.01, 0.015, 0.02],  # 0.5%-2%
    }
    
    optimizer = ParameterOptimizer()
    optimizer.param_grid = param_grid
    
    # 运行优化
    print(f"\n🔄 开始参数优化...")
    top_results = optimizer.optimize(highs, lows, closes, volumes, top_n=3)
    
    # 保存结果
    save_results(symbol, top_results)
    
    return top_results

def save_results(symbol: str, results: list):
    """保存优化结果"""
    output = {
        "symbol": symbol,
        "timestamp": datetime.now().isoformat(),
        "top_results": []
    }
    
    for r in results:
        output["top_results"].append({
            "params": r.params,
            "score": r.score,
            "win_rate": r.win_rate,
            "profit_factor": r.profit_factor,
            "total_return": r.total_return,
            "max_drawdown": r.max_drawdown,
            "sharpe_ratio": r.sharpe_ratio,
            "total_trades": r.total_trades
        })
    
    filename = f"/root/.openclaw/workspace/moer-chanlun/optimization_{symbol.replace('-', '_')}.json"
    with open(filename, 'w') as f:
        json.dump(output, f, indent=2)
    
    print(f"\n💾 结果已保存: {filename}")

def compare_params(symbol: str = "BTC-USDT"):
    """对比默认参数vs优化参数"""
    
    print("="*70)
    print(f"📊 参数对比测试 - {symbol}")
    print("="*70)
    
    # 获取数据
    data_mgr = RealtimeDataManager("okx")
    highs, lows, closes, volumes = data_mgr.fetch_data(symbol, "4H", limit=200)
    
    if not closes:
        print("❌ 数据获取失败")
        return
    
    # 测试不同参数
    test_params = [
        {"name": "默认参数", "ma_period": 34, "center_min_height": 0.002},
        {"name": "优化参数A", "ma_period": 21, "center_min_height": 0.01},
        {"name": "优化参数B", "ma_period": 13, "center_min_height": 0.015},
    ]
    
    optimizer = ParameterOptimizer()
    
    print(f"\n{'='*70}")
    print("对比结果:")
    print(f"{'='*70}")
    
    for p in test_params:
        params = {
            'ma_period': p['ma_period'],
            'center_min_height': p['center_min_height']
        }
        result = optimizer.run_backtest(highs, lows, closes, volumes, params)
        
        print(f"\n{p['name']}:")
        print(f"   参数: MA={p['ma_period']}, 中枢高度={p['center_min_height']*100:.1f}%")
        print(f"   交易次数: {result.total_trades}")
        print(f"   胜率: {result.win_rate:.1f}%")
        print(f"   盈亏比: {result.profit_factor:.2f}")
        print(f"   总收益: {result.total_return:+.2f}%")
        print(f"   最大回撤: {result.max_drawdown:.2f}%")
        print(f"   综合评分: {result.score:.2f}")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--compare":
        # 对比模式
        symbol = sys.argv[2] if len(sys.argv) > 2 else "BTC-USDT"
        compare_params(symbol)
    else:
        # 优化模式
        print("使用方法:")
        print("  python3 optimize_real_data.py              # 优化参数")
        print("  python3 optimize_real_data.py --compare BTC-USDT  # 对比参数")
        print()
        
        # 默认优化BTC
        results = optimize_with_real_data("BTC-USDT", days=30)
        
        if results:
            print("\n" + "="*70)
            print("✅ 优化完成！使用以下参数:")
            print("="*70)
            best = results[0]
            print(f"""
moer = MoerChanlun(
    ma_period={best.params['ma_period']},
    center_min_height={best.params['center_min_height']}
)
""")
