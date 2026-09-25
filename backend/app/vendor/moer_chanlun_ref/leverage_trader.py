#!/usr/bin/env python3
"""
摩尔缠论 - 20倍杠杆全仓合约交易系统
⚠️ 极高风险！请确保了解杠杆交易机制！
"""

import sys
import time
import json
from datetime import datetime

sys.path.insert(0, '/root/.openclaw/workspace/moer-chanlun')

from config import OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
from moer_quant_pure import MoerChanlun
from realtime_data import RealtimeDataManager
from auto_trading import OKXTrader, OrderSide, OrderType

# ========== 合约配置 ==========
LEVERAGE = 20                 # 杠杆倍数
MARGIN_MODE = "cross"         # cross=全仓, isolated=逐仓
POSITION_PCT = 0.02           # 2%资金开仓（保守）
MIN_STRENGTH = 75             # 信号强度≥75
STOP_LOSS_PCT = 2             # 2%止损
TAKE_PROFIT_PCT = 6           # 6%止盈
CHECK_INTERVAL = 60           # 1分钟检查一次

# 合约交易对
CONTRACT_SYMBOLS = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
]
# =============================

class LeverageTrader:
    """杠杆交易器"""
    
    def __init__(self):
        self.data_mgr = RealtimeDataManager("okx")
        self.moer = MoerChanlun(ma_period=21, center_min_height=0.01)
        self.trader = OKXTrader(OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE, is_demo=False)
        self.open_positions = {}  # 持仓记录
        
    def print_banner(self):
        print("\n" + "="*70)
        print("🔴 摩尔缠论 - 20倍杠杆全仓合约交易")
        print("="*70)
        print(f"⚠️  极高风险警告")
        print(f"   杠杆倍数: {LEVERAGE}x")
        print(f"   保证金模式: {MARGIN_MODE}")
        print(f"   仓位比例: {POSITION_PCT*100}%")
        print(f"   止损: {STOP_LOSS_PCT}% | 止盈: {TAKE_PROFIT_PCT}%")
        print("="*70)
        print("\n📊 交易对:", ", ".join(CONTRACT_SYMBOLS))
        print("\n⏹️  按 Ctrl+C 停止\n")
        
    def set_leverage(self, symbol: str) -> bool:
        """设置杠杆倍数"""
        body = {
            "instId": symbol,
            "lever": str(LEVERAGE),
            "mgnMode": MARGIN_MODE
        }
        
        result = self.trader._request("POST", "/api/v5/account/set-leverage", body)
        
        if result.get('code') == '0':
            print(f"   ✅ {symbol} 杠杆设置: {LEVERAGE}x")
            return True
        else:
            print(f"   ⚠️  {symbol} 杠杆设置失败: {result.get('msg', '未知错误')}")
            return False
    
    def get_contract_price(self, symbol: str) -> float:
        """获取合约标记价格"""
        path = f"/api/v5/public/mark-price?instId={symbol}"
        result = self.trader._request("GET", path)
        
        if result.get('code') == '0' and result.get('data'):
            return float(result['data'][0]['markPx'])
        return 0
    
    def open_position(self, symbol: str, side: str, signal):
        """开仓"""
        print(f"\n{'='*70}")
        print(f"🚀 开仓 - {symbol} {side.upper()}")
        print(f"{'='*70}")
        
        # 获取保证金余额
        balance = self.get_margin_balance()
        if balance < 10:
            print(f"   ❌ 保证金不足: {balance:.2f} USDT")
            return False
        
        # 计算开仓金额
        position_value = balance * POSITION_PCT * LEVERAGE
        
        # 获取价格
        price = self.get_contract_price(symbol)
        if price == 0:
            print(f"   ❌ 获取价格失败")
            return False
        
        # 计算合约数量
        size = position_value / price
        
        print(f"   保证金: {balance:.2f} USDT")
        print(f"   仓位价值: {position_value:.2f} USDT (含{LEVERAGE}x杠杆)")
        print(f"   开仓价格: ${price:,.2f}")
        print(f"   合约数量: {size:.6f}")
        
        # 下单
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        order = self.trader.place_order(
            symbol=symbol,
            side=order_side,
            size=size,
            price=price,
            order_type=OrderType.LIMIT
        )
        
        if order:
            # 记录持仓
            self.open_positions[symbol] = {
                "side": side,
                "entry_price": price,
                "size": size,
                "order_id": order.order_id,
                "signal": signal.signal_type.value,
                "time": datetime.now().isoformat(),
                "stop_loss": price * (0.98 if side == "buy" else 1.02),
                "take_profit": price * (1.06 if side == "buy" else 0.94)
            }
            
            print(f"   ✅ 开仓成功!")
            print(f"      订单: {order.order_id}")
            
            self.save_position_record(self.open_positions[symbol])
            return True
        else:
            print(f"   ❌ 开仓失败")
            return False
    
    def close_position(self, symbol: str, reason: str):
        """平仓"""
        if symbol not in self.open_positions:
            return False
        
        pos = self.open_positions[symbol]
        side = "sell" if pos["side"] == "buy" else "buy"
        
        print(f"\n{'='*70}")
        print(f"📤 平仓 - {symbol} ({reason})")
        print(f"{'='*70}")
        
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        order = self.trader.place_order(
            symbol=symbol,
            side=order_side,
            size=pos["size"],
            order_type=OrderType.MARKET
        )
        
        if order:
            print(f"   ✅ 平仓成功!")
            
            # 计算盈亏
            exit_price = self.get_contract_price(symbol)
            pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
            if pos["side"] == "sell":
                pnl_pct = -pnl_pct
            
            print(f"      盈亏: {pnl_pct:+.2f}%")
            
            del self.open_positions[symbol]
            return True
        else:
            print(f"   ❌ 平仓失败")
            return False
    
    def check_positions(self):
        """检查持仓状态"""
        for symbol, pos in list(self.open_positions.items()):
            current_price = self.get_contract_price(symbol)
            if current_price == 0:
                continue
            
            # 计算盈亏
            pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            if pos["side"] == "sell":
                pnl_pct = -pnl_pct
            
            print(f"   {symbol}: {pnl_pct:+.2f}%", end="")
            
            # 止损检查
            if pnl_pct <= -STOP_LOSS_PCT:
                print(f" ⚠️ 止损!")
                self.close_position(symbol, f"止损 {pnl_pct:.2f}%")
            # 止盈检查
            elif pnl_pct >= TAKE_PROFIT_PCT:
                print(f" ✅ 止盈!")
                self.close_position(symbol, f"止盈 {pnl_pct:.2f}%")
            else:
                print(f" 持仓中")
    
    def get_margin_balance(self) -> float:
        """获取保证金余额"""
        result = self.trader.get_balance()
        if result.get('code') == '0':
            for detail in result.get('data', [{}])[0].get('details', []):
                if detail.get('ccy') == 'USDT':
                    return float(detail.get('availBal', 0))
        return 0
    
    def save_position_record(self, record: dict):
        """保存持仓记录"""
        try:
            filename = f"/root/.openclaw/workspace/moer-chanlun/leverage_positions_{datetime.now().strftime('%Y%m%d')}.json"
            try:
                with open(filename, 'r') as f:
                    history = json.load(f)
            except:
                history = []
            history.append(record)
            with open(filename, 'w') as f:
                json.dump(history, f, indent=2)
        except:
            pass
    
    def scan_and_trade(self):
        """扫描并交易"""
        print(f"\n{'='*70}")
        print(f"🔍 {datetime.now().strftime('%H:%M:%S')} - 扫描合约机会")
        print(f"{'='*70}")
        
        # 检查现有持仓
        if self.open_positions:
            print("\n📊 当前持仓:")
            self.check_positions()
        
        # 扫描新机会
        print("\n🔍 扫描新信号:")
        for symbol in CONTRACT_SYMBOLS:
            if symbol in self.open_positions:
                continue
            
            try:
                # 获取数据
                highs, lows, closes, volumes = self.data_mgr.fetch_data(
                    symbol.replace('-SWAP', ''), '1H', 100
                )
                if not closes:
                    continue
                
                # 分析
                signals = self.moer.scan(highs, lows, closes, volumes)
                strong = [s for s in signals if s.strength >= MIN_STRENGTH]
                
                if strong:
                    best = max(strong, key=lambda x: x.strength)
                    side = "buy" if "买" in best.signal_type.value else "sell"
                    
                    emoji = "🟢" if side == "buy" else "🔴"
                    print(f"\n   {emoji} {symbol}: {best.signal_type.value} (强度{best.strength})")
                    
                    # 开仓
                    self.open_position(symbol, side, best)
                else:
                    print(f"   {symbol}: 无信号")
                    
            except Exception as e:
                print(f"   {symbol}: 错误 - {e}")
    
    def run(self):
        """主循环"""
        self.print_banner()
        
        # 设置杠杆
        print("\n⚙️  设置杠杆倍数...")
        for symbol in CONTRACT_SYMBOLS:
            self.set_leverage(symbol)
        
        print("\n✅ 系统初始化完成，开始交易...")
        
        while True:
            try:
                self.scan_and_trade()
                
                print(f"\n⏰ 下次检查: {CHECK_INTERVAL}秒后...")
                print(f"{'='*70}")
                time.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                print("\n\n👋 杠杆交易已停止")
                break
            except Exception as e:
                print(f"\n❌ 错误: {e}")
                time.sleep(10)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    
    trader = LeverageTrader()
    
    if args.once:
        trader.print_banner()
        trader.scan_and_trade()
    else:
        trader.run()
