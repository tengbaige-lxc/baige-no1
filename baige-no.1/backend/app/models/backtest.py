from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, JSON
from datetime import datetime, timezone
import enum
from app.db.base import Base


class BacktestStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BacktestRun(Base):
    """回测任务记录"""
    __tablename__ = "backtest_runs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    symbol = Column(String(30), nullable=False)
    strategy_type = Column(String(50), nullable=False)        # divergence / ma_cross / rebound / grid
    days = Column(Integer, default=30)
    leverage = Column(Integer, default=20)
    position_percent = Column(Float, default=0.20)
    status = Column(String(20), default=BacktestStatus.PENDING.value)
    
    # 结果统计
    total_signals = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    loss_count = Column(Integer, default=0)
    win_rate = Column(Float, default=0)
    total_return = Column(Float, default=0)
    avg_return = Column(Float, default=0)
    max_drawdown = Column(Float, default=0)
    profit_factor = Column(Float, default=0)
    
    params = Column(JSON, default=dict)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)


class BacktestTrade(Base):
    """回测模拟交易记录"""
    __tablename__ = "backtest_trades"
    
    id = Column(Integer, primary_key=True, index=True)
    backtest_id = Column(Integer, nullable=False, index=True)
    symbol = Column(String(30), nullable=False)
    signal_type = Column(String(50), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    position_size = Column(Float, nullable=False)
    pnl_usdt = Column(Float, nullable=True)
    pnl_percent = Column(Float, nullable=True)
    hold_hours = Column(Float, nullable=True)
    entry_time = Column(DateTime, nullable=True)
    exit_time = Column(DateTime, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
