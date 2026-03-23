#!/usr/bin/env python3
"""
OKX自动化交易系统 v2.0
基于OKX API Guide构建，支持20倍杠杆+移动止损+分级加仓
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
import os

# 添加清算热力图信号模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from liq_signal_module import LiquidationSignal

# 添加清算地图学习系统
try:
    from liqmap_learning import LiqMapLearningSystem, record_liqmap_reduce
    LIQMAP_LEARNING_ENABLED = True
    print('[系统] 清算地图学习系统已加载')
except ImportError:
    LIQMAP_LEARNING_ENABLED = False
    print('[系统] 清算地图学习系统未加载')

# 添加宏观过滤器
try:
    from macro_filter import MacroFilter, quick_macro_check
    MACRO_FILTER_ENABLED = True
    print('[系统] 宏观过滤器已加载')
except ImportError:
    MACRO_FILTER_ENABLED = False
    print('[系统] 宏观过滤器未加载')

# 导入减仓模块
from reduce_module import ReduceManager

# 导入做空模块
from short_module import ShortManager

# 导入摩尔缠论核心模块
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'moer-chanlun'))
try:
    from core.zhongshu import detect_zhongshu, calculate_ma34, find_peaks_troughs
    from core.fenxing import detect_fenxing, FenXingType
    MOER_CHANLUN_AVAILABLE = True
except ImportError:
    MOER_CHANLUN_AVAILABLE = False
    print("⚠️ 摩尔缠论模块未找到，使用简化版缠论检测")

# 导入摩尔缠论背驰系统
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from moer_divergence_system import (
        get_divergence_summary, 
        check_multi_timeframe_divergence,
        check_top_divergence_area,
        calculate_macd
    )
    MOER_DIVERGENCE_AVAILABLE = True
    print('[系统] 摩尔缠论背驰系统已加载')
except ImportError as e:
    MOER_DIVERGENCE_AVAILABLE = False
    print(f"⚠️ 摩尔缠论背驰系统未加载: {e}")

# ==================== 配置区 ====================
API_KEY = '7dae93fe-2a48-4f2b-a889-9dbf30601068'
SECRET_KEY = '9C73FAEF39647DF6FBD06229CA4F0F4C'
PASSPHRASE = 'Lxc@2026888'
BASE_URL = 'https://www.okx.com'
SYMBOLS = [
    'BTC-USDT-SWAP',
    'ETH-USDT-SWAP',
    'SOL-USDT-SWAP',
    'DOGE-USDT-SWAP',
    'XRP-USDT-SWAP',
    'TRX-USDT-SWAP',
    'LTC-USDT-SWAP',
    'BCH-USDT-SWAP',
    'FIL-USDT-SWAP',  # Filecoin
]
DB_PATH = '/root/.openclaw/workspace/trading.db'

# 交易参数
LEVERAGE = 20                # 杠杆倍数
POSITION_PERCENT = 0.20      # 单币种仓位比例（20%）
MAX_DAILY_LOSS_PERCENT = 0.06 # 单日最大亏损6%
MAX_POSITIONS = 5            # 最大同时持仓币种数（用户设定）

# 移动止损参数 - 趋势策略：让利润奔跑
ACTIVATION_PERCENT = 0.05    # 激活价+5% (给趋势足够空间)
CALLBACK_RATIO = 0.45        # 回撤45%触发 (容忍较大回撤，吃完整段)

# 趋势策略：阶梯止盈 (宽松)
PROFIT_TIERS = [
    {'profit': 0.08, 'reduce': 0.25},   # +8% 减仓25% (锁定部分利润)
    {'profit': 0.15, 'reduce': 0.25},   # +15% 再减仓25% (趋势中段)
    {'profit': 0.25, 'reduce': 1.00},   # +25% 清仓 (大趋势结束)
]

# 加仓参数 - 真正的3批加仓
BATCH_SIZES = [0.50, 0.30, 0.20]  # 第一批50%，第二批30%，第三批20%

# ==================== 减仓策略配置 ====================
# 方案：背离信号减仓 + 移动止损
REDUCE_STRATEGY = {
    'enabled': True,                    # 是否启用分批减仓
    'targets': [],                      # 阶梯止盈目标（已禁用，只用背离信号）
    'trailing_after_last': True,        # 启用移动止损
}

# 背离减仓参数 - 优化后：更强的减仓力度
DIVERGENCE_REDUCE_RATIO = 0.70      # 背离信号减仓70% (原50%，更强信号)
DIVERGENCE_COOLDOWN_SECONDS = 60    # 背离检测冷却时间（秒）

# 趋势线跌破减仓参数
TRENDLINE_REDUCE_RATIO = 0.70       # 跌破趋势线减仓70%
TRENDLINE_COOLDOWN_SECONDS = 120    # 趋势线检测冷却时间（秒）
TRENDLINE_LOOKBACK = 20             # 趋势线回看K线数

# 清算地图减仓参数 - 币种自适应阈值
LIQMAP_REDUCE_RATIO = 0.50          # 接近清算密集区减仓50%
LIQMAP_COOLDOWN_SECONDS = 180       # 清算地图检测冷却时间（秒）

# 币种自适应阈值配置 (基于波动率)
LIQMAP_SYMBOL_CONFIG = {
    # 低波动主流币 - 较小阈值，更敏感
    'BTC-USDT-SWAP': {
        'distance_threshold': 0.012,   # 1.2%
        'min_liquidity': 25.0,         # 25M
        'description': 'BTC波动较小，阈值更敏感'
    },
    'ETH-USDT-SWAP': {
        'distance_threshold': 0.015,   # 1.5%
        'min_liquidity': 20.0,         # 20M
        'description': 'ETH标准配置'
    },
    # 中等波动
    'SOL-USDT-SWAP': {
        'distance_threshold': 0.020,   # 2.0%
        'min_liquidity': 15.0,         # 15M
        'description': 'SOL波动中等'
    },
    'BCH-USDT-SWAP': {
        'distance_threshold': 0.020,   # 2.0%
        'min_liquidity': 10.0,         # 10M
        'description': 'BCH波动中等'
    },
    'LTC-USDT-SWAP': {
        'distance_threshold': 0.020,   # 2.0%
        'min_liquidity': 10.0,         # 10M
        'description': 'LTC波动中等'
    },
    # 高波动/Meme币 - 较大阈值，避免假信号
    'DOGE-USDT-SWAP': {
        'distance_threshold': 0.030,   # 3.0%
        'min_liquidity': 8.0,          # 8M
        'description': 'DOGE波动大，阈值放宽'
    },
    'XRP-USDT-SWAP': {
        'distance_threshold': 0.025,   # 2.5%
        'min_liquidity': 12.0,         # 12M
        'description': 'XRP波动较大'
    },
    'TRX-USDT-SWAP': {
        'distance_threshold': 0.025,   # 2.5%
        'min_liquidity': 8.0,          # 8M
        'description': 'TRX波动较大'
    },
    'FIL-USDT-SWAP': {
        'distance_threshold': 0.025,   # 2.5%
        'min_liquidity': 8.0,          # 8M
        'description': 'FIL波动较大'
    }
}

# 默认配置（未配置的币种使用）
LIQMAP_DEFAULT_CONFIG = {
    'distance_threshold': 0.020,   # 2.0%
    'min_liquidity': 15.0,         # 15M
    'description': '默认配置'
}

# 信号参数
REBOUND_THRESHOLD = 0.03     # 反弹3%触发第一次开仓
SIGNAL_CHECK_INTERVAL = 10   # 每10秒检查一次信号

# ==================== 风控升级配置 (2026-03-14) ====================
# 时间止损参数 (2026-03-21 启用 - 针对持仓过久问题)
TIME_STOP_LOSS = {
    'enabled': True,           # ✅ 已启用 - 持仓超48小时无盈利减仓
    'max_hold_hours': 48,      # 最大持仓时间48小时(2天)
    'reduce_if_no_profit': True,  # 超期无盈利减仓
    'reduce_ratio': 0.50,      # 减仓50%
}

# ATR波动率止损参数
ATR_STOP_LOSS = {
    'enabled': True,           # 是否启用ATR止损
    'atr_period': 14,          # ATR计算周期
    'atr_multiplier': 2.0,     # ATR倍数(止损距离)
    'min_stop_pct': 0.015,     # 最小止损1.5%
    'max_stop_pct': 0.08,      # 最大止损8%
}

# 单日风控参数
DAILY_RISK_CONTROL = {
    'max_trades_per_day': 10,  # 单日最大交易次数
    'max_loss_per_day': 0.06,  # 单日最大亏损6%
    'cooldown_after_loss': 2,  # 亏损后冷却时间(小时)
}

# 相关性风控参数
CORRELATION_CONTROL = {
    'enabled': True,           # 是否启用相关性风控
    'max_same_direction': 2,   # 同向持仓最大币种数
    'high_correlation_pairs': [  # 高相关性币种对
        ('BTC', 'ETH'),
        ('ETH', 'SOL'),
        ('DOGE', 'SHIB'),  # 如果交易SHIB
    ]
}

# ==================== API封装类 ====================
class OKXAPI:
    """OKX API封装类"""
    
    def __init__(self):
        self.api_key = API_KEY
        self.secret_key = SECRET_KEY
        self.passphrase = PASSPHRASE
        self.base_url = BASE_URL
    
    def _generate_signature(self, timestamp, method, path, body=''):
        """生成签名"""
        message = timestamp + method.upper() + path + body
        mac = hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode('utf-8')
    
    def _get_headers(self, method, path, body=''):
        """获取请求头"""
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
        """通用请求方法"""
        try:
            headers = self._get_headers(method, path, body or '')
            url = f'{self.base_url}{path}'
            
            if method.upper() == 'GET':
                resp = requests.get(url, headers=headers, timeout=10)
            else:
                resp = requests.post(url, headers=headers, data=body, timeout=10)
            
            result = resp.json()
            
            if result.get('code') != '0':
                print(f"⚠️ API错误: {result.get('msg')} (code: {result.get('code')})")
            
            return result
        except Exception as e:
            print(f"❌ 请求异常: {e}")
            return {'code': '-1', 'msg': str(e)}
    
    # ---------------- 账户类API ----------------
    def get_balance(self):
        """查余额"""
        return self.request('GET', '/api/v5/account/balance')
    
    def get_positions(self):
        """查持仓"""
        return self.request('GET', '/api/v5/account/positions')
    
    def set_leverage(self, symbol, lever, mgn_mode='cross'):
        """设置杠杆"""
        body = json.dumps({
            'instId': symbol,
            'lever': str(lever),
            'mgnMode': mgn_mode
        })
        return self.request('POST', '/api/v5/account/set-leverage', body)
    
    # ---------------- 行情类API ----------------
    def get_price(self, symbol):
        """查最新价格"""
        return self.request('GET', f'/api/v5/market/ticker?instId={symbol}')
    
    def get_candles(self, symbol, bar='5m', limit=100):
        """查K线"""
        return self.request('GET', f'/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}')
    
    # ---------------- 交易类API ----------------
    def place_order(self, symbol, side, sz, ord_type='market', pos_side='long'):
        """下单"""
        body = json.dumps({
            'instId': symbol,
            'tdMode': 'cross',          # 全仓模式
            'side': side,               # buy/sell
            'posSide': pos_side,        # long/short
            'ordType': ord_type,        # market/limit
            'sz': str(sz)               # 数量
        })
        return self.request('POST', '/api/v5/trade/order', body)
    
    def close_position(self, symbol=None, pos_side='long'):
        """市价平仓"""
        inst_id = symbol if symbol else SYMBOL
        body = json.dumps({
            'instId': inst_id,
            'posSide': pos_side,
            'mgnMode': 'cross'
        })
        return self.request('POST', '/api/v5/trade/close-position', body)
    
    def get_orders(self, symbol):
        """查订单"""
        return self.request('GET', f'/api/v5/trade/orders-pending?instId={symbol}')


# ==================== 交易逻辑类 ====================
class Trader:
    """交易逻辑类"""
    
    def __init__(self, symbol='BTC-USDT-SWAP'):
        self.symbol = symbol
        self.api = OKXAPI()
        self.db_path = DB_PATH
        self._init_db()
        
        # 初始化清算热力图信号
        self.liq_signal = LiquidationSignal()
        self._init_liquidation_zones()
        
        # 提取币种名称用于显示
        self.coin = symbol.split('-')[0]
    
    def _init_liquidation_zones(self):
        """初始化清算密集区数据 - 异步加载，不阻塞"""
        # 先加载默认数据确保系统可用
        self._load_default_zones()
        
        # 尝试异步更新爬虫数据
        import threading
        def async_update():
            try:
                from coinglass_manager import CoinglassDataManager
                manager = CoinglassDataManager()
                zones = manager.get_all_zones()
                
                if zones and len(zones) > 0:
                    self.liq_signal.update_zones(zones)
                    # 只在首次加载或数据变化时输出
                    if not hasattr(self, '_last_liq_count') or self._last_liq_count != len(zones):
                        print(f'✅ 清算数据已更新: {len(zones)}个币种')
                        self._last_liq_count = len(zones)
                else:
                    # 尝试获取一次（非阻塞超时）
                    import subprocess
                    result = subprocess.run(
                        ['python3', 'coinglass_manager.py'],
                        cwd='/root/.openclaw/workspace',
                        capture_output=True,
                        timeout=30
                    )
                    if result.returncode == 0:
                        zones = manager.get_all_zones()
                        if zones:
                            self.liq_signal.update_zones(zones)
                            if not hasattr(self, '_last_liq_count'):
                                self._last_liq_count = 0
                            if self._last_liq_count != len(zones):
                                print(f'✅ 爬虫数据更新: {len(zones)}个币种')
                                self._last_liq_count = len(zones)
            except Exception as e:
                # 静默处理异常，避免刷屏
                pass
        
        # 启动后台线程更新
        thread = threading.Thread(target=async_update, daemon=True)
        thread.start()
        # 只在首次显示加载提示
        if not hasattr(self, '_liq_data_loaded'):
            print('⏳ 清算数据异步加载中...')
            self._liq_data_loaded = True
    
    def _load_default_zones(self):
        """加载默认模拟数据"""
        self.liq_signal.update_zones({
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

    def _init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 交易记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date DATETIME,
                symbol TEXT,
                direction TEXT,
                entry_price REAL,
                exit_price REAL,
                position_size REAL,
                pnl_usdt REAL,
                strategy_tag TEXT,
                notes TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 减仓记录表 - 跟踪分批减仓状态
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reduce_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                entry_price REAL,
                original_size REAL,
                reduced_count INTEGER DEFAULT 0,
                reduced_size REAL DEFAULT 0,
                remaining_size REAL,
                targets_hit TEXT DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 做空记录表 - 跟踪做空状态
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS short_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                entry_price REAL,
                original_size REAL,
                reduced_count INTEGER DEFAULT 0,
                reduced_size REAL DEFAULT 0,
                remaining_size REAL,
                targets_hit TEXT DEFAULT '[]',
                direction TEXT DEFAULT 'short',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def get_balance_usdt(self):
        """获取USDT余额"""
        result = self.api.get_balance()
        if result.get('code') == '0':
            for detail in result['data'][0].get('details', []):
                if detail.get('ccy') == 'USDT':
                    return float(detail.get('availEq', 0))
        return 0
    
    def get_position(self):
        """获取当前持仓"""
        result = self.api.get_positions()
        if result.get('code') == '0' and result['data']:
            for pos in result['data']:
                if pos.get('instId') == self.symbol:
                    return {
                        'pos': float(pos.get('pos', 0)),
                        'avg_px': float(pos.get('avgPx', 0) or 0),
                        'lever': int(pos.get('lever', 1)),
                        'liq_px': float(pos.get('liqPx', 0) or 0),
                        'upl': float(pos.get('upl', 0) or 0),
                        'mgn_mode': pos.get('mgnMode', 'cross')
                    }
        return None
    
    def _get_contract_value(self):
        """获取合约面值 (ctVal)"""
        ct_vals = {
            'BTC': 0.01,
            'ETH': 0.1,
            'SOL': 1.0,
            'DOGE': 1000.0,
            'XRP': 100.0,
            'TRX': 100.0,
            'LTC': 1.0,
            'BCH': 0.1,
            'FIL': 0.1,  # Filecoin
        }
        return ct_vals.get(self.coin, 0.01)

    def calculate_position_size(self, batch=1):
        """计算下单数量 - 分批建仓 (sz参数单位是张数!)
        batch: 1=第一批(30%), 2=第二批(40%), 3=第三批(30%)
        根据宏观过滤器动态调整仓位比例
        """
        balance = self.get_balance_usdt()
        
        # 获取合约面值
        ct_val = self._get_contract_value()
        
        # 分批比例
        batch_ratios = {1: 0.30, 2: 0.40, 3: 0.30}
        ratio = batch_ratios.get(batch, 0.30)
        
        # 根据宏观过滤器动态调整仓位比例
        position_pct = POSITION_PERCENT  # 默认20%
        if MACRO_FILTER_ENABLED:
            try:
                from macro_filter import MacroFilter
                macro_filter = MacroFilter()
                params = macro_filter.get_trading_params()
                position_pct = params.get('position_pct', POSITION_PERCENT)
                if position_pct != POSITION_PERCENT:
                    print(f'   📊 宏观调整: 仓位比例 {POSITION_PERCENT*100:.0f}% → {position_pct*100:.0f}% ({macro_filter.risk_level})')
            except:
                pass
        
        # 单币种最大仓位比例 × 分批比例 × 20倍杠杆
        max_margin = balance * position_pct * ratio
        nominal_position = max_margin * LEVERAGE
        
        # 获取价格
        price_data = self.api.get_price(self.symbol)
        if price_data.get('code') == '0':
            price = float(price_data['data'][0]['last'])
        else:
            price = 0
            
        if price > 0:
            # 计算目标张数 (1张 = ct_val个币)
            target_contracts = nominal_position / (ct_val * price)
            # 至少开最小单位(0.01张)
            contracts = max(0.01, round(target_contracts, 2))
        else:
            contracts = 0.01
        
        # sz参数是张数! (不是币的数量)
        # 根据合约精度格式化
        if self.coin in ['BTC', 'ETH']:
            return f"{contracts:.2f}"
        else:
            return f"{contracts:.1f}"
    
    def get_current_batch(self):
        """获取当前是第几批加仓 - 根据持仓比例判断"""
        position = self.get_position()
        if not position or position['pos'] == 0:
            return 1  # 无持仓，第一批
        
        # 计算目标总仓位（张数）
        target_total = float(self.calculate_position_size())
        current_pos = position['pos']
        
        # 根据持仓比例判断批次
        ratio = current_pos / target_total if target_total > 0 else 0
        
        if ratio < BATCH_SIZES[0] * 1.2:  # 第一批范围内（允许一点误差）
            return 2  # 第二批
        elif ratio < (BATCH_SIZES[0] + BATCH_SIZES[1]) * 1.1:
            return 3  # 第三批
        else:
            return None  # 已满仓，不再加仓
    
    def record_trade(self, direction, entry_price, size, pnl=None, notes=''):
        """记录交易"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.now()
        cursor.execute('''
            INSERT INTO trades (trade_date, symbol, direction, entry_price, 
                              position_size, pnl_usdt, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (now, self.symbol, direction, entry_price, size * 100, pnl, notes, now))
        
        conn.commit()
        conn.close()
    
    def open_long(self, batch=None):
        """开多单 - 分批建仓"""
        print('\n' + '='*60)
        print(f'🔥 [{self.coin}] 执行开多单')
        print('='*60)
        
        # 确定批次
        if batch is None:
            batch = self.get_current_batch()
        
        batch_names = {1: f'第一批 ({BATCH_SIZES[0]*100:.0f}%)', 
                       2: f'第二批 ({BATCH_SIZES[1]*100:.0f}%)', 
                       3: f'第三批 ({BATCH_SIZES[2]*100:.0f}%)'}
        print(f'📦 {batch_names.get(batch, "加仓")}')
        
        # 查余额
        balance = self.get_balance_usdt()
        print(f'💰 可用余额: {balance:.2f} USDT')
        
        # 计算下单量（按批次）
        sz = self.calculate_position_size(batch)
        contracts = float(sz)  # sz现在是张数
        ct_val = self._get_contract_value()
        coin_amount = contracts * ct_val  # 币的数量
        
        price_data = self.api.get_price(self.symbol)
        price = float(price_data['data'][0]['last']) if price_data.get('code') == '0' else 0
        
        print(f'📊 下单: {sz} 张 ({coin_amount:.4f} {self.coin}) @ ~${price:.2f}')
        print(f'   名义价值: ${contracts * ct_val * price:.2f}')
        print(f'   占用保证金: ~{contracts * ct_val * price / LEVERAGE:.2f} USDT')
        
        # 设置杠杆
        print(f'⚙️ 设置{LEVERAGE}倍杠杆...')
        self.api.set_leverage(self.symbol, LEVERAGE)
        
        # 下单
        print('📈 市价开多...')
        result = self.api.place_order(self.symbol, 'buy', sz)
        
        if result.get('code') == '0':
            ord_id = result['data'][0]['ordId']
            print(f'✅ 下单成功! 订单ID: {ord_id}')
            
            # 记录
            price_data = self.api.get_price(self.symbol)
            if price_data.get('code') == '0':
                entry = float(price_data['data'][0]['last'])
                note = f'第{batch}批建仓 | 订单:{ord_id}'
                # sz是张数，转换为币的数量记录
                ct_val = self._get_contract_value()
                coin_amount = float(sz) * ct_val
                self.record_trade('多/Long', entry, coin_amount, notes=note)
            
            return True
        else:
            print(f'❌ 下单失败: {result.get("msg")}')
            return False
    
    def close_long(self, close_reason='平仓'):
        """平多单
        close_reason: 平仓原因，用于记录
        """
        print('\n' + '='*60)
        print(f'🔥 [{self.coin}] 执行平多单 - {close_reason}')
        print('='*60)
        
        # 先获取当前持仓信息用于计算盈亏
        position = self.get_position()
        entry_price = position['avg_px'] if position else 0
        position_size = position['pos'] if position else 0
        
        result = self.api.close_position(self.symbol, 'long')
        
        if result.get('code') == '0':
            print('✅ 平仓成功!')
            
            # 获取平仓价格
            price_data = self.api.get_price(self.symbol)
            if price_data.get('code') == '0':
                exit_price = float(price_data['data'][0]['last'])
            else:
                exit_price = 0
            
            # 计算盈亏
            if entry_price > 0 and exit_price > 0 and position_size > 0:
                # 获取合约面值
                ct_val = self._get_contract_value()
                coin_amount = position_size * ct_val
                pnl = (exit_price - entry_price) * coin_amount
                pnl_percent = (exit_price - entry_price) / entry_price if entry_price != 0 else 0
            else:
                pnl = 0
                pnl_percent = 0
            
            # 更新 trades 表记录
            self._update_trade_record(exit_price, pnl, pnl_percent, close_reason)
            
            print(f'💰 盈亏: {pnl:.4f} USDT ({pnl_percent*100:.2f}%)')
            
            # 清除减仓记录
            self._clear_reduce_record()
            return True
        else:
            print(f'❌ 平仓失败: {result.get("msg")}')
            return False
    
    def _update_trade_record(self, exit_price, pnl, pnl_percent, notes=''):
        """更新交易记录（平仓时调用）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 找到该币种最近一次没有exit_price的开仓记录
        cursor.execute('''
            SELECT id, entry_price, position_size FROM trades 
            WHERE symbol = ? AND exit_price IS NULL AND direction = '多/Long'
            ORDER BY trade_date DESC LIMIT 1
        ''', (self.symbol,))
        
        record = cursor.fetchone()
        if record:
            trade_id = record[0]
            cursor.execute('''
                UPDATE trades 
                SET exit_price = ?, pnl_usdt = ?, pnl_percent = ?, notes = notes || ' | ' || ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (exit_price, pnl, pnl_percent, notes, trade_id))
            conn.commit()
            print(f'📝 已更新交易记录 #{trade_id}: exit={exit_price}, pnl={pnl:.4f}')
        else:
            print(f'⚠️ 未找到未平仓的交易记录')
        
        conn.close()
    
    def reduce_position(self, reduce_ratio=0.5):
        """
        部分减仓
        reduce_ratio: 减仓比例 (0.0-1.0)，默认减仓50%
        """
        print('\n' + '='*60)
        print(f'📉 [{self.coin}] 执行部分减仓 {reduce_ratio*100:.0f}%')
        print('='*60)
        
        position = self.get_position()
        if not position or position['pos'] == 0:
            print('⏸️ 无持仓，无需减仓')
            return False
        
        current_pos = position['pos']
        reduce_sz = round(current_pos * reduce_ratio, 2)
        
        if reduce_sz < 0.01:
            print(f'⚠️ 减仓数量太小 ({reduce_sz} 张)，直接全部平仓')
            return self.close_long('减仓数量过小')
        
        print(f'📊 当前持仓: {current_pos} 张')
        print(f'📉 减仓数量: {reduce_sz} 张 ({reduce_ratio*100:.0f}%)')
        print(f'📈 剩余持仓: {current_pos - reduce_sz} 张')
        
        # 执行卖出减仓
        result = self.api.place_order(self.symbol, 'sell', str(reduce_sz), pos_side='long')
        
        if result.get('code') == '0':
            ord_id = result['data'][0]['ordId']
            print(f'✅ 减仓成功! 订单ID: {ord_id}')
            
            # 记录减仓
            self._record_reduce(reduce_sz, reduce_ratio)
            
            return True
        else:
            print(f'❌ 减仓失败: {result.get("msg")}')
            return False
    
    def _init_reduce_record(self, entry_price, position_size):
        """初始化减仓记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 先清除旧的
        cursor.execute('DELETE FROM reduce_records WHERE symbol = ?', (self.symbol,))
        
        # 插入新记录
        cursor.execute('''
            INSERT INTO reduce_records 
            (symbol, entry_price, original_size, remaining_size)
            VALUES (?, ?, ?, ?)
        ''', (self.symbol, entry_price, position_size, position_size))
        
        conn.commit()
        conn.close()
        print(f'📝 初始化减仓记录: {self.coin} {position_size} 张 @ {entry_price}')
    
    def _record_reduce(self, reduced_size, reduce_ratio):
        """记录减仓操作"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取当前记录
        cursor.execute('''
            SELECT * FROM reduce_records 
            WHERE symbol = ? ORDER BY id DESC LIMIT 1
        ''', (self.symbol,))
        
        record = cursor.fetchone()
        if record:
            # 更新记录
            new_reduced_count = record[4] + 1
            new_reduced_size = record[5] + reduced_size
            new_remaining = record[6] - reduced_size
            
            cursor.execute('''
                UPDATE reduce_records 
                SET reduced_count = ?, reduced_size = ?, remaining_size = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (new_reduced_count, new_reduced_size, new_remaining, record[0]))
            
            conn.commit()
            print(f'📝 更新减仓记录: 第{new_reduced_count}次减仓，已减 {new_reduced_size:.2f} 张，剩余 {new_remaining:.2f} 张')
        
        conn.close()
    
    def _clear_reduce_record(self):
        """清除减仓记录（全部平仓后）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM reduce_records WHERE symbol = ?', (self.symbol,))
        conn.commit()
        conn.close()
    
    def _record_divergence_reduce(self, note='顶背离减仓'):
        """记录背离减仓"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取当前记录
        cursor.execute('''
            SELECT * FROM reduce_records 
            WHERE symbol = ? ORDER BY id DESC LIMIT 1
        ''', (self.symbol,))
        
        record = cursor.fetchone()
        if record:
            # 标记已触发背离减仓（用特殊标记-1表示）
            targets_hit = json.loads(record[5]) if record[5] and isinstance(record[5], str) else []
            if -1 not in targets_hit:  # 避免重复记录
                targets_hit.append(-1)
            
            cursor.execute('''
                UPDATE reduce_records 
                SET targets_hit = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (json.dumps(targets_hit), record[0]))
            
            conn.commit()
        
        conn.close()
    
    def _tier_triggered(self, tier_key):
        """检查某一层级是否已经触发过"""
        reduce_status = self._get_reduce_status()
        if reduce_status and 'targets_hit' in reduce_status:
            return tier_key in reduce_status['targets_hit']
        return False
    
    def _record_tier_trigger(self, tier_key):
        """记录某一层级已触发"""
        reduce_status = self._get_reduce_status()
        if reduce_status:
            targets_hit = reduce_status.get('targets_hit', [])
            if tier_key not in targets_hit:
                targets_hit.append(tier_key)
                self._update_targets_hit(targets_hit)
    
    def _get_reduce_status(self):
        """获取当前减仓状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT entry_price, original_size, reduced_count, reduced_size, remaining_size, targets_hit
            FROM reduce_records 
            WHERE symbol = ? ORDER BY id DESC LIMIT 1
        ''', (self.symbol,))
        
        record = cursor.fetchone()
        conn.close()
        
        if record:
            return {
                'entry_price': record[0],
                'original_size': record[1],
                'reduced_count': record[2],
                'reduced_size': record[3],
                'remaining_size': record[4],
                'targets_hit': json.loads(record[5]) if record[5] and isinstance(record[5], str) else []
            }
        return None
    
    def _update_targets_hit(self, targets_hit):
        """更新已触发的目标"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reduce_records 
            SET targets_hit = ?, updated_at = CURRENT_TIMESTAMP
            WHERE symbol = ?
        ''', (json.dumps(targets_hit), self.symbol))
        conn.commit()
        conn.close()
    
    def monitor_reduce_and_trailing(self):
        """
        监控减仓目标 + 移动止损
        使用独立的ReduceManager模块
        """
        reduce_mgr = ReduceManager(self)
        reduce_mgr.start_monitoring()
        
        entry_price = position['avg_px']
        current_size = position['pos']
        
        # 初始化减仓记录（如果是新持仓）
        reduce_status = self._get_reduce_status()
        if not reduce_status or abs(reduce_status['remaining_size'] - current_size) > 0.1:
            self._init_reduce_record(entry_price, current_size)
            reduce_status = self._get_reduce_status()
        
        print('\n' + '='*60)
        print(f'📉📈 [{self.coin}] 启动智能减仓监控')
        print('='*60)
        print(f'入场价: {entry_price:.2f}')
        print(f'持仓量: {current_size:.2f} 张')
        print(f'已减仓次数: {reduce_status["reduced_count"]}')
        print(f'剩余持仓: {reduce_status["remaining_size"]:.2f} 张')
        print('='*60)
        print('📋 减仓策略:')
        print('   1. 跌破上升趋势线 → 减仓70%')
        print('   2. 5分钟顶背离 → 减仓70%')
        # 获取币种清算地图配置
        symbol_liq_config = LIQMAP_SYMBOL_CONFIG.get(self.symbol, LIQMAP_DEFAULT_CONFIG)
        print(f'   3. 接近多头清算区 → 减仓50% (阈值{symbol_liq_config["distance_threshold"]*100:.1f}%)')
        print('   4. 📈趋势止盈 (+8%/15%/25% 让利润奔跑)')
        print(f'   5. 移动止损 (+{ACTIVATION_PERCENT*100:.0f}%激活, 回撤{CALLBACK_RATIO*100:.0f}%触发) [趋势跟踪]')
        print('='*60)
        
        activated = False
        highest_price = entry_price
        last_divergence_time = 0  # 上次背离减仓时间
        last_trendline_time = 0   # 上次趋势线/清算地图减仓时间
        
        try:
            while True:
                result = self.api.get_price(self.symbol)
                if result.get('code') != '0':
                    time.sleep(3)
                    continue
                
                price = float(result['data'][0]['last'])
                profit_pct = (price - entry_price) / entry_price
                current_time = datetime.now().strftime('%H:%M:%S')
                current_timestamp = time.time()
                
                # ========== 风控升级检查 (2026-03-14) ==========
                action, risk_reason = self.check_all_risk_controls()
                
                if action == 'close':
                    print(f'\n🚨 [{current_time}] {risk_reason}')
                    print('执行全部平仓...')
                    return self.close_long(risk_reason)
                
                if action == 'reduce':
                    print(f'\n⚠️ [{current_time}] {risk_reason}')
                    print('执行减仓...')
                    if self.reduce_position(TIME_STOP_LOSS['reduce_ratio']):
                        # 更新持仓
                        position = self.get_position()
                        if not position or position['pos'] == 0:
                            print('✅ 全部平仓完成')
                            return True
                        current_size = position['pos']
                        entry_price = position['avg_px']
                        print(f'\n📉 风控减仓完成，继续监控...')
                
                # ========== 背离信号减仓 ==========
                # 做多时：5分钟顶背离，减仓（根据置信度动态调整）
                # 冷却时间检查，避免连续触发
                if current_timestamp - last_divergence_time > DIVERGENCE_COOLDOWN_SECONDS:
                    has_top_div, div_info, div_msg = self.check_top_divergence()
                    if has_top_div:
                        conf = div_info.get('confidence', 50)
                        strength = div_info.get('strength', '中')
                        # 根据置信度动态减仓：强背驰70%，中背驰50%，弱背驰30%
                        if conf >= 70:
                            reduce_pct = 0.70
                        elif conf >= 50:
                            reduce_pct = 0.50
                        else:
                            reduce_pct = 0.30
                        
                        print(f'\n⚠️ [{current_time}] 检测到顶背离信号!')
                        print(f'   强度: {strength}')
                        print(f'   置信度: {conf}%')
                        print(f'   方法: {div_info.get("method", "unknown")}')
                        print(f'   执行减仓{reduce_pct*100:.0f}%')
                        
                        if self.reduce_position(reduce_pct):
                            # 记录背离减仓
                            self._record_divergence_reduce(f'顶背离减仓{reduce_pct*100:.0f}%')
                            last_divergence_time = current_timestamp
                            
                            # 更新持仓
                            position = self.get_position()
                            if not position or position['pos'] == 0:
                                print('✅ 全部平仓完成')
                                return True
                            current_size = position['pos']
                            entry_price = position['avg_px']
                            
                            print(f'\n📉 顶背离减仓完成，继续监控...')
                
                # ========== 趋势线跌破减仓 ==========
                # 趋势策略：跌破上升趋势线，减仓70%
                if current_timestamp - last_trendline_time > TRENDLINE_COOLDOWN_SECONDS:
                    has_break, tl_info, tl_msg = self.check_trendline_break()
                    if has_break:
                        print(f'\n🔻 [{current_time}] 检测到趋势线跌破!')
                        print(f'   趋势线价格: {tl_info["trendline_price"]:.2f}')
                        print(f'   当前价格: {tl_info["current"]:.2f}')
                        print(f'   跌破幅度: {tl_info["break_pct"]:.2f}%')
                        print(f'   执行减仓{TRENDLINE_REDUCE_RATIO*100:.0f}%')
                        
                        if self.reduce_position(TRENDLINE_REDUCE_RATIO):
                            # 记录趋势线减仓
                            self._record_divergence_reduce('趋势线跌破减仓70%')
                            last_trendline_time = current_timestamp
                            
                            # 更新持仓
                            position = self.get_position()
                            if not position or position['pos'] == 0:
                                print('✅ 全部平仓完成')
                                return True
                            current_size = position['pos']
                            entry_price = position['avg_px']
                            
                            print(f'\n📉 趋势线跌破减仓完成，继续监控...')
                
                # ========== 清算地图减仓 ==========
                # 趋势策略：接近下方多头清算密集区，减仓50%
                if current_timestamp - last_trendline_time > LIQMAP_COOLDOWN_SECONDS:
                    has_liq, liq_info, liq_msg = self.check_liquidation_zone_proximity()
                    if has_liq:
                        print(f'\n⚡ [{current_time}] 检测到接近清算密集区!')
                        print(f'   {liq_info["reason"]}')
                        print(f'   清算流动性: {liq_info["long_liquidity"]:.1f}M')
                        print(f'   执行减仓{LIQMAP_REDUCE_RATIO*100:.0f}%')
                        
                        if self.reduce_position(LIQMAP_REDUCE_RATIO):
                            # 记录清算地图减仓
                            self._record_divergence_reduce('清算地图减仓50%')
                            
                            # ===== 清算地图学习系统 =====
                            if LIQMAP_LEARNING_ENABLED:
                                try:
                                    record_id = record_liqmap_reduce(
                                        symbol=self.symbol,
                                        trigger_price=liq_info['current'],
                                        liq_zone_price=liq_info['liq_price'],
                                        distance_pct=liq_info['distance_pct'] / 100,
                                        liquidity_amount=liq_info['long_liquidity'],
                                        reduce_ratio=LIQMAP_REDUCE_RATIO,
                                        threshold_used=liq_info.get('threshold_used', 0.02)
                                    )
                                    print(f'   📝 学习系统记录ID: {record_id}')
                                except Exception as e:
                                    print(f'   ⚠️ 学习系统记录失败: {e}')
                            # =========================
                            
                            last_trendline_time = current_timestamp  # 共用趋势线冷却时间
                            
                            # 更新持仓
                            position = self.get_position()
                            if not position or position['pos'] == 0:
                                print('✅ 全部平仓完成')
                                return True
                            current_size = position['pos']
                            entry_price = position['avg_px']
                            
                            print(f'\n📉 清算地图减仓完成，继续监控...')
                
                # ========== 阶梯止盈检查 ==========
                if not activated:  # 只在未激活移动止损时检查
                    for tier in PROFIT_TIERS:
                        if profit_pct >= tier['profit']:
                            tier_key = f"tier_{tier['profit']}"
                            if not self._tier_triggered(tier_key):
                                print(f'\n🎯 [{current_time}] 阶梯止盈触发: +{tier["profit"]*100:.0f}% 减仓{tier["reduce"]*100:.0f}%')
                                self.reduce_position(tier['reduce'])
                                self._record_tier_trigger(tier_key)
                                
                                # 如果减仓100%，直接返回
                                if tier['reduce'] >= 1.0:
                                    return True
                                
                                # 更新持仓信息
                                position = self.get_position()
                                if position and position['pos'] > 0:
                                    current_size = position['pos']
                                    print(f'   减仓后持仓: {current_size:.2f} 张')
                                break
                
                # ========== 移动止损检查 ==========
                activation_price = entry_price * (1 + ACTIVATION_PERCENT)
                
                if not activated:
                    # 等待激活
                    if price >= activation_price:
                        activated = True
                        highest_price = price
                        print(f'\n🔥 [{current_time}] 移动止损已激活! 当前:${price:.2f}')
                    else:
                        progress = profit_pct / ACTIVATION_PERCENT * 100
                        print(f'[{current_time}] ${price:.2f} 盈利:{profit_pct*100:.1f}% 激活进度:{progress:.0f}%', end='\r')
                else:
                    # 已激活，跟踪最高价
                    if price > highest_price:
                        highest_price = price
                        print(f'[{current_time}] 新高:${highest_price:.2f} 🚀')
                    
                    # 计算回撤
                    callback = (highest_price - price) / (highest_price - entry_price)
                    print(f'[{current_time}] ${price:.2f} 最高:${highest_price:.2f} 回撤:{callback*100:.0f}%', end='\r')
                    
                    # 检查是否触发
                    if callback >= CALLBACK_RATIO:
                        print(f'\n\n🚨🚨🚨 移动止损触发! 🚨🚨🚨')
                        print(f'   最高: {highest_price:.u003e2f}')
                        print(f'   当前: {price:.2f}')
                        print(f'   回撤: {callback*100:.0f}%')
                        print(f'   盈利: {profit_pct*100:.2f}%')
                        
                        # 执行平仓
                        return self.close_long('移动止损触发')
                
                time.sleep(3)
                
        except KeyboardInterrupt:
            print('\n\n⏹️ 监控已停止')
    
    def _monitor_trailing_only(self, entry_price, position_size):
        """仅监控移动止损（减仓完成后）"""
        activation_price = entry_price * (1 + ACTIVATION_PERCENT)
        
        print('\n' + '='*60)
        print(f'🚀 [{self.coin}] 移动止损模式 [趋势跟踪]')
        print('='*60)
        print(f'成本价: {entry_price:.2f}')
        print(f'激活价: {activation_price:.2f} (+{ACTIVATION_PERCENT*100:.0f}%) 📈')
        print(f'回撤容忍: {CALLBACK_RATIO*100:.0f}% (让利润奔跑)')
        print(f'剩余持仓: {position_size:.2f} 张')
        print('='*60)
        
        activated = False
        highest_price = entry_price
        
        try:
            while True:
                result = self.api.get_price(self.symbol)
                if result.get('code') != '0':
                    time.sleep(3)
                    continue
                
                price = float(result['data'][0]['last'])
                profit_pct = (price - entry_price) / entry_price
                current_time = datetime.now().strftime('%H:%M:%S')
                
                # ========== 移动止损模式下的顶背离检测 ==========
                # 即使进入移动止损模式，顶背离也减仓50%
                has_top_div, div_info, div_msg = self.check_top_divergence()
                if has_top_div:
                    print(f'\n⚠️ [{current_time}] 移动止损模式检测到顶背离!')
                    print(f'   执行减仓50%')
                    
                    if self.reduce_position(0.5):
                        self._record_divergence_reduce('顶背离减仓50%')
                        
                        # 更新持仓
                        position = self.get_position()
                        if not position or position['pos'] == 0:
                            print('✅ 全部平仓完成')
                            return True
                        
                        print(f'\n📉 顶背离减仓完成，继续移动止损监控...')
                        time.sleep(60)
                        continue
                
                if not activated:
                    # 等待激活
                    if price >= activation_price:
                        activated = True
                        highest_price = price
                        print(f'\n🔥 [{current_time}] 移动止损已激活! 当前:${price}')
                    else:
                        progress = profit_pct / ACTIVATION_PERCENT * 100
                        print(f'[{current_time}] ${price} 激活进度:{progress:.0f}%', end='\r')
                else:
                    # 已激活，跟踪最高价
                    if price > highest_price:
                        highest_price = price
                        print(f'[{current_time}] 新高:${highest_price} 🚀')
                    
                    # 计算回撤
                    callback = (highest_price - price) / (highest_price - entry_price)
                    print(f'[{current_time}] ${price} 最高:${highest_price} 回撤:{callback*100:.0f}%', end='\r')
                    
                    # 检查是否触发
                    if callback >= CALLBACK_RATIO:
                        print(f'\n\n🚨🚨🚨 移动止损触发! 🚨🚨🚨')
                        print(f'   最高: {highest_price}')
                        print(f'   当前: {price}')
                        print(f'   回撤: {callback*100:.0f}%')
                        
                        # 执行平仓
                        return self.close_long('移动止损触发')
                
                time.sleep(3)
                
        except KeyboardInterrupt:
            print('\n\n⏹️ 监控已停止')

    def monitor_trailing_stop(self):
        """监控移动止损"""
        position = self.get_position()
        if not position or position['pos'] == 0:
            print('⏸️ 无持仓，无需监控')
            return
        
        entry_price = position['avg_px']
        activation_price = entry_price * (1 + ACTIVATION_PERCENT)
        
        print('\n' + '='*60)
        print(f'🚀 [{self.coin}] 启动移动止损监控')
        print('='*60)
        print(f'入场价: {entry_price:.2f}')
        print(f'激活价: {activation_price:.2f} (+{ACTIVATION_PERCENT*100:.0f}%)')
        print(f'回调比例: {CALLBACK_RATIO*100:.0f}%')
        print('='*60)
        
        activated = False
        highest_price = entry_price
        
        try:
            while True:
                result = self.api.get_price(self.symbol)
                if result.get('code') != '0':
                    time.sleep(3)
                    continue
                
                price = float(result['data'][0]['last'])
                current_time = datetime.now().strftime('%H:%M:%S')
                
                # ========== 顶背离检测 - 减仓50% ==========
                has_top_div, div_info, div_msg = self.check_top_divergence()
                if has_top_div:
                    print(f'\n⚠️ [{current_time}] 检测到顶背离信号!')
                    print(f'   价格高点: {div_info["price_high"]:.2f}')
                    print(f'   当前价格: {div_info["current"]:.2f}')
                    print(f'   执行减仓50%')
                    
                    if self.reduce_position(0.5):
                        print('\n📉 顶背离减仓完成')
                        time.sleep(60)
                        # 继续监控
                        continue
                
                if not activated:
                    # 等待激活
                    if price >= activation_price:
                        activated = True
                        highest_price = price
                        print(f'\n🔥 [{current_time}] 移动止损已激活!')
                        print(f'   当前价: {price}')
                    else:
                        progress = (price - entry_price) / (activation_price - entry_price) * 100
                        print(f'[{current_time}] 价格:{price} 激活进度:{progress:.1f}%', end='\r')
                else:
                    # 已激活，跟踪最高价
                    if price > highest_price:
                        highest_price = price
                        print(f'[{current_time}] 新高:{highest_price} 🚀')
                    
                    # 计算回撤
                    callback = (highest_price - price) / (highest_price - entry_price)
                    trigger_price = highest_price - (highest_price - entry_price) * CALLBACK_RATIO
                    
                    profit_pct = (price - entry_price) / entry_price * 100
                    print(f'[{current_time}] 当前:{price} 最高:{highest_price} 回撤:{callback*100:.1f}% 盈利:{profit_pct:.2f}%', end='\r')
                    
                    # 检查是否触发
                    if callback >= CALLBACK_RATIO:
                        print(f'\n\n🚨🚨🚨 移动止损触发! 🚨🚨🚨')
                        print(f'   最高: {highest_price}')
                        print(f'   当前: {price}')
                        print(f'   回撤: {callback*100:.1f}%')
                        print(f'   盈利: {profit_pct:.2f}%')
                        
                        # 执行平仓
                        self.close_long('移动止损触发')
                        return True
                
                time.sleep(3)
                
        except KeyboardInterrupt:
            print('\n\n⏹️ 监控已停止')
    
    def get_candles(self, bar='5m', limit=100):
        """获取K线数据"""
        result = self.api.get_candles(self.symbol, bar, limit)
        candles = []
        if result.get('code') == '0':
            for item in result['data']:
                candles.append({
                    'time': int(item[0]),
                    'open': float(item[1]),
                    'high': float(item[2]),
                    'low': float(item[3]),
                    'close': float(item[4]),
                    'vol': float(item[5])
                })
        return candles
    
    def calculate_macd(self, candles, fast=12, slow=26, signal=9):
        """计算MACD"""
        if len(candles) < slow:
            return None
        
        closes = [c['close'] for c in candles]
        
        def ema(data, period):
            multiplier = 2 / (period + 1)
            ema_values = [data[0]]
            for i in range(1, len(data)):
                ema_values.append((data[i] - ema_values[-1]) * multiplier + ema_values[-1])
            return ema_values
        
        ema_fast = ema(closes, fast)
        ema_slow = ema(closes, slow)
        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = ema(macd_line, signal)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]
        
        return {'macd': macd_line, 'signal': signal_line, 'hist': histogram}
    
    def check_divergence(self):
        """检查5分钟底背驰 - 使用摩尔缠论背驰系统"""
        candles = self.get_candles('5m', 100)
        if len(candles) < 50:
            return False, None, '数据不足'
        
        # 使用新的背驰系统
        if MOER_DIVERGENCE_AVAILABLE:
            has_div, info, msg = get_divergence_summary(candles, '5m')
            if has_div:
                return True, {
                    'type': info['type'],
                    'price_low': info['price_low'],
                    'current': candles[-1]['close'],
                    'confidence': info['confidence'],
                    'strength': info['strength'],
                    'method': info['method']
                }, f'5分钟{info["type"]} 置信度{info["confidence"]}%'
            return False, None, msg
        
        # 回退到旧版检测
        macd_data = self.calculate_macd(candles)
        if not macd_data:
            return False, None, 'MACD计算失败'
        
        price_lows = []
        macd_lows = []
        
        for i in range(2, len(candles) - 2):
            if candles[i]['low'] < candles[i-1]['low'] and candles[i]['low'] < candles[i-2]['low'] and \
               candles[i]['low'] < candles[i+1]['low'] and candles[i]['low'] < candles[i+2]['low']:
                price_lows.append((i, candles[i]['low']))
                macd_lows.append((i, macd_data['hist'][i]))
        
        if len(price_lows) < 2:
            return False, None, '低点不足'
        
        last_price_low = price_lows[-1]
        prev_price_low = price_lows[-2]
        last_macd_low = macd_lows[-1]
        prev_macd_low = macd_lows[-2]
        
        price_lower = last_price_low[1] < prev_price_low[1]
        macd_not_lower = last_macd_low[1] > prev_macd_low[1]
        is_green = last_macd_low[1] < 0
        
        if price_lower and macd_not_lower and is_green:
            return True, {
                'type': '底背离(旧版)',
                'price_low': last_price_low[1],
                'current': candles[-1]['close'],
                'confidence': 50
            }, '5分钟底背离确认(旧版)'
        
        return False, None, '无背离'
    
    def check_top_divergence(self):
        """检查5分钟顶背驰（用于多单减仓信号）- 使用摩尔缠论背驰系统"""
        candles = self.get_candles('5m', 100)
        if len(candles) < 50:
            return False, None, '数据不足'
        
        # 使用新的背驰系统
        if MOER_DIVERGENCE_AVAILABLE:
            from moer_divergence_system import check_top_divergence_area, calculate_macd
            closes = [c['close'] for c in candles]
            macd = calculate_macd(closes)
            div = check_top_divergence_area(candles, macd['histogram'])
            
            if div:
                return True, {
                    'type': '顶背驰',
                    'price_high': div.price2,
                    'current': candles[-1]['close'],
                    'confidence': div.confidence,
                    'strength': div.strength,
                    'method': div.method
                }, f'5分钟顶背驰(面积法) 强度{div.strength} 置信度{div.confidence}%'
            return False, None, '无顶背驰'
        
        # 回退到旧版检测
        macd_data = self.calculate_macd(candles)
        if not macd_data:
            return False, None, 'MACD计算失败'
        
        price_highs = []
        macd_highs = []
        
        for i in range(2, len(candles) - 2):
            if candles[i]['high'] > candles[i-1]['high'] and candles[i]['high'] > candles[i-2]['high'] and \
               candles[i]['high'] > candles[i+1]['high'] and candles[i]['high'] > candles[i+2]['high']:
                price_highs.append((i, candles[i]['high']))
                macd_highs.append((i, macd_data['hist'][i]))
        
        if len(price_highs) < 2:
            return False, None, '高点不足'
        
        last_price_high = price_highs[-1]
        prev_price_high = price_highs[-2]
        last_macd_high = macd_highs[-1]
        prev_macd_high = macd_highs[-2]
        
        price_higher = last_price_high[1] > prev_price_high[1]
        macd_not_higher = last_macd_high[1] < prev_macd_high[1]
        is_red = last_macd_high[1] > 0
        
        if price_higher and macd_not_higher and is_red:
            return True, {
                'type': '顶背离',
                'price_high': last_price_high[1],
                'current': candles[-1]['close'],
                'strength': (prev_macd_high[1] - last_macd_high[1]) / abs(prev_macd_high[1]) if prev_macd_high[1] != 0 else 0
            }, '5分钟顶背离确认'
        
        return False, None, '无顶背离'
    
    def check_divergence_30m(self):
        """检查30分钟底背离（用于第二批加仓）- 使用摩尔缠论背驰系统"""
        candles = self.get_candles('30m', 100)
        if len(candles) < 50:
            return False, None, '数据不足'
        
        # 使用新的背驰系统
        if MOER_DIVERGENCE_AVAILABLE:
            has_div, info, msg = get_divergence_summary(candles, '30m')
            if has_div:
                return True, {
                    'type': info['type'],
                    'price_low': info['price_low'],
                    'current': candles[-1]['close'],
                    'confidence': info['confidence'],
                    'strength': info['strength'],
                    'method': info['method']
                }, f'30分钟{info["type"]} 置信度{info["confidence"]}%'
            return False, None, msg
        
        # 回退到旧版检测
        macd_data = self.calculate_macd(candles)
        if not macd_data:
            return False, None, 'MACD计算失败'
        
        price_lows = []
        macd_lows = []
        
        for i in range(2, len(candles) - 2):
            if candles[i]['low'] < candles[i-1]['low'] and candles[i]['low'] < candles[i-2]['low'] and \
               candles[i]['low'] < candles[i+1]['low'] and candles[i]['low'] < candles[i+2]['low']:
                price_lows.append((i, candles[i]['low']))
                macd_lows.append((i, macd_data['hist'][i]))
        
        if len(price_lows) < 2:
            return False, None, '低点不足'
        
        last_price_low = price_lows[-1]
        prev_price_low = price_lows[-2]
        last_macd_low = macd_lows[-1]
        prev_macd_low = macd_lows[-2]
        
        price_lower = last_price_low[1] < prev_price_low[1]
        macd_not_lower = last_macd_low[1] > prev_macd_low[1]
        is_green = last_macd_low[1] < 0
        
        if price_lower and macd_not_lower and is_green:
            return True, {
                'type': '30分钟底背离(旧版)',
                'price_low': last_price_low[1],
                'current': candles[-1]['close'],
                'confidence': 50
            }, '30分钟底背离确认(旧版)'
        
        return False, None, '无30分钟背离'
    
    def check_multi_timeframe_divergence(self):
        """
        多周期背驰共振检测 - 摩尔缠论背驰系统
        返回: (是否有信号, 信号详情, 消息)
        """
        if not MOER_DIVERGENCE_AVAILABLE:
            # 回退：只检查5分钟背离
            return self.check_divergence()
        
        # 获取各周期K线
        candles_5m = self.get_candles('5m', 100)
        candles_15m = self.get_candles('15m', 100)
        candles_30m = self.get_candles('30m', 100)
        
        if len(candles_5m) < 30:
            return False, None, '数据不足'
        
        # 使用背驰系统检测共振
        has_res, info, msg = check_multi_timeframe_divergence(candles_5m, candles_15m, candles_30m)
        
        if has_res:
            return True, {
                'type': info['type'],
                'timeframes': info['timeframes'],
                'score': info['score'],
                'confidence': info['confidence'],
                'current': candles_5m[-1]['close']
            }, msg
        
        return False, info, msg
    
    def check_divergence_with_volume(self, timeframe='5m'):
        """
        检查带成交量确认的背离信号
        解决假突破问题：背离形成时必须有成交量放大确认
        
        Returns: (has_signal, info, message)
        """
        candles = self.get_candles(timeframe, 100)
        if len(candles) < 50:
            return False, None, '数据不足'
        
        macd_data = self.calculate_macd(candles)
        if not macd_data:
            return False, None, 'MACD计算失败'
        
        price_lows = []
        macd_lows = []
        volume_at_lows = []
        
        for i in range(2, len(candles) - 2):
            if candles[i]['low'] < candles[i-1]['low'] and candles[i]['low'] < candles[i-2]['low'] and \
               candles[i]['low'] < candles[i+1]['low'] and candles[i]['low'] < candles[i+2]['low']:
                price_lows.append((i, candles[i]['low']))
                macd_lows.append((i, macd_data['hist'][i]))
                volume_at_lows.append((i, candles[i]['vol']))
        
        if len(price_lows) < 2:
            return False, None, '低点不足'
        
        last_price_low = price_lows[-1]
        prev_price_low = price_lows[-2]
        last_macd_low = macd_lows[-1]
        prev_macd_low = macd_lows[-2]
        last_vol = volume_at_lows[-1][1]
        prev_vol = volume_at_lows[-2][1]
        
        price_lower = last_price_low[1] < prev_price_low[1]
        macd_not_lower = last_macd_low[1] > prev_macd_low[1]
        is_green = last_macd_low[1] < 0
        
        # 背离基础条件
        base_divergence = price_lower and macd_not_lower and is_green
        
        if not base_divergence:
            return False, None, '无背离'
        
        # 成交量确认：当前低点成交量 >= 前低点成交量的80%（避免无量假突破）
        volume_confirmed = last_vol >= prev_vol * 0.8
        
        # 计算平均成交量（最近20根K线）
        avg_volume = sum(c['vol'] for c in candles[-20:]) / 20
        # 当前成交量应大于平均成交量的1.2倍（确认有资金介入）
        volume_significant = last_vol > avg_volume * 1.2
        
        if base_divergence and volume_confirmed and volume_significant:
            return True, {
                'type': f'{timeframe}底背离+量能确认',
                'price_low': last_price_low[1],
                'current': candles[-1]['close'],
                'strength': (prev_macd_low[1] - last_macd_low[1]) / abs(prev_macd_low[1]) if prev_macd_low[1] != 0 else 0,
                'volume_ratio': last_vol / avg_volume,
                'volume_confirmed': True
            }, f'{timeframe}底背离确认 | 成交量放大{last_vol/avg_volume:.1f}倍'
        elif base_divergence:
            return False, {
                'type': f'{timeframe}底背离(无量)',
                'price_low': last_price_low[1],
                'volume_ratio': last_vol / avg_volume if avg_volume > 0 else 0
            }, f'{timeframe}底背离但成交量不足({last_vol/avg_volume:.1f}倍<1.2倍)，忽略'
        
        return False, None, '无背离'
    
    def check_multi_timeframe_divergence(self):
        """
        多周期背离共振检测
        要求：5分钟底背离 + (15分钟底背离或30分钟底背离)
        
        Returns: (has_signal, info, message)
        """
        # 检查5分钟底背离
        has_5m, info_5m, msg_5m = self.check_divergence_with_volume('5m')
        
        if not has_5m:
            return False, None, f'5分钟无背离: {msg_5m}'
        
        # 检查15分钟底背离
        has_15m, info_15m, msg_15m = self.check_divergence_with_volume('15m')
        
        # 检查30分钟底背离
        has_30m, info_30m, msg_30m = self.check_divergence_with_volume('30m')
        
        # 多周期共振：5分钟 + (15分钟 或 30分钟)
        if has_5m and (has_15m or has_30m):
            return True, {
                'type': '多周期底背离共振',
                'timeframes': ['5m'] + (['15m'] if has_15m else []) + (['30m'] if has_30m else []),
                'price_5m': info_5m['price_low'],
                'current': info_5m['current'],
                'strength_5m': info_5m['strength'],
                'volume_ratio': info_5m['volume_ratio']
            }, f'✅ 多周期底背离共振: 5m+{"15m" if has_15m else ""}+{"30m" if has_30m else ""} | 量能{info_5m["volume_ratio"]:.1f}倍'
        
        # 只有5分钟背离，无共振
        return False, {
            'type': '5分钟底背离(无共振)',
            'has_5m': has_5m,
            'has_15m': has_15m,
            'has_30m': has_30m,
            'volume_ratio': info_5m['volume_ratio'] if info_5m else 0
        }, f'5分钟底背离但无多周期共振(15m:{has_15m}, 30m:{has_30m})，信号较弱'
    
    def calculate_atr(self, candles, period=14):
        """计算ATR（平均真实波幅）"""
        if len(candles) < period + 1:
            return None
        
        tr_values = []
        for i in range(1, len(candles)):
            high = candles[i]['high']
            low = candles[i]['low']
            prev_close = candles[i-1]['close']
            
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_values.append(tr)
        
        if len(tr_values) < period:
            return None
        
        # 简单移动平均计算ATR
        atr = sum(tr_values[-period:]) / period
        return atr
    
    def check_rebound_dynamic(self, lookback_hours=24):
        """
        动态反弹阈值检测
        根据ATR自适应调整反弹阈值，替代固定3%
        
        Returns: (has_signal, info, message)
        """
        # 获取当前价格
        result = self.api.get_price(self.symbol)
        if result.get('code') != '0':
            return False, None, '价格获取失败'
        
        current_price = float(result['data'][0]['last'])
        
        # 获取24小时K线
        candles = self.get_candles('1H', lookback_hours + 5)
        if len(candles) < lookback_hours:
            return False, None, '历史数据不足'
        
        # 计算24小时低点
        recent_candles = candles[-lookback_hours:]
        low_24h = min(c['low'] for c in recent_candles)
        
        # 计算ATR（用于动态阈值）
        atr = self.calculate_atr(candles, 14)
        if atr is None:
            # 回退到固定阈值
            rebound_pct = (current_price - low_24h) / low_24h
            threshold = 0.03  # 3%
            
            if rebound_pct >= threshold:
                return True, {
                    'type': '反弹信号(固定阈值)',
                    'low_24h': low_24h,
                    'current': current_price,
                    'rebound_pct': rebound_pct * 100,
                    'threshold': threshold * 100
                }, f'从24h低点反弹{rebound_pct*100:.2f}% (阈值{threshold*100:.0f}%)'
            return False, None, f'反弹{rebound_pct*100:.2f}% < 阈值{threshold*100:.0f}%'
        
        # 动态阈值：ATR的2倍作为最小反弹要求
        # 同时不低于1.5%，不高于5%
        dynamic_threshold = max(0.015, min(atr / current_price * 2, 0.05))
        
        rebound_pct = (current_price - low_24h) / low_24h
        
        # 额外条件：反弹必须伴随成交量放大
        recent_volume = sum(c['vol'] for c in candles[-3:]) / 3
        avg_volume = sum(c['vol'] for c in candles[-12:]) / 12
        volume_confirmed = recent_volume > avg_volume * 1.3
        
        if rebound_pct >= dynamic_threshold and volume_confirmed:
            return True, {
                'type': '动态反弹信号',
                'low_24h': low_24h,
                'current': current_price,
                'rebound_pct': rebound_pct * 100,
                'threshold': dynamic_threshold * 100,
                'atr': atr,
                'volume_ratio': recent_volume / avg_volume
            }, f'✅ 动态反弹{rebound_pct*100:.2f}% >= 阈值{dynamic_threshold*100:.2f}% (ATR={atr:.2f}) | 量能{recent_volume/avg_volume:.1f}倍'
        elif rebound_pct >= dynamic_threshold:
            return False, {
                'type': '反弹(无量)',
                'rebound_pct': rebound_pct * 100,
                'volume_ratio': recent_volume / avg_volume
            }, f'反弹达标但成交量不足({recent_volume/avg_volume:.1f}倍<1.3倍)'
        
        return False, None, f'反弹{rebound_pct*100:.2f}% < 动态阈值{dynamic_threshold*100:.2f}%'
    
    def check_composite_buy_signal(self):
        """
        综合买入信号检测 - 整合摩尔缠论背驰系统 (优化版 v2.1)
        结合多种信号源，提高可靠性
        
        优化点 (2026-03-21):
        1. 收紧入场条件: 总权重>=5 且至少2个不同类型信号
        2. 降低底背离权重: 3->2 (历史表现一般)
        3. 增加信号多样性检查，避免单一信号类型开仓
        
        Returns: (has_signal, signal_type, info, message)
        """
        signals = []
        
        # 1. 检查多周期背驰共振（新系统）
        if MOER_DIVERGENCE_AVAILABLE:
            has_res, info_res, msg_res = self.check_multi_timeframe_divergence()
            if has_res:
                signals.append(('多周期背驰共振', info_res, 5))  # 权重5
            elif info_res and info_res.get('score', 0) >= 2:
                # 有部分共振但不够强
                signals.append(('部分背驰共振', info_res, 2))
        
        # 2. 检查单一5分钟背离（降低权重，历史表现一般）
        has_5m, info_5m, msg_5m = self.check_divergence()
        if has_5m:
            # 检查置信度 - 降低权重: 高置信度2分，中1分，低0分
            conf = info_5m.get('confidence', 50)
            weight = 2 if conf >= 70 else (1 if conf >= 50 else 0)
            if weight > 0:
                signals.append(('5分钟背驰', info_5m, weight))
        
        # 3. 检查30分钟背离（第二批加仓用）
        has_30m, info_30m, msg_30m = self.check_divergence_30m()
        if has_30m:
            conf = info_30m.get('confidence', 50)
            weight = 4 if conf >= 70 else (3 if conf >= 50 else 2)
            signals.append(('30分钟背驰', info_30m, weight))
        
        # 4. 检查动态反弹
        has_rebound, info_rebound, msg_rebound = self.check_rebound_dynamic()
        if has_rebound:
            signals.append(('动态反弹', info_rebound, 2))
        
        # 5. 检查均线金叉
        has_cross, info_cross, msg_cross = self.check_ma_cross()
        if has_cross:
            signals.append(('均线金叉', info_cross, 1))
        
        # 6. 检查摩尔缠论买点
        has_chan, info_chan, msg_chan = self.check_chan_buy()
        if has_chan:
            chan_type = info_chan.get('type', '')
            if '三买' in chan_type:
                signals.append(('摩尔缠论三买', info_chan, 4))
            elif '二买' in chan_type:
                signals.append(('摩尔缠论二买', info_chan, 3))
            elif '一买' in chan_type:
                signals.append(('摩尔缠论一买', info_chan, 2))
        
        # 计算总权重
        total_weight = sum(s[2] for s in signals)
        
        # 增强信号：背驰+缠论买点组合
        has_divergence = any('背驰' in s[0] for s in signals)
        has_chan_buy = any('缠论' in s[0] for s in signals)
        if has_divergence and has_chan_buy:
            total_weight += 2  # 组合加分
        
        # 计算信号类型数量（用于多样性检查）
        signal_types = set(s[0] for s in signals)
        
        # 信号判断逻辑 - 收紧入场条件 (优化后)
        if total_weight >= 6 and len(signal_types) >= 2:
            # 强信号: 权重>=6 且 至少2种不同类型信号
            return True, '强买入信号', {
                'signals': [s[0] for s in signals],
                'total_weight': total_weight,
                'signal_types': len(signal_types),
                'details': {s[0]: s[1] for s in signals}
            }, f'🚀 强买入信号(权重{total_weight}, {len(signal_types)}种信号): {" + ".join(s[0] for s in signals)}'
        
        elif total_weight >= 5 and len(signal_types) >= 2:
            # 中等信号: 权重>=5 且 至少2种不同类型信号
            return True, '中等买入信号', {
                'signals': [s[0] for s in signals],
                'total_weight': total_weight,
                'signal_types': len(signal_types),
                'details': {s[0]: s[1] for s in signals}
            }, f'📈 中等买入信号(权重{total_weight}, {len(signal_types)}种信号): {" + ".join(s[0] for s in signals)}'
        
        elif total_weight >= 4:
            # 信号强度够但类型单一，观望
            return False, '信号单一', {
                'signals': [s[0] for s in signals],
                'total_weight': total_weight,
                'signal_types': len(signal_types)
            }, f'⚠️ 信号强度{total_weight}但类型单一({len(signal_types)}种)，等待更多确认'
        
        elif total_weight >= 2:
            # 弱信号
            return False, '弱信号', {
                'signals': [s[0] for s in signals],
                'total_weight': total_weight
            }, f'⚠️ 弱信号(权重{total_weight})，等待更强确认: {", ".join(s[0] for s in signals)}'
        
        # 无有效信号
        details = []
        if not has_5m and info_5m:
            details.append(msg_5m)
        if not has_rebound and info_rebound:
            details.append(msg_rebound)
        
        return False, '无信号', {}, f'无有效买入信号 | {" | ".join(details) if details else "等待机会"}'
    
    # ==================== 风控升级函数 (2026-03-14) ====================
    
    def get_position_entry_time(self):
        """获取当前持仓的入场时间"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT trade_date FROM trades 
            WHERE symbol = ? AND exit_price IS NULL AND direction = '多/Long'
            ORDER BY trade_date DESC LIMIT 1
        ''', (self.symbol,))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            try:
                entry_time = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S.%f')
                return entry_time
            except:
                try:
                    entry_time = datetime.strptime(result[0], '%Y-%m-%d %H:%M:%S')
                    return entry_time
                except:
                    return None
        return None
    
    def check_time_stop_loss(self):
        """
        时间止损检测
        持仓超过max_hold_hours小时且无盈利时触发减仓
        
        Returns: (should_reduce, reason, reduce_ratio)
        """
        if not TIME_STOP_LOSS['enabled']:
            return False, None, 0
        
        position = self.get_position()
        if not position or position['pos'] == 0:
            return False, None, 0
        
        entry_time = self.get_position_entry_time()
        if not entry_time:
            return False, None, 0
        
        now = datetime.now()
        hold_hours = (now - entry_time).total_seconds() / 3600
        
        # 计算当前盈亏
        entry_price = position['avg_px']
        result = self.api.get_price(self.symbol)
        if result.get('code') != '0':
            return False, None, 0
        current_price = float(result['data'][0]['last'])
        profit_pct = (current_price - entry_price) / entry_price
        
        # 持仓超时且无盈利
        if hold_hours >= TIME_STOP_LOSS['max_hold_hours'] and profit_pct <= 0:
            if TIME_STOP_LOSS['reduce_if_no_profit']:
                return True, f'时间止损: 持仓{hold_hours:.1f}小时无盈利({profit_pct*100:.2f}%)', TIME_STOP_LOSS['reduce_ratio']
        
        # 持仓严重超时(2倍时间)无论盈亏都减仓
        if hold_hours >= TIME_STOP_LOSS['max_hold_hours'] * 2:
            return True, f'时间止损: 严重超时({hold_hours:.1f}小时)', TIME_STOP_LOSS['reduce_ratio']
        
        return False, None, 0
    
    def calculate_atr_stop_price(self, entry_price, candles=None):
        """
        计算ATR止损价格
        
        Returns: stop_price 或 None
        """
        if not ATR_STOP_LOSS['enabled']:
            return None
        
        if candles is None:
            candles = self.get_candles('5m', ATR_STOP_LOSS['atr_period'] + 10)
        
        if len(candles) < ATR_STOP_LOSS['atr_period']:
            return None
        
        atr = self.calculate_atr(candles, ATR_STOP_LOSS['atr_period'])
        if atr is None:
            return None
        
        # 计算ATR止损距离
        atr_distance = atr * ATR_STOP_LOSS['atr_multiplier']
        
        # 转换为百分比
        atr_pct = atr_distance / entry_price
        
        # 限制在最小和最大止损之间
        stop_pct = max(ATR_STOP_LOSS['min_stop_pct'], min(atr_pct, ATR_STOP_LOSS['max_stop_pct']))
        
        # 计算止损价格（多头）
        stop_price = entry_price * (1 - stop_pct)
        
        return stop_price
    
    def check_atr_stop_loss(self):
        """
        ATR波动率止损检测
        根据市场波动率动态调整止损距离
        
        Returns: (should_close, reason)
        """
        if not ATR_STOP_LOSS['enabled']:
            return False, None
        
        position = self.get_position()
        if not position or position['pos'] == 0:
            return False, None
        
        entry_price = position['avg_px']
        
        # 计算ATR止损价格
        stop_price = self.calculate_atr_stop_price(entry_price)
        if stop_price is None:
            return False, None
        
        # 获取当前价格
        result = self.api.get_price(self.symbol)
        if result.get('code') != '0':
            return False, None
        current_price = float(result['data'][0]['last'])
        
        # 检查是否触及止损
        if current_price <= stop_price:
            stop_pct = (entry_price - stop_price) / entry_price
            return True, f'ATR止损触发: 价格{current_price:.2f} <= 止损价{stop_price:.2f} (止损{stop_pct*100:.2f}%)'
        
        return False, None
    
    def check_daily_risk_limit(self):
        """
        检查单日风控限制
        
        Returns: (can_trade, reason)
        """
        today = datetime.now().strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 查询今日交易次数
        cursor.execute('''
            SELECT COUNT(*) FROM trades 
            WHERE date(trade_date) = date('now', 'localtime')
        ''')
        trade_count = cursor.fetchone()[0]
        
        # 查询今日盈亏
        cursor.execute('''
            SELECT COALESCE(SUM(pnl_usdt), 0) FROM trades 
            WHERE date(trade_date) = date('now', 'localtime')
            AND exit_price IS NOT NULL
        ''')
        daily_pnl = cursor.fetchone()[0] or 0
        
        conn.close()
        
        # 检查交易次数限制
        if trade_count >= DAILY_RISK_CONTROL['max_trades_per_day']:
            return False, f'单日交易次数已达上限({trade_count}/{DAILY_RISK_CONTROL["max_trades_per_day"]})'
        
        # 检查单日亏损限制
        balance = self.get_balance_usdt()
        daily_loss_pct = abs(daily_pnl) / balance if balance > 0 else 0
        
        if daily_loss_pct >= DAILY_RISK_CONTROL['max_loss_per_day']:
            return False, f'单日亏损已达上限({daily_loss_pct*100:.2f}% >= {DAILY_RISK_CONTROL["max_loss_per_day"]*100}%)'
        
        return True, None
    
    def get_all_positions_summary(self):
        """
        获取所有币种持仓摘要（用于相关性风控）
        
        Returns: list of {'symbol', 'direction', 'size'}
        """
        all_positions = []
        
        for sym in SYMBOLS:
            # 临时切换币种检查
            original_symbol = self.symbol
            original_coin = self.coin
            
            self.symbol = sym
            self.coin = sym.split('-')[0]
            
            position = self.get_position()
            if position and position['pos'] > 0:
                all_positions.append({
                    'symbol': sym,
                    'coin': self.coin,
                    'direction': 'long',
                    'size': position['pos'],
                    'avg_px': position['avg_px']
                })
            
            # 恢复原始币种
            self.symbol = original_symbol
            self.coin = original_coin
        
        return all_positions
    
    def check_correlation_risk(self):
        """
        相关性风控检测
        避免同向高度相关币种过度集中
        
        Returns: (should_block, reason)
        """
        if not CORRELATION_CONTROL['enabled']:
            return False, None
        
        all_positions = self.get_all_positions_summary()
        
        if len(all_positions) == 0:
            return False, None
        
        # 统计同向持仓数量
        long_positions = [p for p in all_positions if p['direction'] == 'long']
        
        # 检查同向持仓上限
        if len(long_positions) >= CORRELATION_CONTROL['max_same_direction']:
            # 检查是否包含高相关对
            held_coins = set(p['coin'] for p in long_positions)
            
            for coin1, coin2 in CORRELATION_CONTROL['high_correlation_pairs']:
                if coin1 in held_coins and coin2 in held_coins:
                    return True, f'相关性风控: 已持有高相关币种{coin1}和{coin2}，避免过度集中'
        
        return False, None
    
    def check_all_risk_controls(self):
        """
        综合风控检查
        在监控循环中调用
        
        Returns: (action, reason) 
            action: 'hold', 'reduce', 'close', 'block'
        """
        # 1. 检查ATR止损（最高优先级 - 全部平仓）
        should_close, reason = self.check_atr_stop_loss()
        if should_close:
            return 'close', reason
        
        # 2. 检查时间止损（减仓）
        should_reduce, reason, reduce_ratio = self.check_time_stop_loss()
        if should_reduce:
            return 'reduce', reason
        
        # 3. 检查相关性风控（阻止新开仓）
        should_block, reason = self.check_correlation_risk()
        if should_block:
            return 'block', reason
        
        return 'hold', None
    
    def check_trendline_break(self):
        """
        检测上升趋势线跌破
        用于趋势策略减仓信号
        """
        candles = self.get_candles('5m', TRENDLINE_LOOKBACK + 10)
        if len(candles) < TRENDLINE_LOOKBACK:
            return False, None, '数据不足'
        
        # 获取低点数据
        lows = [(i, c['low']) for i, c in enumerate(candles)]
        
        # 找最近的两个显著低点（局部最低点）
        significant_lows = []
        for i in range(2, len(lows) - 2):
            if lows[i][1] < lows[i-1][1] and lows[i][1] < lows[i-2][1] and \
               lows[i][1] < lows[i+1][1] and lows[i][1] < lows[i+2][1]:
                significant_lows.append(lows[i])
        
        if len(significant_lows) < 2:
            return False, None, '低点不足，无法画趋势线'
        
        # 取最近两个低点画上升趋势线
        low1 = significant_lows[-2]  # 倒数第二个低点
        low2 = significant_lows[-1]  # 最后一个低点
        
        # 确保是上升趋势（后面的低点更高）
        if low2[1] <= low1[1]:
            return False, None, '非上升趋势'
        
        # 计算趋势线斜率和截距
        # 趋势线方程: y = k * x + b
        k = (low2[1] - low1[1]) / (low2[0] - low1[0])
        b = low1[1] - k * low1[0]
        
        # 获取当前价格
        current_price = candles[-1]['close']
        current_idx = len(candles) - 1
        
        # 计算当前K线对应的趋势线价格
        trendline_price = k * current_idx + b
        
        # 计算前一根K线的趋势线价格（确认跌破）
        prev_idx = len(candles) - 2
        prev_trendline_price = k * prev_idx + b
        prev_close = candles[-2]['close']
        
        # 跌破确认：前一根在趋势线上方，当前跌破
        is_break = (prev_close >= prev_trendline_price) and (current_price < trendline_price)
        
        # 跌破幅度（百分比）
        break_pct = (trendline_price - current_price) / trendline_price if trendline_price > 0 else 0
        
        if is_break and break_pct > 0.005:  # 跌破超过0.5%
            return True, {
                'type': '趋势线跌破',
                'trendline_price': trendline_price,
                'current': current_price,
                'break_pct': break_pct * 100,
                'low1': (low1[0], low1[1]),
                'low2': (low2[0], low2[1])
            }, f'跌破上升趋势线 {break_pct*100:.2f}%'
        
        return False, None, f'趋势线价:{trendline_price:.2f} 当前:{current_price:.2f}'
    
    def check_liquidation_zone_proximity(self):
        """
        检测是否接近下方多头清算密集区
        用于趋势策略风控减仓
        
        逻辑：价格接近下方密集清算区时，可能引发连锁爆仓，提前减仓保护
        
        2026-03-20 更新: 使用币种自适应阈值
        """
        # 获取当前价格
        result = self.api.get_price(self.symbol)
        if result.get('code') != '0':
            return False, None, '价格获取失败'
        
        current_price = float(result['data'][0]['last'])
        
        # 获取币种自适应配置
        symbol_config = LIQMAP_SYMBOL_CONFIG.get(self.symbol, LIQMAP_DEFAULT_CONFIG)
        distance_threshold = symbol_config['distance_threshold']
        min_liquidity = symbol_config['min_liquidity']
        
        # 使用清算信号模块检查
        liq_result, liq_msg = self.liq_signal.check_signal(self.coin, current_price)
        
        if not liq_result:
            return False, None, '无清算数据'
        
        # 获取清算区数据
        if self.coin not in self.liq_signal.liquidation_zones:
            return False, None, '无该币种清算数据'
        
        zones = self.liq_signal.liquidation_zones[self.coin]
        
        # 找最近的多头清算区（价格低于当前）
        nearest_long_liq = None
        min_dist = float('inf')
        
        for z in zones:
            p = z['price']
            if p < current_price:  # 下方的清算区
                dist = (current_price - p) / current_price
                if dist < min_dist and z.get('long_liq', 0) > min_liquidity:
                    min_dist = dist
                    nearest_long_liq = z
        
        if not nearest_long_liq:
            return False, None, f'无下方密集清算区(>{min_liquidity}M)'
        
        # 检查距离是否在阈值内
        if min_dist <= distance_threshold:
            return True, {
                'type': '接近清算密集区',
                'liq_price': nearest_long_liq['price'],
                'current': current_price,
                'distance_pct': min_dist * 100,
                'long_liquidity': nearest_long_liq.get('long_liq', 0),
                'threshold_used': distance_threshold,
                'reason': f'距离下方多头清算区{min_dist*100:.2f}%(${nearest_long_liq["price"]:,.0f})'
            }, f'接近多头清算密集区({self.symbol}阈值{distance_threshold*100:.1f}%)，流动性:{nearest_long_liq.get("long_liq", 0):.1f}M'
        
        return False, None, f'距离最近清算区{min_dist*100:.2f}%，{self.symbol}阈值{distance_threshold*100:.1f}%'
    
    def check_ma_cross(self):
        """检查均线金叉"""
        candles = self.get_candles('5m', 50)
        if len(candles) < 20:
            return False, None, '数据不足'
        
        closes = [c['close'] for c in candles]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma5_prev = sum(closes[-6:-1]) / 5
        ma10_prev = sum(closes[-11:-1]) / 10
        
        # 金叉：MA5上穿MA10
        if ma5 > ma10 and ma5_prev <= ma10_prev:
            return True, {
                'type': '均线金叉',
                'ma5': ma5,
                'ma10': ma10,
                'current': closes[-1]
            }, 'MA5上穿MA10'
        
        return False, None, f'MA5:{ma5:.2f} MA10:{ma10:.2f}'
    
    def check_chan_buy(self):
        """
        检查缠论进攻买点 - 摩尔缠论完整版
        基于34均线 + 波峰波谷 + 中枢识别 (纯Python实现，无pandas依赖)
        """
        candles = self.get_candles('4H', 80)
        if len(candles) < 40:
            return False, None, '数据不足'
        
        closes = [c['close'] for c in candles]
        highs = [c['high'] for c in candles]
        lows = [c['low'] for c in candles]
        
        # 1. 计算34均线
        ma34 = []
        for i in range(len(closes)):
            if i < 33:
                ma34.append(sum(closes[:i+1]) / (i+1))
            else:
                ma34.append(sum(closes[i-33:i+1]) / 34)
        
        # 2. 寻找波峰波谷（局部极值点）- 摩尔缠论方法
        window = 3  # 左右各3根K线确认极值
        peaks = []    # (索引, 价格)
        troughs = []  # (索引, 价格)
        
        for i in range(window, len(candles) - window):
            # 波峰：比左右各window根K线都高
            is_peak = highs[i] >= max(highs[i-window:i+window+1])
            if is_peak:
                peaks.append((i, highs[i]))
            
            # 波谷：比左右各window根K线都低
            is_trough = lows[i] <= min(lows[i-window:i+window+1])
            if is_trough:
                troughs.append((i, lows[i]))
        
        if len(peaks) < 2 or len(troughs) < 2:
            return False, None, f'极值点不足(峰:{len(peaks)},谷:{len(troughs)})'
        
        # 3. 识别摩尔中枢（基于相邻波峰波谷的重叠区）
        centers = []
        
        # 按时间排序的极值点
        extremes = sorted(peaks + troughs, key=lambda x: x[0])
        
        # 寻找4个连续极值点形成的重叠区
        for i in range(len(extremes) - 3):
            ext1 = extremes[i]
            ext2 = extremes[i+1]
            ext3 = extremes[i+2]
            ext4 = extremes[i+3]
            
            # 获取高点和低点
            prices = [ext1[1], ext2[1], ext3[1], ext4[1]]
            peak_prices = [p[1] for p in peaks if ext1[0] <= p[0] <= ext4[0]]
            trough_prices = [t[1] for t in troughs if ext1[0] <= t[0] <= ext4[0]]
            
            if len(peak_prices) >= 2 and len(trough_prices) >= 2:
                zg = min(peak_prices)   # 中枢高点 = 最低的高点
                zd = max(trough_prices) # 中枢低点 = 最高的低点
                
                # 验证中枢有效性
                if zg > zd:
                    height_pct = (zg - zd) / zd * 100
                    if height_pct >= 1.0:  # 中枢高度至少1%
                        centers.append({
                            'zg': zg,
                            'zd': zd,
                            'center': (zg + zd) / 2,
                            'height_pct': height_pct,
                            'start_idx': ext1[0],
                            'end_idx': ext4[0]
                        })
        
        if not centers:
            return False, None, '无有效中枢'
        
        # 取最近的中枢
        latest_center = centers[-1]
        zg = latest_center['zg']
        zd = latest_center['zd']
        
        current = closes[-1]
        current_ma34 = ma34[-1]
        
        # 4. 判断摩尔缠论三类买卖点
        
        # 一买：价格在中枢下方，且从低点回升（底背离后回升）
        recent_lows = lows[-20:]  # 最近20根K线低点
        recent_low_min = min(recent_lows)
        
        if current < zd and current > recent_low_min * 1.01:
            # 已经从最低点回升，可能是止跌信号
            return True, {
                'type': '摩尔缠论一买',
                'zg': zg,
                'zd': zd,
                'current': current,
                'ma34': current_ma34,
                'center': latest_center,
                'weight': 3
            }, f'一买：中枢下方止跌回升，中枢区间{zd:.0f}-{zg:.0f}'
        
        # 二买：价格回到中枢区间内（回踩确认）
        if zd <= current <= zg:
            return True, {
                'type': '摩尔缠论二买',
                'zg': zg,
                'zd': zd,
                'current': current,
                'ma34': current_ma34,
                'center': latest_center,
                'weight': 2
            }, f'二买：回踩中枢区间{zd:.0f}-{zg:.0f}'
        
        # 三买：突破中枢后回踩不跌破（进攻买点）
        if current > zg and current < zg * 1.04:  # 突破后4%内回踩
            return True, {
                'type': '摩尔缠论三买',
                'zg': zg,
                'zd': zd,
                'current': current,
                'ma34': current_ma34,
                'center': latest_center,
                'weight': 3
            }, f'三买：突破中枢回踩，中枢区间{zd:.0f}-{zg:.0f}'
        
        return False, None, f'中枢{zd:.0f}-{zg:.0f} 当前{current:.0f} 无买点'

    def get_4h_trend(self):
        """获取4小时趋势"""
        candles = self.get_candles('4H', 20)
        if len(candles) < 10:
            return 'UNKNOWN'
        
        closes = [c['close'] for c in candles]
        ma10 = sum(closes) / len(closes)
        current = closes[-1]
        
        highs = [c['high'] for c in candles[-10:]]
        lows = [c['low'] for c in candles[-10:]]
        
        up_count = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1] and lows[i] > lows[i-1])
        down_count = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i-1] and lows[i] < lows[i-1])
        
        if up_count > down_count + 2 and current > ma10:
            return 'UPTREND'
        elif down_count > up_count + 2 and current < ma10:
            return 'DOWNTREND'
        return 'SIDEWAYS'
    
    def check_signal(self):
        """检查交易信号 - 白鸽一号多信号融合 v1.3 (第二批:30分钟底背离+清算地图)"""
        # 查当前持仓
        position = self.get_position()
        current_pos = position.get('pos', 0) if position else 0
        
        # 获取当前价格
        result = self.api.get_price(self.symbol)
        current_price = float(result['data'][0]['last']) if result.get('code') == '0' else 0
        
        # ===== 第二批加仓逻辑: 已有持仓时 =====
        if current_pos > 0:
            # 检查30分钟底背离
            has_div_30m, div_info_30m, div_msg_30m = self.check_divergence_30m()
            
            # 检查清算地图
            liq_result, liq_msg = self.liq_signal.check_signal(self.coin, current_price)
            has_liq = liq_result and liq_result['signal'] in ['LONG', 'LONG_BIAS'] and liq_result['strength'] >= 2
            
            # 判断当前是第几批
            batch = self.get_current_batch()
            
            if batch == 2:
                # ===== 第二批条件: 30分钟底背离 + 清算地图(≥2分) =====
                if has_div_30m and has_liq:
                    return {
                        'batch': 2,
                        'batch_name': f'第二批({BATCH_SIZES[1]*100:.0f}%)',
                        'entry': current_price,
                        'reason': f'30分钟底背离+清算热力图({liq_result["strength"]}分)[高置信度]',
                        'signal_type': '30分钟底背离',
                        'signal_info': div_info_30m,
                        'target_size': BATCH_SIZES[1],  # 第二批: 30%
                        'total_weight': 5 + liq_result['strength'],
                        'confidence': '高置信度',
                        'position_factor': 1.0
                    }, f"检测到30分钟底背离+清算热力图(第二批加仓{BATCH_SIZES[1]*100:.0f}%)"
                
                # 如果只有30分钟底背离，也给信号但标记为观望
                if has_div_30m:
                    return None, f"第二批 - 30分钟底背离确认，但清算热力图{liq_result.get('strength', 0) if liq_result else 0}分不足(需≥2分)，观望"
                
                if has_liq:
                    return None, f"第二批 - 清算热力图{liq_result['strength']}分，但无30分钟底背离，观望"
                
                return None, f"第二批 - 无30分钟底背离+清算热力图组合信号，等待中"
            
            elif batch == 3:
                # ===== 第三批条件: 均线金叉确认 =====
                ma_cross, ma_info, ma_msg = self.check_ma_cross()
                if ma_cross:
                    return {
                        'batch': 3,
                        'batch_name': f'第三批({BATCH_SIZES[2]*100:.0f}%)',
                        'entry': current_price,
                        'reason': 'MA5上穿MA10金叉确认[趋势确立]',
                        'signal_type': '均线金叉',
                        'signal_info': ma_cross,
                        'target_size': BATCH_SIZES[2],  # 第三批: 20%
                        'total_weight': 4,
                        'confidence': '标准',
                        'position_factor': 1.0
                    }, f"检测到均线金叉信号(第三批加满{BATCH_SIZES[2]*100:.0f}%)"
                
                return None, f"第三批 - 等待均线金叉确认，当前无信号"
            
            else:
                return None, f"已满仓，不再加仓"
        
        # ===== 第一批开仓逻辑: 无持仓时 =====
        # 使用增强版综合信号检测
        has_signal, signal_type, signal_info, msg = self.check_composite_buy_signal()
        
        if has_signal:
            # 获取清算热力图权重（用于增强）
            liq_result, liq_msg = self.liq_signal.check_signal(self.coin, current_price)
            liq_weight = 0
            if liq_result and liq_result['signal'] in ['LONG', 'LONG_BIAS']:
                liq_weight = liq_result['strength']
            
            # 基础权重
            base_weight = signal_info.get('total_weight', 3)
            total_weight = base_weight + liq_weight
            
            # 确定仓位系数
            position_factor = 1.0
            if total_weight >= 7:
                position_factor = 1.0
            elif total_weight >= 5:
                position_factor = 0.8
            elif total_weight >= 3:
                position_factor = 0.6
            else:
                position_factor = 0.4
            
            # 组合信号描述
            signals_str = '+'.join(signal_info.get('signals', [signal_type]))
            if liq_weight >= 2:
                signals_str += f'+清算热力图({liq_weight}分)'
            
            confidence = '高置信度' if total_weight >= 6 else ('标准' if total_weight >= 4 else '低置信度')
            
            return {
                'batch': 1,
                'batch_name': '第一批开仓',
                'entry': current_price,
                'reason': f'{signals_str}[{confidence}]',
                'signal_type': signal_type,
                'signal_info': signal_info,
                'target_size': BATCH_SIZES[0] * position_factor,
                'total_weight': total_weight,
                'confidence': confidence,
                'position_factor': position_factor
            }, f"检测到{signals_str}信号（总权重{total_weight}，{confidence}）"
        
        # 无有效信号，返回详细信息
        return None, f"第一批 - {msg}"
    
    def auto_trade(self):
        """自动交易模式（信号检测+智能减仓监控）"""
        print('\n' + '='*70)
        print(f'🤖 [{self.coin}] 自动交易模式启动')
        print('='*70)
        print('功能: 信号检测 + 智能减仓监控')
        print(f'检查间隔: {SIGNAL_CHECK_INTERVAL}秒')
        print('='*70)
        
        # 先检查是否有持仓
        position = self.get_position()
        
        if position and position['pos'] > 0:
            # 有持仓，启动智能减仓监控（包含移动止损）
            print(f'\n📈 检测到持仓: {position["pos"]} 张')
            print('启动智能减仓监控...')
            self.monitor_reduce_and_trailing()
        else:
            # 无持仓，等待信号
            print('\n⏸️ 无持仓，等待开仓信号...')
            
            while True:
                current_time = datetime.now().strftime('%H:%M:%S')
                
                # ========== 风控升级检查 (2026-03-14) ==========
                # 1. 检查单日风控限制
                can_trade, risk_reason = self.check_daily_risk_limit()
                if not can_trade:
                    print(f'\n🚫 [{current_time}] 单日风控限制: {risk_reason}')
                    print('今日停止交易，明日再试。')
                    return False
                
                # 2. 检查相关性风控
                should_block, block_reason = self.check_correlation_risk()
                if should_block:
                    print(f'\n🚫 [{current_time}] {block_reason}')
                    print('跳过当前币种，监控其他机会...')
                    time.sleep(60)  # 等待1分钟后继续检查
                    continue
                
                # 3. 检查交易信号
                signal, msg = self.check_signal()
                
                if signal:
                    print(f'\n🎯 [{current_time}] {msg}')
                    print(f'信号: {signal["batch_name"]}')
                    print(f'原因: {signal["reason"]}')
                    
                    # 执行开仓
                    success = self.open_long()
                    
                    if success:
                        print('\n✅ 开仓成功，启动智能减仓监控...')
                        time.sleep(2)
                        self.monitor_reduce_and_trailing()
                        return True
                else:
                    print(f'[{current_time}] {msg}', end='\r')
                
                time.sleep(SIGNAL_CHECK_INTERVAL)
    
    def show_status(self):
        """显示状态"""
        print('='*60)
        print(f'📊 [{self.coin}] 账户状态')
        print('='*60)
        
        # 余额
        balance = self.get_balance_usdt()
        print(f'💰 USDT余额: {balance:.2f}')
        
        # 持仓
        position = self.get_position()
        if position and position['pos'] > 0:
            print(f'📈 持仓: {position["pos"]} 张 {self.coin}多仓')
            print(f'   开仓均价: {position["avg_px"]:.2f}')
            print(f'   杠杆: {position["lever"]}x')
            print(f'   爆仓价: {position["liq_px"]:.2f}')
            print(f'   未实现盈亏: {position["upl"]:.2f} USDT')
            
            # 查当前价算盈亏比例
            result = self.api.get_price(self.symbol)
            if result.get('code') == '0':
                current = float(result['data'][0]['last'])
                profit_pct = (current - position['avg_px']) / position['avg_px'] * 100
                print(f'   当前价: {current:.2f}')
                print(f'   浮动盈亏: {profit_pct:+.2f}%')
        else:
            print('⏸️ 无持仓')
        
        print('='*60)


# ==================== 主程序 ====================
def show_all_status():
    """显示所有品种的监控状态"""
    print('\n' + '='*70)
    print('🕊️ 白鸽一号 - 多品种监控系统')
    print('='*70)
    
    # 获取总余额
    trader = Trader(SYMBOLS[0])
    balance = trader.get_balance_usdt()
    print(f'💰 总余额: {balance:.2f} USDT')
    print(f'📊 监控品种: {len(SYMBOLS)} 个')
    print('-'*70)
    
    # 检查每个品种
    for symbol in SYMBOLS:
        coin = symbol.split('-')[0]
        t = Trader(symbol)
        
        # 检查持仓
        position = t.get_position()
        if position and position['pos'] > 0:
            print(f'📈 {coin}: 持仓 {position["pos"]} 张 @ {position["avg_px"]:.2f} | 盈亏: {position["upl"]:+.2f} USDT')
            continue
        
        # 检查信号
        signal, msg = t.check_signal()
        result = t.api.get_price(symbol)
        price = float(result['data'][0]['last']) if result.get('code') == '0' else 0
        
        if signal:
            print(f'🎯 {coin}: ${price:,.2f} | 🔥 {signal["reason"]} ({signal["batch_name"]})')
        else:
            # 简化显示
            trend = t.get_4h_trend()
            trend_emoji = {'UPTREND': '📈', 'DOWNTREND': '📉', 'SIDEWAYS': '➡️'}.get(trend, '➡️')
            print(f'⏸️ {coin}: ${price:,.2f} | {trend_emoji} {trend} | 无信号')
    
    print('='*70)

def main():
    """主程序"""
    if len(sys.argv) < 2:
        print('用法:')
        print('  python3 real_trader_v2.py status         # 查看所有品种状态')
        print('  python3 real_trader_v2.py status BTC     # 查看指定品种状态')
        print('  python3 real_trader_v2.py buy BTC        # 开多单')
        print('  python3 real_trader_v2.py sell BTC       # 平多单')
        print('  python3 real_trader_v2.py reduce BTC     # 智能减仓监控')
        print('  python3 real_trader_v2.py reduce BTC 50% # 手动减仓50%')
        print('  python3 real_trader_v2.py monitor BTC    # 仅监控移动止损')
        print('  python3 real_trader_v2.py auto BTC       # 单品种自动模式')
        print('  python3 real_trader_v2.py auto           # 多品种自动模式')
        print('')
        print('监控品种:', ', '.join([s.split('-')[0] for s in SYMBOLS]))
        return
    
    cmd = sys.argv[1]
    
    # 多品种总览
    if cmd == 'status' and len(sys.argv) == 2:
        show_all_status()
        return
    
    # 确定交易品种
    symbol = 'BTC-USDT-SWAP'  # 默认
    if len(sys.argv) >= 3:
        coin = sys.argv[2].upper()
        symbol = f'{coin}-USDT-SWAP'
    
    trader = Trader(symbol)
    
    if cmd == 'status':
        trader.show_status()
    
    elif cmd == 'buy':
        trader.open_long()
    
    elif cmd == 'sell':
        trader.close_long('手动平仓')
    
    elif cmd == 'reduce':
        # 智能减仓
        if len(sys.argv) >= 4:
            # 手动指定减仓比例，如: reduce BTC 50%
            ratio_str = sys.argv[3].replace('%', '')
            try:
                ratio = float(ratio_str) / 100
                trader.reduce_position(ratio)
            except:
                print(f'❌ 无效的减仓比例: {sys.argv[3]}')
                print('用法: reduce BTC 50% 或 reduce BTC')
        else:
            # 启动智能减仓监控
            trader.monitor_reduce_and_trailing()
    
    elif cmd == 'monitor':
        trader.monitor_trailing_stop()
    
    elif cmd == 'auto':
        if len(sys.argv) == 2:
            # 多品种模式 - 改进版：支持多持仓同时监控
            print('\n🕊️ 启动多品种自动监控 (支持多持仓)...')
            print('='*70)
            
            # 初始化持仓监控状态
            position_monitors = {}  # symbol -> 上次检查时间
            
            while True:
                current_time = datetime.now().strftime('%H:%M:%S')
                signals_found = []
                positions_count = 0
                
                # ===== 宏观过滤器检查 =====
                if MACRO_FILTER_ENABLED:
                    if 'macro_filter' not in locals():
                        macro_filter = MacroFilter()
                    
                    # 获取最新新闻并更新风险等级
                    try:
                        import subprocess
                        news_result = subprocess.run(
                            ['python3', 'skills/crypto-news-free/scripts/fetch_news.py', '--limit', '5'],
                            capture_output=True, text=True, timeout=30
                        )
                        # 简单解析新闻标题
                        news_lines = news_result.stdout.split('\n')
                        news_titles = []
                        for line in news_lines:
                            if line.startswith('###') and '[' in line and ']' in line:
                                title = line.split(']', 1)[-1].strip()
                                if title:
                                    news_titles.append({'title': title})
                        
                        if news_titles:
                            macro_filter.update_risk_level(news_titles)
                    except Exception as e:
                        pass  # 新闻获取失败不影响交易
                    
                    # 检查是否可以交易
                    can_trade, macro_reason = macro_filter.check_can_open_position(positions_count)
                    params = macro_filter.get_trading_params()
                    
                    if not can_trade:
                        print(f'\n[{current_time}] 🔒 宏观过滤器阻止交易: {macro_reason}')
                        print(f'[{current_time}] 持仓:{positions_count} 监控中...', end='', flush=True)
                        time.sleep(SIGNAL_CHECK_INTERVAL)
                        continue
                    
                    # 显示当前宏观状态
                    if positions_count == 0:
                        print(f'\n[{current_time}] 📊 宏观状态: {macro_filter.risk_level} | 最大持仓:{params["max_positions"]} | 仓位:{params["position_pct"]*100:.0f}%')
                
                for sym in SYMBOLS:
                    t = Trader(sym)
                    position = t.get_position()
                    
                    if position and position['pos'] > 0:
                        # 有持仓，记录但不阻塞
                        positions_count += 1
                        coin = sym.split('-')[0]
                        upl = position.get('upl', 0)
                        
                        # 每10秒检查一次持仓风险（提高响应速度）
                        last_check = position_monitors.get(sym, 0)
                        if time.time() - last_check > 10:
                            # ===== 首先检查ATR止损（最高优先级）=====
                            action, risk_reason = t.check_all_risk_controls()
                            if action == 'close':
                                print(f'\n🚨 [{current_time}] {coin}: {risk_reason}')
                                t.close_long('ATR止损触发')
                                position_monitors[sym] = 0
                                continue
                            
                            # 检查移动止损触发条件（仅盈利状态）
                            result = t.api.get_price(sym)
                            if result.get('code') == '0':
                                current_price = float(result['data'][0]['last'])
                                avg_px = position['avg_px']
                                profit_pct = (current_price - avg_px) / avg_px
                                
                                # 激活移动止损检查
                                if profit_pct >= ACTIVATION_PERCENT:
                                    callback_pct = (current_price - avg_px * (1 + ACTIVATION_PERCENT)) / (avg_px * ACTIVATION_PERCENT) if ACTIVATION_PERCENT > 0 else 0
                                    if callback_pct >= CALLBACK_RATIO:
                                        print(f'\n🚨 [{current_time}] {coin}: 移动止损触发！盈利回撤{CALLBACK_RATIO*100:.0f}%')
                                        t.close_long('移动止损触发')
                                        position_monitors[sym] = 0
                                        continue
                            
                            position_monitors[sym] = time.time()
                        continue
                    
                    # 无持仓，检查开仓信号
                    signal, msg = t.check_signal()
                    if signal:
                        coin = sym.split('-')[0]
                        signals_found.append({
                            'symbol': sym,
                            'coin': coin,
                            'signal': signal,
                            'msg': msg
                        })
                
                # 显示状态
                if signals_found:
                    print(f'\n[{current_time}] 发现 {len(signals_found)} 个开仓信号，持仓 {positions_count} 个')
                    for sig in signals_found:
                        print(f"  🎯 {sig['coin']}: {sig['signal']['reason']}")
                
                # 执行开仓（按信号强度排序，优先开强的）
                if signals_found:
                    # 获取宏观过滤器参数
                    if MACRO_FILTER_ENABLED and 'macro_filter' in locals():
                        params = macro_filter.get_trading_params()
                        max_pos = params['max_positions']
                        signal_threshold = params['signal_threshold']
                    else:
                        max_pos = MAX_POSITIONS
                        signal_threshold = 3
                    
                    # 检查信号强度是否达到阈值
                    signals_found.sort(key=lambda x: x['signal'].get('total_weight', 0), reverse=True)
                    best = signals_found[0]
                    
                    if best['signal'].get('total_weight', 0) < signal_threshold:
                        print(f'\n[{current_time}] ⚠️ 最强信号权重{best["signal"].get("total_weight", 0)} < 阈值{signal_threshold}，跳过')
                    elif positions_count < max_pos:
                        print(f"\n🚀 [{current_time}] 执行开仓: {best['coin']} (权重:{best['signal'].get('total_weight', 0)})")
                        t = Trader(best['symbol'])
                        success = t.open_long()
                        if success:
                            position_monitors[best['symbol']] = time.time()
                    else:
                        print(f'\n[{current_time}] ⚠️ 已有{positions_count}个持仓，达到上限{max_pos}，跳过开仓信号')
                
                # 显示扫描状态
                print(f'\r[{current_time}] 持仓:{positions_count} 扫描中...', end='', flush=True)
                time.sleep(SIGNAL_CHECK_INTERVAL)
        else:
            # 单品种模式
            trader.auto_trade()
    
    else:
        print(f'未知命令: {cmd}')
        print('')
        print('可用命令:')
        print('  status [COIN]  - 查看账户状态')
        print('  buy COIN       - 开多单')
        print('  sell COIN      - 平多单')
        print('  reduce COIN    - 智能减仓监控')
        print('  monitor COIN   - 仅监控移动止损')
        print('  auto [COIN]    - 自动交易模式(信号+智能减仓)')

if __name__ == '__main__':
    main()
