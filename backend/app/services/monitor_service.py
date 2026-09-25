"""
实时监控服务
基于 live_monitor.py 和 real_trader_v2.py 的核心逻辑
直接查询 OKX API 获取实时持仓和盈亏
"""
from typing import List, Dict, Optional
import asyncio
import time
from app.services.okx_client import okx_manager, decrypt_text
from app.services.contract_specs import get_static_ct_val
from app.models.exchange_config import ExchangeConfig


# 默认监控币种
DEFAULT_SYMBOLS = [
    'BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP',
    'DOGE-USDT-SWAP', 'XRP-USDT-SWAP', 'TRX-USDT-SWAP',
    'LTC-USDT-SWAP', 'BCH-USDT-SWAP', 'FIL-USDT-SWAP',
]


class MonitorService:
    """实时监控服务"""

    def __init__(self):
        self._live_cache = {}
        self._live_cache_ttl = 10.0
        self._live_cache_lock = asyncio.Lock()
    
    def _get_ct_val(self, coin: str) -> float:
        return get_static_ct_val(coin)
    
    async def get_account_balance(self, config: ExchangeConfig) -> float:
        """获取 USDT 余额。

        fail-closed（docs/02 §3，2026-07-17）：查询失败直接上抛，绝不返回 0.0——
        把"查不到"当"没有钱"会静默扭曲仓位计算与展示。返回 0.0 仅表示
        账户里确实没有 USDT。降级方式由调用方决定，不在这里吞错。
        """
        details = await okx_manager.get_balance(
            decrypt_text(config.api_key),
            decrypt_text(config.api_secret),
            decrypt_text(config.api_passphrase or ""), simulated=bool(config.is_testnet))
        for detail in details:
            if detail.get("ccy") == "USDT":
                return float(detail.get("availEq", 0) or detail.get("eq", 0))
        return 0.0

    async def get_positions(self, config: ExchangeConfig) -> List[dict]:
        """获取所有持仓。

        fail-closed（docs/02 §3，2026-07-17）：查询失败直接上抛，绝不返回 []——
        空列表意味着"确认无持仓"，会让离场检查静默跳过该止损的仓位、让开仓
        风控当作无敞口、让对账把全部本地未平记录误标为已平仓。
        """
        return await okx_manager.get_positions(
            decrypt_text(config.api_key),
            decrypt_text(config.api_secret),
            decrypt_text(config.api_passphrase or ""), simulated=bool(config.is_testnet))
    
    async def _build_live_monitor_data(self, config: ExchangeConfig) -> dict:
        """获取实时监控数据"""
        balance = await self.get_account_balance(config)
        positions_raw = await self.get_positions(config)
        
        positions = []
        total_pnl = 0.0
        total_imr = 0.0
        
        for pos in positions_raw:
            pos_val = float(pos.get("pos", 0) or 0)
            if pos_val == 0:
                continue
            
            symbol = pos.get("instId", "")
            coin = symbol.split("-")[0] if "-" in symbol else symbol
            market_type = "SWAP" if symbol.endswith("-SWAP") else "FUTURES"
            settle_ccy = pos.get("ccy") or pos.get("settleCcy") or ("USDT" if "-USDT-" in symbol else ("USDC" if "-USDC-" in symbol else ""))
            entry = float(pos.get("avgPx", 0) or 0)
            mark_px = float(pos.get("markPx", 0) or pos.get("last", 0) or entry)
            lever = int(pos.get("lever", 1) or 1)
            liq_px = float(pos.get("liqPx", 0) or 0) if pos.get("liqPx") else None
            upl = float(pos.get("upl", 0) or 0)
            upl_ratio = float(pos.get("uplRatio", 0) or 0)
            notional_usd = float(pos.get("notionalUsd", 0) or 0)
            imr = float(pos.get("imr", 0) or 0)
            mgn_ratio = float(pos.get("mgnRatio", 0) or 0)
            pos_side = (pos.get("posSide", "long") or "long").lower()
            mgn_mode = pos.get("mgnMode", "cross")
            side_label = "多" if pos_side == "long" or (pos_side == "net" and pos_val > 0) else "空"
            
            positions.append({
                "symbol": symbol,
                "coin": coin,
                "market_type": market_type,
                "settle_ccy": settle_ccy,
                "side": side_label,
                "raw_pos_side": pos_side,
                "margin_mode": "全仓" if mgn_mode == "cross" else "逐仓",
                "pos": abs(pos_val),
                "notional_usd": round(notional_usd, 2),
                "entry": round(entry, 4),
                "mark_px": round(mark_px, 4),
                "current": round(mark_px, 4),
                "pnl": round(upl, 2),
                "pnl_pct": round(upl_ratio * 100, 2),
                "imr": round(imr, 2),
                "margin": round(imr, 2),
                "mgn_ratio": round(mgn_ratio * 100, 2),
                "lever": lever,
                "liq_px": round(liq_px, 2) if liq_px else None,
            })
            
            total_pnl += upl
            total_imr += imr
        
        total_pnl_pct = (total_pnl / total_imr * 100) if total_imr > 0 else 0
        
        return {
            "balance_usdt": round(balance, 2),
            "positions": positions,
            "total_pnl": round(total_pnl, 2),
            "total_margin": round(total_imr, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "position_count": len(positions),
        }


    async def get_live_monitor_data(self, config: ExchangeConfig) -> dict:
        """获取实时监控数据，短时间缓存以避免 OKX 私有接口限流。"""
        cache_key = str(getattr(config, "id", "default"))
        now = time.monotonic()
        cached = self._live_cache.get(cache_key)
        if cached and now - cached[0] < self._live_cache_ttl:
            return cached[1]

        async with self._live_cache_lock:
            now = time.monotonic()
            cached = self._live_cache.get(cache_key)
            if cached and now - cached[0] < self._live_cache_ttl:
                return cached[1]

            data = await self._build_live_monitor_data(config)
            self._live_cache[cache_key] = (time.monotonic(), data)
            return data


monitor_service = MonitorService()
