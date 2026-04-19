"""
信号扫描服务
整合白鸽一号的清算热力图、摩尔缠论背驰、宏观过滤器
"""
import sys
import os
from typing import Optional, Dict
from datetime import datetime, timezone

# 添加 workspace 到路径以导入原有模块
sys.path.insert(0, '/root/.openclaw/workspace')

from app.services.okx_client import okx_manager


class SignalService:
    """交易信号扫描服务"""
    
    def __init__(self):
        self._liq_signal = None
        self._div_system = None
        self._macro_filter = None
        self._init_modules()
    
    def _init_modules(self):
        """延迟初始化信号模块"""
        try:
            from liq_signal_module import LiquidationSignal
            self._liq_signal = LiquidationSignal()
            # 加载默认数据
            self._liq_signal.update_zones({
                'BTC': [
                    {'price': 68000, 'long_liq': 45.2, 'short_liq': 12.1},
                    {'price': 69000, 'long_liq': 38.5, 'short_liq': 15.3},
                    {'price': 69500, 'long_liq': 22.1, 'short_liq': 18.7},
                    {'price': 70500, 'long_liq': 14.2, 'short_liq': 25.4},
                    {'price': 71000, 'long_liq': 8.5, 'short_liq': 42.8},
                    {'price': 72000, 'long_liq': 5.2, 'short_liq': 38.5},
                ],
                'ETH': [
                    {'price': 1900, 'long_liq': 28.5, 'short_liq': 8.2},
                    {'price': 1950, 'long_liq': 22.1, 'short_liq': 12.5},
                    {'price': 2000, 'long_liq': 15.3, 'short_liq': 14.2},
                    {'price': 2100, 'long_liq': 12.8, 'short_liq': 18.5},
                    {'price': 2200, 'long_liq': 8.5, 'short_liq': 25.3},
                    {'price': 2400, 'long_liq': 5.2, 'short_liq': 22.1},
                ],
                'SOL': [
                    {'price': 80, 'long_liq': 20.0, 'short_liq': 5.0},
                    {'price': 82, 'long_liq': 15.0, 'short_liq': 8.0},
                    {'price': 87, 'long_liq': 8.0, 'short_liq': 15.0},
                    {'price': 90, 'long_liq': 5.0, 'short_liq': 20.0},
                ]
            })
        except Exception as e:
            print(f"清算信号模块加载失败: {e}")
        
        try:
            from moer_divergence_system import check_multi_timeframe_divergence, calculate_macd
            self._div_system = {
                'check_multi_timeframe': check_multi_timeframe_divergence,
                'calculate_macd': calculate_macd,
            }
        except Exception as e:
            print(f"背驰系统加载失败: {e}")
        
        try:
            from macro_filter import MacroFilter
            self._macro_filter = MacroFilter()
        except Exception as e:
            print(f"宏观过滤器加载失败: {e}")
    
    async def scan_liquidation_signal(self, symbol: str) -> Optional[dict]:
        """扫描清算热力图信号"""
        if not self._liq_signal:
            return None
        
        try:
            # 获取当前价格
            ticker = await okx_manager.get_ticker(symbol)
            current_price = float(ticker.get("last", 0))
            
            coin = symbol.split("-")[0]
            result, reason = self._liq_signal.check_signal(coin, current_price)
            
            if result:
                return {
                    "signal": result.get("signal"),
                    "strength": result.get("strength", 1),
                    "reason": result.get("reason", reason),
                    "current_price": current_price,
                    "nearest_long_zone": result.get("nearest_long_zone"),
                    "nearest_short_zone": result.get("nearest_short_zone"),
                    "distance_to_long": round(result.get("distance_to_long", 0) * 100, 2),
                    "distance_to_short": round(result.get("distance_to_short", 0) * 100, 2),
                }
        except Exception as e:
            print(f"清算信号扫描失败: {e}")
        
        return None
    
    async def scan_divergence_signal(self, symbol: str) -> Optional[dict]:
        """扫描缠论背驰信号"""
        if not self._div_system:
            return None
        
        try:
            # 获取K线数据
            candles = await okx_manager.get_candles(symbol, "1H", 100)
            if len(candles) < 50:
                return None
            
            # 转换为字典格式
            candle_dicts = []
            for c in candles:
                candle_dicts.append({
                    'open': float(c[1]),
                    'high': float(c[2]),
                    'low': float(c[3]),
                    'close': float(c[4]),
                    'volume': float(c[5]),
                    'time': c[0]
                })
            
            # 计算MACD
            closes = [d['close'] for d in candle_dicts]
            macd_data = self._div_system['calculate_macd'](closes)
            
            # 简化版背驰检测（单周期）
            histogram = macd_data['histogram']
            
            # 找价格低点和MACD低点
            price_lows = []
            macd_lows = []
            for i in range(2, len(candle_dicts) - 2):
                if (candle_dicts[i]['low'] < candle_dicts[i-1]['low'] and 
                    candle_dicts[i]['low'] < candle_dicts[i-2]['low'] and
                    candle_dicts[i]['low'] < candle_dicts[i+1]['low'] and 
                    candle_dicts[i]['low'] < candle_dicts[i+2]['low']):
                    price_lows.append((i, candle_dicts[i]['low']))
                    macd_lows.append((i, histogram[i]))
            
            if len(price_lows) < 2:
                return {"signal": "NEUTRAL", "reason": "数据不足以判断背驰"}
            
            # 检查底背驰
            prev_p_low = price_lows[-2]
            curr_p_low = price_lows[-1]
            prev_m_low = macd_lows[-2] if len(macd_lows) >= 2 else None
            curr_m_low = macd_lows[-1] if len(macd_lows) >= 1 else None
            
            price_lower = curr_p_low[1] < prev_p_low[1] * 0.995
            macd_higher = curr_m_low and prev_m_low and curr_m_low[1] > prev_m_low[1] * 1.05
            
            if price_lower and macd_higher and histogram[curr_p_low[0]] < 0:
                confidence = min(100, int(60 + abs(curr_m_low[1] - prev_m_low[1]) / abs(prev_m_low[1]) * 30))
                return {
                    "signal": "BULLISH",
                    "strength": 4 if confidence > 70 else 3,
                    "confidence": confidence,
                    "reason": f"底背驰: 价格新低 {curr_p_low[1]:.2f} < {prev_p_low[1]:.2f}, MACD抬高",
                    "current_price": candle_dicts[-1]['close'],
                }
            
            # 检查顶背驰
            price_highs = []
            macd_highs = []
            for i in range(2, len(candle_dicts) - 2):
                if (candle_dicts[i]['high'] > candle_dicts[i-1]['high'] and 
                    candle_dicts[i]['high'] > candle_dicts[i-2]['high'] and
                    candle_dicts[i]['high'] > candle_dicts[i+1]['high'] and 
                    candle_dicts[i]['high'] > candle_dicts[i+2]['high']):
                    price_highs.append((i, candle_dicts[i]['high']))
                    macd_highs.append((i, histogram[i]))
            
            if len(price_highs) >= 2:
                prev_p_high = price_highs[-2]
                curr_p_high = price_highs[-1]
                prev_m_high = macd_highs[-2] if len(macd_highs) >= 2 else None
                curr_m_high = macd_highs[-1] if len(macd_highs) >= 1 else None
                
                price_higher = curr_p_high[1] > prev_p_high[1] * 1.005
                macd_lower = curr_m_high and prev_m_high and curr_m_high[1] < prev_m_high[1] * 0.95
                
                if price_higher and macd_lower and histogram[curr_p_high[0]] > 0:
                    confidence = min(100, int(60 + abs(prev_m_high[1] - curr_m_high[1]) / abs(prev_m_high[1]) * 30))
                    return {
                        "signal": "BEARISH",
                        "strength": 4 if confidence > 70 else 3,
                        "confidence": confidence,
                        "reason": f"顶背驰: 价格新高 {curr_p_high[1]:.2f} > {prev_p_high[1]:.2f}, MACD降低",
                        "current_price": candle_dicts[-1]['close'],
                    }
            
            return {"signal": "NEUTRAL", "reason": "未检测到明显背驰信号"}
            
        except Exception as e:
            print(f"背驰扫描失败: {e}")
            return None
    
    async def scan_macro_signal(self) -> Optional[dict]:
        """扫描宏观过滤器信号"""
        if not self._macro_filter:
            return None
        
        try:
            params = self._macro_filter.get_trading_params()
            return {
                "signal": "FILTER",
                "risk_level": self._macro_filter.risk_level,
                "max_positions": params.get("max_positions", 4),
                "position_pct": params.get("position_pct", 0.20),
                "reason": f"当前宏观风险等级: {self._macro_filter.risk_level}",
            }
        except Exception as e:
            print(f"宏观扫描失败: {e}")
            return None
    
    async def scan_all_signals(self, symbol: str) -> dict:
        """扫描所有信号"""
        liq = await self.scan_liquidation_signal(symbol)
        div = await self.scan_divergence_signal(symbol)
        macro = await self.scan_macro_signal()
        
        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "liquidation": liq,
            "divergence": div,
            "macro": macro,
        }


signal_service = SignalService()
