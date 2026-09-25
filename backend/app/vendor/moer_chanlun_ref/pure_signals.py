"""
摩尔缠论 - 纯信号版本（无趋势过滤）
专注于中枢识别和三类买卖点
"""

from moer_quant_pure import MoerChanlun, SignalType
import random

def generate_choppy_data(n=300):
    """生成震荡数据（更容易产生信号）"""
    random.seed(42)
    closes = []
    base = 50000
    
    # 明显的震荡区间，形成多个中枢
    for i in range(n):
        if i < 80:
            # 区间1: 48000-52000 震荡
            change = random.uniform(-0.015, 0.015)
        elif i < 160:
            # 区间2: 下跌到 40000-45000
            change = random.uniform(-0.02, 0.01)
        elif i < 240:
            # 区间3: 42000-48000 震荡
            change = random.uniform(-0.015, 0.018)
        else:
            # 区间4: 突破上涨
            change = random.uniform(-0.01, 0.025)
        
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.01)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.01)) for c in closes]
    volumes = [random.randint(10000, 50000) for _ in range(n)]
    
    return highs, lows, closes, volumes

print("=" * 70)
print("🎯 摩尔缠论 - 纯信号版本（无趋势过滤）")
print("=" * 70)
print()
print("特点:")
print("  ✅ 去除所有趋势过滤")
print("  ✅ 专注于中枢和背离")
print("  ✅ 产生可交易的信号")
print()

# 生成数据
highs, lows, closes, volumes = generate_choppy_data(300)

print("📊 市场数据")
print(f"   K线: {len(closes)} 根")
print(f"   价格: ${closes[0]:,.0f} → ${closes[-1]:,.0f}")
print(f"   最高: ${max(closes):,.0f}")
print(f"   最低: ${min(closes):,.0f}")

# 初始化（无趋势过滤）
moer = MoerChanlun(ma_period=34, center_min_height=0.002)

print("\n" + "-" * 70)
print("🔍 步骤1: 识别中枢")
print("-" * 70)

centers = moer.identify_centers(highs, lows, closes)
print(f"\n✅ 识别到 {len(centers)} 个中枢")

for i, c in enumerate(centers, 1):
    bar = "█" * int(c['height'] * 50)
    print(f"   中枢{i}: ZG=${c['zg']:,.0f} ZD=${c['zd']:,.0f} 高度{c['height']*100:.1f}% {bar}")

print("\n" + "-" * 70)
print("🔔 步骤2: 扫描所有信号（无过滤）")
print("-" * 70)

# 扫描所有信号
all_signals = []
for i in range(80, len(closes)-20):
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
signal_counts = {}
for sig in all_signals:
    name = sig.signal_type.value
    signal_counts[name] = signal_counts.get(name, 0) + 1

print(f"\n📈 信号分布:")
for stype, count in sorted(signal_counts.items()):
    print(f"   • {stype}: {count} 个")

# 显示前10个信号
print(f"\n📝 前10个信号详情:")
print(f"   {'#':>3} {'时间':>5} {'信号':>8} {'价格':>12} {'强度':>6} {'说明':>20}")
print(f"   {'-'*60}")

for i, sig in enumerate(all_signals[:10], 1):
    emoji = "🟢" if "买" in sig.signal_type.value else "🔴"
    print(f"   {emoji} {i:>2} {sig.index:>5} {sig.signal_type.value:>8} ${sig.price:>10,.0f} {sig.strength:>5} {sig.reason[:25]:>25}")

print("\n" + "-" * 70)
print("💰 步骤3: 模拟交易")
print("-" * 70)

# 简单回测
trades = []
for sig in all_signals:
    i = sig.index
    entry = sig.price
    
    # 统一止盈止损
    if '买' in sig.signal_type.value:
        tp = entry * 1.05  # +5%
        sl = entry * 0.97  # -3%
        
        for j in range(i+1, min(i+20, len(closes))):
            if lows[j] <= sl:
                trades.append({'signal': sig, 'pnl': -0.03, 'result': '止损'})
                break
            elif highs[j] >= tp:
                trades.append({'signal': sig, 'pnl': 0.05, 'result': '止盈'})
                break
        else:
            final = closes[min(i+20, len(closes)-1)]
            pnl = (final - entry) / entry
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

print("\n" + "=" * 70)
print("✅ 纯信号版本运行完成！")
print("=" * 70)
print("\n使用方法:")
print("  from moer_quant_pure import MoerChanlun")
print("  moer = MoerChanlun(ma_period=34, center_min_height=0.002)")
print("  signals = moer.scan(highs, lows, closes, volumes)")
print("  for sig in signals:")
print("      print(f'{sig.signal_type.value} @ {sig.price}')")
print("=" * 70)
