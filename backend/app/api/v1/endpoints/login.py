from datetime import timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_db, get_current_user
from app.core.config import settings
from app.core.security import verify_password, create_access_token
from app.models.user import User
from app.schemas.token import Token
from app.schemas.user import ProfileUpdate, UserProfile
from app.schemas.common import ResponseModel

router = APIRouter()


@router.post("/login/access-token", response_model=ResponseModel[Token])
async def login_access_token(
    db: AsyncSession = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> Any:
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名或密码错误")
    
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户已被禁用")
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username},
        expires_delta=access_token_expires,
    )
    
    return ResponseModel(data=Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    ))


def _profile_of(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        avatar=user.avatar,
        phone=user.phone,
        role_name=user.role.name if user.role else None,
        permissions=["*"] if user.is_superuser else [],
    )


@router.get("/login/profile", response_model=ResponseModel[UserProfile])
async def get_profile(current_user: User = Depends(get_current_user)) -> Any:
    return ResponseModel(data=_profile_of(current_user))


@router.put("/login/profile", response_model=ResponseModel[UserProfile])
async def update_profile(
    profile_in: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """当前用户自助更新资料（Phase 3d/F1）。

    此前前端"个人中心"的保存按钮不调任何 API、直接弹"保存成功（演示）"——
    假成功提示。本端点是它的真实落点；只开放 full_name/email/phone 三个字段，
    改口令/角色/权限不在此（口令走 3d 后续，角色是超管的事）。
    """
    if profile_in.full_name is not None:
        current_user.full_name = profile_in.full_name
    if profile_in.email is not None:
        current_user.email = profile_in.email
    if profile_in.phone is not None:
        current_user.phone = profile_in.phone
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="邮箱已被其他账号使用")
    await db.refresh(current_user)
    return ResponseModel(data=_profile_of(current_user), message="资料已保存")
