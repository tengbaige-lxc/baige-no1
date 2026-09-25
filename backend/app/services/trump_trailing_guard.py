"""MA-gated trailing-profit guard for the manually held TRUMP long.

It deliberately operates outside the paused legacy strategies. The exchange-side
hard stop remains the last line of defence; this guard only performs one partial
profit reduction after the daily MA5 turns down.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from sqlalchemy import desc, select

from app.db.base import AsyncSessionLocal
from app.models.exchange_config import ExchangeConfig
from app.models.user import User
from app.schemas.trading import OrderCreate
from app.services.monitor_service import monitor_service
from app.services.okx_client import okx_manager
from app.services.trade_service import trade_service


SYMBOL = "TRUMP-USDT-SWAP"
LEVERAGE = 22
REDUCE_RATIO = 0.40
PEAK_DRAWDOWN_RATIO = 0.38
POLL_SECONDS = 60
STATE_PATH = Path("/root/baige-no2/runtime/trump_trailing_guard_state.json")
REMARK = "TRUMP日线MA5移动止盈-20260823"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger(__name__)


def daily_ma5_turns_down(closes: list[float]) -> tuple[bool, float, float]:
    if len(closes) < 6:
        raise ValueError("need six confirmed daily closes")
    current = sum(closes[-5:]) / 5
    previous = sum(closes[-6:-1]) / 5
    return current < previous, current, previous


def drawdown_reached(peak_roe: float, current_roe: float) -> bool:
    return peak_roe > 0 and current_roe <= peak_roe * (1 - PEAK_DRAWDOWN_RATIO)


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("state unavailable, resetting: %s", exc)
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, STATE_PATH)


async def active_config_and_user():
    async with AsyncSessionLocal() as db:
        config = (
            await db.execute(
                select(ExchangeConfig)
                .where(ExchangeConfig.is_active == True, ExchangeConfig.exchange.ilike("okx"))
                .order_by(desc(ExchangeConfig.updated_at), desc(ExchangeConfig.id))
                .limit(1)
            )
        ).scalar_one_or_none()
        user = (
            await db.execute(
                select(User).where(User.is_active == True).order_by(desc(User.is_superuser), User.id).limit(1)
            )
        ).scalar_one_or_none()
        if not config or config.is_testnet or not user:
            raise RuntimeError("live OKX config and active user are required")
        return config, user


async def run_once() -> dict:
    config, user = await active_config_and_user()
    positions = await monitor_service.get_positions(config)
    position = next(
        (
            item
            for item in positions
            if item.get("instId") == SYMBOL
            and (item.get("posSide") or "").lower() == "long"
            and abs(float(item.get("pos", 0) or 0)) > 0
        ),
        None,
    )
    if not position:
        return {"status": "no_open_trump_long"}

    daily_rows = await okx_manager.get_candles(SYMBOL, "1D", limit=8)
    confirmed = [row for row in daily_rows if len(row) < 9 or row[8] == "1"]
    closes = [float(row[4]) for row in sorted(confirmed, key=lambda row: int(row[0]))]
    ma5_down, ma5, ma5_previous = daily_ma5_turns_down(closes)
    roe = float(position.get("uplRatio", 0) or 0)
    position_key = f"{SYMBOL}:{position.get('posId') or position.get('cTime') or position.get('pos')}"
    state = load_state()
    if state.get("position_key") != position_key:
        state = {"position_key": position_key, "peak_roe": roe, "fired": False}
    else:
        state["peak_roe"] = max(float(state.get("peak_roe", 0) or 0), roe)

    peak_roe = float(state["peak_roe"])
    result = {
        "status": "holding",
        "roe": roe,
        "peak_roe": peak_roe,
        "daily_ma5": ma5,
        "daily_ma5_previous": ma5_previous,
        "daily_ma5_down": ma5_down,
        "fired": bool(state.get("fired")),
    }
    if not ma5_down:
        result["reason"] = "daily_ma5_not_down_no_profit_taking"
        save_state(state)
        return result
    if state.get("fired") or not drawdown_reached(peak_roe, roe):
        result["reason"] = "waiting_for_38pct_peak_roe_drawdown"
        save_state(state)
        return result

    position_size = abs(float(position["pos"]))
    reduce_size = position_size * REDUCE_RATIO
    async with AsyncSessionLocal() as db:
        order = await trade_service.place_order(
            db,
            user.id,
            config,
            OrderCreate(
                symbol=SYMBOL,
                side="SELL",
                order_type="MARKET",
                quantity=reduce_size,
                market_type="SWAP",
                margin_mode="cross",
                leverage=LEVERAGE,
                pos_side="long",
                reduce_only=True,
                remark=REMARK,
            ),
        )
    state["fired"] = True
    state["fired_order_id"] = order.id
    save_state(state)
    result.update({"status": "reduced", "order_id": order.id, "reduce_size": reduce_size})
    return result


async def main(once: bool) -> None:
    while True:
        try:
            result = await run_once()
            LOG.info("%s", json.dumps(result, ensure_ascii=False))
        except Exception:
            LOG.exception("trailing guard cycle failed")
        if once:
            return
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.once))
