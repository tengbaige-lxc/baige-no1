from sqlalchemy import Column, Integer, String, DateTime, Text, JSON
from datetime import datetime, timezone
from app.db.base import Base


class OperationLog(Base):
    __tablename__ = "operation_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)
    username = Column(String(50), nullable=True)
    action = Column(String(100), nullable=False)
    method = Column(String(10), nullable=True)
    path = Column(String(255), nullable=True)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)
    request_data = Column(JSON, nullable=True)
    response_data = Column(JSON, nullable=True)
    status_code = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
