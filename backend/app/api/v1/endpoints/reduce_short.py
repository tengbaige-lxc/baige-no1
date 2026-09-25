from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.reduce_record import ReduceRecord
from app.models.short_record import ShortRecord
from app.schemas.signal import ReduceRecordResponse, ShortRecordResponse
from app.schemas.common import ResponseModel, PaginatedResponse

router = APIRouter()


# ============== 减仓记录 ==============
@router.get("/reduce", response_model=ResponseModel[PaginatedResponse[ReduceRecordResponse]])
async def list_reduce_records(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    symbol: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    """获取减仓记录列表"""
    query = select(ReduceRecord).where(ReduceRecord.user_id == current_user.id).order_by(desc(ReduceRecord.created_at))
    count_query = select(func.count()).select_from(ReduceRecord).where(ReduceRecord.user_id == current_user.id)
    
    if symbol:
        query = query.where(ReduceRecord.symbol == symbol.upper())
        count_query = count_query.where(ReduceRecord.symbol == symbol.upper())
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    records = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[ReduceRecordResponse.model_validate(r) for r in records],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("/reduce", response_model=ResponseModel[ReduceRecordResponse])
async def create_reduce_record(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    symbol: str = Query(...),
    direction: str = Query("LONG"),
    entry_price: float = Query(...),
    original_size: float = Query(...),
) -> Any:
    """创建减仓记录"""
    db_record = ReduceRecord(
        user_id=current_user.id,
        symbol=symbol.upper(),
        direction=direction.upper(),
        entry_price=entry_price,
        original_size=original_size,
        remaining_size=original_size,
    )
    db.add(db_record)
    await db.commit()
    await db.refresh(db_record)
    return ResponseModel(data=ReduceRecordResponse.model_validate(db_record))


@router.put("/reduce/{record_id}", response_model=ResponseModel[ReduceRecordResponse])
async def update_reduce_record(
    record_id: int,
    reduced_size: float = Query(...),
    reduce_reason: str = Query(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """更新减仓记录（执行减仓）"""
    result = await db.execute(
        select(ReduceRecord).where(
            and_(ReduceRecord.id == record_id, ReduceRecord.user_id == current_user.id)
        )
    )
    db_record = result.scalar_one_or_none()
    if not db_record:
        raise HTTPException(status_code=404, detail="减仓记录不存在")
    
    db_record.reduced_count += 1
    db_record.reduced_size += reduced_size
    db_record.remaining_size -= reduced_size
    if reduce_reason:
        db_record.reduce_reason = reduce_reason
    
    await db.commit()
    await db.refresh(db_record)
    return ResponseModel(data=ReduceRecordResponse.model_validate(db_record))


# ============== 做空记录 ==============
@router.get("/short", response_model=ResponseModel[PaginatedResponse[ShortRecordResponse]])
async def list_short_records(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    symbol: str = Query(None),
    status: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    """获取做空记录列表"""
    query = select(ShortRecord).where(ShortRecord.user_id == current_user.id).order_by(desc(ShortRecord.created_at))
    count_query = select(func.count()).select_from(ShortRecord).where(ShortRecord.user_id == current_user.id)
    
    if symbol:
        query = query.where(ShortRecord.symbol == symbol.upper())
        count_query = count_query.where(ShortRecord.symbol == symbol.upper())
    if status:
        query = query.where(ShortRecord.status == status.upper())
        count_query = count_query.where(ShortRecord.status == status.upper())
    
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    records = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[ShortRecordResponse.model_validate(r) for r in records],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("/short", response_model=ResponseModel[ShortRecordResponse])
async def create_short_record(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    symbol: str = Query(...),
    entry_price: float = Query(...),
    original_size: float = Query(...),
) -> Any:
    """创建做空记录"""
    db_record = ShortRecord(
        user_id=current_user.id,
        symbol=symbol.upper(),
        entry_price=entry_price,
        original_size=original_size,
        remaining_size=original_size,
        status="OPEN",
    )
    db.add(db_record)
    await db.commit()
    await db.refresh(db_record)
    return ResponseModel(data=ShortRecordResponse.model_validate(db_record))


@router.put("/short/{record_id}", response_model=ResponseModel[ShortRecordResponse])
async def update_short_record(
    record_id: int,
    close_price: float = Query(None),
    pnl_usdt: float = Query(None),
    close_reason: str = Query(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """更新做空记录（平仓或减仓）"""
    result = await db.execute(
        select(ShortRecord).where(
            and_(ShortRecord.id == record_id, ShortRecord.user_id == current_user.id)
        )
    )
    db_record = result.scalar_one_or_none()
    if not db_record:
        raise HTTPException(status_code=404, detail="做空记录不存在")
    
    if close_price is not None:
        db_record.close_price = close_price
    if pnl_usdt is not None:
        db_record.pnl_usdt = pnl_usdt
    if close_reason:
        db_record.close_reason = close_reason
    
    if close_price and pnl_usdt is not None:
        db_record.status = "CLOSED"
    
    await db.commit()
    await db.refresh(db_record)
    return ResponseModel(data=ShortRecordResponse.model_validate(db_record))
