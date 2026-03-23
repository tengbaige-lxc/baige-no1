#!/usr/bin/env python3
"""
🕊️ 白鸽一号增强版 v2.1
整合 fully_auto_trader_v4.7 的核心功能：
- DataCache缓存系统
- 缠论3买/3卖精确检测
- KDJ指标+背离检测
- 多时间框架信号融合
- SQLite交易记录
"""

import hmac
import hashlib
import base64
import requests
import json
import sqlite3
from datetime import datetime
import time
import sys
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

# ==================== 配置区 ====================
API_KEY = '7dae93fe-2a48-4f2b-a889-9dbf30601068'
SECRET_KEY = '9C73FAEF39647DF6FBD06229CA4F0F4C'
PASSPHRASE = 'Lxc@2026888'
BASE_URL = 'https://www.okx.com'

SYMBOLS = [
    # 主流币 (6个)
    'BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP',
    'DOGE-USDT-SWAP', 'XRP-USDT-SWAP', 'FIL-USDT-SWAP',
    # 高流动性新币 (6个)
    'GALA-USDT-SWAP', 'ICP-USDT-SWAP', 'APE-USDT-SWAP',
    'PI-USDT-SWAP', 'WIF-USDT-SWAP', 'ACT-USDT-SWAP',
    # 中等流动性 (3个)
    'TRX-USDT-SWAP', 'LTC-USDT-SWAP', 'BCH-USDT-SWAP',
]

DB_PATH = '/root/.openclaw/workspace/trading_v21.db'

# 交易参数
LEVERAGE = 20
POSITION_PERCENT = 0.25      # 单币种25%
BATCH_SIZES = [0.30, 0.40, 0.30]  # 分批建仓
MAX_DAILY_LOSS_PERCENT = 0.06

# 移动止损
ACTIVATION_PERCENT = 0.04
CALLBACK_RATIO = 0.40

# 信号参数
REBOUND_THRESHOLD = 0.03
SIGNAL_CHECK_INTERVAL = 10
MIN_SIGNAL_SCORE = 2         # 最小信号评分

# 缠论参数
CENTER_PERIOD = 34
CENTER_CONFIRM_BARS = 4

# ==================== DataCache缓存系统 (来自v4.7) ====================
class DataCache:
    """数据缓存 - 减少API调用"""
    def __init__(self, ttl_seconds: int = 5):
        self.cache = {}
        self.ttl = ttl_seconds
        self.timestamps = {}
    
    def get(self, key: str):
        if key in self.cache:
            if time.time() - self.timestamps.get(key, 0) < self.ttl:
                return self.cache[key]
        return None
    
    def set(self, key: str, value):
        self.cache[key] = value
        self.timestamps[key] = time.time()
    
    def clear(self):
        self.cache.clear()
        self.timestamps.clear()

# ==================== 数据类 (来自v4.7) ====================
@dataclass
class Center:
    """中枢结构"""
    zg: float  # 中枢高点
    zd: float  # 中枢低点
    start_idx: int
    end_idx: int
    confirmed: bool = False

@dataclass
class ThirdBuySignal:
    """第三类买点"""
    detected: bool
    break_price: float
    pullback_price: float
    zg: float
    strength: int

@dataclass
class ThirdSellSignal:
    """第三类卖点"""
    detected: bool
    break_price: float
    pullback_price: float
    zd: float
    strength: int

# ==================== 信号评分系统 (来自v4.7) ====================
class SignalScore:
    """信号评分器"""
    def __init__(self):
        self.score = 0
        self.reasons = []
    
    def add(self, points: int, reason: str):
        self.score += points
        self.reasons.append(f'{reason}(+{points})')
    
    def get_result(self) -> Tuple[int, List[str]]:
        return self.score, self.reasons

# ==================== 核心指标计算 (来自v4.7) ====================
class TechnicalIndicators:
    """技术指标计算类"""
    
    @staticmethod
    def calculate_ma(closes: List[float], period: int = 34) -> List[float]:
        """计算移动平均线"""
        ma = []
        for i in range(len(closes)):
            if i < period - 1:
                ma.append(sum(closes[:i+1]) / (i+1))
            else:
                ma.append(sum(closes[i-period+1:i+1]) / period)
        return ma
    
    @staticmethod
    def calculate_ema(data: List[float], period: int) -> List[float]:
        """计算EMA"""
        multiplier = 2 / (period + 1)
        ema_vals = [data[0]]
        for price in data[1:]:
            ema_vals.append((price - ema_vals[-1]) * multiplier + ema_vals[-1])
        return ema_vals
    
    @staticmethod
    def calculate_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict:
        """计算MACD"""
        ema_fast = TechnicalIndicators.calculate_ema(closes, fast)
        ema_slow = TechnicalIndicators.calculate_ema(closes, slow)
        
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = TechnicalIndicators.calculate_ema(macd_line, signal)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]
        
        return {
            'macd_line': macd_line,
            'signal_line': signal_line,
            'histogram': histogram
        }
    
    @staticmethod
    def calculate_kdj(highs: List[float], lows: List[float], closes: List[float], 
                      n: int = 9, m1: int = 3, m2: int = 3) -> Dict:
        """计算KDJ"""
        rsv = []
        for i in range(len(closes)):
            if i < n - 1:
                rsv.append(50.0)
            else:
                period_high = max(highs[i-n+1:i+1])
                period_low = min(lows[i-n+1:i+1])
                if period_high == period_low:
                    rsv.append(50.0)
                else:
                    rsv.append((closes[i] - period_low) / (period_high - period_low) * 100)
        
        k, d, j = [], [], []
        k_prev, d_prev = 50.0, 50.0
        
        for i in range(len(rsv)):
            if i == 0:
                k_curr, d_curr = rsv[i], rsv[i]
            else:
                k_curr = (m1 - 1) / m1 * k_prev + rsv[i] / m1
                d_curr = (m2 - 1) / m2 * d_prev + k_curr / m2
            
            j_curr = 3 * k_curr - 2 * d_curr
            k.append(k_curr)
            d.append(d_curr)
            j.append(j_curr)
            k_prev, d_prev = k_curr, d_curr
        
        return {'k': k, 'd': d, 'j': j, 'rsv': rsv}
    
    @staticmethod
    def detect_macd_bullish_divergence(lows: List[float], histogram: List[float],
                                        macd_line: List[float], signal_line: List[float],
                                        closes: List[float] = None, lookback: int = 20) -> Dict:
        """检测MACD底背离"""
        if len(lows) < lookback + 5:
            return {'detected': False}
        
        recent_lows = lows[-lookback:]
        recent_hist = histogram[-lookback:]
        recent_macd = macd_line[-lookback:]
        
        # 找当前低点
        current_low_idx = min(range(len(recent_lows)), key=lambda i: recent_lows[i])
        if current_low_idx < 5:
            return {'detected': False}
        
        # 找前低点
        prev_lows = recent_lows[:current_low_idx]
        prev_hist = recent_hist[:current_low_idx]
        prev_macd = recent_macd[:current_low_idx]
        prev_low_idx = min(range(len(prev_lows)), key=lambda i: prev_lows[i])
        
        # 背离条件
        price_lower = recent_lows[current_low_idx] < prev_lows[prev_low_idx] * 0.99
        hist_higher = recent_hist[current_low_idx] > prev_hist[prev_low_idx] * 1.01
        macd_higher = recent_macd[current_low_idx] > prev_macd[prev_low_idx] * 1.01
        
        if price_lower and hist_higher and macd_higher:
            return {
                'detected': True,
                'strength': 3 if recent_macd[current_low_idx] > recent_macd[prev_low_idx] else 2,
                'price_low': recent_lows[current_low_idx],
                'prev_low': prev_lows[prev_low_idx]
            }
        return {'detected': False}
    
    @staticmethod
    def detect_macd_bearish_divergence(highs: List[float], histogram: List[float],
                                        macd_line: List[float], signal_line: List[float],
                                        lookback: int = 20) -> Dict:
        """检测MACD顶背离"""
        if len(highs) < lookback + 5:
            return {'detected': False}
        
        recent_highs = highs[-lookback:]
        recent_hist = histogram[-lookback:]
        recent_macd = macd_line[-lookback:]
        
        current_high_idx = max(range(len(recent_highs)), key=lambda i: recent_highs[i])
        if current_high_idx < 5:
            return {'detected': False}
        
        prev_highs = recent_highs[:current_high_idx]
        prev_hist = recent_hist[:current_high_idx]
        prev_macd = recent_macd[:current_high_idx]
        prev_high_idx = max(range(len(prev_highs)), key=lambda i: prev_highs[i])
        
        price_higher = recent_highs[current_high_idx] > prev_highs[prev_high_idx] * 1.01
        hist_lower = recent_hist[current_high_idx] < prev_hist[prev_high_idx] * 0.99
        macd_lower = recent_macd[current_high_idx] < prev_macd[prev_high_idx] * 0.99
        
        if price_higher and hist_lower and macd_lower:
            return {'detected': True, 'strength': 3}
        return {'detected': False}
    
    @staticmethod
    def detect_kdj_bullish_divergence(lows: List[float], k: List[float], lookback: int = 20) -> bool:
        """KDJ底背离"""
        if len(lows) < lookback + 5 or len(k) < lookback + 5:
            return False
        
        recent_lows = lows[-lookback:]
        recent_k = k[-lookback:]
        current_low_idx = min(range(len(recent_lows)), key=lambda i: recent_lows[i])
        
        if current_low_idx < 5:
            return False
        
        prev_lows = recent_lows[:current_low_idx]
        prev_k = recent_k[:current_low_idx]
        prev_low_idx = min(range(len(prev_lows)), key=lambda i: prev_lows[i])
        
        return (recent_lows[current_low_idx] < prev_lows[prev_low_idx] * 0.99 and
                recent_k[current_low_idx] > prev_k[prev_low_idx] * 1.02 and
                recent_k[current_low_idx] < 50)

# ==================== 缠论核心 (来自v4.7) ====================
class ChanlunCore:
    """缠论核心算法"""
    
    @staticmethod
    def identify_centers(highs: List[float], lows: List[float], closes: List[float],
                         lookback: int = 50) -> List[Center]:
        """识别中枢"""
        if len(closes) < lookback:
            return []
        
        centers = []
        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]
        
        # 找极值点
        def find_extremes(data, is_high=True):
            extremes = []
            for i in range(1, len(data) - 1):
                if is_high:
                    if data[i] > data[i-1] and data[i] > data[i+1]:
                        extremes.append((i, data[i]))
                else:
                    if data[i] < data[i-1] and data[i] < data[i+1]:
                        extremes.append((i, data[i]))
            return extremes
        
        high_extremes = find_extremes(recent_highs, True)
        low_extremes = find_extremes(recent_lows, False)
        
        # 找重叠区间
        if len(high_extremes) >= 2 and len(low_extremes) >= 2:
            for i in range(len(high_extremes) - 1):
                for j in range(len(low_extremes) - 1):
                    # 检查时间顺序
                    if high_extremes[i][0] < low_extremes[j][0] < high_extremes[i+1][0]:
                        zg = min(high_extremes[i][1], high_extremes[i+1][1])  # 中枢高点
                        zd = max(low_extremes[j][1], low_extremes[j+1][1])    # 中枢低点
                        
                        if zg > zd:  # 有效中枢
                            centers.append(Center(
                                zg=zg, zd=zd,
                                start_idx=high_extremes[i][0],
                                end_idx=high_extremes[i+1][0],
                                confirmed=True
                            ))
        
        return centers
    
    @staticmethod
    def detect_third_buy(highs: List[float], lows: List[float], closes: List[float],
                         lookback: int = 50) -> ThirdBuySignal:
        """检测第三类买点"""
        if len(closes) < lookback:
            return ThirdBuySignal(False, 0, 0, 0, 0)
        
        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]
        recent_closes = closes[-lookback:]
        
        centers = ChanlunCore.identify_centers(highs, lows, closes, lookback)
        if not centers:
            return ThirdBuySignal(False, 0, 0, 0, 0)
        
        center = centers[-1]
        zg = center.zg
        zd = center.zd
        
        # 检测突破中枢上沿
        break_idx = None
        break_price = 0
        for i in range(center.end_idx + 1, len(recent_closes)):
            if recent_closes[i] > zg * 1.005:
                break_idx = i
                break_price = recent_closes[i]
                break
        
        if break_idx is None:
            return ThirdBuySignal(False, 0, 0, zg, 0)
        
        # 检测回抽不进入中枢
        pullback_idx = None
        pullback_price = 0
        for i in range(break_idx + 1, min(break_idx + 10, len(recent_closes))):
            if recent_lows[i] < break_price and recent_lows[i] > zg:
                pullback_idx = i
                pullback_price = recent_lows[i]
                break
        
        if pullback_idx is None:
            return ThirdBuySignal(False, 0, 0, zg, 0)
        
        # 验证当前价格仍在ZG上方
        if recent_closes[-1] < zg * 0.995:
            return ThirdBuySignal(False, 0, 0, zg, 0)
        
        # 计算强度
        rise_pct = (recent_closes[-1] - zg) / zg
        strength = min(100, int(rise_pct * 1000))
        
        return ThirdBuySignal(True, break_price, pullback_price, zg, strength)
    
    @staticmethod
    def detect_third_sell(highs: List[float], lows: List[float], closes: List[float],
                          lookback: int = 50) -> ThirdSellSignal:
        """检测第三类卖点"""
        if len(closes) < lookback:
            return ThirdSellSignal(False, 0, 0, 0, 0)
        
        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]
        recent_closes = closes[-lookback:]
        
        centers = ChanlunCore.identify_centers(highs, lows, closes, lookback)
        if not centers:
            return ThirdSellSignal(False, 0, 0, 0, 0)
        
        center = centers[-1]
        zd = center.zd
        zg = center.zg
        
        # 检测跌破中枢下沿
        break_idx = None
        break_price = 0
        for i in range(center.end_idx + 1, len(recent_closes)):
            if recent_closes[i] < zd * 0.995:
                break_idx = i
                break_price = recent_closes[i]
                break
        
        if break_idx is None:
            return ThirdSellSignal(False, 0, 0, zd, 0)
        
        # 检测回抽不进入中枢
        pullback_idx = None
        pullback_price = 0
        for i in range(break_idx + 1, min(break_idx + 10, len(recent_closes))):
            if recent_highs[i] > break_price and recent_highs[i] < zd:
                pullback_idx = i
                pullback_price = recent_highs[i]
                break
        
        if pullback_idx is None:
            return ThirdSellSignal(False, 0, 0, zd, 0)
        
        # 验证当前价格仍在ZD下方
        if recent_closes[-1] > zd * 1.005:
            return ThirdSellSignal(False, 0, 0, zd, 0)
        
        # 计算强度
        drop_pct = (zd - recent_closes[-1]) / zd
        strength = min(100, int(drop_pct * 1000))
        
        return ThirdSellSignal(True, break_price, pullback_price, zd, strength)

# ==================== API封装 (白鸽一号) ====================
class OKXAPI:
    """OKX API封装"""
    
    def __init__(self):
        self.api_key = API_KEY
        self.secret_key = SECRET_KEY
        self.passphrase = PASSPHRASE
        self.base_url = BASE_URL
        self.cache = DataCache(ttl_seconds=3)  # 使用缓存
    
    def _generate_signature(self, timestamp, method, path, body=''):
        message = timestamp + method.upper() + path + body
        mac = hmac.new(self.secret_key.encode('utf-8'), message.encode('utf-8'), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode('utf-8')
    
    def _get_headers(self, method, path, body=''):
        timestamp = datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'
        signature = self._generate_signature(timestamp, method, path, body)
        return {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': signature,
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json'
        }
    
    def request(self, method, path, body=None):
        """通用请求方法（带缓存）"""
        cache_key = f"{method}:{path}:{body}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        
        try:
            headers = self._get_headers(method, path, body or '')
            url = f'{self.base_url}{path}'
            
            if method.upper() == 'GET':
                resp = requests.get(url, headers=headers, timeout=10)
            else:
                resp = requests.post(url, headers=headers, data=body, timeout=10)
            
            result = resp.json()
            
            # 缓存GET请求结果
            if method.upper() == 'GET':
                self.cache.set(cache_key, result)
            
            return result
        except Exception as e:
            print(f"❌ API请求错误: {e}")
            return {'code': '-1', 'msg': str(e)}
    
    def get_balance(self):
        """获取余额"""
        return self.request('GET', '/api/v5/account/balance')
    
    def get_positions(self):
        """获取持仓"""
        return self.request('GET', '/api/v5/account/positions')
    
    def get_price(self, symbol):
        """获取价格"""
        return self.request('GET', f'/api/v5/market/ticker?instId={symbol}')
    
    def get_candles(self, symbol, bar='5m', limit=100):
        """获取K线"""
        result = self.request('GET', f'/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}')
        if result.get('code') == '0' and result.get('data'):
            candles = []
            for d in result['data']:
                candles.append({
                    'timestamp': int(d[0]),
                    'open': float(d[1]),
                    'high': float(d[2]),
                    'low': float(d[3]),
                    'close': float(d[4]),
                    'volume': float(d[5])
                })
            return candles[::-1]  # 正序
        return []
    
    def set_leverage(self, symbol, lever, mgn_mode='cross'):
        """设置杠杆"""
        body = json.dumps({'instId': symbol, 'lever': str(lever), 'mgnMode': mgn_mode})
        return self.request('POST', '/api/v5/account/set-leverage', body)
    
    def place_order(self, symbol, side, sz, ord_type='market', pos_side='long'):
        """下单"""
        body = json.dumps({
            'instId': symbol,
            'tdMode': 'cross',
            'side': side,
            'posSide': pos_side,
            'ordType': ord_type,
            'sz': str(sz)
        })
        return self.request('POST', '/api/v5/trade/order', body)
    
    def close_position(self, symbol, pos_side='long'):
        """平仓"""
        body = json.dumps({'instId': symbol, 'posSide': pos_side, 'mgnMode': 'cross'})
        return self.request('POST', '/api/v5/trade/close-position', body)

# ==================== 数据库管理 (白鸽一号) ====================
class DatabaseManager:
    """数据库管理"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # 交易记录表
        c.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                quantity REAL,
                pnl REAL,
                pnl_percent REAL,
                batch INTEGER,
                open_time TIMESTAMP,
                close_time TIMESTAMP,
                close_reason TEXT
            )
        ''')
        
        # 持仓表
        c.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT UNIQUE,
                side TEXT,
                entry_price REAL,
                current_qty REAL,
                avg_price REAL,
                unrealized_pnl REAL,
                batch_count INTEGER DEFAULT 0,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 信号记录表（新增）
        c.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                signal_type TEXT,
                score INTEGER,
                reasons TEXT,
                price REAL,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def record_trade(self, symbol, side, entry_price, quantity, batch, pnl=0):
        """记录交易"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            INSERT INTO trades (symbol, side, entry_price, quantity, batch, open_time, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (symbol, side, entry_price, quantity, batch, datetime.now(), pnl))
        conn.commit()
        conn.close()
    
    def record_signal(self, symbol, signal_type, score, reasons, price):
        """记录信号"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            INSERT INTO signals (symbol, signal_type, score, reasons, price)
            VALUES (?, ?, ?, ?, ?)
        ''', (symbol, signal_type, score, json.dumps(reasons), price))
        conn.commit()
        conn.close()

# ==================== 交易类（增强版） ====================
class EnhancedTrader:
    """增强版交易类"""
    
    def __init__(self, symbol='BTC-USDT-SWAP'):
        self.symbol = symbol
        self.api = OKXAPI()
        self.db = DatabaseManager(DB_PATH)
        self.indicators = TechnicalIndicators()
        self.chanlun = ChanlunCore()
        self.batch_count = 0
        self.entry_prices = []
        self.max_profit_pct = 0
    
    def get_candles(self, bar='5m', limit=100):
        """获取K线"""
        return self.api.get_candles(self.symbol, bar, limit)
    
    def get_position(self):
        """获取持仓"""
        result = self.api.get_positions()
        if result.get('code') == '0' and result.get('data'):
            for pos in result['data']:
                if pos['instId'] == self.symbol:
                    return {
                        'pos': float(pos.get('pos', 0)),
                        'avgPx': float(pos.get('avgPx', 0)),
                        'upl': float(pos.get('upl', 0)),
                        'side': pos.get('posSide', 'long')
                    }
        return None
    
    def analyze_signals(self, bar='5m') -> Tuple[int, List[str], Dict]:
        """
        综合分析信号（融合v4.7的多指标分析）
        返回: (评分, 原因列表, 信号详情)
        """
        candles = self.get_candles(bar, 100)
        if len(candles) < 50:
            return 0, ['数据不足'], {}
        
        closes = [c['close'] for c in candles]
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        
        scorer = SignalScore()
        signals_detail = {}
        
        # 1. MACD分析
        macd = self.indicators.calculate_macd(closes)
        signals_detail['macd'] = {
            'histogram': macd['histogram'][-1],
            'macd_line': macd['macd_line'][-1],
            'signal_line': macd['signal_line'][-1]
        }
        
        # MACD金叉
        if macd['macd_line'][-1] > macd['signal_line'][-1] and macd['macd_line'][-2] <= macd['signal_line'][-2]:
            scorer.add(2, 'MACD金叉')
        
        # MACD底背离
        div = self.indicators.detect_macd_bullish_divergence(lows, macd['histogram'], 
                                                              macd['macd_line'], macd['signal_line'], closes)
        if div['detected']:
            scorer.add(3 if div['strength'] >= 3 else 2, f"MACD底背离(强度{div['strength']})")
            signals_detail['macd_divergence'] = div
        
        # 2. KDJ分析
        kdj = self.indicators.calculate_kdj(highs, lows, closes)
        signals_detail['kdj'] = {'k': kdj['k'][-1], 'd': kdj['d'][-1], 'j': kdj['j'][-1]}
        
        # KDJ金叉
        if kdj['k'][-1] > kdj['d'][-1] and kdj['k'][-2] <= kdj['d'][-2] and kdj['k'][-1] < 40:
            scorer.add(2, f"KDJ金叉(K:{kdj['k'][-1]:.1f})")
        
        # KDJ底背离
        if self.indicators.detect_kdj_bullish_divergence(lows, kdj['k']):
            scorer.add(2, 'KDJ底背离')
        
        # 3. 缠论3买检测
        third_buy = self.chanlun.detect_third_buy(highs, lows, closes)
        if third_buy.detected:
            scorer.add(4, f"缠论3买(强度{third_buy.strength})")
            signals_detail['third_buy'] = {
                'zg': third_buy.zg,
                'break_price': third_buy.break_price,
                'strength': third_buy.strength
            }
        
        # 4. 均线分析
        ma5 = self.indicators.calculate_ma(closes, 5)[-1]
        ma10 = self.indicators.calculate_ma(closes, 10)[-1]
        ma20 = self.indicators.calculate_ma(closes, 20)[-1]
        signals_detail['ma'] = {'ma5': ma5, 'ma10': ma10, 'ma20': ma20}
        
        if ma5 > ma10 > ma20:
            scorer.add(1, '多头排列')
        if ma5 > ma10 and closes[-1] > ma5:
            scorer.add(1, '价格站上MA5')
        
        # 5. 反弹检测
        last_24h_low = min(lows[-288:]) if len(lows) >= 288 else min(lows)
        rebound_pct = (closes[-1] - last_24h_low) / last_24h_low
        if rebound_pct >= REBOUND_THRESHOLD and rebound_pct < 0.15:
            scorer.add(2, f"反弹{rebound_pct*100:.1f}%")
            signals_detail['rebound'] = rebound_pct
        
        score, reasons = scorer.get_result()
        return score, reasons, signals_detail
    
    def should_enter(self) -> Tuple[bool, Dict]:
        """是否应该入场"""
        # 检查是否已有持仓
        position = self.get_position()
        if position and position['pos'] > 0:
            return False, {'reason': '已有持仓'}
        
        # 多时间框架分析
        score_5m, reasons_5m, details_5m = self.analyze_signals('5m')
        score_15m, reasons_15m, details_15m = self.analyze_signals('15m')
        
        # 综合评分
        total_score = score_5m + score_15m * 0.5  # 15分钟权重稍低
        
        result = {
            'score_5m': score_5m,
            'score_15m': score_15m,
            'total_score': total_score,
            'reasons_5m': reasons_5m,
            'reasons_15m': reasons_15m,
            'details_5m': details_5m,
            'details_15m': details_15m
        }
        
        # 入场条件：总分>=3且5分钟>=2
        if total_score >= MIN_SIGNAL_SCORE and score_5m >= 2:
            result['should_enter'] = True
            return True, result
        
        result['should_enter'] = False
        return False, result
    
    def calculate_position_size(self, balance: float, price: float, batch: int = 0) -> float:
        """计算仓位大小"""
        # 获取合约面值
        ct_val = 0.01 if 'BTC' in self.symbol else 0.1 if 'ETH' in self.symbol else 1
        
        # 计算张数
        position_value = balance * POSITION_PERCENT * BATCH_SIZES[batch]
        contracts = (position_value * LEVERAGE) / (ct_val * price)
        
        return round(contracts, 2)
    
    def open_position(self, batch: int = 0):
        """开仓"""
        # 设置杠杆
        self.api.set_leverage(self.symbol, LEVERAGE)
        
        # 获取余额
        balance_result = self.api.get_balance()
        if balance_result.get('code') != '0':
            print(f"❌ 获取余额失败")
            return False
        
        balance = float(balance_result['data'][0]['details'][0].get('availEq', 0))
        
        # 获取价格
        price_result = self.api.get_price(self.symbol)
        if price_result.get('code') != '0':
            print(f"❌ 获取价格失败")
            return False
        
        price = float(price_result['data'][0]['last'])
        
        # 计算仓位
        sz = self.calculate_position_size(balance, price, batch)
        
        if sz < 0.01:
            print(f"⚠️ 仓位太小: {sz}")
            return False
        
        # 下单
        result = self.api.place_order(self.symbol, 'buy', sz, pos_side='long')
        
        if result.get('code') == '0':
            print(f"✅ 开仓成功: {self.symbol} {sz}张 @ {price}")
            self.db.record_trade(self.symbol, 'long', price, sz, batch)
            self.entry_prices.append(price)
            self.batch_count = batch + 1
            return True
        else:
            print(f"❌ 开仓失败: {result.get('msg')}")
            return False
    
    def check_exit(self) -> Tuple[bool, str]:
        """检查是否应该出场"""
        position = self.get_position()
        if not position or position['pos'] <= 0:
            return False, '无持仓'
        
        avg_price = position['avgPx']
        current_pnl_pct = position['upl'] / (position['pos'] * avg_price / LEVERAGE)
        
        # 更新最大盈利
        if current_pnl_pct > self.max_profit_pct:
            self.max_profit_pct = current_pnl_pct
        
        # 1. 固定止损 -5%
        if current_pnl_pct <= -0.05:
            return True, f'固定止损({current_pnl_pct*100:.2f}%)'
        
        # 2. 移动止损: 盈利>4%后回撤40%
        if self.max_profit_pct >= ACTIVATION_PERCENT:
            callback_pct = self.max_profit_pct - current_pnl_pct
            if callback_pct >= self.max_profit_pct * CALLBACK_RATIO:
                return True, f'移动止损(最大盈利{self.max_profit_pct*100:.2f}%, 回撤{callback_pct*100:.2f}%)'
        
        # 3. 检测顶背离出场
        candles = self.get_candles('5m', 50)
        if len(candles) >= 30:
            closes = [c['close'] for c in candles]
            highs = [c['high'] for c in candles]
            macd = self.indicators.calculate_macd(closes)
            
            bear_div = self.indicators.detect_macd_bearish_divergence(
                highs, macd['histogram'], macd['macd_line'], macd['signal_line']
            )
            if bear_div['detected'] and current_pnl_pct > 0.02:
                return True, f'顶背离出场({current_pnl_pct*100:.2f}%)'
        
        return False, f'持仓中(盈亏{current_pnl_pct*100:.2f}%)'
    
    def monitor(self):
        """主监控循环"""
        print(f"\n🕊️ 白鸽一号增强版监控: {self.symbol}")
        
        # 检查出场
        should_exit, exit_reason = self.check_exit()
        if should_exit:
            print(f"📤 平仓信号: {exit_reason}")
            result = self.api.close_position(self.symbol, 'long')
            if result.get('code') == '0':
                print(f"✅ 平仓成功")
                self.batch_count = 0
                self.entry_prices = []
                self.max_profit_pct = 0
            return
        
        # 检查入场
        should_enter, signal_info = self.should_enter()
        
        if should_enter:
            print(f"📥 入场信号: 综合评分 {signal_info['total_score']:.1f}")
            print(f"   5分钟: {signal_info['score_5m']}分 - {', '.join(signal_info['reasons_5m'])}")
            print(f"   15分钟: {signal_info['score_15m']}分 - {', '.join(signal_info['reasons_15m'])}")
            
            # 记录信号
            self.db.record_signal(
                self.symbol, 'entry',
                int(signal_info['total_score']),
                signal_info['reasons_5m'] + signal_info['reasons_15m'],
                signal_info['details_5m'].get('macd', {}).get('macd_line', 0)
            )
            
            # 执行开仓
            self.open_position(batch=0)
        else:
            if signal_info.get('total_score', 0) > 0:
                print(f"⏳ 信号评分: {signal_info['total_score']:.1f}/3 (5m:{signal_info['score_5m']}, 15m:{signal_info['score_15m']})")

# ==================== 多币种监控器 ====================
class MultiSymbolMonitor:
    """多币种监控"""
    
    def __init__(self, symbols):
        self.symbols = symbols
        self.traders = {s: EnhancedTrader(s) for s in symbols}
        self.api = OKXAPI()
    
    def get_all_positions(self):
        """获取所有持仓"""
        result = self.api.get_positions()
        positions = {}
        if result.get('code') == '0' and result.get('data'):
            for pos in result['data']:
                if float(pos.get('pos', 0)) > 0:
                    positions[pos['instId']] = {
                        'pos': float(pos['pos']),
                        'avgPx': float(pos.get('avgPx', 0)),
                        'upl': float(pos.get('upl', 0)),
                        'side': pos.get('posSide', 'long')
                    }
        return positions
    
    def scan_all(self):
        """扫描所有币种"""
        positions = self.get_all_positions()
        
        # 先处理有持仓的
        for symbol, pos in positions.items():
            if symbol in self.traders:
                trader = self.traders[symbol]
                should_exit, reason = trader.check_exit()
                
                pnl_pct = pos['upl'] / (pos['pos'] * pos['avgPx'] / LEVERAGE) * 100
                emoji = "🟢" if pnl_pct > 0 else "🔴"
                print(f"\n{emoji} {symbol} | 持仓{pos['pos']}张 | 均价{pos['avgPx']:.2f} | 盈亏{pnl_pct:+.2f}%")
                print(f"   状态: {reason}")
                
                if should_exit:
                    print(f"   📤 执行平仓...")
                    result = self.api.close_position(symbol, 'long')
                    if result.get('code') == '0':
                        print(f"   ✅ 平仓成功")
        
        # 再找入场机会
        if len(positions) < 3:  # 最多3个持仓
            print(f"\n🔍 扫描入场机会 (当前持仓: {len(positions)}/3)...")
            
            opportunities = []
            for symbol, trader in self.traders.items():
                if symbol not in positions:
                    should_enter, info = trader.should_enter()
                    if should_enter:
                        opportunities.append((symbol, info['total_score'], info))
            
            # 按评分排序
            opportunities.sort(key=lambda x: x[1], reverse=True)
            
            for symbol, score, info in opportunities[:2]:  # 最多开2个新仓
                print(f"\n📈 {symbol}: 评分 {score:.1f}")
                print(f"   5m: {info['reasons_5m']}")
                print(f"   15m: {info['reasons_15m']}")
                self.traders[symbol].open_position(batch=0)

# ==================== 主程序 ====================
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='🕊️ 白鸽一号增强版 v2.1')
    parser.add_argument('mode', choices=['monitor', 'scan', 'test'], help='运行模式')
    parser.add_argument('--symbol', default='BTC-USDT-SWAP', help='交易币种')
    
    args = parser.parse_args()
    
    if args.mode == 'monitor':
        # 单币种监控
        trader = EnhancedTrader(args.symbol)
        while True:
            try:
                trader.monitor()
                time.sleep(SIGNAL_CHECK_INTERVAL)
            except KeyboardInterrupt:
                print("\n👋 停止监控")
                break
            except Exception as e:
                print(f"❌ 错误: {e}")
                time.sleep(5)
    
    elif args.mode == 'scan':
        # 多币种扫描
        monitor = MultiSymbolMonitor(SYMBOLS)
        while True:
            try:
                print(f"\n{'='*60}")
                print(f"🕊️ 白鸽一号扫描 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                monitor.scan_all()
                time.sleep(30)  # 每30秒扫描一次
            except KeyboardInterrupt:
                print("\n👋 停止扫描")
                break
            except Exception as e:
                print(f"❌ 错误: {e}")
                time.sleep(10)
    
    elif args.mode == 'test':
        # 测试模式
        print("🧪 测试模式")
        trader = EnhancedTrader(args.symbol)
        
        print(f"\n测试 {args.symbol} 信号分析:")
        should_enter, info = trader.should_enter()
        
        print(f"综合评分: {info.get('total_score', 0):.1f}")
        print(f"5分钟评分: {info.get('score_5m', 0)}")
        print(f"15分钟评分: {info.get('score_15m', 0)}")
        print(f"5分钟原因: {info.get('reasons_5m', [])}")
        print(f"15分钟原因: {info.get('reasons_15m', [])}")
        
        if 'details_5m' in info:
            print(f"\nMACD详情: {info['details_5m'].get('macd', {})}")
            print(f"KDJ详情: {info['details_5m'].get('kdj', {})}")
            if 'third_buy' in info['details_5m']:
                print(f"缠论3买: {info['details_5m']['third_buy']}")
