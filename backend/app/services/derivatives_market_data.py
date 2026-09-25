"""Public OKX OI/funding snapshots and observation-only entry context."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from math import isfinite

from sqlalchemy import delete, select

from app.db.base import AsyncSessionLocal
from app.models.derivatives_market_snapshot import DerivativesMarketSnapshot
from app.services.okx_client import okx_manager


class DerivativesMarketDataService:
    def __init__(self, interval_seconds: int = 300, retention_days: int = 35):
        self._interval = interval_seconds
        self._retention_days = retention_days
        self._task = None
        self._get_symbols = None
        self._last_cleanup = 0.0

    def start(self, get_symbols):
        self._get_symbols = get_symbols
        self._task = asyncio.create_task(self._run())
        print("OI/资金费率快照采集服务已启动")

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _run(self):
        while True:
            try:
                await self.collect_once()
            except Exception as exc:
                print(f"OI/资金费率快照采集异常: {exc}")
            await asyncio.sleep(self._interval)

    async def collect_once(self):
        if not self._get_symbols:
            return
        symbols = sorted(set(await self._get_symbols()))
        if not symbols:
            return
        tickers = {row.get("instId"): row for row in await okx_manager.get_tickers("SWAP")}
        semaphore = asyncio.Semaphore(4)

        async def collect_symbol(symbol):
            async with semaphore:
                ticker = tickers.get(symbol) or await okx_manager.get_ticker(symbol)
                oi, funding = await asyncio.gather(
                    okx_manager.get_open_interest(symbol),
                    okx_manager.get_funding_rate(symbol),
                )
                values = [ticker.get("last"), oi.get("oi"), funding.get("fundingRate")]
                if any(value is None or value == "" for value in values):
                    raise ValueError("incomplete derivatives snapshot")
                price, interest, rate = map(float, values)
                if not all(isfinite(value) for value in (price, interest, rate)) or price <= 0 or interest <= 0:
                    raise ValueError("invalid derivatives snapshot")
                return DerivativesMarketSnapshot(
                    symbol=symbol,
                    last_price=price,
                    open_interest=interest,
                    funding_rate=rate,
                    observed_at=datetime.now(timezone.utc),
                )

        rows = []
        for result in await asyncio.gather(*(collect_symbol(symbol) for symbol in symbols), return_exceptions=True):
            if isinstance(result, Exception):
                print(f"OI/资金费率单币采集失败: {result}")
            elif result.last_price > 0 and result.open_interest >= 0:
                rows.append(result)
        if not rows:
            return
        async with AsyncSessionLocal() as db:
            db.add_all(rows)
            if time.monotonic() - self._last_cleanup > 86400:
                cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
                await db.execute(delete(DerivativesMarketSnapshot).where(DerivativesMarketSnapshot.observed_at < cutoff))
                self._last_cleanup = time.monotonic()
            await db.commit()
        print(f"OI/资金费率快照已写入: {len(rows)} 币")

    async def entry_observation(self, symbol: str, direction: str, atr_pct: float | None, cfg: dict) -> dict:
        if not bool(cfg.get("enabled", False)):
            return {"enabled": False, "status": "disabled", "would_block": False, "enforced": False, "reason": "未启用"}
        lookback = max(64, int(cfg.get("history_limit", 2300) or 2300))
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DerivativesMarketSnapshot)
                .where(DerivativesMarketSnapshot.symbol == symbol)
                .order_by(DerivativesMarketSnapshot.observed_at.desc())
                .limit(lookback)
            )
            rows = list(result.scalars())
        return self.evaluate_rows(rows, direction, atr_pct, cfg)

    @staticmethod
    def evaluate_rows(rows, direction, atr_pct, cfg, *, now=None):
        def neutral(status, reason):
            return {"enabled": True, "status": status, "would_block": False,
                    "enforced": False, "reason": reason}

        def utc(value):
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

        now = utc(now or datetime.now(timezone.utc))
        if direction not in {"LONG", "SHORT"}:
            return neutral("unavailable", "invalid_direction")
        if len(rows) < 4:
            return {"enabled": True, "status": "warming_up", "would_block": False, "enforced": False, "reason": "OI样本不足"}
        latest = rows[0]
        latest_at = latest.observed_at.replace(tzinfo=timezone.utc) if latest.observed_at.tzinfo is None else latest.observed_at
        age = (now - latest_at).total_seconds()
        if age < 0 or age > max(60, float(cfg.get("max_data_age_seconds", 900))):
            return neutral("stale", "OI snapshot is stale or in the future")
        target_age = max(5, int(cfg.get("oi_window_minutes", 15) or 15)) * 60
        reference = next((row for row in rows[1:] if target_age * 0.8 <= (latest_at - utc(row.observed_at)).total_seconds() <= target_age * 1.5), None)
        if not reference or reference.last_price <= 0 or reference.open_interest <= 0:
            return {"enabled": True, "status": "warming_up", "would_block": False, "enforced": False, "reason": "OI时间窗口不足"}
        if not all(isfinite(float(value)) and float(value) > 0 for value in
                   (latest.last_price, latest.open_interest, reference.last_price, reference.open_interest)):
            return neutral("unavailable", "invalid OI/price sample")
        price_change = latest.last_price / reference.last_price - 1
        oi_change = latest.open_interest / reference.open_interest - 1
        price_threshold = max(float(atr_pct or 0) * float(cfg.get("price_atr_multiple", 0.5) or 0.5), 0.001)
        oi_threshold = float(cfg.get("min_oi_change_pct", 0.003) or 0.003)
        if price_change >= price_threshold and oi_change >= oi_threshold:
            quadrant = "long_build"
        elif price_change <= -price_threshold and oi_change >= oi_threshold:
            quadrant = "short_build"
        elif price_change >= price_threshold and oi_change <= -oi_threshold:
            quadrant = "short_cover"
        elif price_change <= -price_threshold and oi_change <= -oi_threshold:
            quadrant = "long_unwind"
        else:
            quadrant = "neutral"
        funding_values = sorted(float(row.funding_rate) for row in rows
                                if row.funding_rate is not None and isfinite(float(row.funding_rate)))
        warmup_min = max(1, int(cfg.get("warmup_min_samples", 2016) or 2016))
        warmup_ready = len(rows) >= warmup_min
        funding_extreme = None
        if warmup_ready and len(funding_values) >= warmup_min and funding_values[-1] > funding_values[0]:
            percentile = min(1.0, max(0.5, float(cfg.get("funding_extreme_percentile", 0.95) or 0.95)))
            index = int((len(funding_values) - 1) * percentile)
            high = funding_values[index]
            low = funding_values[len(funding_values) - 1 - index]
            if latest.funding_rate is not None and direction == "LONG" and latest.funding_rate > 0 and latest.funding_rate >= high:
                funding_extreme = "crowded_long"
            elif latest.funding_rate is not None and direction == "SHORT" and latest.funding_rate < 0 and latest.funding_rate <= low:
                funding_extreme = "crowded_short"
        conflict = (direction == "LONG" and quadrant in {"short_cover", "long_unwind"}) or (direction == "SHORT" and quadrant in {"long_unwind", "short_cover"})
        would_block = bool(conflict or funding_extreme)
        mode = str(cfg.get("mode", "observe") or "observe").lower()
        enforced = mode == "enforce" and warmup_ready and would_block
        return {
            "enabled": True, "status": quadrant, "would_block": would_block, "enforced": enforced,
            "warmup_ready": warmup_ready, "sample_count": len(rows), "price_change": price_change,
            "oi_change": oi_change, "funding_rate": latest.funding_rate, "funding_extreme": funding_extreme,
            "reason": f"OI {target_age // 60}m: 价格{price_change:+.2%}, OI{oi_change:+.2%}, 状态={quadrant}",
        }


derivatives_market_data = DerivativesMarketDataService()
