#!/usr/bin/env python3
"""
摩尔缠论 - 20倍杠杆全仓合约交易系统
⚠️ 高风险警告: 可能导致快速爆仓！
"""

import sys
sys.path.insert(0, '/root/.openclaw/workspace/moer-chanlun')

from config import OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
from auto_trading import OKXTrader, OrderSide, OrderType
from moer_quant_pure import MoerChanlun
from realtime_data import RealtimeDataManager

import time
from datetime import datetime

# ========== 20倍杠杆配置 ==========
LEVERAGE = 20
MARGIN_MODE = "cross"
POSITION_PCT = 0.01
STOP_LOSS_PCT = 2
TAKE_PROFIT_PCT = 4
SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
# ==================================

print("🔴"*35)
print("⚠️  20倍杠杆全仓模式配置已创建")
print("🔴"*35)
print(f"\n杠杆: {LEVERAGE}x")
print(f"模式: {MARGIN_MODE} (全仓)")
print(f"仓位: {POSITION_PCT*100}%")
print(f"\n⚠️  高风险！请确保了解杠杆交易机制！")
print("🔴"*35)
