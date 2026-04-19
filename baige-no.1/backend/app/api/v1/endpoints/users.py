from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.api.deps import get_db, get_current_user, get_current_active_superuser
from app.core.security import get_password_hash
from app.models.user import User
from app.models.role import Role
from app.schemas.user import User as UserSchema, UserCreate, UserUpdate
from app.schemas.common import ResponseModel, PaginatedResponse

router = APIRouter()


@router.get("", response_model=ResponseModel[PaginatedResponse[UserSchema]])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_superuser),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str = Query(None),
    is_active: bool = Query(None),
) -> Any:
    query = select(User)
    count_query = select(func.count(User.id))
    
    filters = []
    if keyword:
        filters.append(User.username.ilike(f"%{keyword}%"))
    if is_active is not None:
        filters.append(User.is_active == is_active)
    
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    users = result.scalars().all()
    
    user_list = []
    for u in users:
        user_dict = {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "full_name": u.full_name,
            "avatar": u.avatar,
            "phone": u.phone,
            "is_active": u.is_active,
            "is_superuser": u.is_superuser,
            "last_login": u.last_login,
            "created_at": u.created_at,
            "role_id": u.role_id,
            "role_name": u.role.name if u.role else None,
        }
        user_list.append(UserSchema.model_validate(user_dict))
    
    return ResponseModel(data=PaginatedResponse(
        items=user_list,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("", response_model=ResponseModel[UserSchema])
async def create_user(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    result = await db.execute(select(User).where(User.username == user_in.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="邮箱已存在")
    
    db_user = User(
        username=user_in.username,
        email=user_in.email,
        full_name=user_in.full_name,
        phone=user_in.phone,
        hashed_password=get_password_hash(user_in.password),
        role_id=user_in.role_id,
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    
    user_dict = {
        "id": db_user.id,
        "username": db_user.username,
        "email": db_user.email,
        "full_name": db_user.full_name,
        "avatar": db_user.avatar,
        "phone": db_user.phone,
        "is_active": db_user.is_active,
        "is_superuser": db_user.is_superuser,
        "last_login": db_user.last_login,
        "created_at": db_user.created_at,
        "role_id": db_user.role_id,
        "role_name": db_user.role.name if db_user.role else None,
    }
    return ResponseModel(data=UserSchema.model_validate(user_dict))


@router.put("/{user_id}", response_model=ResponseModel[UserSchema])
async def update_user(
    user_id: int,
    user_in: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    for field, value in user_in.model_dump(exclude_unset=True).items():
        setattr(db_user, field, value)
    
    await db.commit()
    await db.refresh(db_user)
    
    user_dict = {
        "id": db_user.id,
        "username": db_user.username,
        "email": db_user.email,
        "full_name": db_user.full_name,
        "avatar": db_user.avatar,
        "phone": db_user.phone,
        "is_active": db_user.is_active,
        "is_superuser": db_user.is_superuser,
        "last_login": db_user.last_login,
        "created_at": db_user.created_at,
        "role_id": db_user.role_id,
        "role_name": db_user.role.name if db_user.role else None,
    }
    return ResponseModel(data=UserSchema.model_validate(user_dict))


@router.delete("/{user_id}", response_model=ResponseModel)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_superuser),
) -> Any:
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    await db.delete(db_user)
    await db.commit()
    return ResponseModel(message="删除成功")
