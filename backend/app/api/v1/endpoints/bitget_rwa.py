from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.common import ResponseModel
from app.services.bitget_wallet_rwa import bitget_wallet_rwa_service


router = APIRouter()


class RwaQuoteRequest(BaseModel):
    chainId: str | None = Field(default=None)
    fromContract: str | None = Field(default=None)
    toContract: str | None = Field(default=None)
    fromAmount: str | None = Field(default=None)
    fromAddress: str | None = Field(default=None)
    slippage: str | None = Field(default="0.005")


class RwaConfigRequest(BaseModel):
    base_url: str | None = Field(default="https://web3.bitget.com")
    api_key: str | None = None
    api_secret: str | None = None
    passphrase: str | None = None
    wallet_address: str | None = None
    dry_run: bool = True
    allow_live_trading: bool = False
    single_order_usd_limit: float = 10
    daily_order_usd_limit: float = 50


@router.get("/status", response_model=ResponseModel[dict])
async def get_status(current_user: User = Depends(get_current_user)) -> Any:
    return ResponseModel(data=bitget_wallet_rwa_service.status())


@router.post("/config", response_model=ResponseModel[dict])
async def save_config(
    payload: RwaConfigRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    data = bitget_wallet_rwa_service.save_config(payload.model_dump())
    return ResponseModel(data=data, message="Bitget RWA config saved")


@router.get("/assets", response_model=ResponseModel[list])
async def get_assets(current_user: User = Depends(get_current_user)) -> Any:
    return ResponseModel(data=bitget_wallet_rwa_service.assets())


@router.post("/quote", response_model=ResponseModel[dict])
async def get_quote(
    payload: RwaQuoteRequest,
    current_user: User = Depends(get_current_user),
) -> Any:
    params = payload.model_dump(exclude_none=True)
    try:
        data = bitget_wallet_rwa_service.quote(params)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bitget RWA quote failed: {exc}") from exc
    return ResponseModel(data=data)
