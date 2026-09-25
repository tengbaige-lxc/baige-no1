from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, JSON
from datetime import datetime, timezone
from app.db.base import Base


class RiskConfig(Base):
    """风控配置 - 白鸽一号交易参数持久化"""
    __tablename__ = "risk_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(100), default="默认风控")
    
    # 基础交易参数
    leverage = Column(Integer, default=20)
    position_percent = Column(Float, default=0.35)           # 目标保证金仓位比例（分批打满）
    max_symbol_margin_percent = Column(Float, default=0.30)  # 单币种保证金仓位上限
    max_daily_loss_percent = Column(Float, default=0.06)     # 单日最大亏损
    max_positions = Column(Integer, default=4)               # 最大持仓数
    
    # 移动止损参数
    activation_percent = Column(Float, default=0.05)         # 激活价
    callback_ratio = Column(Float, default=0.45)             # 回撤触发
    
    # 阶梯止盈 (JSON)
    profit_tiers = Column(JSON, default=list)
    
    # 加仓参数
    batch_sizes = Column(JSON, default=list)                 # [0.50, 0.30, 0.20]
    
    # 时间止损
    time_stop_enabled = Column(Boolean, default=True)
    max_hold_hours = Column(Integer, default=48)
    time_stop_reduce_ratio = Column(Float, default=0.50)
    
    # ATR止损
    atr_stop_enabled = Column(Boolean, default=True)
    atr_period = Column(Integer, default=14)
    atr_multiplier = Column(Float, default=2.0)
    
    # 单日风控
    max_trades_per_day = Column(Integer, default=10)
    cooldown_after_loss = Column(Integer, default=2)         # 小时
    
    # 相关性风控
    correlation_control_enabled = Column(Boolean, default=True)
    max_same_direction = Column(Integer, default=2)
    
    # 监控币种列表
    symbols = Column(JSON, default=list)
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
