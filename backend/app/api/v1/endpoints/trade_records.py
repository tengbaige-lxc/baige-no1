from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.trade_record import TradeRecord
from app.models.exchange_config import ExchangeConfig
from app.schemas.performance import TradeRecordCreate, TradeRecordUpdate, TradeRecordResponse
from app.schemas.common import ResponseModel, PaginatedResponse

router = APIRouter()


async def _get_active_okx_config(db: AsyncSession) -> ExchangeConfig:
    result = await db.execute(
        select(ExchangeConfig).where(
            ExchangeConfig.is_active == True,
            ExchangeConfig.exchange.ilike("okx"),
        ).order_by(desc(ExchangeConfig.updated_at), desc(ExchangeConfig.id)).limit(1)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=400, detail="未配置活跃的 OKX API，请先添加 OKX 交易所配置")
    return config


@router.get("", response_model=ResponseModel[PaginatedResponse[TradeRecordResponse]])
async def list_trade_records(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    symbol: str = Query(None),
    is_closed: bool = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    """获取交易记录列表"""
    from app.services.analyzer_service import analyzer_service
    records, total = await analyzer_service.get_trade_records(
        db, current_user.id, symbol=symbol, is_closed=is_closed, page=page, page_size=page_size
    )
    
    return ResponseModel(data=PaginatedResponse(
        items=[TradeRecordResponse.model_validate(r) for r in records],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("/sync", response_model=ResponseModel)
async def sync_trade_records(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """从 OKX 同步历史成交记录，并校准当前实时持仓"""
    from app.services.analyzer_service import analyzer_service

    config = await _get_active_okx_config(db)

    try:
        sync_result = await analyzer_service.sync_from_okx(db, current_user.id, config)
        message = (
            f"同步完成：导入 {sync_result['imported']} 条，"
            f"跳过 {sync_result['skipped']} 条"
        )
        if sync_result.get("invalid"):
            message += f"，无效 {sync_result['invalid']} 条"
        live_sync = sync_result.get("live_sync") or {}
        if live_sync:
            message += (
                f"，实时持仓创建 {live_sync.get('created', 0)} 条，"
                f"更新 {live_sync.get('updated', 0)} 条，"
                f"关闭失效 {live_sync.get('closed_stale', 0)} 条"
            )
        return ResponseModel(
            message=message,
            data=sync_result
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"同步失败: {str(e)}")


@router.post("", response_model=ResponseModel[TradeRecordResponse])
async def create_trade_record(
    record_in: TradeRecordCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """创建交易记录"""
    db_record = TradeRecord(
        user_id=current_user.id,
        symbol=record_in.symbol.upper(),
        direction=record_in.direction.upper(),
        entry_price=record_in.entry_price,
        exit_price=record_in.exit_price,
        position_size=record_in.position_size,
        pnl_usdt=record_in.pnl_usdt,
        leverage=record_in.leverage,
        strategy_tag=record_in.strategy_tag,
        notes=record_in.notes,
        is_closed=record_in.is_closed,
    )
    db.add(db_record)
    await db.commit()
    await db.refresh(db_record)
    return ResponseModel(data=TradeRecordResponse.model_validate(db_record))


@router.put("/{record_id}", response_model=ResponseModel[TradeRecordResponse])
async def update_trade_record(
    record_id: int,
    record_in: TradeRecordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """更新交易记录（平仓等）"""
    result = await db.execute(
        select(TradeRecord).where(
            and_(TradeRecord.id == record_id, TradeRecord.user_id == current_user.id)
        )
    )
    db_record = result.scalar_one_or_none()
    if not db_record:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    
    for field, value in record_in.model_dump(exclude_unset=True).items():
        setattr(db_record, field, value)
    
    await db.commit()
    await db.refresh(db_record)
    return ResponseModel(data=TradeRecordResponse.model_validate(db_record))


@router.delete("/{record_id}", response_model=ResponseModel)
async def delete_trade_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """删除交易记录"""
    result = await db.execute(
        select(TradeRecord).where(
            and_(TradeRecord.id == record_id, TradeRecord.user_id == current_user.id)
        )
    )
    db_record = result.scalar_one_or_none()
    if not db_record:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    
    await db.delete(db_record)
    await db.commit()
    return ResponseModel(message="删除成功")
