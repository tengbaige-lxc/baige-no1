"""
摩尔缠论 - 实时市场分析
使用OKX真实数据
"""

from realtime_data import RealtimeDataManager, OKXAPI
from moer_quant_pure import MoerChanlun
from visualization import create_simple_visualization

def realtime_analysis():
    """实时市场分析"""
    
    print("=" * 70)
    print("🚀 摩尔缠论 - 实时市场分析 (OKX真实数据)")
    print("=" * 70)
    
    # 选择币种
    symbols = [
        ("BTC-USDT", "比特币"),
        ("ETH-USDT", "以太坊"),
        ("SOL-USDT", "Solana"),
    ]
    
    print("\n📊 可分析币种:")
    for i, (symbol, name) in enumerate(symbols, 1):
        price = OKXAPI.get_symbol_price(symbol)
        if price:
            print(f"   {i}. {name} ({symbol}): ${price:,.2f}")
        else:
            print(f"   {i}. {name} ({symbol})")
    
    # 分析BTC
    symbol = "BTC-USDT"
    name = "比特币"
    
    print(f"\n{'='*70}")
    print(f"🔍 分析 {name} ({symbol})")
    print(f"{'='*70}")
    
    # 获取1小时数据
    print("\n📡 获取1小时K线数据...")
    data_mgr = RealtimeDataManager("okx")
    highs, lows, closes, volumes = data_mgr.fetch_data(symbol, "1H", limit=150)
    
    if not closes:
        print("❌ 数据获取失败")
        return
    
    # 摩尔缠论分析
    print("\n" + "-" * 70)
    print("🧠 摩尔缠论分析")
    print("-" * 70)
    
    moer = MoerChanlun(ma_period=34, center_min_height=0.002)
    
    # 识别中枢
    centers = moer.identify_centers(highs, lows, closes)
    print(f"\n📍 识别中枢: {len(centers)} 个")
    
    if centers:
        for i, c in enumerate(centers[-3:], 1):
            bar = "█" * int(c['height'] * 50)
            print(f"   中枢{i}: ZG=${c['zg']:,.0f} ZD=${c['zd']:,.0f} 高度{c['height']*100:.1f}% {bar}")
    
    # 扫描信号
    signals = moer.scan(highs, lows, closes, volumes)
    
    print(f"\n🔔 交易信号: {len(signals)} 个")
    
    if signals:
        # 按类型分组
        signal_groups = {}
        for sig in signals:
            stype = sig.signal_type.value
            if stype not in signal_groups:
                signal_groups[stype] = []
            signal_groups[stype].append(sig)
        
        for stype, sigs in sorted(signal_groups.items()):
            emoji = "🟢" if "买" in stype else "🔴"
            print(f"\n   {emoji} {stype}: {len(sigs)} 个")
            
            for sig in sigs[:3]:  # 只显示前3个
                print(f"      @ ${sig.price:,.0f} 强度{sig.strength}")
                print(f"      止损: ${sig.stop_loss:,.0f} 止盈: ${sig.take_profit:,.0f}")
                
            if len(sigs) > 3:
                print(f"      ... 还有 {len(sigs)-3} 个")
    else:
        print("   ⏳ 当前无信号")
    
    # 最新价格位置分析
    current_price = closes[-1]
    print(f"\n💰 当前价格分析:")
    print(f"   最新价: ${current_price:,.2f}")
    
    if centers:
        latest_center = centers[-1]
        zg, zd = latest_center['zg'], latest_center['zd']
        
        if current_price > zg:
            print(f"   位置: 📈 高于中枢上轨 (ZG=${zg:,.0f})")
            print(f"   建议: 关注三买机会或追涨")
        elif current_price < zd:
            print(f"   位置: 📉 低于中枢下轨 (ZD=${zd:,.0f})")
            print(f"   建议: 关注一买机会或观望")
        else:
            print(f"   位置: 🔄 中枢区间内 (${zd:,.0f} - ${zg:,.0f})")
            print(f"   建议: 等待突破方向")
    
    # 生成可视化
    print("\n" + "-" * 70)
    print("📊 生成可视化图表")
    print("-" * 70)
    
    chart_path = f"/root/.openclaw/workspace/moer-chanlun/{symbol.replace('-', '_')}_chart.html"
    output = create_simple_visualization(highs, lows, closes, volumes, signals, centers, chart_path)
    
    print(f"✅ 图表已保存: {output}")
    
    # 交易建议
    print("\n" + "=" * 70)
    print("💡 交易建议")
    print("=" * 70)
    
    if signals:
        # 找出最强信号
        strongest = max(signals, key=lambda s: s.strength)
        
        print(f"\n🎯 最强信号: {strongest.signal_type.value}")
        print(f"   入场价: ${strongest.price:,.2f}")
        print(f"   止损价: ${strongest.stop_loss:,.2f} ({(strongest.stop_loss/strongest.price-1)*100:+.1f}%)")
        print(f"   止盈价: ${strongest.take_profit:,.2f} ({(strongest.take_profit/strongest.price-1)*100:+.1f}%)")
        print(f"   信号强度: {strongest.strength}/100")
        print(f"\n   说明: {strongest.reason}")
    else:
        print("\n⏳ 暂无明确交易信号")
        print("   建议: 继续观察，等待更好的入场时机")
    
    print("\n" + "=" * 70)
    print("⚠️ 风险提示")
    print("=" * 70)
    print("   • 以上分析仅供参考，不构成投资建议")
    print("   • 加密货币市场波动剧烈，请做好风险管理")
    print("   • 建议结合资金管理策略使用")
    print("=" * 70)


if __name__ == "__main__":
    realtime_analysis()
