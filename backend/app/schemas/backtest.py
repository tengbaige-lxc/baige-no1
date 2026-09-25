from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


class BacktestCreate(BaseModel):
    name: str
    symbol: str
    strategy_type: str = "divergence"
    days: int = 30
    leverage: int = 20
    position_percent: float = 0.20
    params: dict = {}


class BacktestResponse(BaseModel):
    id: int
    user_id: int
    name: str
    symbol: str
    strategy_type: str
    days: int
    leverage: int
    position_percent: float
    status: str
    total_signals: int
    win_count: int
    loss_count: int
    win_rate: float
    total_return: float
    avg_return: float
    max_drawdown: float
    profit_factor: float
    params: dict
    error_msg: Optional[str]
    created_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class BacktestTradeResponse(BaseModel):
    id: int
    backtest_id: int
    symbol: str
    signal_type: str
    entry_price: float
    exit_price: Optional[float]
    position_size: float
    pnl_usdt: Optional[float]
    pnl_percent: Optional[float]
    hold_hours: Optional[float]
    entry_time: Optional[datetime]
    exit_time: Optional[datetime]
    reason: Optional[str]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class BacktestDetailResponse(BaseModel):
    backtest: BacktestResponse
    trades: List[BacktestTradeResponse]
