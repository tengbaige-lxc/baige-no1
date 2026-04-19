from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class RoleBase(BaseModel):
    name: str
    code: str
    description: Optional[str] = None
    is_active: bool = True


class RoleCreate(RoleBase):
    menu_ids: Optional[List[int]] = []


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    menu_ids: Optional[List[int]] = None


class Role(RoleBase):
    id: int
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
