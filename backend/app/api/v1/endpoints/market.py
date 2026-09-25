from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.exchange_config import ExchangeConfig
from app.schemas.common import ResponseModel
from app.services.market_service import market_service

router = APIRouter()


async def _get_active_config(db: AsyncSession) -> ExchangeConfig:
    result = await db.execute(
        select(ExchangeConfig)
        .where(ExchangeConfig.is_active == True, ExchangeConfig.exchange.ilike("okx"))
        .order_by(ExchangeConfig.updated_at.desc(), ExchangeConfig.id.desc())
        .limit(1)
    )
    config = result.scalar_one_or_none()
    return config


@router.get("/ticker/{symbol}", response_model=ResponseModel[dict])
async def get_ticker(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    config = await _get_active_config(db)
    data = await market_service.get_ticker(symbol.upper())
    return ResponseModel(data=data)


@router.get("/klines/{symbol}", response_model=ResponseModel[list])
async def get_klines(
    symbol: str,
    interval: str = "1h",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    config = await _get_active_config(db)
    try:
        data = await market_service.get_klines(symbol.upper(), interval, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ResponseModel(data=data)


@router.get("/balance", response_model=ResponseModel[list])
async def get_balance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    config = await _get_active_config(db)
    if not config:
        raise HTTPException(status_code=400, detail="未配置交易所 API，无法查询账户余额")
    data = await market_service.get_account_balance(config)
    return ResponseModel(data=data)


@router.get("/orderbook/{symbol}", response_model=ResponseModel[dict])
async def get_orderbook(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    config = await _get_active_config(db)
    data = await market_service.get_orderbook(symbol.upper())
    return ResponseModel(data=data)


@router.get("/instruments", response_model=ResponseModel[dict])
async def get_instruments(
    inst_type: str = Query("SWAP"),
    quote_ccy: str = Query("USDT"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    try:
        items = await market_service.get_instruments(inst_type, quote_ccy)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"获取 OKX 币种列表失败: {str(exc)}") from exc
    return ResponseModel(data={"items": items, "total": len(items)})
