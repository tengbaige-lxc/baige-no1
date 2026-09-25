from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime


class OperationLogBase(BaseModel):
    action: str
    method: Optional[str] = None
    path: Optional[str] = None
    ip_address: Optional[str] = None


class OperationLogCreate(OperationLogBase):
    user_id: Optional[int] = None
    username: Optional[str] = None
    user_agent: Optional[str] = None
    request_data: Optional[Any] = None
    response_data: Optional[Any] = None
    status_code: Optional[int] = None
    duration_ms: Optional[int] = None


class OperationLog(OperationLogBase):
    id: int
    username: Optional[str] = None
    user_agent: Optional[str] = None
    status_code: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
