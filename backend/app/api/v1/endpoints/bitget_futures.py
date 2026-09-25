from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.common import ResponseModel
from app.services.bitget_futures import bitget_futures_service


router = APIRouter()


class FuturesConfigRequest(BaseModel):
    base_url: str | None = Field(default="https://api.bitget.com")
    api_key: str | None = None
    api_secret: str | None = None
    passphrase: str | None = None
    dry_run: bool = True
    allow_live_trading: bool = False
    single_order_usdt_limit: float = 10
    daily_order_usdt_limit: float = 50
    max_open_symbols: int = 2
    default_leverage: int = 5
    margin_mode: str = "isolated"
    product_type: str = "USDT-FUTURES"
    margin_coin: str = "USDT"


class FuturesOrderRequest(BaseModel):
    symbol: str = "BTCUSDT"
    productType: str | None = "USDT-FUTURES"
    marginMode: str | None = "isolated"
    marginCoin: str | None = "USDT"
    size: str
    notional_usdt: float | None = 0
    leverage: int | None = 5
    side: str = "buy"
    tradeSide: str = "open"
    orderType: str = "market"
    price: str | None = None
    force: str | None = "gtc"
    clientOid: str | None = None
    reduceOnly: str | None = None
    presetStopLossPrice: str | None = None
    presetStopSurplusPrice: str | None = None


class FuturesLeverageRequest(BaseModel):
    symbol: str = "BTCUSDT"
    productType: str | None = "USDT-FUTURES"
    marginCoin: str | None = "USDT"
    leverage: int = 5
    holdSide: str | None = None


@router.get("/status", response_model=ResponseModel[dict])
async def get_status(current_user: User = Depends(get_current_user)) -> Any:
    return ResponseModel(data=bitget_futures_service.status())


@router.post("/config", response_model=ResponseModel[dict])
async def save_config(
    payload: FuturesConfigRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    data = bitget_futures_service.save_config(payload.model_dump())
    return ResponseModel(data=data, message="Bitget 合约配置已保存")


@router.get("/symbols", response_model=ResponseModel[list])
async def get_symbols(current_user: User = Depends(get_current_user)) -> Any:
    return ResponseModel(data=bitget_futures_service.symbols())


@router.post("/order-preview", response_model=ResponseModel[dict])
async def preview_order(
    payload: FuturesOrderRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    try:
        data = bitget_futures_service.preview_order(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bitget 合约预览失败: {exc}") from exc
    return ResponseModel(data=data)


@router.post("/place-order", response_model=ResponseModel[dict])
async def place_order(
    payload: FuturesOrderRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    try:
        data = bitget_futures_service.place_order(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bitget 合约下单失败: {exc}") from exc
    return ResponseModel(data=data)


@router.post("/set-leverage", response_model=ResponseModel[dict])
async def set_leverage(
    payload: FuturesLeverageRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    try:
        data = bitget_futures_service.set_leverage(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bitget 杠杆设置失败: {exc}") from exc
    return ResponseModel(data=data)
