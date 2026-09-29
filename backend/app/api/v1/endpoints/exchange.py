import asyncio
import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.exchange_config import ExchangeConfig
from app.schemas.trading import ExchangeConfigCreate, ExchangeConfigUpdate
from app.schemas.common import ResponseModel
from app.services.okx_client import decrypt_text, encrypt_text, okx_manager  # compatibility export
from app.services.exchange_accounts import (
    SUPPORTED_EXCHANGES,
    build_okx_account_summary,
    load_account_summary,
    normalize_exchange,
    validate_exchange_credentials,
)

router = APIRouter()


# Compatibility entry points used by the existing performance endpoint and
# backend regression tests. The implementation now lives in the exchange
# adapter layer, but callers do not need to change atomically with this deploy.
def _build_account_summary(
    config: ExchangeConfig,
    balances: list[dict],
    positions: list[dict],
) -> dict:
    return build_okx_account_summary(config, balances, positions)


async def _load_account_summary(config: ExchangeConfig) -> dict:
    return await load_account_summary(config)


DEFAULT_ACCOUNT_SUMMARY_SOURCES = [
    {
        "id": "baige-no4",
        "label": "白鸽四号",
        "url": "http://127.0.0.1:3044/status",
        "performance_url": "http://127.0.0.1:3044/account-performance",
    }
]


def _account_summary_sources() -> list[dict]:
    raw = os.getenv("ACCOUNT_SUMMARY_SOURCES", "").strip()
    if not raw:
        return DEFAULT_ACCOUNT_SUMMARY_SOURCES
    try:
        sources = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_ACCOUNT_SUMMARY_SOURCES
    return [
        source
        for source in sources
        if isinstance(source, dict) and source.get("url")
    ]


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _decorate_local_summary(summary: dict) -> dict:
    return {
        **summary,
        "source": os.getenv("ACCOUNT_LOCAL_SOURCE_ID", "baige-no3"),
        "source_label": os.getenv("ACCOUNT_LOCAL_SOURCE_LABEL", "白鸽三号"),
        "managed_here": True,
    }


def _build_remote_account_summary(source: dict, account: dict) -> dict:
    source_id = str(source.get("id") or "external")
    ready = bool(account.get("ready"))
    return {
        "id": f"{source_id}:{account.get('id', account.get('name', 'account'))}",
        "name": str(account.get("name") or "未命名账户"),
        "source": source_id,
        "source_label": str(source.get("label") or source_id),
        "managed_here": False,
        "exchange": str(account.get("exchange") or "okx").lower(),
        "is_active": ready,
        "is_testnet": False,
        "execution_supported": True,
        "connection_status": "connected" if account.get("checked_at") else "idle",
        "position_mode": str(account.get("position_mode") or "unknown"),
        "usdt_equity": round(_as_float(account.get("equity_usd")), 4),
        "usdt_available": round(_as_float(account.get("available_usd")), 4),
        "position_count": int(account.get("live_position_count") or 0),
        "long_count": int(account.get("long_count") or 0),
        "short_count": int(account.get("short_count") or 0),
        "position_notional_usd": round(
            _as_float(account.get("position_notional_usd")), 2
        ),
        "position_margin_usd": round(
            _as_float(account.get("position_margin_usd")), 2
        ),
        "unrealized_pnl": round(_as_float(account.get("unrealized_pnl_usd")), 2),
        "error": None,
    }


async def _load_remote_account_summaries(source: dict) -> list[dict]:
    try:
        timeout = httpx.Timeout(4.0, connect=1.5)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(str(source["url"]))
            response.raise_for_status()
            payload = response.json()
        accounts = payload.get("accounts") or []
        return [
            _build_remote_account_summary(source, account)
            for account in accounts
            if isinstance(account, dict)
        ]
    except Exception:
        # An unavailable strategy service must not break the local account page.
        return []


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
    # Older callers and regression fixtures predate the exchange selector;
    # preserve their established OKX behavior when the field is absent.
    exchange = normalize_exchange(getattr(config_in, "exchange", "okx"))
    try:
        await validate_exchange_credentials(
            exchange,
            api_key=config_in.api_key,
            api_secret=config_in.api_secret,
            api_passphrase=config_in.api_passphrase or "",
            is_testnet=bool(config_in.is_testnet),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"交易所密钥校验失败，配置未保存: {exc}",
        )

    db_config = ExchangeConfig(
        name=config_in.name,
        exchange=exchange,
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
        .where(func.lower(ExchangeConfig.exchange).in_(tuple(SUPPORTED_EXCHANGES)))
        .order_by(ExchangeConfig.id)
    )
    configs = list(result.scalars().all())
    local_summaries = await asyncio.gather(
        *(load_account_summary(config) for config in configs)
    )
    remote_groups = await asyncio.gather(
        *(
            _load_remote_account_summaries(source)
            for source in _account_summary_sources()
        )
    )
    summaries = [_decorate_local_summary(item) for item in local_summaries]
    summaries.extend(item for group in remote_groups for item in group)
    return ResponseModel(data=summaries)


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

    updates = config_in.model_dump(exclude_unset=True)
    for required_field in ("api_key", "api_secret"):
        if required_field in updates and not updates[required_field]:
            raise HTTPException(
                status_code=422,
                detail=f"{required_field} 不能为空",
            )
    credential_fields = {"api_key", "api_secret", "api_passphrase", "is_testnet"}
    if credential_fields.intersection(updates):
        api_key = updates.get("api_key") or decrypt_text(db_config.api_key)
        api_secret = updates.get("api_secret") or decrypt_text(db_config.api_secret)
        passphrase = (
            updates["api_passphrase"]
            if "api_passphrase" in updates
            else decrypt_text(db_config.api_passphrase or "")
        )
        is_testnet = updates.get("is_testnet", bool(db_config.is_testnet))
        try:
            await validate_exchange_credentials(
                db_config.exchange,
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=passphrase,
                is_testnet=bool(is_testnet),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"交易所密钥校验失败，配置未更新: {exc}",
            )

    for field, value in updates.items():
        if field == "api_key" and value:
            value = encrypt_text(value)
        if field == "api_secret" and value:
            value = encrypt_text(value)
        if field == "api_passphrase":
            value = encrypt_text(value) if value else None
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
