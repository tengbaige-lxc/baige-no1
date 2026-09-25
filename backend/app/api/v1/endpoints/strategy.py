from copy import deepcopy
from typing import Any
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.trading_strategy import TradingStrategy
from app.models.strategy_log import StrategyLog
from app.models.strategy_version import StrategyVersion
from app.schemas.trading import (
    StrategyCreate,
    StrategyUpdate,
    StrategyResponse,
    StrategyLogResponse,
    StrategyVersionPublishRequest,
    StrategyVersionRollbackRequest,
    StrategyVersionResponse,
)
from app.schemas.common import ResponseModel, PaginatedResponse
from app.services.strategy_engine import strategy_engine
from app.services.okx_client import okx_manager
from app.services.backtest_service import backtest_service

router = APIRouter()


def _normalize_symbol_for_market(symbol: str, market_type: str = "SWAP") -> str:
    normalized = (symbol or "").strip().upper()
    if not normalized:
        return normalized

    target_market = (market_type or "").upper()
    if target_market == "FUTURES":
        return strategy_engine._normalize_strategy_symbol_for_market(normalized, "FUTURES")

    if target_market == "SWAP":
        return strategy_engine._normalize_strategy_symbol(normalized)

    if "-" in normalized:
        return normalized
    if normalized.endswith("USDT"):
        return f"{normalized[:-4]}-USDT"
    return f"{normalized}-USDT"


def _normalize_strategy_payload(params: dict | None, market_type: str) -> dict:
    normalized_params = deepcopy(params or {})
    symbols = normalized_params.get("symbols")
    if isinstance(symbols, list):
        seen = set()
        normalized_symbols = []
        for item in symbols:
            normalized_symbol = _normalize_symbol_for_market(str(item), market_type)
            if normalized_symbol and normalized_symbol not in seen:
                seen.add(normalized_symbol)
                normalized_symbols.append(normalized_symbol)
        normalized_params["symbols"] = normalized_symbols
    return normalized_params


def _strategy_snapshot(strategy: TradingStrategy) -> dict:
    return {
        "name": strategy.name,
        "strategy_type": strategy.strategy_type,
        "symbol": strategy.symbol,
        "market_type": strategy.market_type,
        "side": strategy.side,
        "params": strategy.params or {},
        "interval_seconds": strategy.interval_seconds,
        "remark": strategy.remark,
        "is_active": strategy.is_active,
    }


async def _next_version(db: AsyncSession, strategy_id: int) -> int:
    result = await db.execute(
        select(func.max(StrategyVersion.version)).where(StrategyVersion.strategy_id == strategy_id)
    )
    max_version = result.scalar()
    return int(max_version or 0) + 1


def _as_aware_utc(value):
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _build_strategy_health(db: AsyncSession, strategy: TradingStrategy) -> dict:
    now = datetime.now(timezone.utc)
    last_run_at = _as_aware_utc(strategy.last_run_at)
    next_run_at = None
    seconds_until_next_run = None
    seconds_since_last_run = None

    if last_run_at:
        seconds_since_last_run = max(0, int((now - last_run_at).total_seconds()))
        next_run_at = last_run_at + timedelta(seconds=strategy.interval_seconds or 0)
        seconds_until_next_run = max(0, int((next_run_at - now).total_seconds()))

    latest_log_result = await db.execute(
        select(StrategyLog)
        .where(StrategyLog.strategy_id == strategy.id)
        .order_by(desc(StrategyLog.created_at), desc(StrategyLog.id))
        .limit(1)
    )
    latest_log = latest_log_result.scalar_one_or_none()

    latest_error_result = await db.execute(
        select(StrategyLog)
        .where(
            StrategyLog.strategy_id == strategy.id,
            StrategyLog.reason.like("%异常%"),
        )
        .order_by(desc(StrategyLog.created_at), desc(StrategyLog.id))
        .limit(1)
    )
    latest_error = latest_error_result.scalar_one_or_none()

    latest_reason = latest_log.reason if latest_log else ""
    if not strategy.is_active:
        status = "paused"
        text = "已暂停"
    elif not last_run_at:
        status = "pending"
        text = "等待首次运行"
    elif "异常" in (latest_reason or ""):
        status = "error"
        text = "最近异常"
    elif seconds_since_last_run is not None and seconds_since_last_run > max((strategy.interval_seconds or 60) * 3, 300):
        status = "stale"
        text = "运行延迟"
    else:
        status = "running"
        text = "正常运行"

    return {
        "status": status,
        "text": text,
        "last_run_at": strategy.last_run_at,
        "next_run_at": next_run_at,
        "seconds_since_last_run": seconds_since_last_run,
        "seconds_until_next_run": seconds_until_next_run,
        "latest_log": {
            "id": latest_log.id,
            "symbol": latest_log.symbol,
            "signal": latest_log.signal,
            "reason": latest_log.reason,
            "created_at": latest_log.created_at,
        } if latest_log else None,
        "latest_error": {
            "id": latest_error.id,
            "symbol": latest_error.symbol,
            "reason": latest_error.reason,
            "created_at": latest_error.created_at,
        } if latest_error else None,
    }


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
    items = []
    for strategy in strategies:
        item = StrategyResponse.model_validate(strategy)
        item.health = await _build_strategy_health(db, strategy)
        items.append(item)
    
    return ResponseModel(data=PaginatedResponse(
        items=items,
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
        symbol=_normalize_symbol_for_market(strategy_in.symbol, strategy_in.market_type),
        market_type=strategy_in.market_type,
        side=strategy_in.side,
        params=_normalize_strategy_payload(strategy_in.params, strategy_in.market_type),
        is_active=strategy_in.is_active,
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
    
    update_data = strategy_in.model_dump(exclude_unset=True)
    target_market_type = update_data.get("market_type", db_strategy.market_type)
    if "symbol" in update_data or "market_type" in update_data:
        update_data["symbol"] = _normalize_symbol_for_market(
            update_data.get("symbol", db_strategy.symbol),
            target_market_type,
        )
    if "params" in update_data or "market_type" in update_data:
        update_data["params"] = _normalize_strategy_payload(
            update_data.get("params", db_strategy.params),
            target_market_type,
        )

    for field, value in update_data.items():
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
    page_size: int = Query(10, ge=1, le=500),
    signal: str = Query(None),
    symbol: str = Query(None),
) -> Any:
    if strategy_id == 0:
        # 查询当前用户所有策略的日志
        user_strategy_ids = await db.execute(
            select(TradingStrategy.id).where(TradingStrategy.user_id == current_user.id)
        )
        ids = user_strategy_ids.scalars().all()
        query = (
            select(StrategyLog)
            .where(StrategyLog.strategy_id.in_(ids))
            .order_by(desc(StrategyLog.created_at), desc(StrategyLog.id))
        )
    else:
        strategy_result = await db.execute(
            select(TradingStrategy.id).where(
                and_(
                    TradingStrategy.id == strategy_id,
                    TradingStrategy.user_id == current_user.id,
                )
            )
        )
        if not strategy_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="策略不存在")
        query = (
            select(StrategyLog)
            .where(StrategyLog.strategy_id == strategy_id)
            .order_by(desc(StrategyLog.created_at), desc(StrategyLog.id))
        )
    if signal:
        query = query.where(StrategyLog.signal == signal.upper())
    if symbol:
        query = query.where(StrategyLog.symbol == symbol.upper().strip())
    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    logs = result.scalars().all()
    strategy_names = {}
    if logs:
        name_result = await db.execute(
            select(TradingStrategy.id, TradingStrategy.name).where(
                TradingStrategy.id.in_({log.strategy_id for log in logs})
            )
        )
        strategy_names = {row.id: row.name for row in name_result.all()}
    
    return ResponseModel(data=PaginatedResponse(
        items=[
            StrategyLogResponse.model_validate(l).model_copy(
                update={"strategy_name": strategy_names.get(l.strategy_id)}
            )
            for l in logs
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.get("/{strategy_id}/live-factors")
async def get_strategy_live_factors(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """获取多因子策略的实时因子信号"""
    result = await db.execute(
        select(TradingStrategy).where(
            and_(TradingStrategy.id == strategy_id, TradingStrategy.user_id == current_user.id)
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    
    symbol = _normalize_symbol_for_market(strategy.symbol, strategy.market_type)
    
    try:
        ticker = await okx_manager.get_ticker(symbol)
        current_price = float(ticker.get("last", 0))
        scan_result = await strategy_engine.scan_factors(strategy, current_price, symbol)
        return ResponseModel(data=scan_result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"信号扫描失败: {str(e)}")


@router.post("/{strategy_id}/optimize")
async def optimize_strategy_weights(
    strategy_id: int,
    days: int = Query(30, ge=7, le=90),
    metric: str = Query("total_return", enum=["total_return", "win_rate", "profit_factor"]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """自动优化多因子策略权重"""
    result = await db.execute(
        select(TradingStrategy).where(
            and_(TradingStrategy.id == strategy_id, TradingStrategy.user_id == current_user.id)
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")
    
    if strategy.strategy_type not in ("multi_factor", "white_dove"):
        raise HTTPException(status_code=400, detail="仅支持多因子策略的权重优化")
    
    symbol = _normalize_symbol_for_market(strategy.symbol, strategy.market_type)
    
    try:
        optimize_result = await backtest_service.optimize_weights(
            symbol=symbol,
            strategy_type=strategy.strategy_type,
            days=days,
            leverage=20,
            position_percent=0.2,
            params=strategy.params or {},
            metric=metric,
        )
        return ResponseModel(data=optimize_result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"权重优化失败: {str(e)}")


@router.get("/{strategy_id}/versions", response_model=ResponseModel[PaginatedResponse[StrategyVersionResponse]])
async def list_strategy_versions(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    strategy_result = await db.execute(
        select(TradingStrategy).where(
            and_(TradingStrategy.id == strategy_id, TradingStrategy.user_id == current_user.id)
        )
    )
    strategy = strategy_result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    query = select(StrategyVersion).where(StrategyVersion.strategy_id == strategy_id).order_by(desc(StrategyVersion.version))
    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    versions = result.scalars().all()

    return ResponseModel(data=PaginatedResponse(
        items=[StrategyVersionResponse.model_validate(v) for v in versions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("/{strategy_id}/publish", response_model=ResponseModel[StrategyVersionResponse])
async def publish_strategy_version(
    strategy_id: int,
    payload: StrategyVersionPublishRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    strategy_result = await db.execute(
        select(TradingStrategy).where(
            and_(TradingStrategy.id == strategy_id, TradingStrategy.user_id == current_user.id)
        )
    )
    strategy = strategy_result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    old_active_result = await db.execute(
        select(StrategyVersion).where(
            and_(StrategyVersion.strategy_id == strategy_id, StrategyVersion.is_active == True)
        )
    )
    old_active = old_active_result.scalars().all()
    for item in old_active:
        item.is_active = False

    db_version = StrategyVersion(
        strategy_id=strategy.id,
        user_id=current_user.id,
        version=await _next_version(db, strategy.id),
        snapshot=_strategy_snapshot(strategy),
        note=payload.note or "手动发布",
        is_active=True,
    )
    db.add(db_version)
    await db.commit()
    await db.refresh(db_version)
    return ResponseModel(data=StrategyVersionResponse.model_validate(db_version))


@router.post("/{strategy_id}/rollback", response_model=ResponseModel[StrategyVersionResponse])
async def rollback_strategy_version(
    strategy_id: int,
    payload: StrategyVersionRollbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    strategy_result = await db.execute(
        select(TradingStrategy).where(
            and_(TradingStrategy.id == strategy_id, TradingStrategy.user_id == current_user.id)
        )
    )
    strategy = strategy_result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    target = None
    if payload.target_version_id:
        target_result = await db.execute(
            select(StrategyVersion).where(
                and_(
                    StrategyVersion.id == payload.target_version_id,
                    StrategyVersion.strategy_id == strategy_id,
                    StrategyVersion.user_id == current_user.id,
                )
            )
        )
        target = target_result.scalar_one_or_none()
    else:
        active_result = await db.execute(
            select(StrategyVersion).where(
                and_(StrategyVersion.strategy_id == strategy_id, StrategyVersion.is_active == True)
            )
        )
        active = active_result.scalar_one_or_none()
        if active:
            target_result = await db.execute(
                select(StrategyVersion).where(
                    and_(StrategyVersion.strategy_id == strategy_id, StrategyVersion.version < active.version)
                ).order_by(desc(StrategyVersion.version)).limit(1)
            )
            target = target_result.scalar_one_or_none()
        if target is None:
            target_result = await db.execute(
                select(StrategyVersion).where(StrategyVersion.strategy_id == strategy_id).order_by(desc(StrategyVersion.version)).limit(1)
            )
            target = target_result.scalar_one_or_none()

    if not target:
        raise HTTPException(status_code=400, detail="没有可回滚的历史版本")

    snapshot = target.snapshot or {}
    strategy.name = snapshot.get("name", strategy.name)
    strategy.strategy_type = snapshot.get("strategy_type", strategy.strategy_type)
    strategy.symbol = snapshot.get("symbol", strategy.symbol)
    strategy.market_type = snapshot.get("market_type", strategy.market_type)
    strategy.side = snapshot.get("side", strategy.side)
    strategy.params = snapshot.get("params", strategy.params)
    strategy.interval_seconds = snapshot.get("interval_seconds", strategy.interval_seconds)
    strategy.remark = snapshot.get("remark", strategy.remark)
    strategy.is_active = snapshot.get("is_active", strategy.is_active)

    old_active_result = await db.execute(
        select(StrategyVersion).where(
            and_(StrategyVersion.strategy_id == strategy_id, StrategyVersion.is_active == True)
        )
    )
    old_active = old_active_result.scalars().all()
    for item in old_active:
        item.is_active = False

    new_version = StrategyVersion(
        strategy_id=strategy.id,
        user_id=current_user.id,
        version=await _next_version(db, strategy.id),
        snapshot=snapshot,
        note=payload.note or f"回滚到v{target.version}",
        source_version_id=target.id,
        is_active=True,
    )
    db.add(new_version)
    await db.commit()
    await db.refresh(new_version)
    return ResponseModel(data=StrategyVersionResponse.model_validate(new_version))
