from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from datetime import datetime, timezone
import enum
from app.db.base import Base


class SignalType(str, enum.Enum):
    LIQUIDATION = "liquidation"      # 清算热力图
    DIVERGENCE = "divergence"        # 缠论背驰
    MACRO = "macro"                  # 宏观过滤
    TRENDLINE = "trendline"          # 趋势线
    MANUAL = "manual"                # 手动


class SignalDirection(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    CAUTION = "CAUTION"
    NEUTRAL = "NEUTRAL"
    LONG_BIAS = "LONG_BIAS"
    SHORT_BIAS = "SHORT_BIAS"


class TradingSignal(Base):
    """交易信号记录"""
    __tablename__ = "trading_signals"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(30), nullable=False, index=True)
    signal_type = Column(String(20), nullable=False)          # liquidation/divergence/macro
    direction = Column(String(20), nullable=False)            # LONG/SHORT/CAUTION/NEUTRAL
    strength = Column(Integer, default=1)                     # 1-5
    confidence = Column(Integer, default=50)                  # 0-100
    current_price = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)               # 信号目标价
    stop_price = Column(Float, nullable=True)                 # 建议止损价
    reason = Column(Text, nullable=True)                      # 信号原因
    is_active = Column(Boolean, default=True)                 # 信号是否有效
    triggered_at = Column(DateTime, nullable=True)            # 触发时间
    resolved_at = Column(DateTime, nullable=True)             # 解决时间
    result_pnl = Column(Float, nullable=True)                 # 实际盈亏
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
