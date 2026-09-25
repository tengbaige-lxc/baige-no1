import asyncio
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.exchange_config import ExchangeConfig
from app.schemas.trading import ExchangeConfigCreate, ExchangeConfigUpdate, ExchangeConfig as ExchangeConfigSchema
from app.schemas.common import ResponseModel
from app.services.okx_client import encrypt_text, decrypt_text, okx_manager

router = APIRouter()


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _build_account_summary(
    config: ExchangeConfig,
    balances: list[dict],
    positions: list[dict],
) -> dict:
    usdt = next((item for item in balances if item.get("ccy") == "USDT"), {})
    open_positions = [item for item in positions if _as_float(item.get("pos")) != 0]
    long_count = 0
    short_count = 0
    for position in open_positions:
        side = (position.get("posSide") or "net").lower()
        size = _as_float(position.get("pos"))
        if side == "long" or (side == "net" and size > 0):
            long_count += 1
        else:
            short_count += 1

    return {
        "id": config.id,
        "name": config.name,
        "exchange": config.exchange,
        "is_active": bool(config.is_active),
        "is_testnet": bool(config.is_testnet),
        "connection_status": "connected",
        "usdt_equity": round(_as_float(usdt.get("eq")), 4),
        "usdt_available": round(
            _as_float(usdt.get("availEq") or usdt.get("availBal")), 4
        ),
        "position_count": len(open_positions),
        "long_count": long_count,
        "short_count": short_count,
        "position_notional_usd": round(
            sum(abs(_as_float(item.get("notionalUsd"))) for item in open_positions), 2
        ),
        "position_margin_usd": round(
            sum(
                abs(_as_float(item.get("imr") or item.get("margin")))
                for item in open_positions
            ),
            2,
        ),
        "unrealized_pnl": round(
            sum(_as_float(item.get("upl")) for item in open_positions), 2
        ),
        "error": None,
    }


async def _load_account_summary(config: ExchangeConfig) -> dict:
    try:
        api_key = decrypt_text(config.api_key)
        api_secret = decrypt_text(config.api_secret)
        passphrase = decrypt_text(config.api_passphrase or "")
        balances, positions = await asyncio.gather(
            okx_manager.get_balance(
                api_key,
                api_secret,
                passphrase,
                simulated=bool(config.is_testnet),
            ),
            okx_manager.get_positions(
                api_key,
                api_secret,
                passphrase,
                simulated=bool(config.is_testnet),
            ),
        )
        return _build_account_summary(config, balances, positions)
    except Exception as exc:
        return {
            "id": config.id,
            "name": config.name,
            "exchange": config.exchange,
            "is_active": bool(config.is_active),
            "is_testnet": bool(config.is_testnet),
            "connection_status": "error",
            "usdt_equity": 0.0,
            "usdt_available": 0.0,
            "position_count": 0,
            "long_count": 0,
            "short_count": 0,
            "position_notional_usd": 0.0,
            "position_margin_usd": 0.0,
            "unrealized_pnl": 0.0,
            "error": str(exc),
        }


@router.get("", response_model=ResponseModel[list])
async def get_configs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(select(ExchangeConfig))
    configs = result.scalars().all()
    data = []
    for c in configs:
        data.append({
            "id": c.id,
            "name": c.name,
            "exchange": c.exchange,
            "is_testnet": c.is_testnet,
            "is_active": c.is_active,
            "created_at": c.created_at,
        })
    return ResponseModel(data=data)


@router.post("", response_model=ResponseModel)
async def create_config(
    config_in: ExchangeConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    # Phase 3e：密钥校验 fail-closed。此前 except: pass——无效密钥也返回"配置成功"，
    # 坏配置静默落库，直到引擎第一次真下单才暴露。原注释担心"零余额会失败"不成立：
    # OKX 余额接口对零余额正常返回，失败只发生在密钥无效或网络不通，两者都该拒绝保存。
    try:
        await okx_manager.get_balance(
            api_key=config_in.api_key,
            api_secret=config_in.api_secret,
            passphrase=config_in.api_passphrase or "", simulated=bool(config_in.is_testnet))
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"交易所密钥校验失败，配置未保存: {exc}",
        )


    db_config = ExchangeConfig(
        name=config_in.name,
        exchange="okx",
        api_key=encrypt_text(config_in.api_key),
        api_secret=encrypt_text(config_in.api_secret),
        api_passphrase=encrypt_text(config_in.api_passphrase) if config_in.api_passphrase else None,
        is_testnet=config_in.is_testnet,
        is_active=config_in.is_active,
    )
    db.add(db_config)
    await db.commit()
    await db.refresh(db_config)
    return ResponseModel(message="配置成功", data={"id": db_config.id})


@router.get("/accounts/summary", response_model=ResponseModel[list])
async def get_account_summaries(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(
        select(ExchangeConfig)
        .where(ExchangeConfig.exchange.ilike("okx"))
        .order_by(ExchangeConfig.id)
    )
    configs = list(result.scalars().all())
    summaries = await asyncio.gather(
        *(_load_account_summary(config) for config in configs)
    )
    return ResponseModel(data=list(summaries))


@router.put("/{config_id}", response_model=ResponseModel)
async def update_config(
    config_id: int,
    config_in: ExchangeConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(select(ExchangeConfig).where(ExchangeConfig.id == config_id))
    db_config = result.scalar_one_or_none()
    if not db_config:
        raise HTTPException(status_code=404, detail="配置不存在")
    
    for field, value in config_in.model_dump(exclude_unset=True).items():
        if field == "api_key" and value:
            value = encrypt_text(value)
        if field == "api_secret" and value:
            value = encrypt_text(value)
        if field == "api_passphrase" and value:
            value = encrypt_text(value)
        setattr(db_config, field, value)
    
    await db.commit()
    return ResponseModel(message="更新成功")


@router.delete("/{config_id}", response_model=ResponseModel)
async def delete_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    result = await db.execute(select(ExchangeConfig).where(ExchangeConfig.id == config_id))
    db_config = result.scalar_one_or_none()
    if not db_config:
        raise HTTPException(status_code=404, detail="配置不存在")
    await db.delete(db_config)
    await db.commit()
    return ResponseModel(message="删除成功")
