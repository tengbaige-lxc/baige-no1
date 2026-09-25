"""
摩尔缠论 - 可视化模块
绘制中枢、买卖点、MA线和MACD
生成HTML/PNG图表
"""

import json
from typing import List, Dict, Tuple

def generate_html_chart(highs: List[float], lows: List[float], closes: List[float],
                       volumes: List[float], signals: List, centers: List[Dict],
                       output_path: str = "chart.html"):
    """
    生成HTML可视化图表
    """
    
    # 准备数据
    data_points = []
    for i in range(len(closes)):
        data_points.append({
            'index': i,
            'open': closes[i] * 0.998,  # 模拟开盘价
            'high': highs[i],
            'low': lows[i],
            'close': closes[i],
            'volume': volumes[i] if volumes else 0
        })
    
    # 准备信号数据
    signal_data = []
    for sig in signals:
        signal_data.append({
            'index': sig.index,
            'type': sig.signal_type.value,
            'price': sig.price,
            'strength': sig.strength,
            'stop_loss': sig.stop_loss,
            'take_profit': sig.take_profit
        })
    
    # 准备中枢数据
    center_data = []
    for c in centers:
        center_data.append({
            'start': c['start_idx'],
            'end': c['end_idx'],
            'zg': c['zg'],
            'zd': c['zd']
        })
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>摩尔缠论可视化</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            background: #1a1a2e;
            font-family: Arial, sans-serif;
            color: #fff;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            color: #eee;
            margin-bottom: 20px;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            margin-bottom: 20px;
        }}
        .stat-box {{
            background: #16213e;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 24px;
            font-weight: bold;
            color: #4CAF50;
        }}
        .stat-label {{
            color: #888;
            font-size: 12px;
            margin-top: 5px;
        }}
        #main-chart {{
            width: 100%;
            height: 600px;
            background: #16213e;
            border-radius: 8px;
        }}
        .legend {{
            display: flex;
            justify-content: center;
            gap: 20px;
            margin-top: 15px;
            flex-wrap: wrap;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 12px;
        }}
        .legend-color {{
            width: 12px;
            height: 12px;
            border-radius: 2px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 摩尔缠论技术分析图</h1>
        
        <div class="stats">
            <div class="stat-box">
                <div class="stat-value">${closes[-1]:,.0f}</div>
                <div class="stat-label">最新价格</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{len(centers)}</div>
                <div class="stat-label">中枢数量</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{len(signals)}</div>
                <div class="stat-label">信号数量</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{((closes[-1]/closes[0]-1)*100):+.1f}%</div>
                <div class="stat-label">总涨跌</div>
            </div>
        </div>
        
        <div id="main-chart"></div>
        
        <div class="legend">
            <div class="legend-item">
                <div class="legend-color" style="background: #4CAF50;"></div>
                <span>一买</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #2196F3;"></div>
                <span>二买</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #9C27B0;"></div>
                <span>三买</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #f44336;"></div>
                <span>一卖</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #FF9800;"></div>
                <span>二卖</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: #795548;"></div>
                <span>三卖</span>
            </div>
            <div class="legend-item">
                <div class="legend-color" style="background: rgba(255,215,0,0.3);"></div>
                <span>中枢区间</span>
            </div>
        </div>
    </div>
    
    <script>
        const data = {json.dumps(data_points)};
        const signals = {json.dumps(signal_data)};
        const centers = {json.dumps(center_data)};
        
        const chart = echarts.init(document.getElementById('main-chart'));
        
        // K线数据
        const candleData = data.map(d => [d.open, d.close, d.low, d.high]);
        
        // 准备标记点
        const markPoints = [];
        const markLines = [];
        
        signals.forEach(sig => {{
            let color, symbol;
            switch(sig.type) {{
                case '一买': color = '#4CAF50'; symbol = 'arrow'; break;
                case '二买': color = '#2196F3'; symbol = 'arrow'; break;
                case '三买': color = '#9C27B0'; symbol = 'arrow'; break;
                case '一卖': color = '#f44336'; symbol = 'arrow'; break;
                case '二卖': color = '#FF9800'; symbol = 'arrow'; break;
                case '三卖': color = '#795548'; symbol = 'arrow'; break;
                default: color = '#fff';
            }}
            
            markPoints.push({{
                name: sig.type,
                coord: [sig.index, sig.price],
                value: sig.type,
                itemStyle: {{ color: color }},
                symbol: symbol,
                symbolSize: 15,
                symbolRotate: sig.type.includes('买') ? 0 : 180,
                label: {{
                    show: true,
                    position: sig.type.includes('买') ? 'bottom' : 'top',
                    formatter: sig.type,
                    color: color,
                    fontSize: 10
                }}
            }});
            
            // 添加止损止盈线
            markLines.push({{
                xAxis: sig.index,
                yAxis: sig.stop_loss,
                lineStyle: {{ type: 'dashed', color: color, opacity: 0.5 }},
                label: {{ formatter: 'SL', fontSize: 8 }}
            }});
            markLines.push({{
                xAxis: sig.index,
                yAxis: sig.take_profit,
                lineStyle: {{ type: 'dashed', color: color, opacity: 0.5 }},
                label: {{ formatter: 'TP', fontSize: 8 }}
            }});
        }});
        
        // 中枢区间标记
        const markAreas = centers.map(c => ({{
            xAxis: c.start,
            yAxis: c.zd,
            xAxis2: c.end,
            yAxis2: c.zg,
            itemStyle: {{
                color: 'rgba(255,215,0,0.15)',
                borderColor: 'rgba(255,215,0,0.5)',
                borderWidth: 1
            }},
            label: {{
                show: true,
                position: 'insideTop',
                formatter: '中枢',
                color: 'rgba(255,215,0,0.8)',
                fontSize: 10
            }}
        }}));
        
        const option = {{
            backgroundColor: '#16213e',
            tooltip: {{
                trigger: 'axis',
                axisPointer: {{ type: 'cross' }},
                backgroundColor: 'rgba(22,33,62,0.9)',
                borderColor: '#333',
                textStyle: {{ color: '#fff' }}
            }},
            grid: {{
                left: '3%',
                right: '3%',
                bottom: '15%',
                top: '10%',
                containLabel: true
            }},
            xAxis: {{
                type: 'category',
                data: data.map(d => d.index),
                axisLine: {{ lineStyle: {{ color: '#555' }} }},
                axisLabel: {{ color: '#888' }}
            }},
            yAxis: {{
                type: 'value',
                scale: true,
                axisLine: {{ lineStyle: {{ color: '#555' }} }},
                axisLabel: {{ color: '#888' }},
                splitLine: {{ lineStyle: {{ color: '#333' }} }}
            }},
            dataZoom: [
                {{ type: 'inside', start: 50, end: 100 }},
                {{ type: 'slider', start: 50, end: 100, bottom: '5%' }}
            ],
            series: [
                {{
                    name: 'K线',
                    type: 'candlestick',
                    data: candleData,
                    itemStyle: {{
                        color: '#ef5350',
                        color0: '#26a69a',
                        borderColor: '#ef5350',
                        borderColor0: '#26a69a'
                    }},
                    markPoint: {{
                        data: markPoints,
                        symbolOffset: [0, -20]
                    }},
                    markLine: {{
                        data: markLines,
                        symbol: 'none'
                    }},
                    markArea: {{
                        data: markAreas
                    }}
                }},
                {{
                    name: '成交量',
                    type: 'bar',
                    xAxisIndex: 0,
                    yAxisIndex: 0,
                    data: data.map(d => ({{
                        value: d.volume,
                        itemStyle: {{
                            color: d.close >= d.open ? 'rgba(239,83,80,0.3)' : 'rgba(38,166,154,0.3)'
                        }}
                    }})),
                    barWidth: '60%'
                }}
            ]
        }};
        
        chart.setOption(option);
        window.addEventListener('resize', () => chart.resize());
    </script>
</body>
</html>'''
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    return output_path


def create_simple_visualization(highs, lows, closes, volumes, signals, centers, 
                                 output_path="simple_chart.html"):
    """
    创建简化版可视化（纯文本SVG，无需外部依赖）
    """
    
    width = 1200
    height = 600
    padding = 50
    
    # 计算价格范围
    price_min = min(lows)
    price_max = max(highs)
    price_range = price_max - price_min
    
    # 缩放函数
    def x_scale(idx):
        return padding + (idx / len(closes)) * (width - 2 * padding)
    
    def y_scale(price):
        return height - padding - ((price - price_min) / price_range) * (height - 2 * padding)
    
    # 生成SVG
    svg_parts = [f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
    <defs>
        <linearGradient id="bg" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" style="stop-color:#1a1a2e"/>
            <stop offset="100%" style="stop-color:#16213e"/>
        </linearGradient>
    </defs>
    <rect width="100%" height="100%" fill="url(#bg)"/>
    <text x="{width/2}" y="30" text-anchor="middle" fill="#fff" font-size="20">摩尔缠论技术分析图</text>
''']
    
    # 绘制网格
    for i in range(0, len(closes), 20):
        x = x_scale(i)
        svg_parts.append(f'    <line x1="{x}" y1="{padding}" x2="{x}" y2="{height-padding}" stroke="#333" stroke-width="0.5"/>')
    
    # 绘制K线
    for i in range(len(closes)):
        x = x_scale(i)
        open_p = closes[i] * 0.998
        close_p = closes[i]
        high_p = highs[i]
        low_p = lows[i]
        
        color = "#26a69a" if close_p >= open_p else "#ef5350"
        
        # 实体
        y_open = y_scale(open_p)
        y_close = y_scale(close_p)
        svg_parts.append(f'    <line x1="{x}" y1="{min(y_open, y_close)}" x2="{x}" y2="{max(y_open, y_close)}" stroke="{color}" stroke-width="2"/>')
        
        # 影线
        y_high = y_scale(high_p)
        y_low = y_scale(low_p)
        svg_parts.append(f'    <line x1="{x}" y1="{y_high}" x2="{x}" y2="{y_low}" stroke="{color}" stroke-width="1"/>')
    
    # 绘制中枢
    for c in centers:
        x1 = x_scale(c['start_idx'])
        x2 = x_scale(c['end_idx'])
        y_zg = y_scale(c['zg'])
        y_zd = y_scale(c['zd'])
        
        svg_parts.append(f'    <rect x="{x1}" y="{y_zg}" width="{x2-x1}" height="{y_zd-y_zg}" fill="rgba(255,215,0,0.2)" stroke="rgba(255,215,0,0.5)"/>')
    
    # 绘制信号
    color_map = {
        '一买': '#4CAF50',
        '二买': '#2196F3', 
        '三买': '#9C27B0',
        '一卖': '#f44336',
        '二卖': '#FF9800',
        '三卖': '#795548'
    }
    
    for sig in signals[:50]:  # 只显示前50个信号避免拥挤
        x = x_scale(sig.index)
        y = y_scale(sig.price)
        color = color_map.get(sig.signal_type.value, '#fff')
        
        # 箭头
        arrow = "▼" if '卖' in sig.signal_type.value else "▲"
        svg_parts.append(f'    <text x="{x}" y="{y}" fill="{color}" font-size="14" text-anchor="middle">{arrow}</text>')
        svg_parts.append(f'    <text x="{x}" y="{y-15 if "卖" in sig.signal_type.value else y+20}" fill="{color}" font-size="10" text-anchor="middle">{sig.signal_type.value}</text>')
    
    # 添加图例
    legend_y = height - 20
    legend_x = padding
    svg_parts.append(f'    <text x="{legend_x}" y="{legend_y}" fill="#888" font-size="12">图例: </text>')
    
    x_offset = 50
    for name, color in color_map.items():
        svg_parts.append(f'    <text x="{legend_x + x_offset}" y="{legend_y}" fill="{color}" font-size="12">● {name}</text>')
        x_offset += 70
    
    svg_parts.append('</svg>')
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>摩尔缠论 - 简化图表</title>
    <style>
        body {{ background: #1a1a2e; margin: 0; padding: 20px; }}
        .container {{ max-width: 1250px; margin: 0 auto; }}
    </style>
</head>
<body>
    <div class="container">
        {''.join(svg_parts)}
    </div>
</body>
</html>'''
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    return output_path


# 测试
if __name__ == "__main__":
    import random
    from moer_quant_pure import MoerChanlun
    
    # 生成测试数据
    random.seed(42)
    n = 200
    closes = [50000 * (1 + random.uniform(-0.01, 0.015)) ** i for i in range(n)]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    volumes = [random.randint(10000, 50000) for _ in range(n)]
    
    # 扫描信号
    moer = MoerChanlun(ma_period=34, center_min_height=0.002)
    
    signals = []
    for i in range(80, len(closes)-10):
        sigs = moer.scan(highs[i-80:i+1], lows[i-80:i+1], closes[i-80:i+1], volumes[i-80:i+1])
        for sig in sigs:
            sig.index = i
            signals.append(sig)
    
    centers = moer.centers
    
    # 生成图表
    output = create_simple_visualization(highs, lows, closes, volumes, signals, centers,
                                          "/root/.openclaw/workspace/moer-chanlun/visualization.html")
    
    print(f"✅ 可视化图表已生成: {output}")
    print(f"   信号数量: {len(signals)}")
    print(f"   中枢数量: {len(centers)}")
    print(f"\n请在浏览器中打开查看")
