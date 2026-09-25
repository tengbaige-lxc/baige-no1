"""
信号扫描服务
整合白鸽一号的清算热力图、摩尔缠论背驰、宏观过滤器
"""
from typing import Optional, Dict, List
from datetime import datetime, timezone, timedelta

from app.services.okx_client import okx_manager
from app.services.chanlun_bridge import summarize_chanlun_factors
from app.services.news_analyzer import news_analyzer, NewsFactorResult


class SignalService:
    """交易信号扫描服务"""
    
    def __init__(self):
        self._liq_signal = None
        self._div_system = None
        self._macro_filter = None
        self._init_modules()
    
    def _init_modules(self):
        """延迟初始化信号模块"""
        try:
            from app.vendor.liq_signal_module import LiquidationSignal
            self._liq_signal = LiquidationSignal()
            # 加载默认数据
            self._liq_signal.update_zones({
                'BTC': [
                    {'price': 68000, 'long_liq': 45.2, 'short_liq': 12.1},
                    {'price': 69000, 'long_liq': 38.5, 'short_liq': 15.3},
                    {'price': 69500, 'long_liq': 22.1, 'short_liq': 18.7},
                    {'price': 70500, 'long_liq': 14.2, 'short_liq': 25.4},
                    {'price': 71000, 'long_liq': 8.5, 'short_liq': 42.8},
                    {'price': 72000, 'long_liq': 5.2, 'short_liq': 38.5},
                ],
                'ETH': [
                    {'price': 1900, 'long_liq': 28.5, 'short_liq': 8.2},
                    {'price': 1950, 'long_liq': 22.1, 'short_liq': 12.5},
                    {'price': 2000, 'long_liq': 15.3, 'short_liq': 14.2},
                    {'price': 2100, 'long_liq': 12.8, 'short_liq': 18.5},
                    {'price': 2200, 'long_liq': 8.5, 'short_liq': 25.3},
                    {'price': 2400, 'long_liq': 5.2, 'short_liq': 22.1},
                ],
                'SOL': [
                    {'price': 80, 'long_liq': 20.0, 'short_liq': 5.0},
                    {'price': 82, 'long_liq': 15.0, 'short_liq': 8.0},
                    {'price': 87, 'long_liq': 8.0, 'short_liq': 15.0},
                    {'price': 90, 'long_liq': 5.0, 'short_liq': 20.0},
                ]
            })
        except Exception as e:
            print(f"清算信号模块加载失败: {e}")
        
        try:
            self._div_system = {
                'summarize': summarize_chanlun_factors,
            }
        except Exception as e:
            print(f"背驰系统加载失败: {e}")
        
        try:
            from app.vendor.macro_filter import MacroFilter
            self._macro_filter = MacroFilter()
        except Exception as e:
            print(f"宏观过滤器加载失败: {e}")
    
    async def scan_liquidation_signal(self, symbol: str) -> Optional[dict]:
        """扫描清算热力图信号"""
        if not self._liq_signal:
            return None
        
        try:
            # 获取当前价格
            ticker = await okx_manager.get_ticker(symbol)
            current_price = float(ticker.get("last", 0))
            
            coin = symbol.split("-")[0]
            result, reason = self._liq_signal.check_signal(coin, current_price)
            
            if result:
                return {
                    "signal": result.get("signal"),
                    "strength": result.get("strength", 1),
                    "reason": result.get("reason", reason),
                    "current_price": current_price,
                    "nearest_long_zone": result.get("nearest_long_zone"),
                    "nearest_short_zone": result.get("nearest_short_zone"),
                    "distance_to_long": round(result.get("distance_to_long", 0) * 100, 2),
                    "distance_to_short": round(result.get("distance_to_short", 0) * 100, 2),
                }
        except Exception as e:
            print(f"清算信号扫描失败: {e}")
        
        return None
    
    async def scan_divergence_signal(self, symbol: str) -> Optional[dict]:
        """扫描缠论背驰信号（已替换为完整缠论分析）"""
        try:
            # 获取K线数据（建议至少170根）
            candles = await okx_manager.get_candles(symbol, "1H", 200)
            if len(candles) < 50:
                return None

            # 转换为新包格式
            kline_rows = []
            for c in candles:
                ts = c[0]
                # OKX时间戳可能是ISO格式或毫秒戳，统一处理
                if isinstance(ts, (int, float)):
                    from datetime import datetime
                    dt = datetime.fromtimestamp(ts / 1000)
                    date_str = dt.strftime('%Y%m%d')
                else:
                    date_str = str(ts).replace('-', '').replace('T', '').replace(':', '')[:8]
                    if not date_str or len(date_str) < 8:
                        date_str = str(len(kline_rows))
                kline_rows.append({
                    'trade_date': date_str,
                    'open': float(c[1]),
                    'high': float(c[2]),
                    'low': float(c[3]),
                    'close': float(c[4]),
                    'vol': float(c[5]),
                })

            summary = summarize_chanlun_factors(kline_rows, big_yang_threshold=0.03)
            signals = summary.get('signals', [])
            structure = summary.get('structure') or {}
            best_long = summary.get('best_long_signal')
            best_short = summary.get('best_short_signal')
            bull_div = summary.get('bullish_divergence')

            if best_long:
                strength = 4 if best_long.get('confidence', 0) > 75 else 3
                confidence = round(best_long.get('confidence', 70))
                reason = f"缠论{best_long.get('label', best_long.get('stype'))}: {best_long.get('reason', '')}"
                if bull_div:
                    strength = 5
                    confidence = max(confidence, bull_div['confidence'])
                    reason += f" | {bull_div['reason']}"
                return {
                    "signal": "BULLISH",
                    "strength": strength,
                    "confidence": confidence,
                    "reason": reason,
                    "current_price": best_long.get('price', kline_rows[-1]['close']),
                    "chanlun_signals": signals,
                    "structure": {
                        "fenxings": len(structure.get('fenxings', [])),
                        "bis": len(structure.get('bis', [])),
                        "xianduans": len(structure.get('xianduans', [])),
                        "zhongshus": len(structure.get('zhongshus', [])),
                    },
                }

            if best_short:
                return {
                    "signal": "BEARISH",
                    "strength": 3 if best_short.get('confidence', 0) < 70 else 4,
                    "confidence": round(best_short.get('confidence', 70)),
                    "reason": best_short['reason'],
                    "current_price": kline_rows[-1]['close'],
                    "chanlun_signals": signals,
                    "structure": {
                        "fenxings": len(structure.get('fenxings', [])),
                        "bis": len(structure.get('bis', [])),
                        "xianduans": len(structure.get('xianduans', [])),
                        "zhongshus": len(structure.get('zhongshus', [])),
                    },
                }

            return {"signal": "NEUTRAL", "reason": "未检测到明显缠论多空因子"}

        except Exception as e:
            print(f"背驰扫描失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def scan_macro_signal(self) -> Optional[dict]:
        """扫描宏观过滤器信号"""
        if not self._macro_filter:
            return None
        
        try:
            params = self._macro_filter.get_trading_params()
            return {
                "signal": "FILTER",
                "risk_level": self._macro_filter.risk_level,
                "max_positions": params.get("max_positions", 4),
                "position_pct": params.get("position_pct", 0.20),
                "reason": f"当前宏观风险等级: {self._macro_filter.risk_level}",
            }
        except Exception as e:
            print(f"宏观扫描失败: {e}")
            return None
    
    async def scan_news_signal(self, symbol: str) -> Optional[dict]:
        """扫描新闻因子信号"""
        try:
            from app.db.base import AsyncSessionLocal
            from app.models.news_factor import NewsFactor
            from sqlalchemy import select, and_

            coin = symbol.split("-")[0]
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)
                cutoff = now - timedelta(hours=24)
                result = await db.execute(
                    select(NewsFactor).where(
                        and_(
                            NewsFactor.symbol == coin,
                            NewsFactor.created_at > cutoff,
                            NewsFactor.expires_at > now,
                        )
                    ).order_by(NewsFactor.created_at.desc()).limit(10)
                )
                items = result.scalars().all()

            if not items:
                return None

            net_strength = sum(i.strength for i in items)
            bullish_count = sum(1 for i in items if i.direction == "bullish")
            bearish_count = sum(1 for i in items if i.direction == "bearish")
            dominant_dim = max(set(i.dimension for i in items), key=lambda d: sum(1 for i in items if i.dimension == d))

            direction = "bullish" if net_strength > 0 else ("bearish" if net_strength < 0 else "neutral")
            strength = min(5, abs(net_strength))

            return {
                "signal": direction.upper(),
                "strength": strength,
                "net_strength": net_strength,
                "bullish_count": bullish_count,
                "bearish_count": bearish_count,
                "dominant_dimension": dominant_dim,
                "recent_count": len(items),
                "reason": f"新闻{direction}|净强度{net_strength}|主导维度{dominant_dim}",
                "items": [
                    {
                        "title": i.title[:80],
                        "source": i.source,
                        "direction": i.direction,
                        "strength": i.strength,
                        "dimension": i.dimension,
                        "created_at": i.created_at.isoformat() if i.created_at else None,
                    }
                    for i in items[:5]
                ],
            }
        except Exception as e:
            print(f"新闻信号扫描失败: {e}")
            return None

    async def scan_all_signals(self, symbol: str) -> dict:
        """扫描所有信号"""
        liq = await self.scan_liquidation_signal(symbol)
        div = await self.scan_divergence_signal(symbol)
        macro = await self.scan_macro_signal()
        news = await self.scan_news_signal(symbol)

        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "liquidation": liq,
            "divergence": div,
            "macro": macro,
            "news": news,
        }


signal_service = SignalService()
