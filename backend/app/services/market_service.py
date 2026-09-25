from typing import List, Optional
from app.services.okx_client import okx_manager, decrypt_text
from app.models.exchange_config import ExchangeConfig


OKX_BAR_ALIASES = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "12h": "12H",
    "1d": "1D",
    "1w": "1W",
    "1M": "1M",
}


def normalize_okx_bar(bar: str) -> str:
    """Normalize UI/API interval aliases to OKX's case-sensitive bar values."""
    normalized = OKX_BAR_ALIASES.get((bar or "").strip())
    if normalized:
        return normalized

    lower_alias = OKX_BAR_ALIASES.get((bar or "").strip().lower())
    if lower_alias and lower_alias != "1M":
        return lower_alias

    allowed = ", ".join(OKX_BAR_ALIASES.values())
    raise ValueError(f"Unsupported kline interval '{bar}'. Allowed intervals: {allowed}")


def normalize_okx_symbol(symbol: str) -> str:
    """Normalize trade page symbols to OKX perpetual swap instId."""
    normalized = (symbol or "").strip().upper()
    if not normalized:
        raise ValueError("Symbol is required")
    if normalized.endswith("-SWAP"):
        return normalized
    if normalized.endswith("-USDT"):
        return f"{normalized}-SWAP"
    return normalized


def get_okx_bar_candidates(bar: str) -> List[str]:
    """Return the exact OKX bar value. OKX is strict about case."""
    normalized = normalize_okx_bar(bar)
    return [normalized]


class MarketService:
    """Market data service for OKX."""
    
    async def get_ticker(self, symbol: str) -> dict:
        """Get symbol ticker price."""
        result = await okx_manager.get_ticker(normalize_okx_symbol(symbol))
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
        inst_id = normalize_okx_symbol(symbol)
        last_error = None
        klines = None

        for candidate_bar in get_okx_bar_candidates(bar):
            try:
                klines = await okx_manager.get_candles(inst_id, candidate_bar, limit)
                break
            except Exception as exc:
                last_error = exc
                if "Parameter bar error" not in str(exc):
                    raise

        if klines is None:
            raise last_error or ValueError(f"Unsupported kline interval '{bar}'")

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
            decrypt_text(config.api_passphrase or ""), simulated=bool(config.is_testnet))
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
        inst_id = normalize_okx_symbol(symbol)
        result = await okx_manager.get_orderbook(inst_id, sz=5)
        bids = result.get("bids", [])
        asks = result.get("asks", [])
        return {
            "symbol": result.get("instId") or inst_id,
            "bidPrice": float(bids[0][0]) if bids else 0,
            "bidQty": float(bids[0][1]) if bids else 0,
            "askPrice": float(asks[0][0]) if asks else 0,
            "askQty": float(asks[0][1]) if asks else 0,
            "bids": [[float(item[0]), float(item[1])] for item in bids[:5]],
            "asks": [[float(item[0]), float(item[1])] for item in asks[:5]],
        }

    async def get_instruments(self, inst_type: str = "SWAP", quote_ccy: str = "USDT") -> List[dict]:
        """Get OKX instruments for selector UIs."""
        instruments = await okx_manager.get_instruments(inst_type.upper())
        quote = (quote_ccy or "").upper()
        filtered = [
            item for item in instruments
            if item.get("state") == "live"
            and (
                not quote
                or item.get("quoteCcy", "").upper() == quote
                or item.get("settleCcy", "").upper() == quote
                or item.get("instId", "").upper().endswith(f"-{quote}-SWAP")
            )
        ]
        filtered.sort(key=lambda item: item.get("instId", ""))
        return [
            {
                "symbol": item.get("instId"),
                "base": item.get("baseCcy"),
                "quote": item.get("quoteCcy"),
                "settle": item.get("settleCcy"),
                "type": item.get("instType"),
                "label": item.get("instId"),
                "ctVal": item.get("ctVal"),
                "lotSz": item.get("lotSz"),
                "minSz": item.get("minSz"),
            }
            for item in filtered
        ]


market_service = MarketService()
