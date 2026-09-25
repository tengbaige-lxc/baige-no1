from datetime import timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.exchange_config import ExchangeConfig
from app.models.trading_order import TradingOrder
from app.models.trade_record import TradeRecord
from app.schemas.trading import OrderCreate, OrderResponse
from app.schemas.common import ResponseModel, PaginatedResponse
from app.services.trade_service import trade_service

router = APIRouter()


async def _get_active_config(db: AsyncSession) -> ExchangeConfig:
    result = await db.execute(
        select(ExchangeConfig).where(
            ExchangeConfig.is_active == True,
            ExchangeConfig.exchange.ilike("okx"),
        ).order_by(desc(ExchangeConfig.updated_at), desc(ExchangeConfig.id)).limit(1)
    )
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
        sym = symbol.upper()
        # 兼容 BTCUSDT / BTC-USDT-SWAP 两种格式
        if "-" not in sym:
            sym = sym.replace("USDT", "-USDT").replace("USD", "-USD")
            if not sym.endswith("-SWAP"):
                sym = sym + "-SWAP"
        query = query.where(TradingOrder.symbol == sym)
        count_query = count_query.where(TradingOrder.symbol == sym)
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


def _datetime_to_ms(value) -> int | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _strategy_tag_from_remark(remark: str | None) -> str | None:
    if not remark:
        return None
    text = remark.strip()
    if "策略自动下单:" in text:
        text = text.split("策略自动下单:", 1)[1].strip()
    if " (" in text:
        text = text.split(" (", 1)[0].strip()
    return text or None


def _marker_key(marker: dict) -> tuple:
    ts = marker.get("timestamp") or 0
    bucket = int(ts // 60000) if ts else marker.get("source_id")
    price = round(float(marker.get("price") or 0), 8)
    return (marker.get("symbol"), marker.get("event_type"), price, bucket)


@router.get("/markers", response_model=ResponseModel[list])
async def get_trade_markers(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    symbol: str = Query(None),
    limit: int = Query(200, ge=1, le=500),
    strategy_tag: str = Query(None),
) -> Any:
    """Return executed trade markers for K-line review."""
    symbol_filter = symbol.upper().strip() if symbol else None
    markers: list[dict] = []
    seen: set[tuple] = set()

    order_query = (
        select(TradingOrder)
        .where(
            TradingOrder.user_id == current_user.id,
            TradingOrder.executed_qty > 0,
        )
        .order_by(desc(TradingOrder.created_at), desc(TradingOrder.id))
        .limit(limit)
    )
    if symbol_filter:
        order_query = order_query.where(TradingOrder.symbol == symbol_filter)

    order_result = await db.execute(order_query)
    for order in order_result.scalars().all():
        side = (order.side or "").upper()
        if side not in {"BUY", "SELL"}:
            continue

        reduce_only = bool(order.reduce_only)
        if reduce_only:
            event_type = "CLOSE_SHORT" if side == "BUY" else "CLOSE_LONG"
            direction = "SHORT" if side == "BUY" else "LONG"
        else:
            event_type = "OPEN_LONG" if side == "BUY" else "OPEN_SHORT"
            direction = "LONG" if side == "BUY" else "SHORT"

        tag = _strategy_tag_from_remark(order.remark)
        if strategy_tag and (tag or "").lower() != strategy_tag.lower():
            continue

        marker = {
            "id": f"order-{order.id}",
            "source": "order",
            "source_id": order.id,
            "symbol": order.symbol,
            "event_type": event_type,
            "side": side,
            "direction": direction,
            "price": order.executed_price or order.price,
            "quantity": order.executed_qty or order.quantity,
            "strategy_tag": tag,
            "remark": order.remark,
            "time": order.created_at.isoformat() if order.created_at else None,
            "timestamp": _datetime_to_ms(order.created_at),
        }
        if not marker["price"]:
            continue
        key = _marker_key(marker)
        seen.add(key)
        markers.append(marker)

    record_query = (
        select(TradeRecord)
        .where(TradeRecord.user_id == current_user.id)
        .order_by(desc(TradeRecord.updated_at), desc(TradeRecord.id))
        .limit(limit)
    )
    if symbol_filter:
        record_query = record_query.where(TradeRecord.symbol == symbol_filter)
    if strategy_tag:
        record_query = record_query.where(TradeRecord.strategy_tag == strategy_tag)

    record_result = await db.execute(record_query)
    for record in record_result.scalars().all():
        direction = (record.direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            continue

        record_events = [
            {
                "event_type": "OPEN_LONG" if direction == "LONG" else "OPEN_SHORT",
                "side": "BUY" if direction == "LONG" else "SELL",
                "price": record.entry_price,
                "quantity": record.position_size,
                "time": record.created_at,
            }
        ]
        if record.is_closed and record.exit_price:
            record_events.append(
                {
                    "event_type": "CLOSE_LONG" if direction == "LONG" else "CLOSE_SHORT",
                    "side": "SELL" if direction == "LONG" else "BUY",
                    "price": record.exit_price,
                    "quantity": record.position_size,
                    "time": record.updated_at,
                }
            )

        for event in record_events:
            marker = {
                "id": f"record-{record.id}-{event['event_type'].lower()}",
                "source": "record",
                "source_id": record.id,
                "symbol": record.symbol,
                "event_type": event["event_type"],
                "side": event["side"],
                "direction": direction,
                "price": event["price"],
                "quantity": event["quantity"],
                "strategy_tag": record.strategy_tag,
                "remark": record.notes,
                "time": event["time"].isoformat() if event["time"] else None,
                "timestamp": _datetime_to_ms(event["time"]),
            }
            if not marker["price"]:
                continue
            key = _marker_key(marker)
            if key in seen:
                continue
            seen.add(key)
            markers.append(marker)

    markers.sort(key=lambda item: item.get("timestamp") or 0)
    return ResponseModel(data=markers[-limit:])


@router.post("/orders/sync", response_model=ResponseModel)
async def sync_orders(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """从 OKX 同步历史订单"""
    config = await _get_active_config(db)
    
    try:
        sync_result = await trade_service.sync_orders_from_okx(db, current_user.id, config)
        return ResponseModel(
            message=f"同步完成：导入 {sync_result['imported']} 条，更新 {sync_result['updated']} 条，跳过 {sync_result['skipped']} 条",
            data=sync_result
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"同步失败: {str(e)}")
