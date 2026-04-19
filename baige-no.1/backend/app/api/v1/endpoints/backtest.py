from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.backtest import BacktestRun, BacktestTrade, BacktestStatus
from app.schemas.backtest import BacktestCreate, BacktestResponse, BacktestDetailResponse, BacktestTradeResponse
from app.schemas.common import ResponseModel, PaginatedResponse
from app.services.backtest_service import backtest_service

router = APIRouter()


@router.get("", response_model=ResponseModel[PaginatedResponse[BacktestResponse]])
async def list_backtests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
) -> Any:
    """获取回测任务列表"""
    query = select(BacktestRun).where(BacktestRun.user_id == current_user.id).order_by(desc(BacktestRun.created_at))
    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    backtests = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[BacktestResponse.model_validate(b) for b in backtests],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("", response_model=ResponseModel[BacktestResponse])
async def create_backtest(
    backtest_in: BacktestCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """创建并启动回测任务"""
    db_backtest = BacktestRun(
        user_id=current_user.id,
        name=backtest_in.name,
        symbol=backtest_in.symbol.upper(),
        strategy_type=backtest_in.strategy_type,
        days=backtest_in.days,
        leverage=backtest_in.leverage,
        position_percent=backtest_in.position_percent,
        params=backtest_in.params,
        status=BacktestStatus.PENDING.value,
    )
    db.add(db_backtest)
    await db.commit()
    await db.refresh(db_backtest)
    
    # 在后台执行回测
    background_tasks.add_task(backtest_service.run_backtest, db, db_backtest.id)
    
    return ResponseModel(data=BacktestResponse.model_validate(db_backtest))


@router.get("/{backtest_id}", response_model=ResponseModel[BacktestDetailResponse])
async def get_backtest_detail(
    backtest_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """获取回测详情"""
    detail = await backtest_service.get_backtest_detail(db, backtest_id, current_user.id)
    if not detail:
        raise HTTPException(status_code=404, detail="回测任务不存在")
    
    return ResponseModel(data=BacktestDetailResponse(
        backtest=BacktestResponse.model_validate(detail["backtest"]),
        trades=[BacktestTradeResponse.model_validate(t) for t in detail["trades"]],
    ))


@router.delete("/{backtest_id}", response_model=ResponseModel)
async def delete_backtest(
    backtest_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """删除回测任务"""
    result = await db.execute(
        select(BacktestRun).where(
            and_(BacktestRun.id == backtest_id, BacktestRun.user_id == current_user.id)
        )
    )
    db_backtest = result.scalar_one_or_none()
    if not db_backtest:
        raise HTTPException(status_code=404, detail="回测任务不存在")
    
    await db.delete(db_backtest)
    await db.commit()
    return ResponseModel(message="删除成功")
