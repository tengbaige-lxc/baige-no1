from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime, timezone
from app.db.base import Base


class TradingPosition(Base):
    __tablename__ = "trading_positions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(20), nullable=False, index=True)
    market_type = Column(String(20), default="SPOT")
    side = Column(String(10), nullable=False)              # LONG / SHORT
    entry_price = Column(Float, nullable=False)
    mark_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    notional = Column(Float, nullable=True)                # 名义价值
    pnl = Column(Float, default=0.0)                       # 盈亏
    pnl_percent = Column(Float, default=0.0)               # 盈亏百分比
    leverage = Column(Integer, default=1)
    margin = Column(Float, nullable=True)
    liquidation_price = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
