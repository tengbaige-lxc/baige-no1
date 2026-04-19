from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.trading_strategy import TradingStrategy
from app.models.strategy_log import StrategyLog
from app.schemas.trading import StrategyCreate, StrategyUpdate, StrategyResponse, StrategyLogResponse
from app.schemas.common import ResponseModel, PaginatedResponse

router = APIRouter()


@router.get("", response_model=ResponseModel[PaginatedResponse[StrategyResponse]])
async def list_strategies(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    query = select(TradingStrategy).where(TradingStrategy.user_id == current_user.id).order_by(desc(TradingStrategy.created_at))
    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    strategies = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[StrategyResponse.model_validate(s) for s in strategies],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("", response_model=ResponseModel[StrategyResponse])
async def create_strategy(
    strategy_in: StrategyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    db_strategy = TradingStrategy(
        user_id=current_user.id,
        name=strategy_in.name,
        strategy_type=strategy_in.strategy_type,
        symbol=strategy_in.symbol.upper(),
        market_type=strategy_in.market_type,
        side=strategy_in.side,
        params=strategy_in.params,
        interval_seconds=strategy_in.interval_seconds,
        remark=strategy_in.remark,
    )
    db.add(db_strategy)
    await db.commit()
    await db.refresh(db_strategy)
    return ResponseModel(data=StrategyResponse.model_validate(db_strategy))


@router.put("/{strategy_id}", response_model=ResponseModel[StrategyResponse])
async def update_strategy(
    strategy_id: int,
    strategy_in: StrategyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(
        select(TradingStrategy).where(
            and_(TradingStrategy.id == strategy_id, TradingStrategy.user_id == current_user.id)
        )
    )
    db_strategy = result.scalar_one_or_none()
    if not db_strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    
    for field, value in strategy_in.model_dump(exclude_unset=True).items():
        setattr(db_strategy, field, value)
    
    await db.commit()
    await db.refresh(db_strategy)
    return ResponseModel(data=StrategyResponse.model_validate(db_strategy))


@router.delete("/{strategy_id}", response_model=ResponseModel)
async def delete_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(
        select(TradingStrategy).where(
            and_(TradingStrategy.id == strategy_id, TradingStrategy.user_id == current_user.id)
        )
    )
    db_strategy = result.scalar_one_or_none()
    if not db_strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    
    await db.delete(db_strategy)
    await db.commit()
    return ResponseModel(message="删除成功")


@router.get("/{strategy_id}/logs", response_model=ResponseModel[PaginatedResponse[StrategyLogResponse]])
async def get_strategy_logs(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    query = select(StrategyLog).where(StrategyLog.strategy_id == strategy_id).order_by(desc(StrategyLog.created_at))
    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    logs = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[StrategyLogResponse.model_validate(l) for l in logs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))
