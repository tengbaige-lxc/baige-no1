from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta, timezone

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.message import Message
from app.models.log import OperationLog
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("/stats", response_model=ResponseModel[dict])
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    # User count
    user_result = await db.execute(select(func.count(User.id)).where(User.is_active == True))
    user_count = user_result.scalar()
    
    # Message count (today)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    msg_result = await db.execute(
        select(func.count(Message.id)).where(Message.created_at >= today)
    )
    msg_count = msg_result.scalar()
    
    # Log count (today)
    log_result = await db.execute(
        select(func.count(OperationLog.id)).where(OperationLog.created_at >= today)
    )
    log_count = log_result.scalar()
    
    # Online users (active in last 30 minutes - simplified)
    online_result = await db.execute(select(func.count(User.id)).where(User.is_active == True))
    online_count = online_result.scalar()
    
    return ResponseModel(data={
        "user_count": user_count,
        "message_count": msg_count,
        "log_count": log_count,
        "online_count": online_count,
    })


@router.get("/recent-logs", response_model=ResponseModel[list])
async def get_recent_logs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(
        select(OperationLog)
        .order_by(OperationLog.created_at.desc())
        .limit(10)
    )
    logs = result.scalars().all()
    
    return ResponseModel(data=[
        {
            "id": log.id,
            "action": log.action,
            "username": log.username,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ])
