from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade_record import TradeRecord
from app.services.contract_specs import get_static_ct_val


EPSILON = 1e-9


def calculate_close_pnl(
    direction: str, entry_price: float, exit_price: float, quantity: float, ct_val: float = 1.0
) -> float:
    """PnL in USDT. `quantity` is contract count (position_size); ct_val converts it to underlying coin units."""
    direction = (direction or "").upper()
    if direction == "LONG":
        return (exit_price - entry_price) * quantity * ct_val
    if direction == "SHORT":
        return (entry_price - exit_price) * quantity * ct_val
    raise ValueError(f"Unsupported trade direction: {direction}")


def resolve_ct_val(record: TradeRecord) -> float:
    """Records opened before the ct_val column existed have it as None; fall back to the static table."""
    if record.ct_val:
        return record.ct_val
    return get_static_ct_val(record.symbol)


async def apply_reduce_to_trade_records(
    db: AsyncSession,
    *,
    user_id: int,
    exchange_config_id: int,
    symbol: str,
    direction: str,
    exit_price: float,
    quantity: float,
    strategy_tag: str | None = None,
    closed_at: datetime | None = None,
    note: str | None = None,
) -> dict:
    """Apply a reduce-only fill to open trade records without over-closing lots.

    Open records are consumed FIFO. A partial close creates a realized closed
    record for the consumed quantity and leaves the original record open with
    the remaining size.
    """
    direction = (direction or "").upper()
    symbol = (symbol or "").upper()
    exit_price = float(exit_price or 0)
    remaining = float(quantity or 0)
    closed_at = closed_at or datetime.now(timezone.utc)

    if not symbol or direction not in {"LONG", "SHORT"}:
        raise ValueError("symbol and direction are required")
    if exit_price <= 0:
        raise ValueError("exit_price must be positive")
    if remaining <= 0:
        return {
            "closed_qty": 0.0,
            "remaining_qty": 0.0,
            "realized_pnl": 0.0,
            "closed_records": 0,
            "partial_records": 0,
        }

    filters = [
        TradeRecord.user_id == user_id,
        TradeRecord.exchange_config_id == exchange_config_id,
        TradeRecord.symbol == symbol,
        TradeRecord.direction == direction,
        TradeRecord.is_closed == False,
    ]
    if strategy_tag:
        filters.append(TradeRecord.strategy_tag == strategy_tag)

    result = await db.execute(
        select(TradeRecord)
        .where(
            and_(*filters)
        )
        .order_by(TradeRecord.created_at.asc(), TradeRecord.id.asc())
    )
    records = list(result.scalars().all())

    return apply_reduce_to_loaded_records(
        records,
        add_record=db.add,
        exit_price=exit_price,
        quantity=remaining,
        closed_at=closed_at,
        note=note,
    )


def apply_reduce_to_loaded_records(
    records: list[TradeRecord],
    *,
    add_record,
    exit_price: float,
    quantity: float,
    closed_at: datetime | None = None,
    note: str | None = None,
) -> dict:
    closed_at = closed_at or datetime.now(timezone.utc)
    exit_price = float(exit_price or 0)
    remaining = float(quantity or 0)

    closed_qty = 0.0
    realized_pnl = 0.0
    closed_records = 0
    partial_records = 0

    for record in records:
        if remaining <= EPSILON:
            break

        record_size = float(record.position_size or 0)
        if record_size <= EPSILON:
            record.is_closed = True
            record.updated_at = closed_at
            continue

        close_qty = min(record_size, remaining)
        entry_price = float(record.entry_price or exit_price)
        direction = (record.direction or "").upper()
        ct_val = resolve_ct_val(record)
        pnl = calculate_close_pnl(direction, entry_price, exit_price, close_qty, ct_val)

        if close_qty + EPSILON >= record_size:
            record.exit_price = exit_price
            record.pnl_usdt = round(pnl, 4)
            record.ct_val = ct_val
            record.is_closed = True
            record.updated_at = closed_at
            closed_records += 1
        else:
            closed_lot = TradeRecord(
                user_id=record.user_id,
                exchange_config_id=record.exchange_config_id,
                symbol=record.symbol,
                direction=record.direction,
                entry_price=record.entry_price,
                exit_price=exit_price,
                position_size=close_qty,
                ct_val=ct_val,
                pnl_usdt=round(pnl, 4),
                leverage=record.leverage,
                strategy_tag=record.strategy_tag,
                notes=(record.notes or "") + (f" | 部分平仓: {note}" if note else " | 部分平仓"),
                is_closed=True,
                external_id=record.external_id,
                created_at=record.created_at,
                updated_at=closed_at,
            )
            add_record(closed_lot)

            record.position_size = record_size - close_qty
            record.ct_val = ct_val
            record.pnl_usdt = None
            record.updated_at = closed_at
            partial_records += 1

        closed_qty += close_qty
        realized_pnl += pnl
        remaining -= close_qty

    return {
        "closed_qty": round(closed_qty, 10),
        "remaining_qty": round(max(remaining, 0.0), 10),
        "realized_pnl": round(realized_pnl, 4),
        "closed_records": closed_records,
        "partial_records": partial_records,
    }
