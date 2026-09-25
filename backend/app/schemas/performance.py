from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


# ============== 交易记录 ==============
class TradeRecordCreate(BaseModel):
    symbol: str
    direction: str = "LONG"
    entry_price: float
    exit_price: Optional[float] = None
    position_size: float
    pnl_usdt: Optional[float] = None
    leverage: int = 20
    strategy_tag: Optional[str] = None
    notes: Optional[str] = None
    is_closed: bool = False


class TradeRecordUpdate(BaseModel):
    exit_price: Optional[float] = None
    pnl_usdt: Optional[float] = None
    notes: Optional[str] = None
    is_closed: Optional[bool] = None


class TradeRecordResponse(BaseModel):
    id: int
    user_id: int
    symbol: str
    direction: str
    entry_price: float
    exit_price: Optional[float]
    position_size: float
    pnl_usdt: Optional[float]
    leverage: int
    strategy_tag: Optional[str]
    notes: Optional[str]
    is_closed: bool
    external_id: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ============== 绩效分析 ==============
class TradeStats(BaseModel):
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    avg_win: Optional[float]
    avg_loss: Optional[float]
    profit_factor: float
    max_drawdown: float
    max_win: Optional[float]
    max_loss: Optional[float]


class SymbolStats(BaseModel):
    symbol: str
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


class StrategyStats(BaseModel):
    strategy_tag: str
    total_trades: int
    win_count: int
    loss_count: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


class DailyPnl(BaseModel):
    date: str
    pnl: float
    trade_count: int


class PerformanceOverview(BaseModel):
    overall: TradeStats
    symbol_stats: List[SymbolStats]
    strategy_stats: List[StrategyStats]
    daily_pnl: List[DailyPnl]
    period_days: int


# ============== 实时监控 ==============
class PositionItem(BaseModel):
    symbol: str
    coin: str
    market_type: str = "SWAP"
    settle_ccy: Optional[str] = None
    side: str
    raw_pos_side: Optional[str] = None
    strategy_tag: Optional[str] = None
    margin_mode: str
    pos: float
    notional_usd: float
    entry: float
    mark_px: float
    current: float
    pnl: float
    pnl_pct: float
    imr: float
    margin: float
    mgn_ratio: float
    lever: int
    liq_px: Optional[float] = None


class LiveMonitorData(BaseModel):
    balance_usdt: float
    positions: List[PositionItem]
    total_pnl: float
    total_margin: float
    total_pnl_pct: float
    position_count: int


# ============== 风控配置 ==============
class RiskConfigCreate(BaseModel):
    name: str = "默认风控"
    leverage: int = 20
    position_percent: float = 0.35
    max_symbol_margin_percent: float = 0.30
    max_daily_loss_percent: float = 0.06
    max_positions: int = 4
    activation_percent: float = 0.05
    callback_ratio: float = 0.45
    profit_tiers: List[dict] = []
    batch_sizes: List[float] = [0.50, 0.30, 0.20]
    time_stop_enabled: bool = True
    max_hold_hours: int = 48
    time_stop_reduce_ratio: float = 0.50
    atr_stop_enabled: bool = True
    atr_period: int = 14
    atr_multiplier: float = 2.0
    max_trades_per_day: int = 10
    cooldown_after_loss: int = 2
    correlation_control_enabled: bool = True
    max_same_direction: int = 2
    symbols: List[str] = []


class RiskConfigUpdate(BaseModel):
    name: Optional[str] = None
    leverage: Optional[int] = None
    position_percent: Optional[float] = None
    max_symbol_margin_percent: Optional[float] = None
    max_daily_loss_percent: Optional[float] = None
    max_positions: Optional[int] = None
    activation_percent: Optional[float] = None
    callback_ratio: Optional[float] = None
    profit_tiers: Optional[List[dict]] = None
    batch_sizes: Optional[List[float]] = None
    time_stop_enabled: Optional[bool] = None
    max_hold_hours: Optional[int] = None
    time_stop_reduce_ratio: Optional[float] = None
    atr_stop_enabled: Optional[bool] = None
    atr_period: Optional[int] = None
    atr_multiplier: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    cooldown_after_loss: Optional[int] = None
    correlation_control_enabled: Optional[bool] = None
    max_same_direction: Optional[int] = None
    symbols: Optional[List[str]] = None
    is_active: Optional[bool] = None


class RiskConfigResponse(BaseModel):
    id: int
    user_id: int
    name: str
    leverage: int
    position_percent: float
    max_symbol_margin_percent: Optional[float] = 0.30
    max_daily_loss_percent: float
    max_positions: int
    activation_percent: float
    callback_ratio: float
    profit_tiers: List[dict]
    batch_sizes: List[float]
    time_stop_enabled: bool
    max_hold_hours: int
    time_stop_reduce_ratio: float
    atr_stop_enabled: bool
    atr_period: int
    atr_multiplier: float
    max_trades_per_day: int
    cooldown_after_loss: int
    correlation_control_enabled: bool
    max_same_direction: int
    symbols: List[str]
    is_active: bool
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
