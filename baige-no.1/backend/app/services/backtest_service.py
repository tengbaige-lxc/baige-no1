"""
回测服务
基于OKX历史K线数据模拟交易
"""
import httpx
from typing import List, Dict, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models.backtest import BacktestRun, BacktestTrade, BacktestStatus
from app.services.okx_client import okx_manager


async def fetch_historical_candles(symbol: str, bar: str = "4H", days: int = 30) -> List[dict]:
    """获取历史K线数据"""
    all_candles = []
    needed = days * 6 if bar == "4H" else days * 24 if bar == "1H" else days
    fetched = 0
    before_ts = ""
    
    while fetched < needed:
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={symbol}&bar={bar}&limit=100"
        if before_ts:
            url += f"&before={before_ts}"
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                data = resp.json()
                
                if data.get("code") == "0" and data.get("data"):
                    candles = data["data"]
                    if len(candles) == 0:
                        break
                    
                    for c in candles:
                        all_candles.append({
                            "timestamp": datetime.fromtimestamp(int(c[0]) / 1000, tz=timezone.utc),
                            "open": float(c[1]),
                            "high": float(c[2]),
                            "low": float(c[3]),
                            "close": float(c[4]),
                            "volume": float(c[5]),
                        })
                    
                    before_ts = candles[-1][0]
                    fetched += len(candles)
                else:
                    break
        except Exception as e:
            print(f"获取历史数据失败: {e}")
            break
    
    # 按时间正序排列
    all_candles.reverse()
    return all_candles


def calculate_ema(data: List[float], period: int) -> float:
    multiplier = 2 / (period + 1)
    ema = data[0]
    for price in data[1:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calculate_macd(closes: List[float], fast=12, slow=26, signal=9) -> tuple:
    if len(closes) < slow:
        return None, None, None
    ema_fast = calculate_ema(closes[-fast:], fast)
    ema_slow = calculate_ema(closes[-slow:], slow)
    macd = ema_fast - ema_slow
    
    macd_series = []
    for i in range(signal, 0, -1):
        if len(closes) >= slow + i:
            e1 = calculate_ema(closes[-(fast+i):-i], fast)
            e2 = calculate_ema(closes[-(slow+i):-i], slow)
            macd_series.append(e1 - e2)
    
    signal_line = sum(macd_series[-signal:]) / signal if len(macd_series) >= signal else macd
    histogram = macd - signal_line
    return macd, signal_line, histogram


def check_divergence(candles: List[dict]) -> bool:
    """检查底背离 - 简化版"""
    if len(candles) < 20:
        return False
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    
    last_5_low = min(lows[-5:])
    prev_5_low = min(lows[-10:-5]) if len(lows) >= 10 else last_5_low
    price_lower = last_5_low < prev_5_low * 0.99
    
    last_3_close = closes[-3:]
    is_dropping = last_3_close[0] > last_3_close[1] > last_3_close[2]
    return price_lower and not is_dropping


def check_ma_cross(candles: List[dict]) -> bool:
    """检查均线金叉"""
    if len(candles) < 15:
        return False
    closes = [c["close"] for c in candles]
    ma5_now = sum(closes[-5:]) / 5
    ma10_now = sum(closes[-10:]) / 10
    ma5_prev = sum(closes[-6:-1]) / 5
    ma10_prev = sum(closes[-11:-1]) / 10
    return ma5_now > ma10_now and ma5_prev <= ma10_prev


def check_rebound(candles: List[dict], threshold=0.015) -> bool:
    """检查反弹信号"""
    if len(candles) < 10:
        return False
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    recent_low = min(lows[-10:])
    current = closes[-1]
    rebound_pct = (current - recent_low) / recent_low
    return rebound_pct >= threshold


class BacktestService:
    """回测服务"""
    
    async def run_backtest(self, db: AsyncSession, backtest_id: int):
        """执行回测任务"""
        result = await db.execute(select(BacktestRun).where(BacktestRun.id == backtest_id))
        run = result.scalar_one_or_none()
        if not run:
            return
        
        run.status = BacktestStatus.RUNNING.value
        await db.commit()
        
        try:
            # 获取历史数据
            candles = await fetch_historical_candles(run.symbol, "4H", run.days)
            if len(candles) < 50:
                raise ValueError(f"历史数据不足: 仅获取 {len(candles)} 根K线")
            
            # 模拟交易
            trades = self._simulate_trades(candles, run.strategy_type, run.leverage, run.position_percent)
            
            # 保存交易记录
            for t in trades:
                db_trade = BacktestTrade(
                    backtest_id=run.id,
                    symbol=run.symbol,
                    signal_type=run.strategy_type,
                    entry_price=t["entry_price"],
                    exit_price=t.get("exit_price"),
                    position_size=t["position_size"],
                    pnl_usdt=t.get("pnl_usdt"),
                    pnl_percent=t.get("pnl_percent"),
                    hold_hours=t.get("hold_hours"),
                    entry_time=t.get("entry_time"),
                    exit_time=t.get("exit_time"),
                    reason=t.get("reason"),
                )
                db.add(db_trade)
            
            # 计算统计指标
            closed_trades = [t for t in trades if t.get("pnl_usdt") is not None]
            total = len(closed_trades)
            wins = sum(1 for t in closed_trades if t["pnl_usdt"] > 0)
            losses = total - wins
            
            total_pnl = sum(t["pnl_usdt"] for t in closed_trades) if closed_trades else 0
            avg_pnl = total_pnl / total if total > 0 else 0
            
            gross_profit = sum(t["pnl_usdt"] for t in closed_trades if t["pnl_usdt"] > 0)
            gross_loss = abs(sum(t["pnl_usdt"] for t in closed_trades if t["pnl_usdt"] < 0))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
            
            max_dd = min((t["pnl_usdt"] for t in closed_trades), default=0)
            
            run.status = BacktestStatus.COMPLETED.value
            run.total_signals = total
            run.win_count = wins
            run.loss_count = losses
            run.win_rate = round(wins / total * 100, 2) if total > 0 else 0
            run.total_return = round(total_pnl, 2)
            run.avg_return = round(avg_pnl, 2)
            run.max_drawdown = round(max_dd, 2)
            run.profit_factor = round(profit_factor, 2)
            run.completed_at = datetime.now(timezone.utc)
            
            await db.commit()
            
        except Exception as e:
            run.status = BacktestStatus.FAILED.value
            run.error_msg = str(e)
            await db.commit()
            print(f"回测失败: {e}")
    
    def _simulate_trades(self, candles: List[dict], strategy_type: str, leverage: int, position_pct: float) -> List[dict]:
        """模拟交易"""
        trades = []
        in_position = False
        entry_candle = None
        
        # 模拟账户
        balance = 10000.0
        
        for i in range(30, len(candles)):
            window = candles[:i+1]
            current = candles[i]
            
            if not in_position:
                # 检测开仓信号
                signal = False
                reason = ""
                
                if strategy_type == "divergence":
                    signal = check_divergence(window)
                    reason = "底背离信号"
                elif strategy_type == "ma_cross":
                    signal = check_ma_cross(window)
                    reason = "均线金叉"
                elif strategy_type == "rebound":
                    signal = check_rebound(window)
                    reason = "反弹信号"
                elif strategy_type == "grid":
                    # 网格策略：每跌5%买入
                    if i > 0 and (candles[i-1]["close"] - current["close"]) / candles[i-1]["close"] > 0.05:
                        signal = True
                        reason = "网格下跌买入"
                
                if signal:
                    position_size = (balance * position_pct * leverage) / current["close"]
                    entry_candle = {
                        "index": i,
                        "price": current["close"],
                        "size": position_size,
                        "time": current["timestamp"],
                        "reason": reason,
                    }
                    in_position = True
            
            else:
                # 检测平仓信号
                entry_price = entry_candle["price"]
                current_price = current["close"]
                pnl_pct = (current_price - entry_price) / entry_price
                
                # 止盈止损
                take_profit = 0.08  # 8%
                stop_loss = -0.04   # 4%
                max_hold = 48       # 48根4H = 8天
                
                exit_reason = None
                if pnl_pct >= take_profit:
                    exit_reason = f"止盈 {pnl_pct*100:.1f}%"
                elif pnl_pct <= stop_loss:
                    exit_reason = f"止损 {pnl_pct*100:.1f}%"
                elif i - entry_candle["index"] >= max_hold:
                    exit_reason = "持仓超时"
                
                if exit_reason:
                    pnl_usdt = entry_candle["size"] * (current_price - entry_price)
                    hold_hours = (i - entry_candle["index"]) * 4
                    
                    trades.append({
                        "entry_price": entry_price,
                        "exit_price": current_price,
                        "position_size": entry_candle["size"],
                        "pnl_usdt": round(pnl_usdt, 2),
                        "pnl_percent": round(pnl_pct * 100, 2),
                        "hold_hours": hold_hours,
                        "entry_time": entry_candle["time"],
                        "exit_time": current["timestamp"],
                        "reason": f"{entry_candle['reason']} -> {exit_reason}",
                    })
                    in_position = False
                    entry_candle = None
        
        # 如果还有未平仓的，按最后价格平仓
        if in_position and entry_candle:
            current_price = candles[-1]["close"]
            entry_price = entry_candle["price"]
            pnl_usdt = entry_candle["size"] * (current_price - entry_price)
            pnl_pct = (current_price - entry_price) / entry_price
            hold_hours = (len(candles) - 1 - entry_candle["index"]) * 4
            
            trades.append({
                "entry_price": entry_price,
                "exit_price": current_price,
                "position_size": entry_candle["size"],
                "pnl_usdt": round(pnl_usdt, 2),
                "pnl_percent": round(pnl_pct * 100, 2),
                "hold_hours": hold_hours,
                "entry_time": entry_candle["time"],
                "exit_time": candles[-1]["timestamp"],
                "reason": f"{entry_candle['reason']} -> 回测结束平仓",
            })
        
        return trades
    
    async def get_backtest_detail(self, db: AsyncSession, backtest_id: int, user_id: int) -> dict:
        """获取回测详情"""
        result = await db.execute(
            select(BacktestRun).where(
                BacktestRun.id == backtest_id,
                BacktestRun.user_id == user_id
            )
        )
        backtest = result.scalar_one_or_none()
        if not backtest:
            return None
        
        trades_result = await db.execute(
            select(BacktestTrade).where(BacktestTrade.backtest_id == backtest_id).order_by(BacktestTrade.entry_time)
        )
        trades = trades_result.scalars().all()
        
        return {
            "backtest": backtest,
            "trades": trades,
        }


backtest_service = BacktestService()
