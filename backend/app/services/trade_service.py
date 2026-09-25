from typing import List
import asyncio
from decimal import Decimal, ROUND_FLOOR
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from app.services.okx_client import okx_manager, decrypt_text, generate_cl_ord_id
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
        
        market_type = (market_type or "SPOT").upper()
        if market_type == "SWAP":
            if not base.endswith("-SWAP"):
                base = base + "-SWAP"
        return base
    
    def _get_td_mode(self, market_type: str, margin_mode: str = "cross") -> str:
        if market_type == "SPOT":
            return "cash"
        return margin_mode  # cross | isolated
    
    def _get_pos_side(self, side: str, market_type: str, pos_side: str = None, reduce_only: bool = False) -> str:
        if market_type == "SPOT":
            return None
        if pos_side:
            return pos_side.lower()
        if reduce_only:
            return None
        return "long" if side == "BUY" else "short"

    async def _normalize_order_size(
        self, symbol: str, quantity: float, market_type: str,
        *, reduce_only: bool = False, allow_min_size_bump: bool = False,
    ) -> float:
        if market_type == "SPOT":
            return quantity

        inst_type = "FUTURES" if (market_type or "").upper() == "FUTURES" else "SWAP"
        instruments = await okx_manager.get_instruments(inst_type)
        instrument = next((item for item in instruments if item.get("instId") == symbol), None)
        if not instrument:
            return quantity

        lot_size = Decimal(str(instrument.get("lotSz") or "0.01"))
        min_size = Decimal(str(instrument.get("minSz") or lot_size))
        max_size_raw = (
            instrument.get("maxMktSz")
            or instrument.get("maxLmtSz")
            or instrument.get("maxSz")
        )
        max_size = Decimal(str(max_size_raw)) if max_size_raw else None
        requested = Decimal(str(quantity))
        if lot_size <= 0:
            normalized = requested
        else:
            # 向下取整到 lotSz：宁可略少也绝不超出上游保证金计划算出的数量
            normalized = (requested / lot_size).to_integral_value(rounding=ROUND_FLOOR) * lot_size
        if normalized < min_size:
            # 取整后不足最小下单单位（Phase 2.5g / E4，2026-07-17 经用户确认）：
            # - 平仓单（reduce_only）仍抬到 minSz——不足 minSz 的持仓必须能被平掉，
            #   否则会永远困在仓里；reduceOnly 由交易所按实际持仓封顶，只会少平不会多开。
            # - 开仓单默认放弃该笔：抬到 minSz 意味着成交量超出上游保证金计划
            #   （0.7→1 张 = +43%），MAX_AUTO_POSITION_PERCENT 等风控数学全部失真。
            #   买不起最小单位，就说明这笔的真实最小风险从未被计划过。
            # - params.allow_min_size_bump=true（→ OrderCreate.allow_min_size_bump）
            #   可显式恢复旧行为。
            if reduce_only or allow_min_size_bump:
                normalized = min_size
            else:
                raise ValueError(
                    f"下单量不足最小下单单位，放弃该笔: 计划 {requested} → 取整 {normalized} "
                    f"< minSz {min_size}（{symbol}）。抬到 minSz 会超出保证金计划；"
                    f"如确需按最小单位成交，显式设置 allow_min_size_bump=true"
                )
        if max_size and max_size > 0 and normalized > max_size:
            normalized = max_size
        return float(normalized)
    
    async def place_order(self, db: AsyncSession, user_id: int, config: ExchangeConfig,
                          order_data: OrderCreate) -> TradingOrder:
        """Place an order on OKX and save to DB."""
        symbol = self._symbol_to_okx(order_data.symbol, order_data.market_type)
        td_mode = self._get_td_mode(order_data.market_type, getattr(order_data, 'margin_mode', 'cross') or 'cross')
        normalized_quantity = await self._normalize_order_size(
            symbol, order_data.quantity, order_data.market_type,
            reduce_only=bool(getattr(order_data, "reduce_only", False)),
            allow_min_size_bump=bool(getattr(order_data, "allow_min_size_bump", False)),
        )
        pos_side = self._get_pos_side(
            order_data.side,
            order_data.market_type,
            getattr(order_data, 'pos_side', None),
            getattr(order_data, 'reduce_only', False),
        )

        if order_data.market_type != "SPOT" and not order_data.reduce_only:
            leverage = order_data.leverage if order_data.leverage and order_data.leverage > 1 else 20
            await okx_manager.set_leverage(
                api_key=decrypt_text(config.api_key),
                api_secret=decrypt_text(config.api_secret),
                passphrase=decrypt_text(config.api_passphrase or ""),
                inst_id=symbol,
                leverage=leverage,
                margin_mode=td_mode,
                pos_side=pos_side, simulated=bool(config.is_testnet))
        
        # 幂等键在服务层生成一次，同一笔下单意图的所有重试共用；也落库便于对账
        cl_ord_id = generate_cl_ord_id()
        result = await okx_manager.create_order(
            api_key=decrypt_text(config.api_key),
            api_secret=decrypt_text(config.api_secret),
            passphrase=decrypt_text(config.api_passphrase or ""),
            inst_id=symbol,
            side=order_data.side,
            ord_type=order_data.order_type,
            sz=str(normalized_quantity),
            px=str(order_data.price) if order_data.price else None,
            td_mode=td_mode,
            pos_side=pos_side,
            reduce_only=order_data.reduce_only,
            cl_ord_id=cl_ord_id, simulated=bool(config.is_testnet))
        
        # 不再乐观标记 FILLED：状态以交易所确认为准，由下方成交回填块落 FILLED
        db_order = TradingOrder(
            user_id=user_id,
            exchange_config_id=config.id,
            symbol=order_data.symbol,
            side=order_data.side,
            order_type=order_data.order_type,
            market_type=order_data.market_type,
            quantity=normalized_quantity,
            price=order_data.price,
            executed_qty=0,
            status="NEW",
            binance_order_id=result.get("ordId"),
            client_order_id=result.get("clOrdId") or cl_ord_id,
            leverage=order_data.leverage,
            reduce_only=order_data.reduce_only,
            remark=order_data.remark,
        )
        db.add(db_order)
        await db.commit()
        await db.refresh(db_order)
        
        # 新增：市价单回调查成交价和成交量
        if order_data.order_type == "MARKET" and db_order.binance_order_id:
            try:
                await asyncio.sleep(0.5)  # 等待OKX成交
                okx_order = await okx_manager.get_order_detail(
                    api_key=decrypt_text(config.api_key),
                    api_secret=decrypt_text(config.api_secret),
                    passphrase=decrypt_text(config.api_passphrase or ""),
                    inst_id=symbol,
                    ord_id=db_order.binance_order_id, simulated=bool(config.is_testnet))
                if okx_order:
                    avg_px = float(okx_order.get("avgPx", 0) or 0)
                    fill_sz = float(okx_order.get("accFillSz", 0) or 0)
                    state = (okx_order.get("state", "") or "").lower()
                    state_map = {
                        "live": "NEW",
                        "partially_filled": "PARTIALLY_FILLED",
                        "filled": "FILLED",
                        "canceled": "CANCELED",
                        "cancelled": "CANCELED",
                    }
                    if avg_px > 0:
                        db_order.executed_price = avg_px
                    if fill_sz > 0:
                        db_order.executed_qty = fill_sz
                    if state in state_map:
                        db_order.status = state_map[state]
                    if avg_px > 0 or fill_sz > 0 or state in state_map:
                        await db.commit()
                        print(f"[成交回填] {symbol} state={state or '?'} avgPx={avg_px} fillSz={fill_sz}")
            except Exception as e:
                print(f"[成交回填失败] {symbol}: {e}")
        
        return db_order
    
    async def cancel_order(self, db: AsyncSession, user_id: int, config: ExchangeConfig,
                           order_id: int) -> dict:
        """Cancel an order."""
        result = await db.execute(
            select(TradingOrder).where(
                and_(
                    TradingOrder.id == order_id,
                    TradingOrder.user_id == user_id,
                    TradingOrder.exchange_config_id == config.id,
                )
            )
        )
        db_order = result.scalar_one_or_none()
        if not db_order:
            raise ValueError("Order not found")
        if db_order.status not in ("NEW", "PARTIALLY_FILLED", "LIVE"):
            raise ValueError("订单当前状态不可撤销")
        if db_order.order_type == "MARKET":
            db_order.status = "FILLED"
            db_order.executed_qty = db_order.executed_qty or db_order.quantity
            await db.commit()
            raise ValueError("市价单通常会立即成交，不能撤销；已更新本地订单状态")
        if not db_order.binance_order_id:
            raise ValueError("订单缺少交易所订单号，无法撤销")
        
        symbol = self._symbol_to_okx(db_order.symbol, db_order.market_type)
        okx_result = await okx_manager.cancel_order(
            api_key=decrypt_text(config.api_key),
            api_secret=decrypt_text(config.api_secret),
            passphrase=decrypt_text(config.api_passphrase or ""),
            inst_id=symbol,
            ord_id=db_order.binance_order_id, simulated=bool(config.is_testnet))
        
        # 检查 OKX 返回的具体订单级错误码
        s_code = okx_result.get("sCode") if isinstance(okx_result, dict) else None
        if s_code and str(s_code) != "0":
            s_msg = okx_result.get("sMsg", "交易所撤单失败")
            raise ValueError(f"交易所撤单失败: {s_msg} (code={s_code})")
        
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

    async def sync_orders_from_okx(self, db: AsyncSession, user_id: int, config: ExchangeConfig) -> dict:
        """从 OKX 同步历史订单到 trading_orders"""
        api_key = decrypt_text(config.api_key)
        api_secret = decrypt_text(config.api_secret)
        passphrase = decrypt_text(config.api_passphrase or "")

        okx_orders = await okx_manager.get_orders(api_key, api_secret, passphrase, limit=100, simulated=bool(config.is_testnet))

        # 获取已有订单，既用于去重，也用于刷新本地状态
        result = await db.execute(
            select(TradingOrder).where(
                and_(
                    TradingOrder.user_id == user_id,
                    TradingOrder.exchange_config_id == config.id,
                    TradingOrder.binance_order_id.isnot(None),
                )
            )
        )
        existing_orders = {order.binance_order_id: order for order in result.scalars().all()}

        imported = 0
        updated = 0
        skipped = 0
        for order in okx_orders:
            ord_id = order.get("ordId")
            if not ord_id:
                skipped += 1
                continue

            # 解析状态（兼容大小写）
            state = (order.get("state", "") or "").lower()
            status_map = {
                "live": "NEW",
                "partially_filled": "PARTIALLY_FILLED",
                "filled": "FILLED",
                "canceled": "CANCELED",
                "cancelled": "CANCELED",
            }
            status = status_map.get(state, state.upper())

            # 解析数量和价格
            sz = float(order.get("sz", 0) or 0)
            filled_sz = float(order.get("accFillSz", 0) or 0)
            avg_px = float(order.get("avgPx", 0) or 0)
            px = order.get("px")

            existing_order = existing_orders.get(ord_id)
            if existing_order:
                existing_order.status = status
                existing_order.executed_qty = filled_sz
                existing_order.executed_price = avg_px if avg_px > 0 else existing_order.executed_price
                existing_order.price = float(px) if px else existing_order.price
                updated += 1
                continue

            # 解析市场类型
            inst_id = order.get("instId", "")
            market_type = "SWAP" if "SWAP" in inst_id else ("SPOT" if "-" in inst_id and "SWAP" not in inst_id else "FUTURES")

            db_order = TradingOrder(
                user_id=user_id,
                exchange_config_id=config.id,
                symbol=inst_id,
                side=order.get("side", "").upper(),
                order_type=order.get("ordType", "").upper(),
                market_type=market_type,
                quantity=sz,
                price=float(px) if px else None,
                executed_qty=filled_sz,
                executed_price=avg_px if avg_px > 0 else None,
                status=status,
                binance_order_id=ord_id,
                client_order_id=order.get("clOrdId"),
                leverage=int(float(order.get("lever", 1) or 1)),
                reduce_only=order.get("reduceOnly") == "true",
                remark="OKX同步",
            )
            db.add(db_order)
            imported += 1

        await db.commit()
        return {"imported": imported, "updated": updated, "skipped": skipped, "total_from_exchange": len(okx_orders)}


trade_service = TradeService()
