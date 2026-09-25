"""
摩尔缠论量化交易系统 - 演示运行版
使用真实价格模式展示中枢识别和买卖点检测
"""

from moer_quant_pure import MoerChanlun, SignalType
import random
import json

print("=" * 70)
print("  摩尔缠论量化交易系统 - 实战演示")
print("=" * 70)

# 生成模拟真实市场数据
random.seed(42)
n = 150
closes = []
base = 50000  # BTC-like price

print("\n📈 生成模拟数据...")
for i in range(n):
    if i < 50:
        change = random.uniform(-0.02, 0.015)  # 震荡下行
    elif i < 80:
        change = random.uniform(-0.025, 0.01)  # 快速下跌
    elif i < 110:
        change = random.uniform(-0.01, 0.025)  # 反弹回升
    else:
        change = random.uniform(-0.015, 0.02)  # 震荡上行
    
    base = base * (1 + change)
    closes.append(base)

highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
volumes = [random.randint(10000, 50000) for _ in range(n)]

print(f"   K线数量: {n}")
print(f"   价格范围: {min(closes):,.2f} - {max(closes):,.2f}")
print(f"   最新价格: {closes[-1]:,.2f}")

# 初始化系统
moer = MoerChanlun(ma_period=34, center_min_height=0.005)  # 稍微降低中枢高度要求

# 识别中枢
print("\n" + "-" * 70)
print("🔍 步骤1: 识别中枢")
print("-" * 70)

centers = moer.identify_centers(highs, lows, closes)
print(f"\n✅ 识别到 {len(centers)} 个中枢")

for i, c in enumerate(centers, 1):
    bar = "█" * int(c['height'] * 500)
    print(f"\n   中枢 {i}:")
    print(f"      ZG (上轨): {c['zg']:,.2f}")
    print(f"      ZD (下轨): {c['zd']:,.2f}")
    print(f"      高度: {c['height']*100:.2f}% {bar}")
    print(f"      K线范围: {c['start_idx']} - {c['end_idx']}")

# 扫描信号
print("\n" + "-" * 70)
print("🔔 步骤2: 扫描买卖信号")
print("-" * 70)

signals = moer.scan(highs, lows, closes, volumes)

if signals:
    print(f"\n✅ 检测到 {len(signals)} 个交易信号\n")
    
    for sig in signals:
        emoji = "🟢" if "买" in sig.signal_type.value else "🔴"
        strength_bar = "█" * (sig.strength // 10) + "░" * (10 - sig.strength // 10)
        
        print(f"{emoji} {'='*50}")
        print(f"   信号类型: {sig.signal_type.value}")
        print(f"   触发价格: {sig.price:,.2f}")
        print(f"   信号强度: [{strength_bar}] {sig.strength}/100")
        print(f"   止损价格: {sig.stop_loss:,.2f} ({(sig.stop_loss/sig.price-1)*100:+.1f}%)")
        print(f"   止盈价格: {sig.take_profit:,.2f} ({(sig.take_profit/sig.price-1)*100:+.1f}%)")
        print(f"   触发原因: {sig.reason}")
        print(f"{'='*50}\n")
else:
    print("\n⏳ 当前无交易信号")
    print("   可能原因:")
    print("   • 价格未突破中枢区间")
    print("   • MACD未形成有效背离")
    print("   • 量能配合不足")
    print("\n   建议: 继续监控等待机会")

# 显示MACD状态
print("\n" + "-" * 70)
print("📊 步骤3: MACD指标状态")
print("-" * 70)

macd_data = moer.calculate_macd(closes)
macd_current = macd_data['macd'][-1]
signal_current = macd_data['signal'][-1]
hist_current = macd_data['histogram'][-1]

print(f"\n   MACD线: {macd_current:,.2f}")
print(f"   信号线: {signal_current:,.2f}")
print(f"   柱状图: {hist_current:,.2f} {'📈 向上' if hist_current > hist_current * 0.9 else '📉 向下'}")
print(f"   零轴位置: {'上方 ✅' if macd_current > 0 else '下方 ⚠️'}")

# 背离检测
print("\n" + "-" * 70)
print("🔎 步骤4: 背离检测结果")
print("-" * 70)

bull_div = moer.detect_bullish_divergence(lows, macd_data['histogram'], 20)
bear_div = moer.detect_bearish_divergence(highs, macd_data['histogram'], 20)

print(f"\n   底背离 (看涨): {'✅ 检测到' if bull_div else '❌ 未检测到'}")
print(f"   顶背离 (看跌): {'✅ 检测到' if bear_div else '❌ 未检测到'}")

# 交易建议
print("\n" + "=" * 70)
print("💡 交易建议")
print("=" * 70)

if signals:
    for sig in signals:
        if "买" in sig.signal_type.value:
            print(f"\n   🟢 买入机会: {sig.signal_type.value}")
            print(f"      • 建议在 {sig.price:,.2f} 附近入场")
            print(f"      • 严格止损: {sig.stop_loss:,.2f}")
            print(f"      • 第一目标: {sig.take_profit:,.2f}")
            if sig.strength >= 70:
                print(f"      • 信号强度优秀，可考虑重仓")
            elif sig.strength >= 50:
                print(f"      • 信号强度一般，轻仓试探")
            else:
                print(f"      • 信号偏弱，建议观望")
        else:
            print(f"\n   🔴 卖出信号: {sig.signal_type.value}")
            print(f"      • 建议减仓或离场")
            print(f"      • 保护利润，设置追踪止损")
else:
    print("\n   ⏸️ 当前处于观望状态")
    print("   • 等待价格进入中枢区间")
    print("   • 等待MACD背离形成")
    print("   • 等待量能配合")

print("\n" + "=" * 70)
print("演示结束！这是基于模拟数据的运行结果。")
print("实盘使用: from moer_quant_pure import MoerChanlun")
print("=" * 70)
