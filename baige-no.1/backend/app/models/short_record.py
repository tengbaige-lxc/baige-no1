from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from datetime import datetime, timezone
from app.db.base import Base


class ShortRecord(Base):
    """做空记录"""
    __tablename__ = "short_records"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(30), nullable=False, index=True)
    entry_price = Column(Float, nullable=False)
    original_size = Column(Float, nullable=False)
    reduced_count = Column(Integer, default=0)
    reduced_size = Column(Float, default=0.0)
    remaining_size = Column(Float, nullable=False)
    targets_hit = Column(Text, default="[]")
    close_price = Column(Float, nullable=True)
    pnl_usdt = Column(Float, nullable=True)
    close_reason = Column(Text, nullable=True)
    status = Column(String(20), default="OPEN")               # OPEN / CLOSED
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
