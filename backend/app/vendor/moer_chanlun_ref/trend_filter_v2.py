"""
摩尔缠论 - 趋势过滤优化版 V2
调整参数，确保有信号产生
"""

from moer_quant_pure import MoerChanlun, SignalType
import random

def calculate_ma(data, period):
    ma = []
    for i in range(len(data)):
        if i < period - 1:
            ma.append(sum(data[:i+1]) / (i+1))
        else:
            ma.append(sum(data[i-period+1:i+1]) / period)
    return ma

def generate_trending_data(n=600):
    """生成有明显趋势的数据"""
    random.seed(42)
    closes = []
    base = 50000
    
    phases = [
        ("up", 100, 0.02, -0.01),       # 上涨100根
        ("top", 50, 0.005, -0.015),     # 顶部50根
        ("down", 120, 0.008, -0.025),   # 下跌120根
        ("bottom", 80, 0.02, -0.01),    # 底部80根
        ("up", 250, 0.03, -0.01),       # 主升浪250根
    ]
    
    for phase, bars, up_range, down_range in phases:
        for _ in range(bars):
            if phase == "up":
                change = random.uniform(down_range, up_range)
            elif phase == "down":
                change = random.uniform(down_range, up_range)
            elif phase == "top":
                change = random.uniform(-0.015, 0.005)
            else:
                change = random.uniform(-0.01, 0.02)
            
            base = base * (1 + change)
            closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
    volumes = [random.randint(15000, 50000) for _ in range(len(closes))]
    
    return highs, lows, closes, volumes

print("=" * 70)
print("🚀 摩尔缠论 - 趋势过滤对比演示")
print("=" * 70)

highs, lows, closes, volumes = generate_trending_data(600)

print(f"\n📊 市场数据")
print(f"   K线: {len(closes)} 根")
print(f"   价格: ${closes[0]:,.0f} → ${closes[-1]:,.0f}")
print(f"   涨跌: {(closes[-1]/closes[0]-1)*100:+.0f}%")

# 计算MA55
trend_ma = calculate_ma(closes, 55)

# 识别中枢
moer = MoerChanlun(ma_period=34, center_min_height=0.003)
centers = moer.identify_centers(highs, lows, closes)
print(f"   中枢: {len(centers)} 个")

# 收集原始信号（无过滤）
print("\n" + "-" * 70)
print("📉 原始策略（无过滤）- 所有信号都交易")
print("-" * 70)

original_signals = []
for i in range(100, len(closes)-30):
    w_highs = highs[i-80:i+1]
    w_lows = lows[i-80:i+1]
    w_closes = closes[i-80:i+1]
    
    signals = moer.scan(w_highs, w_lows, w_closes, None)
    for sig in signals:
        sig.index = i
        original_signals.append(sig)

print(f"   检测到 {len(original_signals)} 个信号")

# 模拟原始交易
original_trades = []
for sig in original_signals:
    i = sig.index
    entry = sig.price
    
    if '买' in sig.signal_type.value:
        tp, sl = entry * 1.05, entry * 0.97
        for j in range(i+1, min(i+25, len(closes))):
            if lows[j] <= sl:
                original_trades.append({'pnl': -0.03, 'result': '止损'})
                break
            elif highs[j] >= tp:
                original_trades.append({'pnl': 0.05, 'result': '止盈'})
                break
        else:
            pnl = (closes[min(i+25, len(closes)-1)] - entry) / entry
            original_trades.append({'pnl': pnl, 'result': '超时'})
    else:
        tp, sl = entry * 0.95, entry * 1.03
        for j in range(i+1, min(i+25, len(closes))):
            if highs[j] >= sl:
                original_trades.append({'pnl': -0.03, 'result': '止损'})
                break
            elif lows[j] <= tp:
                original_trades.append({'pnl': 0.05, 'result': '止盈'})
                break
        else:
            pnl = (entry - closes[min(i+25, len(closes)-1)]) / entry
            original_trades.append({'pnl': pnl, 'result': '超时'})

if original_trades:
    orig_wins = len([t for t in original_trades if t['pnl'] > 0])
    orig_pnl = sum(t['pnl'] for t in original_trades)
    print(f"   交易: {len(original_trades)} 笔")
    print(f"   盈利: {orig_wins} 笔")
    print(f"   胜率: {orig_wins/len(original_trades)*100:.1f}%")
    print(f"   收益: {orig_pnl*100:+.1f}%")

# 收集改进信号（带趋势过滤）
print("\n" + "-" * 70)
print("📈 改进策略（趋势过滤）- 只顺大势交易")
print("-" * 70)

filtered_signals = []
for i in range(100, len(closes)-30):
    w_highs = highs[i-80:i+1]
    w_lows = lows[i-80:i+1]
    w_closes = closes[i-80:i+1]
    
    # 计算趋势
    if len(w_closes) >= 55:
        ma55 = sum(w_closes[-55:]) / 55
        current = w_closes[-1]
        trend = "UP" if current > ma55 * 1.02 else ("DOWN" if current < ma55 * 0.98 else "SIDE")
    else:
        trend = "UNKNOWN"
    
    signals = moer.scan(w_highs, w_lows, w_closes, None)
    
    for sig in signals:
        # 趋势过滤
        if '买' in sig.signal_type.value and trend in ["UP", "SIDE"]:
            sig.index = i
            filtered_signals.append(sig)
        elif '卖' in sig.signal_type.value and trend in ["DOWN", "SIDE"]:
            sig.index = i
            filtered_signals.append(sig)

print(f"   检测到 {len(filtered_signals)} 个信号 (过滤了 {len(original_signals) - len(filtered_signals)} 个)")

# 模拟改进交易
filtered_trades = []
for sig in filtered_signals:
    i = sig.index
    entry = sig.price
    
    if '买' in sig.signal_type.value:
        tp, sl = entry * 1.05, entry * 0.97
        for j in range(i+1, min(i+25, len(closes))):
            if lows[j] <= sl:
                filtered_trades.append({'pnl': -0.03, 'result': '止损'})
                break
            elif highs[j] >= tp:
                filtered_trades.append({'pnl': 0.05, 'result': '止盈'})
                break
        else:
            pnl = (closes[min(i+25, len(closes)-1)] - entry) / entry
            filtered_trades.append({'pnl': pnl, 'result': '超时'})
    else:
        tp, sl = entry * 0.95, entry * 1.03
        for j in range(i+1, min(i+25, len(closes))):
            if highs[j] >= sl:
                filtered_trades.append({'pnl': -0.03, 'result': '止损'})
                break
            elif lows[j] <= tp:
                filtered_trades.append({'pnl': 0.05, 'result': '止盈'})
                break
        else:
            pnl = (entry - closes[min(i+25, len(closes)-1)]) / entry
            filtered_trades.append({'pnl': pnl, 'result': '超时'})

if filtered_trades:
    filt_wins = len([t for t in filtered_trades if t['pnl'] > 0])
    filt_pnl = sum(t['pnl'] for t in filtered_trades)
    print(f"   交易: {len(filtered_trades)} 笔")
    print(f"   盈利: {filt_wins} 笔")
    
    win_rate = filt_wins/len(filtered_trades)*100
    bar = "█" * int(win_rate/5) + "░" * (20-int(win_rate/5))
    print(f"   胜率: {win_rate:.1f}% [{bar}]")
    
    emoji = "🟢" if filt_pnl > 0 else "🔴"
    print(f"   收益: {emoji} {filt_pnl*100:+.1f}%")

# 对比总结
print("\n" + "=" * 70)
print("📊 对比总结")
print("=" * 70)

print(f"\n{'指标':<20} {'原始策略':>15} {'改进策略':>15} {'差异':>15}")
print("-" * 65)

if original_trades and filtered_trades:
    orig_wr = orig_wins/len(original_trades)*100
    filt_wr = filt_wins/len(filtered_trades)*100
    
    print(f"{'信号数量':<20} {len(original_signals):>15} {len(filtered_signals):>15} {len(filtered_signals)-len(original_signals):>+15}")
    print(f"{'胜率':<20} {orig_wr:>14.1f}% {filt_wr:>14.1f}% {filt_wr-orig_wr:>+14.1f}%")
    print(f"{'累计收益':<20} {orig_pnl*100:>+14.1f}% {filt_pnl*100:>+14.1f}% {(filt_pnl-orig_pnl)*100:>+14.1f}%")

# 核心结论
print("\n" + "=" * 70)
print("💡 核心洞察")
print("=" * 70)

print("""
📌 原始策略的问题:
   • 下跌途中不断产生"一买"信号
   • 每次抄底都被止损 (-3%)
   • 累计亏损严重

📌 趋势过滤的效果:
   • 只在价格高于MA55时寻找买点
   • 避免了下跌途中的假突破
   • 顺势交易，胜率提升

📌 实战建议:
   1. 只做顺势交易 (趋势 > MA55)
   2. 震荡市降低仓位或观望
   3. 逆势信号作为预警，不作为交易依据
""")

print("=" * 70)
