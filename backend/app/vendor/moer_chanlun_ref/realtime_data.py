"""
摩尔缠论 - 实时数据接入模块
支持币安(Binance)和OKX交易所
"""

import json
import urllib.request
import urllib.error
import ssl
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

# 禁用SSL验证（某些环境需要）
ssl._create_default_https_context = ssl._create_unverified_context

@dataclass
class KlineData:
    """K线数据结构"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp / 1000)


class BinanceAPI:
    """币安API封装"""
    
    BASE_URL = "https://api.binance.com"
    
    @staticmethod
    def get_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 200) -> List[KlineData]:
        """
        获取K线数据
        
        Args:
            symbol: 交易对，如 "BTCUSDT", "ETHUSDT"
            interval: 时间周期，如 "1m", "5m", "15m", "1h", "4h", "1d"
            limit: 获取条数，最大1000
            
        Returns:
            KlineData列表
        """
        url = f"{BinanceAPI.BASE_URL}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                klines = []
                for item in data:
                    klines.append(KlineData(
                        timestamp=int(item[0]),
                        open=float(item[1]),
                        high=float(item[2]),
                        low=float(item[3]),
                        close=float(item[4]),
                        volume=float(item[5])
                    ))
                
                return klines
                
        except urllib.error.HTTPError as e:
            print(f"❌ HTTP错误: {e.code} - {e.reason}")
            return []
        except urllib.error.URLError as e:
            print(f"❌ 网络错误: {e.reason}")
            return []
        except Exception as e:
            print(f"❌ 错误: {e}")
            return []
    
    @staticmethod
    def get_symbol_price(symbol: str = "BTCUSDT") -> Optional[float]:
        """获取最新价格"""
        url = f"{BinanceAPI.BASE_URL}/api/v3/ticker/price?symbol={symbol}"
        
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0'
            })
            
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return float(data['price'])
                
        except Exception as e:
            print(f"❌ 获取价格失败: {e}")
            return None
    
    @staticmethod
    def get_all_symbols() -> List[str]:
        """获取所有USDT交易对"""
        url = f"{BinanceAPI.BASE_URL}/api/v3/exchangeInfo"
        
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0'
            })
            
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))
                symbols = []
                for s in data['symbols']:
                    if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING':
                        symbols.append(s['symbol'])
                return symbols
                
        except Exception as e:
            print(f"❌ 获取交易对失败: {e}")
            return []


class OKXAPI:
    """OKX API封装"""
    
    BASE_URL = "https://www.okx.com"
    
    @staticmethod
    def get_klines(symbol: str = "BTC-USDT", interval: str = "1H", limit: int = 200) -> List[KlineData]:
        """
        获取K线数据
        
        Args:
            symbol: 交易对，如 "BTC-USDT", "ETH-USDT"
            interval: 时间周期，如 "1m", "5m", "15m", "1H", "4H", "1D"
            limit: 获取条数
        """
        # OKX时间周期格式略有不同
        bar_map = {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "1h": "1H", "4h": "4H", "1d": "1D"
        }
        bar = bar_map.get(interval, interval)
        
        url = f"{OKXAPI.BASE_URL}/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}"
        
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                if result.get('code') != '0':
                    print(f"❌ API错误: {result.get('msg')}")
                    return []
                
                data = result.get('data', [])
                
                klines = []
                for item in reversed(data):  # OKX数据是倒序的
                    klines.append(KlineData(
                        timestamp=int(item[0]),
                        open=float(item[1]),
                        high=float(item[2]),
                        low=float(item[3]),
                        close=float(item[4]),
                        volume=float(item[5])
                    ))
                
                return klines
                
        except Exception as e:
            print(f"❌ 错误: {e}")
            return []
    
    @staticmethod
    def get_symbol_price(symbol: str = "BTC-USDT") -> Optional[float]:
        """获取最新价格"""
        url = f"{OKXAPI.BASE_URL}/api/v5/market/ticker?instId={symbol}"
        
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0'
            })
            
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                if result.get('code') == '0' and result.get('data'):
                    return float(result['data'][0]['last'])
                return None
                
        except Exception as e:
            print(f"❌ 获取价格失败: {e}")
            return None


class RealtimeDataManager:
    """实时数据管理器"""
    
    def __init__(self, exchange: str = "binance"):
        """
        Args:
            exchange: "binance" 或 "okx"
        """
        self.exchange = exchange.lower()
        self.api = BinanceAPI() if self.exchange == "binance" else OKXAPI()
        
    def fetch_data(self, symbol: str, interval: str = "1h", limit: int = 200) -> Tuple[List[float], List[float], List[float], List[float]]:
        """
        获取数据并转换为摩尔缠论格式
        
        Returns:
            (highs, lows, closes, volumes)
        """
        print(f"\n📡 从 {self.exchange.upper()} 获取 {symbol} {interval} 数据...")
        
        klines = self.api.get_klines(symbol, interval, limit)
        
        if not klines:
            print("❌ 获取数据失败")
            return [], [], [], []
        
        highs = [k.high for k in klines]
        lows = [k.low for k in klines]
        closes = [k.close for k in klines]
        volumes = [k.volume for k in klines]
        
        print(f"✅ 获取成功: {len(klines)} 根K线")
        print(f"   时间: {klines[0].datetime} → {klines[-1].datetime}")
        print(f"   价格: ${closes[0]:,.2f} → ${closes[-1]:,.2f}")
        
        return highs, lows, closes, volumes
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """获取当前价格"""
        return self.api.get_symbol_price(symbol)


def demo():
    """演示：获取真实数据并分析"""
    
    print("=" * 70)
    print("📡 摩尔缠论 - 实时数据接入演示")
    print("=" * 70)
    
    # 选择交易所
    print("\n选择交易所:")
    print("  1. Binance (币安)")
    print("  2. OKX")
    
    # 默认使用币安
    exchange = "binance"
    symbol = "BTCUSDT"
    interval = "1h"
    
    # 初始化数据管理器
    data_mgr = RealtimeDataManager(exchange)
    
    # 获取数据
    highs, lows, closes, volumes = data_mgr.fetch_data(symbol, interval, limit=200)
    
    if not closes:
        print("\n❌ 无法获取数据，请检查网络连接")
        return
    
    # 使用摩尔缠论分析
    print("\n" + "-" * 70)
    print("🔍 摩尔缠论分析")
    print("-" * 70)
    
    from moer_quant_pure import MoerChanlun
    
    moer = MoerChanlun(ma_period=34, center_min_height=0.002)
    
    # 识别中枢
    centers = moer.identify_centers(highs, lows, closes)
    print(f"\n📍 识别中枢: {len(centers)} 个")
    
    for i, c in enumerate(centers[-3:], 1):
        print(f"   中枢{i}: ZG=${c['zg']:,.0f} ZD=${c['zd']:,.0f} 高度{c['height']*100:.1f}%")
    
    # 扫描信号
    signals = moer.scan(highs, lows, closes, volumes)
    print(f"\n🔔 当前信号: {len(signals)} 个")
    
    for sig in signals:
        emoji = "🟢" if "买" in sig.signal_type.value else "🔴"
        print(f"   {emoji} {sig.signal_type.value} @ ${sig.price:,.0f} (强度{sig.strength})")
        print(f"      止损: ${sig.stop_loss:,.0f} 止盈: ${sig.take_profit:,.0f}")
    
    if not signals:
        print("   ⏳ 暂无信号，等待机会...")
    
    # 获取当前价格
    current_price = data_mgr.get_current_price(symbol)
    if current_price:
        print(f"\n💰 当前价格: ${current_price:,.2f}")
    
    print("\n" + "=" * 70)
    print("✅ 演示完成！")
    print("=" * 70)


if __name__ == "__main__":
    demo()
