"""Fresh realised-liquidation aftershock feed used only as a risk filter."""
import asyncio
import inspect
import math
import time
from collections import defaultdict

import httpx

OKX_BASE = "https://www.okx.com"
RECENT_WINDOW_SECONDS = 30 * 60
DECAY_HALF_LIFE_SECONDS = 10 * 60
UPDATE_INTERVAL_SECONDS = 5 * 60
COIN_FAMILY_MAP = {
    "BTC": "BTC-USDT", "ETH": "ETH-USDT", "SOL": "SOL-USDT", "XRP": "XRP-USDT",
    "DOGE": "DOGE-USDT", "TRX": "TRX-USDT", "LTC": "LTC-USDT", "FIL": "FIL-USDT", "PEPE": "PEPE-USDT",
}

async def fetch_liquidations(uly: str, limit: int = 100) -> list[dict]:
    """Fetch completed OKX liquidations and retain the event time."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{OKX_BASE}/api/v5/public/liquidation-orders",
                params={"instType": "SWAP", "uly": uly, "state": "filled", "limit": str(limit)},
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("code") != "0":
            return []
        records = []
        for item in payload.get("data", []):
            for detail in item.get("details", []):
                try:
                    records.append({
                        "price": float(detail.get("bkPx", 0) or 0),
                        "side": str(detail.get("posSide", "") or "").lower(),
                        "size": float(detail.get("sz", 0) or 0),
                        "timestamp_ms": int(detail.get("ts") or detail.get("time") or 0),
                    })
                except (TypeError, ValueError):
                    continue
        return records
    except Exception as exc:
        print(f"清算数据拉取失败 [{uly}]: {exc}")
        return []


def aggregate_zones(
    records: list[dict], current_price: float, bucket_pct: float = 0.01,
    recent_window_seconds: int = RECENT_WINDOW_SECONDS,
    half_life_seconds: int = DECAY_HALF_LIFE_SECONDS, now_ms: int | None = None,
) -> list[dict]:
    """Aggregate only fresh completed liquidations, decayed by age."""
    if not records or current_price <= 0:
        return []
    now_ms = now_ms or int(time.time() * 1000)
    bucket_size = current_price * bucket_pct
    if bucket_size <= 0:
        return []
    buckets = defaultdict(lambda: {"long_liq": 0.0, "short_liq": 0.0, "long_latest_ts": 0, "short_latest_ts": 0})
    for record in records:
        try:
            price = float(record.get("price", 0) or 0)
            size = float(record.get("size", 0) or 0)
            side = record.get("side")
            timestamp_ms = int(record.get("timestamp_ms", 0) or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or size <= 0 or side not in {"long", "short"} or timestamp_ms <= 0:
            continue
        age_seconds = max(0.0, (now_ms - timestamp_ms) / 1000)
        if age_seconds > recent_window_seconds:
            continue
        decay = math.pow(0.5, age_seconds / max(float(half_life_seconds), 1.0))
        bucket = buckets[round(price / bucket_size) * bucket_size]
        bucket[f"{side}_liq"] += size * decay
        bucket[f"{side}_latest_ts"] = max(bucket[f"{side}_latest_ts"], timestamp_ms)
    zones = []
    for price in sorted(buckets):
        bucket = buckets[price]
        if bucket["long_liq"] <= 0 and bucket["short_liq"] <= 0:
            continue
        zones.append({
            "price": round(price, 8),
            "long_liq": round(bucket["long_liq"], 6),
            "short_liq": round(bucket["short_liq"], 6),
            "long_latest_ts": bucket["long_latest_ts"],
            "short_latest_ts": bucket["short_latest_ts"],
            "updated_at_ms": now_ms,
        })
    return zones


class LiqDataUpdater:
    """Refresh short-lived realised-liquidation aftershock context from OKX."""
    def __init__(self, liq_signal, interval_seconds: int = UPDATE_INTERVAL_SECONDS):
        self._liq_signal = liq_signal
        self._interval = interval_seconds
        self._task = None
        self._get_price = None

    def start(self, get_price_func=None):
        self._get_price = get_price_func
        self._task = asyncio.create_task(self._run())
        print("清算近期瀑布风险数据更新服务已启动")

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _get_live_price(self, coin: str) -> float:
        if not self._get_price:
            return 0.0
        try:
            value = self._get_price(coin)
            if inspect.isawaitable(value):
                value = await value
            return float(value or 0)
        except Exception as exc:
            print(f"清算参考价格获取失败 [{coin}]: {exc}")
            return 0.0

    async def _run(self):
        while True:
            try:
                await self._update_all()
            except Exception as exc:
                print(f"清算数据更新异常: {exc}")
            await asyncio.sleep(self._interval)

    async def _update_all(self):
        zones_data = {}
        now_ms = int(time.time() * 1000)
        for coin, uly in COIN_FAMILY_MAP.items():
            try:
                current_price = await self._get_live_price(coin)
                if current_price <= 0:
                    continue
                zones = aggregate_zones(await fetch_liquidations(uly, limit=100), current_price, now_ms=now_ms)
                if zones:
                    zones_data[coin] = zones
                    print(f"清算近期风险更新 [{coin}]: {len(zones)} 个区间")
            except Exception as exc:
                print(f"清算数据更新失败 [{coin}]: {exc}")
        if self._liq_signal:
            # Never carry stale cascade context across an empty/failed refresh.
            self._liq_signal.update_zones(zones_data)
            print(f"清算近期风险图已更新: {list(zones_data.keys())}")


liq_data_updater = LiqDataUpdater(None)
