"""
摩尔缠论 - 自动交易执行模块
支持OKX交易所自动下单、止盈止损、仓位管理
"""

import json
import urllib.request
import urllib.error
import ssl
import time
import hmac
import base64
import hashlib
from datetime import datetime, timezone
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum

# 禁用SSL验证
ssl._create_default_https_context = ssl._create_unverified_context

class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"

class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"

@dataclass
class Order:
    """订单信息"""
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    size: float
    status: str  # live, filled, cancelled
    filled_size: float = 0
    avg_price: float = 0
    created_at: str = ""

class OKXTrader:
    """OKX交易接口"""
    
    BASE_URL = "https://www.okx.com"
    
    def __init__(self, api_key: str, api_secret: str, passphrase: str, is_demo: bool = True):
        """
        Args:
            api_key: API Key
            api_secret: API Secret
            passphrase: 密码短语
            is_demo: 是否使用模拟盘
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.is_demo = is_demo
        
        if is_demo:
            self.BASE_URL = "https://www.okx.com"  # 模拟盘也用这个域名，需要在OKX开启模拟盘模式
        
    def _get_timestamp(self) -> str:
        """生成ISO格式时间戳"""
        now = datetime.now(timezone.utc)
        return now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    
    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        """生成签名"""
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        )
        return base64.b64encode(mac.digest()).decode('utf-8')
    
    def _request(self, method: str, path: str, body: Dict = None) -> Dict:
        """发送请求"""
        timestamp = self._get_timestamp()
        body_json = json.dumps(body) if body else ""
        
        headers = {
            'OK-ACCESS-KEY': self.api_key,
            'OK-ACCESS-SIGN': self._sign(timestamp, method, path, body_json),
            'OK-ACCESS-TIMESTAMP': timestamp,
            'OK-ACCESS-PASSPHRASE': self.passphrase,
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        
        if self.is_demo:
            headers['x-simulated-trading'] = '1'
        
        url = f"{self.BASE_URL}{path}"
        
        try:
            req = urllib.request.Request(
                url,
                data=body_json.encode('utf-8') if body else None,
                headers=headers,
                method=method
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode('utf-8'))
                
        except Exception as e:
            print(f"❌ 请求失败: {e}")
            return {"code": "-1", "msg": str(e)}
    
    def get_balance(self) -> Dict:
        """获取账户余额"""
        return self._request("GET", "/api/v5/account/balance")
    
    def get_positions(self, symbol: str = None) -> List[Dict]:
        """获取持仓信息"""
        path = "/api/v5/account/positions"
        if symbol:
            path += f"?instId={symbol}"
        return self._request("GET", path)
    
    def place_order(self, symbol: str, side: OrderSide, size: float, 
                    price: float = None, order_type: OrderType = OrderType.MARKET) -> Optional[Order]:
        """
        下单
        
        Args:
            symbol: 交易对，如 "DOGE-USDT"
            side: buy 或 sell
            size: 下单数量 (币的数量)
            price: 价格 (市价单可不填)
            order_type: market 或 limit
        """
        # 确保size是字符串且格式化正确
        size_str = f"{size:.8f}".rstrip('0').rstrip('.')
        
        body = {
            "instId": symbol,
            "tdMode": "cash",  # 现货模式
            "side": side.value,
            "ordType": order_type.value,
            "sz": size_str
        }
        
        if order_type == OrderType.LIMIT and price:
            body["px"] = str(price)
        
        print(f"   请求体: {body}")  # 调试输出
        
        result = self._request("POST", "/api/v5/trade/order", body)
        
        if result.get('code') == '0' and result.get('data'):
            data = result['data'][0]
            # 市价单返回的字段可能不同
            return Order(
                order_id=data['ordId'],
                symbol=symbol,
                side=side,
                price=float(data.get('px', 0) or price or 0),
                size=float(size),  # 使用请求中的size
                status='live',  # 新订单状态
                created_at=datetime.now().isoformat()
            )
        else:
            error_msg = result.get('msg', result.get('data', [{}])[0].get('sMsg', '未知错误'))
            print(f"❌ 下单失败: {error_msg}")
            print(f"   完整响应: {result}")
            return None
    
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """撤单"""
        body = {
            "instId": symbol,
            "ordId": order_id
        }
        result = self._request("POST", "/api/v5/trade/cancel-order", body)
        return result.get('code') == '0'
    
    def get_order(self, symbol: str, order_id: str) -> Optional[Order]:
        """查询订单状态"""
        path = f"/api/v5/trade/order?instId={symbol}&ordId={order_id}"
        result = self._request("GET", path)
        
        if result.get('code') == '0' and result.get('data'):
            data = result['data'][0]
            return Order(
                order_id=data['ordId'],
                symbol=symbol,
                side=OrderSide.BUY if data['side'] == 'buy' else OrderSide.SELL,
                price=float(data.get('px', 0)),
                size=float(data['sz']),
                status=data['state'],
                filled_size=float(data.get('accFillSz', 0)),
                avg_price=float(data.get('avgPx', 0))
            )
        return None


class AutoTrader:
    """自动交易执行器"""
    
    def __init__(self, api_key: str, api_secret: str, passphrase: str, is_demo: bool = True):
        self.trader = OKXTrader(api_key, api_secret, passphrase, is_demo)
        self.active_orders = {}  # 存储活跃订单
        
    def execute_signal(self, signal, symbol: str, position_pct: float = 0.1):
        """
        执行交易信号
        
        Args:
            signal: 交易信号
            symbol: 交易对
            position_pct: 仓位百分比 (0.1 = 10%)
        """
        print(f"\n{'='*70}")
        print(f"🚀 执行交易信号 - {signal.signal_type.value}")
        print(f"{'='*70}")
        
        # 1. 获取账户余额
        balance_result = self.trader.get_balance()
        if balance_result.get('code') != '0':
            print("❌ 获取余额失败")
            return None
        
        # 提取USDT余额
        usdt_balance = 0
        for detail in balance_result.get('data', [{}])[0].get('details', []):
            if detail.get('ccy') == 'USDT':
                usdt_balance = float(detail.get('availBal', 0))
                break
        
        print(f"💰 账户余额: {usdt_balance:.2f} USDT")
        
        # 2. 计算下单金额
        trade_amount = usdt_balance * position_pct
        
        if trade_amount < 10:  # 最小下单金额
            print(f"❌ 下单金额不足: {trade_amount:.2f} USDT (最小10)")
            return None
        
        # 3. 确定买卖方向
        if '买' in signal.signal_type.value:
            side = OrderSide.BUY
        else:
            side = OrderSide.SELL
        
        # 4. 计算下单数量（简化：按当前价格估算）
        current_price = signal.price
        size = trade_amount / current_price
        
        # 5. 执行下单
        print(f"\n📊 下单信息:")
        print(f"   交易对: {symbol}")
        print(f"   方向: {side.value}")
        print(f"   金额: {trade_amount:.2f} USDT")
        print(f"   数量: {size:.6f}")
        print(f"   价格: ${current_price:,.2f}")
        
        # 实际下单（注释掉，避免误操作）
        # order = self.trader.place_order(symbol, side, size, price=current_price, order_type=OrderType.LIMIT)
        
        print(f"\n⚠️  模拟下单（未实际执行）")
        print(f"   如需实盘，请取消注释 place_order 调用")
        
        # 6. 设置止盈止损（通过跟踪订单实现）
        print(f"\n🛡️  风控设置:")
        print(f"   止损: ${signal.stop_loss:,.2f}")
        print(f"   止盈: ${signal.take_profit:,.2f}")
        
        return {
            "symbol": symbol,
            "side": side.value,
            "size": size,
            "entry_price": current_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "status": "simulated"
        }
    
    def monitor_positions(self):
        """监控持仓并执行止盈止损"""
        print(f"\n{'='*70}")
        print("📊 持仓监控")
        print(f"{'='*70}")
        
        # 获取持仓
        positions = self.trader.get_positions()
        
        if not positions or positions.get('code') != '0':
            print("暂无持仓")
            return
        
        for pos in positions.get('data', []):
            symbol = pos['instId']
            pos_size = float(pos['pos'])
            avg_price = float(pos['avgPx'])
            mark_price = float(pos['markPx'])
            
            if pos_size == 0:
                continue
            
            pnl_pct = (mark_price - avg_price) / avg_price * 100
            
            print(f"\n   {symbol}:")
            print(f"      持仓: {pos_size}")
            print(f"      均价: ${avg_price:,.2f}")
            print(f"      市价: ${mark_price:,.2f}")
            print(f"      盈亏: {pnl_pct:+.2f}%")
            
            # 这里可以实现自动止盈止损逻辑
            # if pnl_pct <= -3:  # 止损
            #     self.trader.place_order(symbol, OrderSide.SELL, abs(pos_size))
            # elif pnl_pct >= 5:  # 止盈
            #     self.trader.place_order(symbol, OrderSide.SELL, abs(pos_size))


def demo_trading():
    """演示自动交易"""
    
    print("="*70)
    print("🚀 摩尔缠论 - 自动交易演示")
    print("="*70)
    print("\n⚠️  警告: 这是演示模式，不会实际下单")
    print("   如需实盘交易，请配置OKX API Key\n")
    
    # 模拟信号
    from moer_quant_pure import TradeSignal, SignalType
    
    mock_signal = TradeSignal(
        signal_type=SignalType.THIRD_BUY,
        price=67200,
        strength=85,
        stop_loss=65000,
        take_profit=72000,
        reason="三买信号测试",
        index=0
    )
    
    # 创建交易器（演示模式）
    trader = AutoTrader(
        api_key="demo_key",
        api_secret="demo_secret", 
        passphrase="demo_pass",
        is_demo=True
    )
    
    # 执行信号
    result = trader.execute_signal(
        signal=mock_signal,
        symbol="BTC-USDT",
        position_pct=0.1  # 10%仓位
    )
    
    if result:
        print(f"\n✅ 信号执行完成")
        print(f"   状态: {result['status']}")
    
    print("\n" + "="*70)
    print("💡 实盘配置方法:")
    print("="*70)
    print("""
1. 登录OKX官网 → API管理 → 创建API Key
2. 获取: API Key, API Secret, Passphrase
3. 开启模拟盘模式（建议先用模拟盘测试）
4. 修改代码:
   trader = AutoTrader(
       api_key="your_api_key",
       api_secret="your_api_secret",
       passphrase="your_passphrase",
       is_demo=True  # 先测试，确认后再改为False
   )
    """)


if __name__ == "__main__":
    demo_trading()
