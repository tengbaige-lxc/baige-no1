from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class SignalResponse(BaseModel):
    id: int
    user_id: int
    symbol: str
    signal_type: str
    direction: str
    strength: int
    confidence: int
    current_price: Optional[float]
    target_price: Optional[float]
    stop_price: Optional[float]
    reason: Optional[str]
    is_active: bool
    triggered_at: Optional[datetime]
    resolved_at: Optional[datetime]
    result_pnl: Optional[float]
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class SignalScanRequest(BaseModel):
    symbol: str


class SignalScanResult(BaseModel):
    symbol: str
    liquidation: Optional[dict]
    divergence: Optional[dict]
    macro: Optional[dict]
    timestamp: str


class ReduceRecordResponse(BaseModel):
    id: int
    user_id: int
    symbol: str
    direction: str
    entry_price: float
    original_size: float
    reduced_count: int
    reduced_size: float
    remaining_size: float
    targets_hit: Optional[str]
    reduce_reason: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class ShortRecordResponse(BaseModel):
    id: int
    user_id: int
    symbol: str
    entry_price: float
    original_size: float
    reduced_count: int
    reduced_size: float
    remaining_size: float
    targets_hit: Optional[str]
    close_price: Optional[float]
    pnl_usdt: Optional[float]
    close_reason: Optional[str]
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
