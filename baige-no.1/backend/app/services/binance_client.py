import asyncio
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import base64
import hashlib
import os

# Fallback secret (in production should be env var)
_KEY_SECRET = os.environ.get("EXCHANGE_KEY_SECRET", "baige-no1-secret-key-32bytes!!")


def _get_key() -> bytes:
    """Derive 32-byte AES key from secret."""
    return hashlib.sha256(_KEY_SECRET.encode()).digest()


def encrypt_text(plain: str) -> str:
    """Encrypt text with AES-CBC."""
    if not plain:
        return ""
    key = _get_key()
    cipher = AES.new(key, AES.MODE_CBC)
    ct_bytes = cipher.encrypt(pad(plain.encode(), AES.block_size))
    return base64.b64encode(cipher.iv + ct_bytes).decode()


def decrypt_text(encrypted: str) -> str:
    """Decrypt text with AES-CBC."""
    if not encrypted:
        return ""
    key = _get_key()
    data = base64.b64decode(encrypted.encode())
    iv = data[:16]
    ct = data[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    return unpad(cipher.decrypt(ct), AES.block_size).decode()


class BinanceClientManager:
    """Manage Binance client instances."""
    
    def __init__(self):
        self._clients = {}
    
    async def _get_client(self, api_key: str, api_secret: str, testnet: bool = True) -> Client:
        """Async get or create a Binance client."""
        cache_key = f"{api_key}:{testnet}"
        if cache_key not in self._clients:
            loop = asyncio.get_event_loop()
            def _init():
                return Client(api_key=api_key, api_secret=api_secret, testnet=testnet)
            self._clients[cache_key] = await loop.run_in_executor(None, _init)
        return self._clients[cache_key]
    
    async def ping(self, api_key: str, api_secret: str, testnet: bool = True) -> bool:
        """Test connectivity."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, client.ping)
            return True
        except Exception:
            return False
    
    async def get_account(self, api_key: str, api_secret: str, testnet: bool = True):
        """Get account info."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, client.get_account)
    
    async def get_symbol_ticker(self, symbol: str, api_key: str = "", api_secret: str = "", testnet: bool = True):
        """Get symbol price ticker."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, client.get_symbol_ticker, symbol)
    
    async def get_klines(self, symbol: str, interval: str, limit: int = 100,
                         api_key: str = "", api_secret: str = "", testnet: bool = True):
        """Get kline/candlestick data."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, client.get_klines, symbol, interval, limit)
    
    async def get_orderbook_ticker(self, symbol: str, api_key: str = "", api_secret: str = "", testnet: bool = True):
        """Get order book ticker."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, client.get_orderbook_ticker, symbol)
    
    async def create_order(self, symbol: str, side: str, order_type: str, quantity: float,
                           price: float = None, api_key: str = "", api_secret: str = "",
                           testnet: bool = True):
        """Create a new order."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        kwargs = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
        }
        if price is not None and order_type != "MARKET":
            kwargs["price"] = price
        
        def _create():
            return client.create_order(**kwargs)
        
        return await loop.run_in_executor(None, _create)
    
    async def cancel_order(self, symbol: str, order_id: str,
                           api_key: str = "", api_secret: str = "", testnet: bool = True):
        """Cancel an order."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, client.cancel_order, symbol, orderId=order_id)
    
    async def get_all_orders(self, symbol: str, limit: int = 50,
                             api_key: str = "", api_secret: str = "", testnet: bool = True):
        """Get all orders for a symbol."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, client.get_all_orders, symbol, limit=limit)
    
    async def get_open_orders(self, symbol: str = None,
                              api_key: str = "", api_secret: str = "", testnet: bool = True):
        """Get open orders."""
        client = await self._get_client(api_key, api_secret, testnet)
        loop = asyncio.get_event_loop()
        if symbol:
            return await loop.run_in_executor(None, client.get_open_orders, symbol)
        return await loop.run_in_executor(None, client.get_open_orders)


# Global instance
binance_manager = BinanceClientManager()
