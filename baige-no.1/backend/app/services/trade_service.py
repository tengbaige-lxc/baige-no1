from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from app.services.okx_client import okx_manager, decrypt_text
from app.models.exchange_config import ExchangeConfig
from app.models.trading_order import TradingOrder, OrderStatus
from app.schemas.trading import OrderCreate


class TradeService:
    """Trading execution service for OKX."""
    
    def _symbol_to_okx(self, symbol: str, market_type: str) -> str:
        """Convert symbol to OKX format."""
        if "-" in symbol:
            base = symbol
        elif symbol.endswith("USDT"):
            base = symbol[:-4] + "-USDT"
        elif symbol.endswith("USD"):
            base = symbol[:-3] + "-USD"
        else:
            base = symbol
        
        if market_type in ("FUTURES", "SWAP"):
            if not base.endswith("-SWAP"):
                base = base + "-SWAP"
        return base
    
    def _get_td_mode(self, market_type: str, margin_mode: str = "cross") -> str:
        if market_type == "SPOT":
            return "cash"
        return margin_mode  # cross | isolated
    
    def _get_pos_side(self, side: str, market_type: str) -> str:
        if market_type == "SPOT":
            return None
        return "long" if side == "BUY" else "short"
    
    async def place_order(self, db: AsyncSession, user_id: int, config: ExchangeConfig,
                          order_data: OrderCreate) -> TradingOrder:
        """Place an order on OKX and save to DB."""
        symbol = self._symbol_to_okx(order_data.symbol, order_data.market_type)
        td_mode = self._get_td_mode(order_data.market_type, getattr(order_data, 'margin_mode', 'cross') or 'cross')
        pos_side = self._get_pos_side(order_data.side, order_data.market_type)
        
        result = await okx_manager.create_order(
            api_key=decrypt_text(config.api_key),
            api_secret=decrypt_text(config.api_secret),
            passphrase=decrypt_text(config.api_passphrase or ""),
            inst_id=symbol,
            side=order_data.side,
            ord_type=order_data.order_type,
            sz=str(order_data.quantity),
            px=str(order_data.price) if order_data.price else None,
            td_mode=td_mode,
            pos_side=pos_side,
        )
        
        db_order = TradingOrder(
            user_id=user_id,
            symbol=order_data.symbol,
            side=order_data.side,
            order_type=order_data.order_type,
            market_type=order_data.market_type,
            quantity=order_data.quantity,
            price=order_data.price,
            executed_qty=0,
            status="NEW",
            binance_order_id=result.get("ordId"),
            client_order_id=result.get("clOrdId"),
            leverage=order_data.leverage,
            reduce_only=order_data.reduce_only,
            remark=order_data.remark,
        )
        db.add(db_order)
        await db.commit()
        await db.refresh(db_order)
        return db_order
    
    async def cancel_order(self, db: AsyncSession, user_id: int, config: ExchangeConfig,
                           order_id: int) -> dict:
        """Cancel an order."""
        result = await db.execute(
            select(TradingOrder).where(
                and_(TradingOrder.id == order_id, TradingOrder.user_id == user_id)
            )
        )
        db_order = result.scalar_one_or_none()
        if not db_order:
            raise ValueError("Order not found")
        
        symbol = self._symbol_to_okx(db_order.symbol, db_order.market_type)
        okx_result = await okx_manager.cancel_order(
            api_key=decrypt_text(config.api_key),
            api_secret=decrypt_text(config.api_secret),
            passphrase=decrypt_text(config.api_passphrase or ""),
            inst_id=symbol,
            ord_id=db_order.binance_order_id,
        )
        
        db_order.status = "CANCELED"
        await db.commit()
        return okx_result
    
    async def get_orders(self, db: AsyncSession, user_id: int, symbol: str = None,
                         status: str = None, limit: int = 50) -> List[TradingOrder]:
        """Get orders from DB."""
        query = select(TradingOrder).where(TradingOrder.user_id == user_id).order_by(desc(TradingOrder.created_at))
        if symbol:
            query = query.where(TradingOrder.symbol == symbol)
        if status:
            query = query.where(TradingOrder.status == status)
        query = query.limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())


trade_service = TradeService()
