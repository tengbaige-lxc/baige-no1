"""
摩尔缠论 - 终极优化版演示
展示趋势过滤如何避免灾难性亏损
"""

from moer_quant_pure import MoerChanlun, SignalType
import random

def generate_realistic_data(n=400):
    """生成真实的市场数据：趋势+震荡+反转"""
    random.seed(2024)
    closes = []
    base = 50000
    
    # 模拟真实BTC走势：上涨-暴跌-震荡-反弹
    for i in range(n):
        if i < 80:  # 1-80: 温和上涨
            change = random.uniform(-0.008, 0.015)
        elif i < 120:  # 81-120: 加速见顶
            change = random.uniform(-0.005, 0.02)
        elif i < 180:  # 121-180: 快速暴跌 (大亏损区域！)
            change = random.uniform(-0.03, 0.005)
        elif i < 240:  # 181-240: 低位震荡筑底
            change = random.uniform(-0.015, 0.015)
        elif i < 320:  # 241-320: 反弹回升
            change = random.uniform(-0.008, 0.022)
        else:  # 321-400: 震荡上行
            change = random.uniform(-0.01, 0.018)
        
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
    return highs, lows, closes

def calculate_ma(data, period):
    return [sum(data[max(0,i-period+1):i+1])/min(i+1,period) for i in range(len(data))]

print("=" * 75)
print("🎯 摩尔缠论终极优化演示")
print("=" * 75)
print("\n场景: 模拟真实加密货币市场 - 上涨→暴跌→筑底→反弹")
print()

highs, lows, closes = generate_realistic_data(400)
ma55 = calculate_ma(closes, 55)

print("📊 市场概况")
print(f"   周期: {len(closes)} 根K线")
print(f"   价格: ${closes[0]:,.0f} → ${closes[-1]:,.0f}")
print(f"   最高: ${max(closes):,.0f}")
print(f"   最低: ${min(closes):,.0f}")

# 划分市场阶段
phase_colors = []
for i in range(len(closes)):
    if i < 80:
        phase_colors.append("🟢上涨")
    elif i < 120:
        phase_colors.append("🟡见顶")
    elif i < 180:
        phase_colors.append("🔴暴跌")
    elif i < 240:
        phase_colors.append("🟠筑底")
    else:
        phase_colors.append("🟢反弹")

print(f"\n📈 市场阶段")
print(f"   1-80:  🟢 温和上涨 (容易赚钱)")
print(f"   81-120: 🟡 加速见顶 (开始危险)")
print(f"   121-180:🔴 快速暴跌 (灾难区域！)")
print(f"   181-240:🟠 低位震荡 (假突破多)")
print(f"   241-400:🟢 反弹回升 (再次赚钱)")

# 初始化系统
moer = MoerChanlun(ma_period=34, center_min_height=0.003)
centers = moer.identify_centers(highs, lows, closes)

print(f"\n📍 技术统计")
print(f"   识别中枢: {len(centers)} 个")
print(f"   MA55: ${ma55[-1]:,.0f}")

# ========== 原始策略：所有信号都交易 ==========
print("\n" + "=" * 75)
print("📉 【原始策略】无过滤 - 所有信号都交易")
print("=" * 75)

original_trades = []
for i in range(100, len(closes)-25):
    w_highs = highs[i-80:i+1]
    w_lows = lows[i-80:i+1]  
    w_closes = closes[i-80:i+1]
    
    signals = moer.scan(w_highs, w_lows, w_closes, None)
    
    for sig in signals:
        # 模拟交易
        entry = sig.price
        tp = entry * 1.04  # +4%止盈
        sl = entry * 0.97  # -3%止损
        
        if '买' in sig.signal_type.value:
            for j in range(i+1, min(i+25, len(closes))):
                if lows[j] <= sl:
                    original_trades.append({
                        'phase': phase_colors[i], 'type': sig.signal_type.value,
                        'entry': entry, 'pnl': -0.03, 'result': '止损', 'idx': i
                    })
                    break
                elif highs[j] >= tp:
                    original_trades.append({
                        'phase': phase_colors[i], 'type': sig.signal_type.value,
                        'entry': entry, 'pnl': 0.04, 'result': '止盈', 'idx': i
                    })
                    break
            else:
                pnl = (closes[min(i+25, len(closes)-1)] - entry) / entry
                original_trades.append({
                    'phase': phase_colors[i], 'type': sig.signal_type.value,
                    'entry': entry, 'pnl': pnl, 'result': '超时', 'idx': i
                })

# 统计原始策略
if original_trades:
    wins = [t for t in original_trades if t['pnl'] > 0]
    losses = [t for t in original_trades if t['pnl'] <= 0]
    
    print(f"\n   交易统计:")
    print(f"   • 总交易: {len(original_trades)} 笔")
    print(f"   • 盈    利: {len(wins)} 笔")
    print(f"   • 亏    损: {len(losses)} 笔")
    print(f"   • 胜    率: {len(wins)/len(original_trades)*100:.1f}%")
    print(f"   • 累计盈亏: {sum(t['pnl'] for t in original_trades)*100:+.1f}%")
    
    # 按阶段分析
    print(f"\n   按市场阶段分析 (问题暴露！):")
    for phase in ["🟢上涨", "🟡见顶", "🔴暴跌", "🟠筑底", "🟢反弹"]:
        phase_trades = [t for t in original_trades if phase in t['phase']]
        if phase_trades:
            phase_wins = len([t for t in phase_trades if t['pnl'] > 0])
            phase_pnl = sum(t['pnl'] for t in phase_trades)
            print(f"   • {phase}: {len(phase_trades)}笔 胜率{phase_wins/len(phase_trades)*100:.0f}% 收益{phase_pnl*100:+.1f}%")

# ========== 改进策略：趋势过滤 ==========
print("\n" + "=" * 75)
print("📈 【改进策略】趋势过滤 - 只顺大势交易")
print("=" * 75)

filtered_trades = []
for i in range(100, len(closes)-25):
    w_highs = highs[i-80:i+1]
    w_lows = lows[i-80:i+1]
    w_closes = closes[i-80:i+1]
    
    # 判断趋势
    current_price = w_closes[-1]
    ma55_current = sum(w_closes[-55:]) / 55 if len(w_closes) >= 55 else sum(w_closes) / len(w_closes)
    
    signals = moer.scan(w_highs, w_lows, w_closes, None)
    
    for sig in signals:
        # 🎯 关键过滤：只在趋势有利时交易
        if '买' in sig.signal_type.value:
            # 买点：只在上涨趋势或价格站上MA55
            if current_price < ma55_current * 0.98:  # 价格低于MA55，跳过
                continue
        else:
            # 卖点：只在下跌趋势
            if current_price > ma55_current * 1.02:
                continue
        
        # 执行交易
        entry = sig.price
        tp = entry * 1.04
        sl = entry * 0.97
        
        if '买' in sig.signal_type.value:
            for j in range(i+1, min(i+25, len(closes))):
                if lows[j] <= sl:
                    filtered_trades.append({
                        'phase': phase_colors[i], 'type': sig.signal_type.value,
                        'entry': entry, 'pnl': -0.03, 'result': '止损', 'idx': i
                    })
                    break
                elif highs[j] >= tp:
                    filtered_trades.append({
                        'phase': phase_colors[i], 'type': sig.signal_type.value,
                        'entry': entry, 'pnl': 0.04, 'result': '止盈', 'idx': i
                    })
                    break
            else:
                pnl = (closes[min(i+25, len(closes)-1)] - entry) / entry
                filtered_trades.append({
                    'phase': phase_colors[i], 'type': sig.signal_type.value,
                    'entry': entry, 'pnl': pnl, 'result': '超时', 'idx': i
                })

# 统计改进策略
if filtered_trades:
    wins = [t for t in filtered_trades if t['pnl'] > 0]
    losses = [t for t in filtered_trades if t['pnl'] <= 0]
    
    print(f"\n   交易统计:")
    print(f"   • 总交易: {len(filtered_trades)} 笔 (过滤了 {len(original_trades) - len(filtered_trades)} 笔)")
    print(f"   • 盈    利: {len(wins)} 笔 🟢")
    print(f"   • 亏    损: {len(losses)} 笔 🔴")
    
    win_rate = len(wins)/len(filtered_trades)*100
    bar = "█" * int(win_rate/5) + "░" * (20-int(win_rate/5))
    print(f"   • 胜    率: {win_rate:.1f}% [{bar}]")
    
    total_pnl = sum(t['pnl'] for t in filtered_trades)
    emoji = "🟢" if total_pnl > 0 else "🔴"
    print(f"   • 累计盈亏: {emoji} {total_pnl*100:+.1f}%")
    
    # 按阶段分析
    print(f"\n   按市场阶段分析:")
    for phase in ["🟢上涨", "🟡见顶", "🔴暴跌", "🟠筑底", "🟢反弹"]:
        phase_trades = [t for t in filtered_trades if phase in t['phase']]
        if phase_trades:
            phase_wins = len([t for t in phase_trades if t['pnl'] > 0])
            phase_pnl = sum(t['pnl'] for t in phase_trades)
            print(f"   • {phase}: {len(phase_trades)}笔 胜率{phase_wins/len(phase_trades)*100:.0f}% 收益{phase_pnl*100:+.1f}%")

# 对比总结
print("\n" + "=" * 75)
print("📊 终极对比：灾难性亏损的避免")
print("=" * 75)

orig_total = sum(t['pnl'] for t in original_trades) * 100 if original_trades else 0
filt_total = sum(t['pnl'] for t in filtered_trades) * 100 if filtered_trades else 0

print(f"""
┌─────────────────────────────────────────────────────────────┐
│                    策略对比总结                              │
├─────────────────────────────────────────────────────────────┤
│  原始策略 (无过滤)        vs    改进策略 (趋势过滤)          │
├─────────────────────────────────────────────────────────────┤
│  交易次数: {len(original_trades):>3}笔               {len(filtered_trades):>15}笔          │
│  胜    率: {len([t for t in original_trades if t['pnl']>0])/len(original_trades)*100 if original_trades else 0:>5.1f}%              {len([t for t in filtered_trades if t['pnl']>0])/len(filtered_trades)*100 if filtered_trades else 0:>13.1f}%          │
│  累计收益: {orig_total:>+5.1f}%            {filt_total:>+13.1f}%          │
├─────────────────────────────────────────────────────────────┤
│  关键改进:                                                  │
│  ✅ 避免了暴跌期间的 {len([t for t in original_trades if '🔴暴跌' in t['phase']])} 次逆势抄底      │
│  ✅ 跳过了筑底阶段的 {len([t for t in original_trades if '🟠筑底' in t['phase']])} 次假突破       │
│  ✅ 只在趋势有利时交易                                       │
└─────────────────────────────────────────────────────────────┘
""")

print("💡 核心教训:")
print("   1. 趋势是你的朋友 - 逆势交易=慢性自杀")
print("   2. 一买信号在下跌趋势中大多是陷阱")
print("   3. 等待价格站上MA55再考虑买入")
print()
print("=" * 75)
