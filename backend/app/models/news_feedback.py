from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from datetime import datetime, timezone
from app.db.base import Base


class NewsFactorFeedback(Base):
    __tablename__ = "news_factor_feedback"

    id = Column(Integer, primary_key=True, index=True)
    news_factor_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    predicted_direction = Column(String(10), default="neutral")
    predicted_strength = Column(Integer, default=0)
    price_at_news = Column(Float, default=0)
    price_after_1h = Column(Float, default=0)
    price_after_4h = Column(Float, default=0)
    return_1h = Column(Float, default=0)
    return_4h = Column(Float, default=0)
    is_correct_1h = Column(Boolean, default=False)
    is_correct_4h = Column(Boolean, default=False)
    checked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
