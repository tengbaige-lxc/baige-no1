"""
摩尔缠论 - 趋势过滤优化版
核心改进: 只在上涨趋势中找买点，下跌趋势中找卖点
"""

from moer_quant_pure import MoerChanlun, SignalType
import random

def calculate_ma(data, period):
    """计算简单移动平均"""
    ma = []
    for i in range(len(data)):
        if i < period - 1:
            ma.append(sum(data[:i+1]) / (i+1))
        else:
            ma.append(sum(data[i-period+1:i+1]) / period)
    return ma

def generate_trending_data(n=500):
    """生成有明显趋势的数据"""
    random.seed(2024)
    closes = []
    base = 50000
    
    # 构造清晰的趋势阶段
    phases = [
        ("up", 80, 0.018, -0.008),      # 上涨80根
        ("top", 40, 0.005, -0.015),     # 顶部40根  
        ("down", 100, 0.008, -0.022),   # 下跌100根
        ("bottom", 60, 0.015, -0.012),  # 底部60根
        ("up", 220, 0.025, -0.01),      # 主升浪220根
    ]
    
    for phase, bars, up_range, down_range in phases:
        for _ in range(bars):
            if phase == "up":
                change = random.uniform(down_range, up_range)
            elif phase == "down":
                change = random.uniform(down_range, up_range)
            elif phase == "top":
                change = random.uniform(-0.012, 0.008)
            else:  # bottom
                change = random.uniform(-0.008, 0.015)
            
            base = base * (1 + change)
            closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.006)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.006)) for c in closes]
    volumes = [random.randint(15000, 50000) for _ in range(len(closes))]
    
    return highs, lows, closes, volumes

class TrendFilteredMoer(MoerChanlun):
    """带趋势过滤的摩尔缠论"""
    
    def __init__(self, *args, trend_period=55, **kwargs):
        super().__init__(*args, **kwargs)
        self.trend_period = trend_period
    
    def get_trend(self, closes):
        """判断趋势方向"""
        if len(closes) < self.trend_period:
            return "UNKNOWN"
        
        ma = sum(closes[-self.trend_period:]) / self.trend_period
        current = closes[-1]
        
        if current > ma * 1.03:  # 价格在MA55上方3%
            return "UP"
        elif current < ma * 0.97:  # 价格在MA55下方3%
            return "DOWN"
        else:
            return "SIDE"
    
    def scan_with_trend_filter(self, highs, lows, closes, volumes=None):
        """带趋势过滤的扫描"""
        trend = self.get_trend(closes)
        all_signals = self.scan(highs, lows, closes, volumes)
        
        filtered = []
        for sig in all_signals:
            # 关键过滤逻辑：顺大势，逆小势
            if '买' in sig.signal_type.value:
                # 买点：只在大趋势向上时寻找
                if trend == "UP":
                    filtered.append(sig)
                # 震荡市需要更高质量信号
                elif trend == "SIDE" and sig.strength >= 70:
                    filtered.append(sig)
                    
            elif '卖' in sig.signal_type.value:
                # 卖点：只在大趋势向下时寻找
                if trend == "DOWN":
                    filtered.append(sig)
                elif trend == "SIDE" and sig.strength >= 70:
                    filtered.append(sig)
        
        return filtered, trend


def simulate_trades(signals, highs, lows, closes):
    """模拟交易"""
    trades = []
    
    for sig in signals:
        entry_idx = sig.index
        if entry_idx >= len(closes) - 10:
            continue
        
        entry_price = sig.price
        
        # 动态止损止盈
        if '买' in sig.signal_type.value:
            tp = entry_price * 1.05  # +5%止盈
            sl = entry_price * 0.97  # -3%止损
            
            for j in range(entry_idx + 1, min(entry_idx + 30, len(closes))):
                if lows[j] <= sl:
                    trades.append({
                        'signal': sig, 'entry': entry_price, 'exit': sl,
                        'pnl': -0.03, 'result': '止损', 'bars': j - entry_idx
                    })
                    break
                elif highs[j] >= tp:
                    trades.append({
                        'signal': sig, 'entry': entry_price, 'exit': tp,
                        'pnl': 0.05, 'result': '止盈', 'bars': j - entry_idx
                    })
                    break
            else:
                final = closes[min(entry_idx + 25, len(closes)-1)]
                pnl = (final - entry_price) / entry_price
                trades.append({
                    'signal': sig, 'entry': entry_price, 'exit': final,
                    'pnl': pnl, 'result': '超时', 'bars': 25
                })
        else:
            tp = entry_price * 0.95  # -5%止盈
            sl = entry_price * 1.03  # +3%止损
            
            for j in range(entry_idx + 1, min(entry_idx + 30, len(closes))):
                if highs[j] >= sl:
                    trades.append({
                        'signal': sig, 'entry': entry_price, 'exit': sl,
                        'pnl': -0.03, 'result': '止损', 'bars': j - entry_idx
                    })
                    break
                elif lows[j] <= tp:
                    trades.append({
                        'signal': sig, 'entry': entry_price, 'exit': tp,
                        'pnl': 0.05, 'result': '止盈', 'bars': j - entry_idx
                    })
                    break
            else:
                final = closes[min(entry_idx + 25, len(closes)-1)]
                pnl = (entry_price - final) / entry_price
                trades.append({
                    'signal': sig, 'entry': entry_price, 'exit': final,
                    'pnl': pnl, 'result': '超时', 'bars': 25
                })
    
    return trades


print("=" * 70)
print("🚀 摩尔缠论 - 趋势过滤优化版")
print("=" * 70)
print("\n核心改进:")
print("  ✅ 只在上涨趋势找买点 (价格 > MA55 * 1.03)")
print("  ✅ 只在下跌趋势找卖点 (价格 < MA55 * 0.97)")
print("  ✅ 避免逆势抄底/摸顶")
print()

# 生成数据
highs, lows, closes, volumes = generate_trending_data(500)

print("📊 市场数据概览")
print(f"   K线数量: {len(closes)}")
print(f"   起始价格: ${closes[0]:,.0f}")
print(f"   结束价格: ${closes[-1]:,.0f}")
print(f"   总涨跌: {(closes[-1]/closes[0]-1)*100:+.1f}%")

# 计算MA55
trend_ma = calculate_ma(closes, 55)

# 初始化带过滤的系统
moer_filtered = TrendFilteredMoer(ma_period=34, center_min_height=0.003, trend_period=55)
moer_original = MoerChanlun(ma_period=34, center_min_height=0.003)

# ===== 原始版本（无过滤）=====
print("\n" + "=" * 70)
print("📉 原始版本（无趋势过滤）")
print("=" * 70)

original_signals = []
for i in range(100, len(closes)-30):
    w_highs = highs[i-80:i+1]
    w_lows = lows[i-80:i+1]
    w_closes = closes[i-80:i+1]
    
    signals = moer_original.scan(w_highs, w_lows, w_closes, None)
    for sig in signals:
        sig.index = i
        original_signals.append(sig)

original_trades = simulate_trades(original_signals, highs, lows, closes)

if original_trades:
    wins = len([t for t in original_trades if t['pnl'] > 0])
    total_pnl = sum(t['pnl'] for t in original_trades) * 100
    print(f"\n   信号数量: {len(original_signals)}")
    print(f"   交易次数: {len(original_trades)}")
    print(f"   胜    率: {wins/len(original_trades)*100:.1f}%")
    print(f"   累计收益: {total_pnl:+.1f}%")

# ===== 改进版本（带过滤）=====
print("\n" + "=" * 70)
print("📈 改进版本（带趋势过滤）")
print("=" * 70)

filtered_signals = []
for i in range(100, len(closes)-30):
    w_highs = highs[i-80:i+1]
    w_lows = lows[i-80:i+1]
    w_closes = closes[i-80:i+1]
    w_volumes = volumes[i-80:i+1]
    
    signals, trend = moer_filtered.scan_with_trend_filter(w_highs, w_lows, w_closes, w_volumes)
    for sig in signals:
        sig.index = i
        sig.trend = trend
        filtered_signals.append(sig)

filtered_trades = simulate_trades(filtered_signals, highs, lows, closes)

if filtered_trades:
    wins = len([t for t in filtered_trades if t['pnl'] > 0])
    total_pnl = sum(t['pnl'] for t in filtered_trades) * 100
    
    print(f"\n   信号数量: {len(filtered_signals)} (过滤掉了 {len(original_signals) - len(filtered_signals)} 个逆势信号)")
    print(f"   交易次数: {len(filtered_trades)}")
    
    win_rate = wins/len(filtered_trades)*100
    bar = "█" * int(win_rate / 5) + "░" * (20 - int(win_rate / 5))
    print(f"   胜    率: {win_rate:.1f}% [{bar}]")
    
    color = "🟢" if total_pnl > 0 else "🔴"
    print(f"   累计收益: {color} {total_pnl:+.1f}%")

# ===== 详细对比 =====
print("\n" + "=" * 70)
print("📊 详细对比分析")
print("=" * 70)

print(f"\n{'指标':<20} {'原始版本':>15} {'改进版本':>15} {'提升':>15}")
print(f"{'-'*65}")

orig_win_rate = len([t for t in original_trades if t['pnl'] > 0]) / len(original_trades) * 100 if original_trades else 0
filt_win_rate = len([t for t in filtered_trades if t['pnl'] > 0]) / len(filtered_trades) * 100 if filtered_trades else 0
orig_pnl = sum(t['pnl'] for t in original_trades) * 100 if original_trades else 0
filt_pnl = sum(t['pnl'] for t in filtered_trades) * 100 if filtered_trades else 0

print(f"{'信号数量':<20} {len(original_signals):>15} {len(filtered_signals):>15} {'-'+str(len(original_signals)-len(filtered_signals)):>15}")
print(f"{'胜率':<20} {orig_win_rate:>14.1f}% {filt_win_rate:>14.1f}% {f'+'+(filt_win_rate-orig_win_rate):>14.1f}%")
print(f"{'累计收益':<20} {orig_pnl:>+14.1f}% {filt_pnl:>+14.1f}% {f'+'+(filt_pnl-orig_pnl):>+14.1f}%")

# 交易明细
print(f"\n📝 改进版交易明细")
print(f"   {'#':>3} {'信号':>8} {'入场价':>12} {'出场价':>12} {'盈亏%':>8} {'持仓':>5} {'结果':>6}")
print(f"   {'-'*60}")

for i, t in enumerate(filtered_trades[:15], 1):
    emoji = "🟢" if t['pnl'] > 0 else "🔴"
    print(f"   {emoji} {i:>2} {t['signal'].signal_type.value:>8} ${t['entry']:>10,.0f} ${t['exit']:>10,.0f} {t['pnl']*100:>+7.2f}% {t['bars']:>4}K {t['result']:>6}")

if len(filtered_trades) > 15:
    print(f"   ... 还有 {len(filtered_trades)-15} 笔交易")

# 总结
print("\n" + "=" * 70)
print("💡 核心结论")
print("=" * 70)

if filt_pnl > orig_pnl:
    print(f"\n   ✅ 趋势过滤显著提升了策略表现！")
    print(f"   ✅ 胜率提升: +{filt_win_rate - orig_win_rate:.1f}%")
    print(f"   ✅ 收益提升: +{filt_pnl - orig_pnl:.1f}%")
    print(f"\n   关键洞察:")
    print(f"   • 过滤掉了 {len(original_signals) - len(filtered_signals)} 个逆势信号")
    print(f"   • 避免了下跌途中反复抄底")
    print(f"   • 只在趋势有利时交易")
else:
    print(f"\n   ⚠️ 趋势过滤效果有限")
    print(f"   建议: 尝试更长的趋势周期（MA89/MA144）")

print("\n" + "=" * 70)
