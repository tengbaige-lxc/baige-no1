from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class MessageBase(BaseModel):
    title: str
    content: str
    msg_type: str = "system"


class MessageCreate(MessageBase):
    receiver_id: Optional[int] = None


class MessageUpdate(BaseModel):
    status: Optional[str] = None
    is_read: Optional[bool] = None


class Message(MessageBase):
    id: int
    status: str
    sender_id: Optional[int] = None
    sender_name: Optional[str] = None
    receiver_id: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
