from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.api.deps import get_db, get_current_active_superuser
from app.models.role import Role
from app.models.menu import Menu
from app.schemas.role import Role as RoleSchema, RoleCreate, RoleUpdate
from app.schemas.common import ResponseModel, PaginatedResponse

router = APIRouter()


@router.get("", response_model=ResponseModel[PaginatedResponse[RoleSchema]])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_active_superuser),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str = Query(None),
) -> Any:
    query = select(Role)
    count_query = select(func.count(Role.id))
    
    if keyword:
        query = query.where(Role.name.ilike(f"%{keyword}%"))
        count_query = count_query.where(Role.name.ilike(f"%{keyword}%"))
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    roles = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[RoleSchema.model_validate(r) for r in roles],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("", response_model=ResponseModel[RoleSchema])
async def create_role(
    role_in: RoleCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_active_superuser),
) -> Any:
    result = await db.execute(select(Role).where(Role.code == role_in.code))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="角色编码已存在")
    
    db_role = Role(
        name=role_in.name,
        code=role_in.code,
        description=role_in.description,
    )
    db.add(db_role)
    await db.flush()
    
    if role_in.menu_ids:
        menus_result = await db.execute(select(Menu).where(Menu.id.in_(role_in.menu_ids)))
        menus = menus_result.scalars().all()
        db_role.menus.extend(menus)
    
    await db.commit()
    await db.refresh(db_role)
    return ResponseModel(data=RoleSchema.model_validate(db_role))


@router.put("/{role_id}", response_model=ResponseModel[RoleSchema])
async def update_role(
    role_id: int,
    role_in: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_active_superuser),
) -> Any:
    result = await db.execute(select(Role).where(Role.id == role_id))
    db_role = result.scalar_one_or_none()
    if not db_role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    for field, value in role_in.model_dump(exclude_unset=True, exclude={"menu_ids"}).items():
        setattr(db_role, field, value)
    
    if role_in.menu_ids is not None:
        menus_result = await db.execute(select(Menu).where(Menu.id.in_(role_in.menu_ids)))
        db_role.menus = list(menus_result.scalars().all())
    
    await db.commit()
    await db.refresh(db_role)
    return ResponseModel(data=RoleSchema.model_validate(db_role))


@router.delete("/{role_id}", response_model=ResponseModel)
async def delete_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_active_superuser),
) -> Any:
    result = await db.execute(select(Role).where(Role.id == role_id))
    db_role = result.scalar_one_or_none()
    if not db_role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    await db.delete(db_role)
    await db.commit()
    return ResponseModel(message="删除成功")
