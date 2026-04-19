from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.exchange_config import ExchangeConfig
from app.schemas.performance import LiveMonitorData
from app.schemas.common import ResponseModel
from app.services.monitor_service import monitor_service

router = APIRouter()


async def _get_active_config(db: AsyncSession) -> ExchangeConfig:
    result = await db.execute(select(ExchangeConfig).where(ExchangeConfig.is_active == True))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=400, detail="未配置 OKX API，请先添加交易所配置")
    return config


@router.get("/live", response_model=ResponseModel[LiveMonitorData])
async def get_live_positions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """实时监控数据 - 持仓、盈亏、余额"""
    config = await _get_active_config(db)
    try:
        data = await monitor_service.get_live_monitor_data(config)
        return ResponseModel(data=data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"获取监控数据失败: {str(e)}")
