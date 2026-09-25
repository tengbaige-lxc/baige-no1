from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func
from datetime import datetime, timezone, timedelta

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


def _map_signal_direction(raw_signal: str) -> str:
    """将原始信号方向映射为数据库枚举值"""
    mapping = {
        "LONG": "LONG",
        "SHORT": "SHORT",
        "BULLISH": "LONG_BIAS",
        "BEARISH": "SHORT_BIAS",
        "CAUTION": "CAUTION",
        "NEUTRAL": "NEUTRAL",
        "FILTER": "NEUTRAL",
    }
    return mapping.get(raw_signal, "NEUTRAL")


async def _save_scan_result(db: AsyncSession, user_id: int, symbol: str, result: dict):
    """将扫描结果保存到 trading_signals 表（按 signal_type 去重：1小时内同类型不重复保存）"""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    saved_count = 0

    for signal_type, data in [("liquidation", result.get("liquidation")),
                               ("divergence", result.get("divergence")),
                               ("macro", result.get("macro")),
                               ("news", result.get("news"))]:
        if not data:
            continue

        raw_signal = data.get("signal", "NEUTRAL")
        direction = _map_signal_direction(raw_signal)

        # 检查 1 小时内是否已有相同记录
        dup_check = await db.execute(
            select(TradingSignal).where(
                and_(
                    TradingSignal.user_id == user_id,
                    TradingSignal.symbol == symbol.upper(),
                    TradingSignal.signal_type == signal_type,
                    TradingSignal.direction == direction,
                    TradingSignal.created_at >= since,
                )
            )
        )
        if dup_check.scalar_one_or_none():
            continue

        ts = datetime.fromisoformat(result.get("timestamp", datetime.now(timezone.utc).isoformat()))

        db_signal = TradingSignal(
            user_id=user_id,
            symbol=symbol.upper(),
            signal_type=signal_type,
            direction=direction,
            strength=data.get("strength", 1),
            confidence=data.get("confidence", 50),
            current_price=data.get("current_price"),
            reason=data.get("reason"),
            is_active=True,
            triggered_at=ts,
            created_at=ts,
        )
        db.add(db_signal)
        saved_count += 1

    if saved_count:
        await db.commit()
    return saved_count


@router.post("/scan", response_model=ResponseModel[SignalScanResult])
async def scan_signals(
    req: SignalScanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """扫描指定币种的信号并保存到历史记录"""
    try:
        data = await signal_service.scan_all_signals(req.symbol.upper())
        saved = await _save_scan_result(db, current_user.id, req.symbol.upper(), data)
        return ResponseModel(data=data, message=f"扫描完成，新增 {saved} 条信号记录")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"信号扫描失败: {str(e)}")


# Phase 3e：批量扫描上限。每个 symbol 的扫描都是一串真实 OKX 请求，
# 此前裸 List[str] 无上限——一次请求塞几千个 symbol 就能占满引擎共享的限流预算。
MAX_SCAN_BATCH = 20


@router.post("/scan-batch", response_model=ResponseModel[List[SignalScanResult]])
async def scan_batch_signals(
    symbols: List[str],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """批量扫描多个币种信号并保存到历史记录（单次上限 MAX_SCAN_BATCH 个）"""
    if len(symbols) > MAX_SCAN_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"单次批量扫描最多 {MAX_SCAN_BATCH} 个币种（收到 {len(symbols)} 个），请分批",
        )
    try:
        results = []
        total_saved = 0
        for symbol in symbols:
            data = await signal_service.scan_all_signals(symbol.upper())
            saved = await _save_scan_result(db, current_user.id, symbol.upper(), data)
            total_saved += saved
            results.append(data)
        return ResponseModel(data=results, message=f"批量扫描完成，共新增 {total_saved} 条信号记录")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"批量扫描失败: {str(e)}")
