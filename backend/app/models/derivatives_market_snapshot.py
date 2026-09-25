from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, String

from app.db.base import Base


class DerivativesMarketSnapshot(Base):
    __tablename__ = "derivatives_market_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(30), nullable=False, index=True)
    last_price = Column(Float, nullable=False)
    open_interest = Column(Float, nullable=False)
    funding_rate = Column(Float, nullable=True)
    observed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("ix_derivatives_market_snapshots_symbol_observed_at", "symbol", "observed_at"),
    )
