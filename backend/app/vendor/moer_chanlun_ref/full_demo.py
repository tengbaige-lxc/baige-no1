"""
摩尔缠论 - 完整可视化演示
生成HTML报告展示所有信号和交易
"""

from moer_quant_pure import MoerChanlun, SignalType
import random

def generate_data(n=400):
    """生成有明显中枢和背离的数据"""
    random.seed(42)
    closes = []
    base = 50000
    
    # 构造特定阶段来产生信号
    for i in range(n):
        if i < 100:  # 阶段1: 上涨后盘整
            change = random.uniform(-0.008, 0.012)
        elif i < 150:  # 阶段2: 快速下跌（找一买）
            change = random.uniform(-0.025, 0.005)
        elif i < 200:  # 阶段3: 反弹回升
            change = random.uniform(-0.005, 0.02)
        elif i < 280:  # 阶段4: 横盘震荡（形成中枢）
            change = random.uniform(-0.01, 0.01)
        else:  # 阶段5: 突破上涨（找三买）
            change = random.uniform(-0.005, 0.025)
        
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
    volumes = [random.randint(10000, 50000) for _ in range(n)]
    
    return highs, lows, closes, volumes

print("=" * 70)
print("🎯 摩尔缠论完整回测演示")
print("=" * 70)

highs, lows, closes, volumes = generate_data(400)

print(f"\n📊 数据概览")
print(f"   K线数量: {len(closes)}")
print(f"   起始价格: ${closes[0]:,.0f}")
print(f"   结束价格: ${closes[-1]:,.0f}")
print(f"   总涨跌: {(closes[-1]/closes[0]-1)*100:+.1f}%")

moer = MoerChanlun(ma_period=34, center_min_height=0.003)

# 识别中枢
centers = moer.identify_centers(highs, lows, closes)
print(f"\n📍 中枢统计: {len(centers)} 个")

# 滑动扫描所有信号
print(f"\n🔔 扫描信号中...")

all_signals = []
trade_results = []

for i in range(100, len(closes)-30):
    w_highs = highs[i-80:i+1]
    w_lows = lows[i-80:i+1]
    w_closes = closes[i-80:i+1]
    w_volumes = volumes[i-80:i+1]
    
    signals = moer.scan(w_highs, w_lows, w_closes, w_volumes)
    
    for sig in signals:
        sig.index = i
        all_signals.append(sig)
        
        # 模拟交易
        entry = sig.price
        
        # 简化的止盈止损（固定比例）
        if '买' in sig.signal_type.value:
            tp = entry * 1.04  # +4%
            sl = entry * 0.97  # -3%
            
            # 检查未来30根K线
            for j in range(i+1, min(i+31, len(closes))):
                if lows[j] <= sl:
                    trade_results.append({
                        'signal': sig, 'entry': entry, 'exit': sl,
                        'pnl': -0.03, 'result': '止损', 'bars': j-i
                    })
                    break
                elif highs[j] >= tp:
                    trade_results.append({
                        'signal': sig, 'entry': entry, 'exit': tp,
                        'pnl': 0.04, 'result': '止盈', 'bars': j-i
                    })
                    break
            else:
                # 超时
                final = closes[min(i+30, len(closes)-1)]
                pnl = (final - entry) / entry
                trade_results.append({
                    'signal': sig, 'entry': entry, 'exit': final,
                    'pnl': pnl, 'result': '超时', 'bars': 30
                })
        else:
            tp = entry * 0.96  # -4%
            sl = entry * 1.03  # +3%
            
            for j in range(i+1, min(i+31, len(closes))):
                if highs[j] >= sl:
                    trade_results.append({
                        'signal': sig, 'entry': entry, 'exit': sl,
                        'pnl': -0.03, 'result': '止损', 'bars': j-i
                    })
                    break
                elif lows[j] <= tp:
                    trade_results.append({
                        'signal': sig, 'entry': entry, 'exit': tp,
                        'pnl': 0.04, 'result': '止盈', 'bars': j-i
                    })
                    break
            else:
                final = closes[min(i+30, len(closes)-1)]
                pnl = (entry - final) / entry
                trade_results.append({
                    'signal': sig, 'entry': entry, 'exit': final,
                    'pnl': pnl, 'result': '超时', 'bars': 30
                })

print(f"   检测到 {len(all_signals)} 个信号")
print(f"   完成 {len(trade_results)} 笔模拟交易")

# 统计
if trade_results:
    wins = [t for t in trade_results if t['pnl'] > 0]
    losses = [t for t in trade_results if t['pnl'] <= 0]
    
    win_rate = len(wins) / len(trade_results) * 100
    total_pnl = sum(t['pnl'] for t in trade_results) * 100
    
    print(f"\n{'='*70}")
    print("📊 回测结果汇总")
    print(f"{'='*70}")
    
    print(f"\n💰 总体表现")
    print(f"   交易次数: {len(trade_results)}")
    print(f"   盈利次数: {len(wins)} 🟢")
    print(f"   亏损次数: {len(losses)} 🔴")
    
    bar = "█" * int(win_rate / 5) + "░" * (20 - int(win_rate / 5))
    print(f"   胜率: {win_rate:.1f}% [{bar}]")
    
    avg_win = sum(t['pnl'] for t in wins) / len(wins) * 100 if wins else 0
    avg_loss = sum(t['pnl'] for t in losses) / len(losses) * 100 if losses else 0
    
    print(f"   平均盈利: +{avg_win:.2f}%")
    print(f"   平均亏损: {avg_loss:.2f}%")
    print(f"   盈亏比: {abs(avg_win/avg_loss):.2f}:1")
    print(f"   累计收益: {total_pnl:+.2f}%")
    
    # 按信号类型
    print(f"\n🎯 按信号类型")
    for st in ['一买', '一卖', '二买', '二卖', '三买', '三卖']:
        type_trades = [t for t in trade_results if st in t['signal'].signal_type.value]
        if type_trades:
            type_wins = len([t for t in type_trades if t['pnl'] > 0])
            type_pnl = sum(t['pnl'] for t in type_trades)
            print(f"   {st}: {len(type_trades)}笔 胜率{type_wins/len(type_trades)*100:.0f}% 累计{type_pnl*100:+.2f}%")
    
    # 交易明细
    print(f"\n📝 全部交易明细")
    print(f"   {'#':>3s} {'时间':>5s} {'信号':>8s} {'入场':>12s} {'出场':>12s} {'盈亏%':>8s} {'持仓':>5s} {'结果':>6s}")
    print(f"   {'-'*70}")
    
    for i, t in enumerate(trade_results, 1):
        emoji = "🟢" if t['pnl'] > 0 else "🔴"
        print(f"   {emoji} {i:>2d} {t['signal'].index:>5d} {t['signal'].signal_type.value:>8s} ${t['entry']:>10,.0f} ${t['exit']:>10,.0f} {t['pnl']*100:>+7.2f}% {t['bars']:>4d}K {t['result']:>6s}")

print(f"\n{'='*70}")
print("演示完成！")
print(f"{'='*70}")

# 生成HTML报告
html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>摩尔缠论回测报告</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1000px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; }}
        h1 {{ color: #333; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }}
        .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
        .stat-box {{ background: #f0f0f0; padding: 20px; border-radius: 8px; text-align: center; }}
        .stat-value {{ font-size: 32px; font-weight: bold; color: #4CAF50; }}
        .stat-label {{ color: #666; margin-top: 5px; }}
        .win {{ color: #4CAF50; }}
        .loss {{ color: #f44336; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th {{ background: #4CAF50; color: white; padding: 12px; text-align: left; }}
        td {{ padding: 10px; border-bottom: 1px solid #ddd; }}
        tr:hover {{ background: #f5f5f5; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 摩尔缠论回测报告</h1>
        
        <div class="stats">
            <div class="stat-box">
                <div class="stat-value">{len(trade_results)}</div>
                <div class="stat-label">总交易</div>
            </div>
            <div class="stat-box">
                <div class="stat-value {'win' if win_rate >= 50 else 'loss'}">{win_rate:.1f}%</div>
                <div class="stat-label">胜率</div>
            </div>
            <div class="stat-box">
                <div class="stat-value {'win' if total_pnl > 0 else 'loss'}">{total_pnl:+.1f}%</div>
                <div class="stat-label">累计收益</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{len(centers)}</div>
                <div class="stat-label">识别中枢</div>
            </div>
        </div>
        
        <h2>交易明细</h2>
        <table>
            <tr>
                <th>#</th>
                <th>信号</th>
                <th>入场价</th>
                <th>出场价</th>
                <th>盈亏</th>
                <th>持仓</th>
                <th>结果</th>
            </tr>
"""

for i, t in enumerate(trade_results, 1):
    pnl_class = 'win' if t['pnl'] > 0 else 'loss'
    html += f"""
            <tr>
                <td>{i}</td>
                <td>{t['signal'].signal_type.value}</td>
                <td>${t['entry']:,.0f}</td>
                <td>${t['exit']:,.0f}</td>
                <td class="{pnl_class}">{t['pnl']*100:+.2f}%</td>
                <td>{t['bars']}K</td>
                <td>{t['result']}</td>
            </tr>
"""

html += """
        </table>
    </div>
</body>
</html>
"""

with open('/root/.openclaw/workspace/moer-chanlun/backtest_report.html', 'w') as f:
    f.write(html)

print(f"\n📄 HTML报告已生成: moer-chanlun/backtest_report.html")
