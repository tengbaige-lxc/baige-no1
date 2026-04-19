from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.exchange_config import ExchangeConfig
from app.models.trading_order import TradingOrder
from app.schemas.trading import OrderCreate, OrderResponse
from app.schemas.common import ResponseModel, PaginatedResponse
from app.services.trade_service import trade_service

router = APIRouter()


async def _get_active_config(db: AsyncSession) -> ExchangeConfig:
    result = await db.execute(select(ExchangeConfig).where(ExchangeConfig.is_active == True))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=400, detail="未配置 OKX API，请先添加交易所配置")
    return config


@router.post("/order", response_model=ResponseModel[OrderResponse])
async def place_order(
    order_in: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    config = await _get_active_config(db)
    try:
        db_order = await trade_service.place_order(db, current_user.id, config, order_in)
        return ResponseModel(data=OrderResponse.model_validate(db_order))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"下单失败: {str(e)}")


@router.delete("/order/{order_id}", response_model=ResponseModel)
async def cancel_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    config = await _get_active_config(db)
    try:
        result = await trade_service.cancel_order(db, current_user.id, config, order_id)
        return ResponseModel(data=result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"撤单失败: {str(e)}")


@router.get("/orders", response_model=ResponseModel[PaginatedResponse[OrderResponse]])
async def get_orders(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    symbol: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    query = select(TradingOrder).where(TradingOrder.user_id == current_user.id).order_by(desc(TradingOrder.created_at))
    count_query = select(TradingOrder).where(TradingOrder.user_id == current_user.id)
    
    if symbol:
        query = query.where(TradingOrder.symbol == symbol.upper())
        count_query = count_query.where(TradingOrder.symbol == symbol.upper())
    if status:
        query = query.where(TradingOrder.status == status)
        count_query = count_query.where(TradingOrder.status == status)
    
    from sqlalchemy import func
    total_result = await db.execute(select(func.count()).select_from(count_query.subquery()))
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    orders = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[OrderResponse.model_validate(o) for o in orders],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))
