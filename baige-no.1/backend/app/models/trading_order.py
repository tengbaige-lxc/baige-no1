from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text
from datetime import datetime, timezone
import enum
from app.db.base import Base


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class OrderStatus(str, enum.Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class MarketType(str, enum.Enum):
    SPOT = "SPOT"
    FUTURES = "FUTURES"       # U本位合约
    DELIVERY = "DELIVERY"     # 币本位合约


class TradingOrder(Base):
    __tablename__ = "trading_orders"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    symbol = Column(String(20), nullable=False, index=True)      # BTCUSDT
    side = Column(String(10), nullable=False)                    # BUY/SELL
    order_type = Column(String(20), nullable=False)              # MARKET/LIMIT
    market_type = Column(String(20), default=MarketType.SPOT.value)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=True)                         # 市价单为空
    executed_qty = Column(Float, default=0.0)
    executed_price = Column(Float, nullable=True)
    status = Column(String(20), default=OrderStatus.NEW.value)
    binance_order_id = Column(String(50), nullable=True, index=True)
    client_order_id = Column(String(50), nullable=True)
    leverage = Column(Integer, default=1)                        # 合约杠杆
    reduce_only = Column(Boolean, default=False)
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
