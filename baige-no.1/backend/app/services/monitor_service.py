"""
实时监控服务
基于 live_monitor.py 和 real_trader_v2.py 的核心逻辑
直接查询 OKX API 获取实时持仓和盈亏
"""
from typing import List, Dict, Optional
from app.services.okx_client import okx_manager, decrypt_text
from app.models.exchange_config import ExchangeConfig


# 合约面值配置 (ctVal)
CT_VALS = {
    'BTC': 0.01, 'ETH': 0.1, 'SOL': 1.0, 'DOGE': 1000.0,
    'XRP': 100.0, 'TRX': 1000.0, 'LTC': 1.0, 'BCH': 0.1, 'FIL': 0.1,
}

# 默认监控币种
DEFAULT_SYMBOLS = [
    'BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP',
    'DOGE-USDT-SWAP', 'XRP-USDT-SWAP', 'TRX-USDT-SWAP',
    'LTC-USDT-SWAP', 'BCH-USDT-SWAP', 'FIL-USDT-SWAP',
]


class MonitorService:
    """实时监控服务"""
    
    def _get_ct_val(self, coin: str) -> float:
        return CT_VALS.get(coin, 0.01)
    
    async def get_account_balance(self, config: ExchangeConfig) -> float:
        """获取 USDT 余额"""
        try:
            details = await okx_manager.get_balance(
                decrypt_text(config.api_key),
                decrypt_text(config.api_secret),
                decrypt_text(config.api_passphrase or ""),
            )
            for detail in details:
                if detail.get("ccy") == "USDT":
                    return float(detail.get("availEq", 0) or detail.get("eq", 0))
            return 0.0
        except Exception as e:
            print(f"获取余额失败: {e}")
            return 0.0
    
    async def get_positions(self, config: ExchangeConfig) -> List[dict]:
        """获取所有持仓"""
        try:
            path = "/api/v5/account/positions"
            from app.services.okx_client import generate_signature
            from datetime import datetime, timezone
            import httpx, json
            
            api_key = decrypt_text(config.api_key)
            api_secret = decrypt_text(config.api_secret)
            passphrase = decrypt_text(config.api_passphrase or "")
            
            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            signature = generate_signature(timestamp, "GET", path, "", api_secret)
            headers = {
                "OK-ACCESS-KEY": api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": passphrase,
            }
            
            async with httpx.AsyncClient(base_url="https://www.okx.com", timeout=30) as client:
                resp = await client.get(path, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != "0":
                    return []
                return data.get("data", [])
        except Exception as e:
            print(f"获取持仓失败: {e}")
            return []
    
    async def get_live_monitor_data(self, config: ExchangeConfig) -> dict:
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
            entry = float(pos.get("avgPx", 0) or 0)
            mark_px = float(pos.get("markPx", 0) or pos.get("last", 0) or entry)
            lever = int(pos.get("lever", 1) or 1)
            liq_px = float(pos.get("liqPx", 0) or 0) if pos.get("liqPx") else None
            upl = float(pos.get("upl", 0) or 0)
            upl_ratio = float(pos.get("uplRatio", 0) or 0)
            notional_usd = float(pos.get("notionalUsd", 0) or 0)
            imr = float(pos.get("imr", 0) or 0)
            mgn_ratio = float(pos.get("mgnRatio", 0) or 0)
            pos_side = pos.get("posSide", "long")
            mgn_mode = pos.get("mgnMode", "cross")
            
            positions.append({
                "symbol": symbol,
                "coin": coin,
                "side": "多" if pos_side == "long" else "空",
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


monitor_service = MonitorService()
