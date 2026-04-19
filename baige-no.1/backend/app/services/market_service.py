from typing import List, Optional
from app.services.okx_client import okx_manager, decrypt_text
from app.models.exchange_config import ExchangeConfig


class MarketService:
    """Market data service for OKX."""
    
    async def get_ticker(self, symbol: str) -> dict:
        """Get symbol ticker price."""
        result = await okx_manager.get_ticker(symbol)
        return {
            "symbol": result.get("instId"),
            "price": float(result.get("last", 0)),
            "open24h": float(result.get("open24h", 0)),
            "high24h": float(result.get("high24h", 0)),
            "low24h": float(result.get("low24h", 0)),
            "vol24h": float(result.get("vol24h", 0)),
        }
    
    async def get_klines(self, symbol: str, bar: str = "1H", limit: int = 100) -> List[dict]:
        """Get candlestick data. OKX bar: 1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M"""
        klines = await okx_manager.get_candles(symbol, bar, limit)
        # OKX format: [ts, o, h, l, c, vol, volCcy]
        return [
            {
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in klines
        ]
    
    async def get_account_balance(self, config: ExchangeConfig) -> List[dict]:
        """Get account balances. Unified account uses eq/availEq instead of bal/availBal."""
        if not config:
            return []
        details = await okx_manager.get_balance(
            decrypt_text(config.api_key),
            decrypt_text(config.api_secret),
            decrypt_text(config.api_passphrase or ""),
        )
        return [
            {
                "asset": d.get("ccy", ""),
                "free": float(d.get("availEq", d.get("availBal", 0)) or 0),
                "locked": float(d.get("frozenBal", 0) or 0),
                "total": float(d.get("eq", d.get("bal", 0)) or 0),
            }
            for d in details
            if float(d.get("eq", d.get("bal", 0)) or 0) > 0
        ]
    
    async def get_orderbook(self, symbol: str) -> dict:
        """Get order book."""
        result = await okx_manager.get_orderbook(symbol, sz=5)
        bids = result.get("bids", [])
        asks = result.get("asks", [])
        return {
            "symbol": result.get("instId"),
            "bidPrice": float(bids[0][0]) if bids else 0,
            "bidQty": float(bids[0][1]) if bids else 0,
            "askPrice": float(asks[0][0]) if asks else 0,
            "askQty": float(asks[0][1]) if asks else 0,
            "bids": [[float(item[0]), float(item[1])] for item in bids[:5]],
            "asks": [[float(item[0]), float(item[1])] for item in asks[:5]],
        }


market_service = MarketService()
