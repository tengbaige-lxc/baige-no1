from sqlalchemy import Column, Integer, String, Float, DateTime, Text, JSON
from datetime import datetime, timezone
from app.db.base import Base


class StrategyLog(Base):
    __tablename__ = "strategy_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False)
    exchange_config_id = Column(Integer, nullable=True, index=True)
    symbol = Column(String(20), nullable=False)
    signal = Column(String(10), nullable=False)            # BUY/SELL/HOLD
    price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)                   # 触发原因
    details = Column(JSON, default=dict)                   # 结构化详情：因子、开仓等
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
