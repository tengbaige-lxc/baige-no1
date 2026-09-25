from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Enum
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum
from app.db.base import Base


class MessageType(str, enum.Enum):
    SYSTEM = "system"
    NOTICE = "notice"
    PRIVATE = "private"


class MessageStatus(str, enum.Enum):
    UNREAD = "unread"
    READ = "read"


class Message(Base):
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    msg_type = Column(String(20), default=MessageType.SYSTEM.value)
    status = Column(String(20), default=MessageStatus.UNREAD.value)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    sender_name = Column(String(50), nullable=True)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    sender = relationship("User", foreign_keys=[sender_id], backref="sent_messages", lazy="selectin")
    receiver = relationship("User", foreign_keys=[receiver_id], backref="received_messages", lazy="selectin")
