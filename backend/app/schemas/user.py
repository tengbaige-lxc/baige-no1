from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    username: str
    email: EmailStr
    full_name: Optional[str] = None
    avatar: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool = True


class UserCreate(UserBase):
    password: str
    role_id: Optional[int] = None


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    avatar: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    role_id: Optional[int] = None


class User(UserBase):
    id: int
    is_superuser: bool
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None
    role_id: Optional[int] = None
    role_name: Optional[str] = None

    class Config:
        from_attributes = True


class UserInDB(User):
    hashed_password: str


class UserLogin(BaseModel):
    username: str
    password: str


class ProfileUpdate(BaseModel):
    """当前用户自助更新个人资料（Phase 3d/F1：取代前端假保存）。只开放三个无害字段。"""
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class UserProfile(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    avatar: Optional[str] = None
    phone: Optional[str] = None
    role_name: Optional[str] = None
    permissions: list[str] = []
