from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from jose import JWTError

from app.db.base import get_db
from app.core.config import settings
from app.core.security import decode_token
from app.models.user import User
from app.schemas.token import TokenPayload

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/login/access-token")

# 前端 utils/auth.js 存 token 的 cookie 名（js-cookie，非 httpOnly——存储方式本身是
# 🟡 决策 #8 的议题，这里只是读取既有事实）
TOKEN_COOKIE_NAME = "baige_token"


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _resolve_token_user(db: AsyncSession, token: str) -> User:
    """token → User 的共享解析：签名/过期校验、用户存在、未禁用。"""
    credentials_exception = _credentials_exception()
    try:
        payload = decode_token(token)
        if payload is None:
            raise credentials_exception
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        token_data = TokenPayload(sub=int(user_id))
    except (JWTError, ValueError):
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == token_data.sub))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=400, detail="用户已被禁用")
    return user


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    return await _resolve_token_user(db, token)


async def get_current_user_from_header_or_cookie(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """头优先、cookie 兜底的鉴权（Phase 3c，专供 SSE 类端点）。

    浏览器 EventSource 无法设置 Authorization 头，它唯一能带的凭据是同源 cookie；
    且 frontend/dist 是已提交的陈旧构建物——cookie 兜底让 src 与 dist 都无需改动。
    普通端点请继续用 get_current_user（标准 Bearer 头），不要扩散 cookie 读取面。
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    else:
        token = request.cookies.get(TOKEN_COOKIE_NAME, "")
    if not token:
        raise _credentials_exception()
    return await _resolve_token_user(db, token)


async def get_current_active_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足",
        )
    return current_user
