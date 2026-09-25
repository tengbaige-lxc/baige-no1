"""
摩尔缠论策略诊断工具
分析为什么信号表现不佳
"""

from moer_quant_pure import MoerChanlun, SignalType
import random

def generate_btc_data(n=500):
    random.seed(42)
    closes = []
    base = 45000
    for i in range(n):
        phase = (i // 100) % 5
        if phase == 0:
            change = random.uniform(-0.008, 0.008)
        elif phase == 1:
            change = random.uniform(-0.005, 0.015)
        elif phase == 2:
            change = random.uniform(-0.02, 0.008)
        elif phase == 3:
            change = random.uniform(-0.015, 0.015) if i % 100 < 50 else random.uniform(-0.005, 0.02)
        else:
            change = random.uniform(-0.008, 0.025)
        base = base * (1 + change)
        closes.append(base)
    
    highs = [c * (1 + random.uniform(0, 0.008)) for c in closes]
    lows = [c * (1 - random.uniform(0, 0.008)) for c in closes]
    return highs, lows, closes

def diagnose():
    print("=" * 70)
    print("🔍 摩尔缠论策略诊断")
    print("=" * 70)
    
    highs, lows, closes = generate_btc_data(500)
    
    moer = MoerChanlun(ma_period=34, center_min_height=0.003)
    
    print("\n📊 数据概览")
    print(f"   数据点数: {len(closes)}")
    print(f"   价格范围: {min(closes):,.2f} - {max(closes):,.2f}")
    print(f"   最终价格: {closes[-1]:,.2f}")
    
    # 识别中枢
    centers = moer.identify_centers(highs, lows, closes)
    print(f"\n📍 中枢统计")
    print(f"   识别中枢数: {len(centers)}")
    
    if centers:
        avg_height = sum(c['height'] for c in centers) / len(centers)
        print(f"   平均高度: {avg_height*100:.2f}%")
        print(f"   高度范围: {min(c['height'] for c in centers)*100:.2f}% - {max(c['height'] for c in centers)*100:.2f}%")
    
    # 扫描所有信号
    print(f"\n🔔 信号扫描")
    
    all_signals = []
    for i in range(80, len(closes)):
        w_highs = highs[i-80:i+1]
        w_lows = lows[i-80:i+1]
        w_closes = closes[i-80:i+1]
        
        signals = moer.scan(w_highs, w_lows, w_closes, None)
        for sig in signals:
            all_signals.append({
                'time': i,
                'type': sig.signal_type.value,
                'price': sig.price,
                'strength': sig.strength,
                'stop_loss': sig.stop_loss,
                'take_profit': sig.take_profit,
                'future_prices': closes[i:min(i+20, len(closes))]  # 之后20根K线价格
            })
    
    print(f"   总信号数: {len(all_signals)}")
    
    if not all_signals:
        print("\n   ⚠️ 无信号生成，可能原因:")
        print("      - 中枢高度阈值过高")
        print("      - MACD背离条件过于严格")
        print("      - 数据波动不够剧烈")
        return
    
    # 分析每个信号的表现
    print(f"\n📈 信号后价格走势分析 (信号后20根K线)")
    print(f"   {'时间':>6s} {'信号':>8s} {'入场价':>12s} {'止损':>12s} {'止盈':>12s} {'最强反弹':>10s} {'最大亏损':>10s}")
    print(f"   {'-'*80}")
    
    win_count = 0
    loss_count = 0
    
    for sig in all_signals:
        future = sig['future_prices']
        if len(future) < 5:
            continue
        
        entry = sig['price']
        
        if '买' in sig['type']:
            # 多单：看最高能涨多少，最低跌多少
            max_up = (max(future) - entry) / entry * 100
            max_down = (min(future) - entry) / entry * 100
            
            # 是否触及止盈/止损
            hit_tp = max(future) >= sig['take_profit']
            hit_sl = min(future) <= sig['stop_loss']
            
            status = "✅止盈" if hit_tp and (not hit_sl or sig['take_profit'] > entry) else ("❌止损" if hit_sl else "⏳持仓")
            if hit_tp and (not hit_sl or max(future) > entry):
                win_count += 1
            elif hit_sl:
                loss_count += 1
        else:
            # 空单
            max_up = (entry - min(future)) / entry * 100
            max_down = (entry - max(future)) / entry * 100
            
            hit_tp = min(future) <= sig['take_profit']
            hit_sl = max(future) >= sig['stop_loss']
            
            status = "✅止盈" if hit_tp else ("❌止损" if hit_sl else "⏳持仓")
            if hit_tp:
                win_count += 1
            elif hit_sl:
                loss_count += 1
        
        print(f"   {sig['time']:>6d} {sig['type']:>8s} {entry:>12,.2f} {sig['stop_loss']:>12,.2f} {sig['take_profit']:>12,.2f} {max_up:>+9.2f}% {max_down:>+9.2f}% {status}")
    
    total = win_count + loss_count
    if total > 0:
        print(f"\n📊 信号质量统计")
        print(f"   成功信号: {win_count}")
        print(f"   失败信号: {loss_count}")
        print(f"   理论胜率: {win_count/total*100:.1f}%")
    
    # 问题诊断
    print(f"\n🔧 问题诊断")
    
    if len(all_signals) < 10:
        print("   ⚠️ 信号过少")
        print("      建议: 降低中枢高度阈值 (当前0.3% → 0.1%)")
        print("      建议: 降低信号强度要求 (当前40 → 20)")
    
    if total > 0 and win_count/total < 0.5:
        print("   ⚠️ 信号质量不佳")
        print("      建议: 增加过滤条件（如成交量确认）")
        print("      建议: 调整MACD背离检测窗口")
        print("      建议: 加入大周期趋势过滤")
    
    # 检测假突破
    false_breakouts = 0
    for sig in all_signals:
        if '买' in sig['type'] and len(sig['future_prices']) > 3:
            # 如果入场后3根K线就跌破入场价，可能是假突破
            if sig['future_prices'][3] < sig['price'] * 0.99:
                false_breakouts += 1
    
    if false_breakouts > len(all_signals) * 0.3:
        print(f"   ⚠️ 检测到 {false_breakouts}/{len(all_signals)} 假突破 ({false_breakouts/len(all_signals)*100:.0f}%)")
        print("      建议: 加入延迟确认（突破后等2-3根K线）")
        print("      建议: 提高突破时的成交量要求")

if __name__ == "__main__":
    diagnose()
