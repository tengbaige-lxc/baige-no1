from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.db.base import Base

role_menus = Table(
    "role_menus",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id"), primary_key=True),
    Column("menu_id", Integer, ForeignKey("menus.id"), primary_key=True),
)


class Menu(Base):
    __tablename__ = "menus"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    path = Column(String(100), nullable=True)
    component = Column(String(100), nullable=True)
    icon = Column(String(50), nullable=True)
    title = Column(String(50), nullable=False)
    sort_order = Column(Integer, default=0)
    parent_id = Column(Integer, ForeignKey("menus.id"), nullable=True)
    menu_type = Column(String(20), default="menu")  # menu, button, directory
    permission = Column(String(100), nullable=True)
    is_hidden = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    parent = relationship("Menu", remote_side=[id], back_populates="children", lazy="selectin")
    children = relationship("Menu", back_populates="parent", lazy="selectin")
    roles = relationship("Role", secondary=role_menus, back_populates="menus", lazy="selectin")
