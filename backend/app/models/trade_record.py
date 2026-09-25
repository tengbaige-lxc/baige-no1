from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from datetime import datetime, timezone
from app.db.base import Base


class TradeRecord(Base):
    """交易记录 - 对应白鸽一号 real_trader_v2 的 trades 表"""
    __tablename__ = "trade_records"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    # A user may connect more than one exchange account.  Keep every FIFO lot
    # scoped to the account that actually owns it.
    exchange_config_id = Column(Integer, nullable=True, index=True)
    symbol = Column(String(30), nullable=False, index=True)        # BTC-USDT-SWAP
    direction = Column(String(10), nullable=False)                 # LONG / SHORT
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    position_size = Column(Float, nullable=False)                  # 持仓张数
    ct_val = Column(Float, nullable=True)                          # 合约面值(张->币)，开仓时落库；历史记录为空则按 symbol 回退查静态表
    pnl_usdt = Column(Float, nullable=True)                        # 盈亏(USDT)，= (exit-entry)*position_size*ct_val
    leverage = Column(Integer, default=20)
    strategy_tag = Column(String(50), nullable=True)               # 策略标签
    notes = Column(Text, nullable=True)
    is_closed = Column(Boolean, default=False)                     # 是否已平仓
    external_id = Column(String(50), nullable=True, index=True)    # 交易所原始ID（如OKX posId）
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
