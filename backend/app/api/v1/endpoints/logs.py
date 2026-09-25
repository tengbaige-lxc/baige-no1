from typing import Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.api.deps import get_db, get_current_active_superuser
from app.models.log import OperationLog
from app.schemas.log import OperationLog as LogSchema
from app.schemas.common import ResponseModel, PaginatedResponse

router = APIRouter()


@router.get("", response_model=ResponseModel[PaginatedResponse[LogSchema]])
async def list_logs(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_active_superuser),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    action: str = Query(None),
    username: str = Query(None),
) -> Any:
    query = select(OperationLog).order_by(OperationLog.created_at.desc())
    count_query = select(func.count(OperationLog.id))
    
    filters = []
    if action:
        filters.append(OperationLog.action.ilike(f"%{action}%"))
    if username:
        filters.append(OperationLog.username.ilike(f"%{username}%"))
    
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    logs = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[LogSchema.model_validate(l) for l in logs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))
