from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class MenuBase(BaseModel):
    name: str
    path: Optional[str] = None
    component: Optional[str] = None
    icon: Optional[str] = None
    title: str
    sort_order: int = 0
    parent_id: Optional[int] = None
    menu_type: str = "menu"
    permission: Optional[str] = None
    is_hidden: bool = False
    is_active: bool = True


class MenuCreate(MenuBase):
    pass


class MenuUpdate(BaseModel):
    name: Optional[str] = None
    path: Optional[str] = None
    component: Optional[str] = None
    icon: Optional[str] = None
    title: Optional[str] = None
    sort_order: Optional[int] = None
    parent_id: Optional[int] = None
    menu_type: Optional[str] = None
    permission: Optional[str] = None
    is_hidden: Optional[bool] = None
    is_active: Optional[bool] = None


class Menu(MenuBase):
    id: int
    created_at: Optional[datetime] = None
    children: Optional[List["Menu"]] = []

    class Config:
        from_attributes = True


Menu.model_rebuild()
