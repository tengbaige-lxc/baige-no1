from typing import Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.performance import PerformanceOverview
from app.schemas.common import ResponseModel
from app.services.analyzer_service import analyzer_service

router = APIRouter()


@router.get("/overview", response_model=ResponseModel[PerformanceOverview])
async def get_performance_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365),
) -> Any:
    """交易绩效概览 - 胜率、盈亏比、夏普、回撤等"""
    data = await analyzer_service.get_performance_overview(db, current_user.id, days)
    return ResponseModel(data=data)
