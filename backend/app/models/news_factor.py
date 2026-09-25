from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from datetime import datetime, timezone
from app.db.base import Base


class NewsFactor(Base):
    __tablename__ = "news_factors"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    source = Column(String(100), default="")
    url = Column(String(500), nullable=True)
    direction = Column(String(10), default="neutral")   # bullish / bearish / neutral
    strength = Column(Integer, default=0)               # -5 ~ +5
    dimension = Column(String(20), default="general")   # capital/demand/regulation/liquidity/narrative
    raw_summary = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
