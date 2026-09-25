#!/usr/bin/env python3
"""
清算热力图信号模块 - 整合到白鸽一号
"""


def _fmt_price(price):
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    else:
        return f"${price:.4f}"


class LiquidationSignal:
    """基于清算热力图的交易信号"""

    def __init__(self):
        self.liquidation_zones = {}

    def update_zones(self, zones):
        self.liquidation_zones = zones


    def check_recent_cascade_risk(
        self,
        symbol,
        current_price,
        direction,
        *,
        distance_threshold=0.015,
        min_relative_zone_size=0.40,
        max_data_age_seconds=10 * 60,
    ):
        """Use fresh completed liquidations only to block chasing a cascade."""
        zones = self.liquidation_zones.get(symbol) or []
        if current_price <= 0 or direction not in {"LONG", "SHORT"}:
            return {"blocked": False, "status": "invalid", "reason": "清算风险过滤参数无效"}
        if not zones:
            return {"blocked": False, "status": "unavailable", "reason": "无近期清算数据，按中性处理"}

        side_key = "long_liq" if direction == "LONG" else "short_liq"
        timestamp_key = "long_latest_ts" if direction == "LONG" else "short_latest_ts"
        now_ms = int(__import__("time").time() * 1000)
        updated_at = max(int(zone.get("updated_at_ms", 0) or 0) for zone in zones)
        if not updated_at or (now_ms - updated_at) / 1000 > max_data_age_seconds:
            return {"blocked": False, "status": "stale", "reason": "清算数据已过期，按中性处理"}

        active = []
        for zone in zones:
            try:
                value = float(zone.get(side_key, 0) or 0)
                timestamp_ms = int(zone.get(timestamp_key, 0) or 0)
                price = float(zone.get("price", 0) or 0)
            except (TypeError, ValueError):
                continue
            if value <= 0 or price <= 0 or timestamp_ms <= 0:
                continue
            age_seconds = max(0.0, (now_ms - timestamp_ms) / 1000)
            if age_seconds <= max_data_age_seconds:
                active.append((price, value, age_seconds))
        if not active:
            return {"blocked": False, "status": "unavailable", "reason": "无足够新的同向清算数据，按中性处理"}

        peak_value = max(value for _, value, _ in active)
        floor = peak_value * max(0.0, min(1.0, float(min_relative_zone_size)))
        candidates = []
        for price, value, age_seconds in active:
            risk_side = price < current_price if direction == "LONG" else price > current_price
            distance = abs(price - current_price) / current_price
            if risk_side and distance <= distance_threshold and value >= floor:
                candidates.append((distance, -value, price, value, age_seconds))
        if not candidates:
            return {
                "blocked": False,
                "status": "clear",
                "reason": "附近无高集中的近期同向清算瀑布",
                "peak_value": peak_value,
            }

        distance, _, price, value, age_seconds = min(candidates)
        label = "下方多头清算" if direction == "LONG" else "上方空头清算"
        return {
            "blocked": True,
            "status": "blocked",
            "reason": f"{label}区 {_fmt_price(price)} 距现价{distance * 100:.2f}% (近期{age_seconds / 60:.1f}分钟，衰减量{value:.4g})",
            "zone_price": price,
            "distance": distance,
            "weighted_liquidation": value,
            "age_seconds": age_seconds,
            "peak_value": peak_value,
        }


    def check_signal(self, symbol, current_price):
        if symbol not in self.liquidation_zones:
            return None, '无清算数据'

        zones = self.liquidation_zones[symbol]
        if not zones:
            return None, '无清算数据'

        nearest_long_liq = None
        nearest_short_liq = None
        min_dist_long = float('inf')
        min_dist_short = float('inf')
        total_long_liq = 0
        total_short_liq = 0

        # 动态阈值：取所有区间最大量的5%作为最低门槛
        all_liq = [z['long_liq'] for z in zones] + [z['short_liq'] for z in zones]
        threshold = max(max(all_liq) * 0.05, 0.01) if all_liq else 0.01

        for z in zones:
            p = z['price']
            if p <= 0 or current_price <= 0:
                continue
            dist = abs(p - current_price) / current_price

            if p < current_price:
                total_long_liq += z['long_liq']
                if dist < min_dist_long and z['long_liq'] > threshold:
                    min_dist_long = dist
                    nearest_long_liq = z
            else:
                total_short_liq += z['short_liq']
                if dist < min_dist_short and z['short_liq'] > threshold:
                    min_dist_short = dist
                    nearest_short_liq = z

        signal = None
        strength = 0
        reason = ''

        closer_to_short = nearest_short_liq and (not nearest_long_liq or min_dist_short < min_dist_long)
        closer_to_long = nearest_long_liq and (not nearest_short_liq or min_dist_long < min_dist_short)

        if closer_to_short and min_dist_short < 0.02:
            signal = 'LONG'
            strength = min(5, max(1, int(nearest_short_liq['short_liq'] / max(threshold, 1))))
            reason = f'接近上方空头清算区 {_fmt_price(nearest_short_liq["price"])}({min_dist_short*100:.2f}%)，向上猎杀流动性'

        elif closer_to_long and min_dist_long < 0.02:
            signal = 'CAUTION'
            strength = min(5, max(1, int(nearest_long_liq['long_liq'] / max(threshold, 1))))
            reason = f'接近下方多头清算区 {_fmt_price(nearest_long_liq["price"])}({min_dist_long*100:.2f}%)，有下跌风险'

        elif total_short_liq > total_long_liq * 2 and total_long_liq > 0:
            signal = 'LONG_BIAS'
            strength = 3
            reason = f'上方空头清算({total_short_liq:.1f})是下方多头({total_long_liq:.1f})的{total_short_liq/total_long_liq:.1f}倍，有向上引力'

        elif total_long_liq > total_short_liq * 2 and total_short_liq > 0:
            signal = 'SHORT_BIAS'
            strength = 3
            reason = f'下方多头清算({total_long_liq:.1f})是上方空头({total_short_liq:.1f})的{total_long_liq/total_short_liq:.1f}倍，有向下引力'

        else:
            signal = 'NEUTRAL'
            reason = '清算分布均衡'

        return {
            'signal': signal,
            'strength': strength,
            'reason': reason,
            'nearest_long_zone': nearest_long_liq['price'] if nearest_long_liq else None,
            'nearest_short_zone': nearest_short_liq['price'] if nearest_short_liq else None,
            'distance_to_long': min_dist_long,
            'distance_to_short': min_dist_short,
        }, reason


# ==================== 整合到白鸽一号 ====================

class EnhancedTrader:
    """增强版白鸽一号，整合清算热力图"""
    
    def __init__(self):
        self.liq_signal = LiquidationSignal()
        # ... 原有初始化
    
    def check_all_signals(self, symbol, current_price):
        """多因子信号融合"""
        signals = []
        
        # 1. 原有信号
        # has_div, div_info = self.check_divergence()
        # has_cross, cross_info = self.check_ma_cross()
        
        # 2. 清算热力图信号
        liq_result, liq_msg = self.liq_signal.check_signal(symbol, current_price)
        if liq_result and liq_result['signal'] in ['LONG', 'LONG_BIAS']:
            signals.append({
                'type': '清算热力图',
                'weight': liq_result['strength'],
                'info': liq_result
            })
        
        # 3. 信号融合 (示例)
        total_weight = sum(s['weight'] for s in signals)
        
        return {
            'total_weight': total_weight,
            'signals': signals,
            'liq_data': liq_result
        }


# ==================== 使用示例 ====================
if __name__ == '__main__':
    # 模拟使用
    liq = LiquidationSignal()
    
    # 设置清算数据 (实际应从Coinglass API获取)
    liq.update_zones({
        'BTC': [
            {'price': 68000, 'long_liq': 45.2, 'short_liq': 12.1},
            {'price': 69000, 'long_liq': 38.5, 'short_liq': 15.3},
            {'price': 71000, 'long_liq': 8.5, 'short_liq': 42.8},
            {'price': 72000, 'long_liq': 5.2, 'short_liq': 38.5},
        ]
    })
    
    # 测试不同价格
    test_prices = [69500, 70500, 70800]
    
    for price in test_prices:
        result, msg = liq.check_signal('BTC', price)
        print(f'\n价格 ${price:,.0f}:')
        print(f'  信号: {result["signal"]} (强度{result["strength"]})')
        print(f'  原因: {result["reason"]}')
