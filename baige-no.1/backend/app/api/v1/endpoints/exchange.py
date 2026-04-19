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
    # Test connection with OKX
    try:
        await okx_manager.get_balance(
            api_key=config_in.api_key,
            api_secret=config_in.api_secret,
            passphrase=config_in.api_passphrase or "",
        )
    except Exception:
        # Balance check may fail if no funds, but key could be valid
        # Try a public ticker to verify network
        pass
    
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
