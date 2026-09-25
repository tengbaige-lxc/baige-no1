from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON, ForeignKey
from datetime import datetime, timezone
from app.db.base import Base


class StrategyVersion(Base):
    """策略版本快照（支持发布与回滚）"""
    __tablename__ = "strategy_versions"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(Integer, ForeignKey("trading_strategies.id"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False, default=dict)
    note = Column(String(255), nullable=True)
    source_version_id = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    published_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

