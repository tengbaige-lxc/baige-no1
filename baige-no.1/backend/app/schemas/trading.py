from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime


# Exchange Config
class ExchangeConfigBase(BaseModel):
    name: str = "币安主账户"
    exchange: str = "binance"
    is_testnet: bool = True
    is_active: bool = True


class ExchangeConfigCreate(ExchangeConfigBase):
    api_key: str
    api_secret: str
    api_passphrase: str = ""


class ExchangeConfigUpdate(BaseModel):
    name: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    is_testnet: Optional[bool] = None
    is_active: Optional[bool] = None


class ExchangeConfig(ExchangeConfigBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Order
class OrderCreate(BaseModel):
    symbol: str
    side: str          # BUY / SELL
    order_type: str    # MARKET / LIMIT
    quantity: float
    price: Optional[float] = None
    market_type: str = "SPOT"
    margin_mode: str = "cross"   # cross | isolated
    leverage: int = 1
    reduce_only: bool = False
    remark: Optional[str] = None


class OrderResponse(BaseModel):
    id: int
    symbol: str
    side: str
    order_type: str
    market_type: str
    quantity: float
    price: Optional[float]
    executed_qty: float
    executed_price: Optional[float]
    status: str
    binance_order_id: Optional[str]
    leverage: int
    remark: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# Market Data
class TickerResponse(BaseModel):
    symbol: str
    price: float


class KlineResponse(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class BalanceResponse(BaseModel):
    asset: str
    free: float
    locked: float
    total: float


class OrderbookResponse(BaseModel):
    symbol: str
    bidPrice: float
    bidQty: float
    askPrice: float
    askQty: float
    bids: List[List[float]] = []
    asks: List[List[float]] = []


# Strategy
class StrategyCreate(BaseModel):
    name: str
    strategy_type: str = "grid"
    symbol: str
    market_type: str = "SPOT"
    side: str = "BUY"
    params: dict = {}
    interval_seconds: int = 60
    remark: Optional[str] = None


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    params: Optional[dict] = None
    is_active: Optional[bool] = None
    interval_seconds: Optional[int] = None
    remark: Optional[str] = None


class StrategyResponse(BaseModel):
    id: int
    name: str
    strategy_type: str
    symbol: str
    market_type: str
    side: str
    params: dict
    is_active: bool
    interval_seconds: int
    last_run_at: Optional[datetime]
    last_signal: Optional[str]
    total_pnl: float
    total_trades: int
    remark: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# Strategy Log
class StrategyLogResponse(BaseModel):
    id: int
    strategy_id: int
    symbol: str
    signal: str
    price: Optional[float]
    quantity: Optional[float]
    pnl: Optional[float]
    reason: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True
