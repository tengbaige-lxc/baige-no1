import json
from pydantic import BaseModel, field_validator
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
    pos_side: Optional[str] = None
    remark: Optional[str] = None
    # Phase 2.5g：取整后不足 minSz 时默认放弃该笔（抬到 minSz 会超出保证金计划）；
    # 显式置 true 恢复"抬到 minSz"的旧行为。平仓单不受影响（始终可平）。
    allow_min_size_bump: bool = False


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
    is_active: bool = False
    interval_seconds: int = 60
    remark: Optional[str] = None

    @field_validator('strategy_type')
    @classmethod
    def validate_strategy_type(cls, v):
        allowed = {'grid', 'ma_cross', 'rsi', 'divergence', 'liquidation_map', 'macro_filtered', 'white_dove', 'multi_factor', 'micro_scalp', 'custom'}
        if v not in allowed:
            raise ValueError(f'无效的策略类型: {v}，允许的类型: {allowed}')
        return v

    @field_validator('side')
    @classmethod
    def validate_side(cls, v):
        side = (v or "BUY").upper()
        allowed = {'BUY', 'SELL', 'BOTH'}
        if side not in allowed:
            raise ValueError(f'无效的策略方向: {v}，允许的方向: {allowed}')
        return side


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    strategy_type: Optional[str] = None
    symbol: Optional[str] = None
    market_type: Optional[str] = None
    side: Optional[str] = None
    params: Optional[dict] = None
    is_active: Optional[bool] = None
    interval_seconds: Optional[int] = None
    remark: Optional[str] = None

    @field_validator('strategy_type')
    @classmethod
    def validate_strategy_type(cls, v):
        if v is None:
            return v
        allowed = {'grid', 'ma_cross', 'rsi', 'divergence', 'liquidation_map', 'macro_filtered', 'white_dove', 'multi_factor', 'micro_scalp', 'custom'}
        if v not in allowed:
            raise ValueError(f'无效的策略类型: {v}，允许的类型: {allowed}')
        return v

    @field_validator('side')
    @classmethod
    def validate_side(cls, v):
        if v is None:
            return v
        side = v.upper()
        allowed = {'BUY', 'SELL', 'BOTH'}
        if side not in allowed:
            raise ValueError(f'无效的策略方向: {v}，允许的方向: {allowed}')
        return side


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
    health: Optional[dict] = None

    class Config:
        from_attributes = True


class StrategyVersionPublishRequest(BaseModel):
    note: Optional[str] = None


class StrategyVersionRollbackRequest(BaseModel):
    target_version_id: Optional[int] = None
    note: Optional[str] = None


class StrategyVersionResponse(BaseModel):
    id: int
    strategy_id: int
    user_id: int
    version: int
    note: Optional[str]
    source_version_id: Optional[int]
    is_active: bool
    published_at: Optional[datetime]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


# Strategy Log
class StrategyLogResponse(BaseModel):
    id: int
    strategy_id: int
    strategy_name: Optional[str] = None
    symbol: str
    signal: str
    price: Optional[float]
    quantity: Optional[float]
    pnl: Optional[float]
    reason: Optional[str]
    details: Optional[dict] = None
    created_at: Optional[datetime]

    @field_validator('details', mode='before')
    @classmethod
    def parse_details(cls, v):
        if isinstance(v, str):
            if not v:
                return None
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {"raw": v}
        return v

    class Config:
        from_attributes = True
