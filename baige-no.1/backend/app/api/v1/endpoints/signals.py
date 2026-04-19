from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.trading_signal import TradingSignal
from app.schemas.signal import SignalResponse, SignalScanRequest, SignalScanResult
from app.schemas.common import ResponseModel, PaginatedResponse
from app.services.signal_service import signal_service

router = APIRouter()


@router.get("", response_model=ResponseModel[PaginatedResponse[SignalResponse]])
async def list_signals(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    symbol: str = Query(None),
    signal_type: str = Query(None),
    is_active: bool = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    """获取信号记录列表"""
    query = select(TradingSignal).where(TradingSignal.user_id == current_user.id).order_by(desc(TradingSignal.created_at))
    count_query = select(func.count()).select_from(TradingSignal).where(TradingSignal.user_id == current_user.id)
    
    if symbol:
        query = query.where(TradingSignal.symbol == symbol.upper())
        count_query = count_query.where(TradingSignal.symbol == symbol.upper())
    if signal_type:
        query = query.where(TradingSignal.signal_type == signal_type)
        count_query = count_query.where(TradingSignal.signal_type == signal_type)
    if is_active is not None:
        query = query.where(TradingSignal.is_active == is_active)
        count_query = count_query.where(TradingSignal.is_active == is_active)
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    signals = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[SignalResponse.model_validate(s) for s in signals],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("/scan", response_model=ResponseModel[SignalScanResult])
async def scan_signals(
    req: SignalScanRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    """扫描指定币种的信号"""
    try:
        data = await signal_service.scan_all_signals(req.symbol.upper())
        return ResponseModel(data=data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"信号扫描失败: {str(e)}")


@router.post("/scan-batch", response_model=ResponseModel[List[SignalScanResult]])
async def scan_batch_signals(
    symbols: List[str],
    current_user: User = Depends(get_current_user),
) -> Any:
    """批量扫描多个币种信号"""
    try:
        results = []
        for symbol in symbols:
            data = await signal_service.scan_all_signals(symbol.upper())
            results.append(data)
        return ResponseModel(data=results)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"批量扫描失败: {str(e)}")
