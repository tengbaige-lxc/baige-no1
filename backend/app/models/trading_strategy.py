from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, JSON
from datetime import datetime, timezone
import enum
from app.db.base import Base


class StrategyType(str, enum.Enum):
    GRID = "grid"               # 网格交易
    MA_CROSS = "ma_cross"       # 均线交叉
    RSI = "rsi"                 # RSI 超买超卖
    DIVERGENCE = "divergence"   # 缠论背驰
    LIQUIDATION_MAP = "liquidation_map"  # 清算热力图
    MACRO_FILTERED = "macro_filtered"    # 宏观过滤
    WHITE_DOVE = "white_dove"   # 白鸽综合策略
    MULTI_FACTOR = "multi_factor"        # 多因子组合
    MICRO_SCALP = "micro_scalp"          # 1分钟高频短线
    CUSTOM = "custom"           # 自定义


class TradingStrategy(Base):
    __tablename__ = "trading_strategies"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)
    strategy_type = Column(String(20), default=StrategyType.GRID.value)
    symbol = Column(String(20), nullable=False)
    market_type = Column(String(20), default="SPOT")
    side = Column(String(10), default="BUY")               # 策略方向
    params = Column(JSON, default=dict)                     # 策略参数 JSON
    is_active = Column(Boolean, default=False)
    interval_seconds = Column(Integer, default=60)          # 轮询间隔
    last_run_at = Column(DateTime, nullable=True)
    last_signal = Column(String(20), nullable=True)         # BUY/SELL/HOLD
    total_pnl = Column(Float, default=0.0)
    total_trades = Column(Integer, default=0)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
