"""
摩尔缠论 - 完整系统演示
包含：二买/二卖 + 可视化图表
"""

from moer_quant_pure import MoerChanlun, SignalType
from visualization import generate_html_chart, create_simple_visualization
import random

def generate_complete_data(n=300):
    """生成能产生所有类型信号的数据"""
    random.seed(42)
    closes = []
    base = 50000
    
    # 构造完整的市场周期
    for i in range(n):
        if i < 60:
            # 阶段1: 下跌（找一买）
            change = random.uniform(-0.025, 0.005)
        elif i < 100:
            # 阶段2: 反弹后回抽（找二买）
            change = random.uniform(-0.01, 0.02)
        elif i < 160:
            # 阶段3: 震荡形成中枢
            change = random.uniform(-0.015, 0.015)
        elif i < 220:
            # 阶段4: 突破上涨（找三买）
            change = random.uniform(-0.008, 0.025)
        elif i < 260:
            # 阶段5: 见顶下跌（找一卖/二卖）
            change = random.uniform(-0.02, 0.008)
        else:
            # 阶段6: 震荡
            change = random.uniform(-0.012, 0.012)
        
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
    volumes = [random.randint(10000, 50000) for _ in range(n)]
    
    return highs, lows, closes, volumes

print("=" * 70)
print("🎯 摩尔缠论完整系统演示")
print("=" * 70)
print("\n包含功能:")
print("  ✅ 中枢识别")
print("  ✅ 一买/一卖（背离+突破）")
print("  ✅ 二买/二卖（回抽确认）")
print("  ✅ 三买/三卖（趋势延续）")
print("  ✅ 可视化图表")
print()

# 生成数据
highs, lows, closes, volumes = generate_complete_data(300)

print("📊 数据概览")
print(f"   K线: {len(closes)} 根")
print(f"   价格: ${closes[0]:,.0f} → ${closes[-1]:,.0f}")
print(f"   最高: ${max(closes):,.0f}")
print(f"   最低: ${min(closes):,.0f}")

# 初始化系统
moer = MoerChanlun(ma_period=34, center_min_height=0.002)

print("\n" + "-" * 70)
print("🔍 步骤1: 识别中枢")
print("-" * 70)

centers = moer.identify_centers(highs, lows, closes)
print(f"\n✅ 识别到 {len(centers)} 个中枢")

for i, c in enumerate(centers[-5:], 1):
    bar = "█" * int(c['height'] * 30)
    print(f"   中枢{i}: ZG=${c['zg']:,.0f} ZD=${c['zd']:,.0f} 高度{c['height']*100:.1f}% {bar}")

print("\n" + "-" * 70)
print("🔔 步骤2: 扫描所有买卖点（含二买/二卖）")
print("-" * 70)

# 滑动窗口扫描
all_signals = []
for i in range(80, len(closes)-10):
    w_highs = highs[i-80:i+1]
    w_lows = lows[i-80:i+1]
    w_closes = closes[i-80:i+1]
    w_volumes = volumes[i-80:i+1]
    
    signals = moer.scan(w_highs, w_lows, w_closes, w_volumes)
    
    for sig in signals:
        sig.index = i
        all_signals.append(sig)

print(f"\n✅ 检测到 {len(all_signals)} 个信号")

# 按类型统计
signal_types = {}
for sig in all_signals:
    name = sig.signal_type.value
    signal_types[name] = signal_types.get(name, 0) + 1

print(f"\n📈 信号分布:")
for stype, count in sorted(signal_types.items()):
    emoji = "🟢" if "买" in stype else "🔴"
    star = "🌟" if "二" in stype else "  "
    print(f"   {star}{emoji} {stype}: {count} 个")

# 显示详细信号
print(f"\n📝 信号详情:")
print(f"   {'#':>3} {'时间':>6} {'类型':>8} {'价格':>12} {'强度':>6} {'说明':>30}")
print(f"   {'-'*70}")

for i, sig in enumerate(all_signals[:20], 1):
    emoji = "🟢" if "买" in sig.signal_type.value else "🔴"
    star = "🌟" if "二" in sig.signal_type.value else "  "
    print(f"   {star}{emoji} {i:>2} {sig.index:>6} {sig.signal_type.value:>8} ${sig.price:>10,.0f} {sig.strength:>5} {sig.reason[:35]:>35}")

if len(all_signals) > 20:
    print(f"   ... 还有 {len(all_signals)-20} 个信号")

# 生成可视化图表
print("\n" + "-" * 70)
print("📊 步骤3: 生成可视化图表")
print("-" * 70)

chart_path = "/root/.openclaw/workspace/moer-chanlun/chart.html"
output = create_simple_visualization(highs, lows, closes, volumes, all_signals, centers, chart_path)

print(f"\n✅ 图表已生成: {output}")
print(f"   包含:")
print(f"   • K线图 ({len(closes)}根)")
print(f"   • 中枢区间 ({len(centers)}个)")
print(f"   • 买卖信号 ({len(all_signals)}个)")
print(f"   • 止损止盈标记")

# 回测
print("\n" + "-" * 70)
print("💰 步骤4: 简单回测")
print("-" * 70)

trades = []
for sig in all_signals:
    i = sig.index
    if i >= len(closes) - 10:
        continue
    
    entry = sig.price
    
    if '买' in sig.signal_type.value:
        tp = entry * 1.05
        sl = entry * 0.97
        
        for j in range(i+1, min(i+20, len(closes))):
            if lows[j] <= sl:
                trades.append({'signal': sig, 'pnl': -0.03, 'result': '止损'})
                break
            elif highs[j] >= tp:
                trades.append({'signal': sig, 'pnl': 0.05, 'result': '止盈'})
                break
        else:
            pnl = (closes[min(i+20, len(closes)-1)] - entry) / entry
            trades.append({'signal': sig, 'pnl': pnl, 'result': '超时'})
    else:
        tp = entry * 0.95
        sl = entry * 1.03
        
        for j in range(i+1, min(i+20, len(closes))):
            if highs[j] >= sl:
                trades.append({'signal': sig, 'pnl': -0.03, 'result': '止损'})
                break
            elif lows[j] <= tp:
                trades.append({'signal': sig, 'pnl': 0.05, 'result': '止盈'})
                break
        else:
            pnl = (entry - closes[min(i+20, len(closes)-1)]) / entry
            trades.append({'signal': sig, 'pnl': pnl, 'result': '超时'})

if trades:
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    total_pnl = sum(t['pnl'] for t in trades)
    
    print(f"\n📊 回测结果:")
    print(f"   总交易: {len(trades)} 笔")
    print(f"   盈利: {len(wins)} 笔 🟢")
    print(f"   亏损: {len(losses)} 笔 🔴")
    
    win_rate = len(wins) / len(trades) * 100
    bar = "█" * int(win_rate / 5) + "░" * (20 - int(win_rate / 5))
    print(f"   胜率: {win_rate:.1f}% [{bar}]")
    
    emoji = "🟢" if total_pnl > 0 else "🔴"
    print(f"   累计收益: {emoji} {total_pnl*100:+.2f}%")
    
    # 按信号类型统计
    print(f"\n📈 按信号类型:")
    type_pnl = {}
    for t in trades:
        stype = t['signal'].signal_type.value
        if stype not in type_pnl:
            type_pnl[stype] = []
        type_pnl[stype].append(t['pnl'])
    
    for stype, pnls in sorted(type_pnl.items()):
        total = sum(pnls)
        win_count = len([p for p in pnls if p > 0])
        print(f"   {stype}: {len(pnls)}笔 胜率{win_count/len(pnls)*100:.0f}% 收益{total*100:+.2f}%")

print("\n" + "=" * 70)
print("✅ 完整系统演示完成！")
print("=" * 70)
print("\n生成文件:")
print(f"  • {output}")
print(f"\n请在浏览器中打开查看可视化图表")
print("=" * 70)
