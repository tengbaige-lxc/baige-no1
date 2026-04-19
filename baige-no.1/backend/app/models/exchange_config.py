from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from datetime import datetime, timezone
from app.db.base import Base


class ExchangeConfig(Base):
    __tablename__ = "exchange_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), default="币安主账户")
    exchange = Column(String(20), default="okx", nullable=False)
    api_key = Column(Text, nullable=False)          # AES encrypted
    api_secret = Column(Text, nullable=False)       # AES encrypted
    api_passphrase = Column(Text, nullable=True)    # AES encrypted (OKX needs this)
    is_testnet = Column(Boolean, default=False)     # OKX demo trading uses separate keys
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
