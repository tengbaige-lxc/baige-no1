"""Stable execution boundary shared by strategy orchestration code.

Strategy modules express position intent through these request objects. Exchange
schema details and close-side inversion stay behind the gateway.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from app.schemas.trading import OrderCreate


class OrderServicePort(Protocol):
    async def place_order(
        self,
        db: Any,
        user_id: int,
        account: Any,
        order: OrderCreate,
    ) -> Any: ...


NativeStopRefresher = Callable[[Any, str, str, dict], Awaitable[float]]


@dataclass(frozen=True, slots=True)
class OpenPositionRequest:
    symbol: str
    direction: str
    quantity: float
    market_type: str
    margin_mode: str
    leverage: int
    remark: str
    allow_min_size_bump: bool = False


@dataclass(frozen=True, slots=True)
class ReducePositionRequest:
    symbol: str
    direction: str
    quantity: float
    market_type: str
    margin_mode: str
    leverage: int
    remark: str
    pos_side: str | None = None


@dataclass(frozen=True, slots=True)
class NativeStopRequest:
    symbol: str
    direction: str
    params: dict


class TradingExecutionGateway:
    """Translate stable position intents into the existing exchange service."""

    def __init__(
        self,
        order_service: OrderServicePort,
        native_stop_refresher: NativeStopRefresher,
    ) -> None:
        self._order_service = order_service
        self._native_stop_refresher = native_stop_refresher

    async def open_position(
        self,
        db: Any,
        user_id: int,
        account: Any,
        request: OpenPositionRequest,
    ) -> Any:
        side = "BUY" if request.direction.upper() == "LONG" else "SELL"
        return await self._order_service.place_order(
            db,
            user_id,
            account,
            OrderCreate(
                symbol=request.symbol,
                side=side,
                order_type="MARKET",
                quantity=request.quantity,
                market_type=request.market_type,
                margin_mode=request.margin_mode,
                leverage=request.leverage,
                remark=request.remark,
                allow_min_size_bump=request.allow_min_size_bump,
            ),
        )

    async def reduce_position(
        self,
        db: Any,
        user_id: int,
        account: Any,
        request: ReducePositionRequest,
    ) -> Any:
        side = "SELL" if request.direction.upper() == "LONG" else "BUY"
        return await self._order_service.place_order(
            db,
            user_id,
            account,
            OrderCreate(
                symbol=request.symbol,
                side=side,
                order_type="MARKET",
                quantity=request.quantity,
                market_type=request.market_type,
                margin_mode=request.margin_mode,
                leverage=request.leverage,
                reduce_only=True,
                pos_side=request.pos_side,
                remark=request.remark,
            ),
        )

    async def refresh_native_stop(
        self,
        account: Any,
        request: NativeStopRequest,
    ) -> float:
        close_side = "SELL" if request.direction.upper() == "LONG" else "BUY"
        return await self._native_stop_refresher(
            account,
            request.symbol,
            close_side,
            request.params,
        )
