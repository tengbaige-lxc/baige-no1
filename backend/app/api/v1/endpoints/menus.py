from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.api.deps import get_db, get_current_active_superuser, get_current_user
from app.models.menu import Menu
from app.models.user import User
from app.schemas.menu import Menu as MenuSchema, MenuCreate, MenuUpdate
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("", response_model=ResponseModel[List[MenuSchema]])
async def list_menus(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(
        select(Menu)
        .where(and_(Menu.parent_id == None, Menu.is_active == True))
        .order_by(Menu.sort_order)
    )
    menus = result.scalars().all()
    
    async def build_tree(parent_id: int):
        result = await db.execute(
            select(Menu)
            .where(and_(Menu.parent_id == parent_id, Menu.is_active == True))
            .order_by(Menu.sort_order)
        )
        children = result.scalars().all()
        tree = []
        for child in children:
            child_dict = {
                "id": child.id,
                "name": child.name,
                "path": child.path,
                "component": child.component,
                "icon": child.icon,
                "title": child.title,
                "sort_order": child.sort_order,
                "parent_id": child.parent_id,
                "menu_type": child.menu_type,
                "permission": child.permission,
                "is_hidden": child.is_hidden,
                "is_active": child.is_active,
                "created_at": child.created_at,
                "children": await build_tree(child.id),
            }
            tree.append(MenuSchema.model_validate(child_dict))
        return tree
    
    menu_list = []
    for menu in menus:
        menu_dict = {
            "id": menu.id,
            "name": menu.name,
            "path": menu.path,
            "component": menu.component,
            "icon": menu.icon,
            "title": menu.title,
            "sort_order": menu.sort_order,
            "parent_id": menu.parent_id,
            "menu_type": menu.menu_type,
            "permission": menu.permission,
            "is_hidden": menu.is_hidden,
            "is_active": menu.is_active,
            "created_at": menu.created_at,
            "children": await build_tree(menu.id),
        }
        menu_list.append(MenuSchema.model_validate(menu_dict))
    
    return ResponseModel(data=menu_list)


@router.post("", response_model=ResponseModel[MenuSchema])
async def create_menu(
    menu_in: MenuCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_active_superuser),
) -> Any:
    db_menu = Menu(**menu_in.model_dump())
    db.add(db_menu)
    await db.commit()
    await db.refresh(db_menu)
    return ResponseModel(data=MenuSchema.model_validate(db_menu))


@router.put("/{menu_id}", response_model=ResponseModel[MenuSchema])
async def update_menu(
    menu_id: int,
    menu_in: MenuUpdate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_active_superuser),
) -> Any:
    result = await db.execute(select(Menu).where(Menu.id == menu_id))
    db_menu = result.scalar_one_or_none()
    if not db_menu:
        raise HTTPException(status_code=404, detail="菜单不存在")
    
    for field, value in menu_in.model_dump(exclude_unset=True).items():
        setattr(db_menu, field, value)
    
    await db.commit()
    await db.refresh(db_menu)
    return ResponseModel(data=MenuSchema.model_validate(db_menu))


@router.delete("/{menu_id}", response_model=ResponseModel)
async def delete_menu(
    menu_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_active_superuser),
) -> Any:
    result = await db.execute(select(Menu).where(Menu.id == menu_id))
    db_menu = result.scalar_one_or_none()
    if not db_menu:
        raise HTTPException(status_code=404, detail="菜单不存在")
    
    await db.delete(db_menu)
    await db.commit()
    return ResponseModel(message="删除成功")
