from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc, func

from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.models.risk_config import RiskConfig
from app.schemas.performance import RiskConfigCreate, RiskConfigUpdate, RiskConfigResponse
from app.schemas.common import ResponseModel, PaginatedResponse

router = APIRouter()


@router.get("", response_model=ResponseModel[PaginatedResponse[RiskConfigResponse]])
async def list_risk_configs(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
) -> Any:
    """获取风控配置列表"""
    query = select(RiskConfig).where(RiskConfig.user_id == current_user.id).order_by(desc(RiskConfig.created_at))
    total_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = total_result.scalar()
    
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    configs = result.scalars().all()
    
    return ResponseModel(data=PaginatedResponse(
        items=[RiskConfigResponse.model_validate(c) for c in configs],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    ))


@router.post("", response_model=ResponseModel[RiskConfigResponse])
async def create_risk_config(
    config_in: RiskConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """创建风控配置"""
    # 如果设置为激活，先取消其他激活配置
    if config_in.symbols is None:
        config_in.symbols = [
            'BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP',
            'DOGE-USDT-SWAP', 'XRP-USDT-SWAP',
        ]
    
    if not config_in.profit_tiers:
        config_in.profit_tiers = [
            {"profit": 0.08, "reduce": 0.25},
            {"profit": 0.15, "reduce": 0.25},
            {"profit": 0.25, "reduce": 1.00},
        ]
    
    db_config = RiskConfig(
        user_id=current_user.id,
        name=config_in.name,
        leverage=config_in.leverage,
        position_percent=config_in.position_percent,
        max_daily_loss_percent=config_in.max_daily_loss_percent,
        max_positions=config_in.max_positions,
        activation_percent=config_in.activation_percent,
        callback_ratio=config_in.callback_ratio,
        profit_tiers=config_in.profit_tiers,
        batch_sizes=config_in.batch_sizes,
        time_stop_enabled=config_in.time_stop_enabled,
        max_hold_hours=config_in.max_hold_hours,
        time_stop_reduce_ratio=config_in.time_stop_reduce_ratio,
        atr_stop_enabled=config_in.atr_stop_enabled,
        atr_period=config_in.atr_period,
        atr_multiplier=config_in.atr_multiplier,
        max_trades_per_day=config_in.max_trades_per_day,
        cooldown_after_loss=config_in.cooldown_after_loss,
        correlation_control_enabled=config_in.correlation_control_enabled,
        max_same_direction=config_in.max_same_direction,
        symbols=config_in.symbols,
    )
    db.add(db_config)
    await db.commit()
    await db.refresh(db_config)
    return ResponseModel(data=RiskConfigResponse.model_validate(db_config))


@router.put("/{config_id}", response_model=ResponseModel[RiskConfigResponse])
async def update_risk_config(
    config_id: int,
    config_in: RiskConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """更新风控配置"""
    result = await db.execute(
        select(RiskConfig).where(
            and_(RiskConfig.id == config_id, RiskConfig.user_id == current_user.id)
        )
    )
    db_config = result.scalar_one_or_none()
    if not db_config:
        raise HTTPException(status_code=404, detail="风控配置不存在")
    
    # 如果激活此配置，取消其他
    update_data = config_in.model_dump(exclude_unset=True)
    if update_data.get("is_active"):
        await db.execute(
            select(RiskConfig).where(
                and_(RiskConfig.user_id == current_user.id, RiskConfig.id != config_id)
            )
        )
        # 简单处理：不自动切换，让用户自己管理
    
    for field, value in update_data.items():
        setattr(db_config, field, value)
    
    await db.commit()
    await db.refresh(db_config)
    return ResponseModel(data=RiskConfigResponse.model_validate(db_config))


@router.delete("/{config_id}", response_model=ResponseModel)
async def delete_risk_config(
    config_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """删除风控配置"""
    result = await db.execute(
        select(RiskConfig).where(
            and_(RiskConfig.id == config_id, RiskConfig.user_id == current_user.id)
        )
    )
    db_config = result.scalar_one_or_none()
    if not db_config:
        raise HTTPException(status_code=404, detail="风控配置不存在")
    
    await db.delete(db_config)
    await db.commit()
    return ResponseModel(message="删除成功")
