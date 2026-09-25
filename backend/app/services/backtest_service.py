"""
回测服务
基于OKX历史K线数据模拟交易
"""
import asyncio
import functools

import httpx
from typing import List, Dict, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

import itertools
from app.models.backtest import BacktestRun, BacktestTrade, BacktestStatus
from app.services.chanlun_bridge import analyze_klines, calc_macd
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
    """检查底背离 - 已替换为缠论核心分析"""
    if len(candles) < 34:
        return False

    # 转换为新包格式
    rows = []
    for i, c in enumerate(candles):
        rows.append({
            'trade_date': str(i),
            'open': float(c.get('open', 0)),
            'high': float(c.get('high', 0)),
            'low': float(c.get('low', 0)),
            'close': float(c.get('close', 0)),
            'vol': float(c.get('volume', c.get('vol', 0))),
        })

    result = analyze_klines(rows, big_yang_threshold=0.03)
    signals = result.get('signals', [])

    # 如果有任何买点信号，认为存在底背离/买点机会
    buy_types = {'jin1_std', 'jin1_ext', 'jin2_approx', 'shou1', 'shou2'}
    for s in signals:
        if s.get('stype') in buy_types:
            return True

    # 没有买点，回退到传统MACD背驰检测
    closes = [float(c['close']) for c in candles]
    dif, dea, hist = calc_macd(closes)

    lows = [c["low"] for c in candles]
    last_5_low = min(lows[-5:])
    prev_5_low = min(lows[-10:-5]) if len(lows) >= 10 else last_5_low
    price_lower = last_5_low < prev_5_low * 0.99

    if price_lower and len(hist) >= 2:
        # 简单检查：价格新低但MACD柱缩小
        return hist[-1] > hist[-5] if len(hist) >= 5 else hist[-1] > hist[-2]

    return False


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


def calculate_rsi_sync(closes: List[float], period=14) -> float:
    """同步计算RSI"""
    if len(closes) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def check_bollinger(candles: List[dict]) -> bool:
    """检查布林带下轨"""
    if len(candles) < 20:
        return False
    closes = [c["close"] for c in candles]
    ma20 = sum(closes[-20:]) / 20
    std = (sum((x - ma20) ** 2 for x in closes[-20:]) / 20) ** 0.5
    lower = ma20 - 2 * std
    return closes[-1] <= lower


def check_macd_cross(closes: List[float]) -> bool:
    """检查MACD金叉"""
    if len(closes) < 35:
        return False
    def ema(data, period):
        mult = 2 / (period + 1)
        result = [data[0]]
        for p in data[1:]:
            result.append(p * mult + result[-1] * (1 - mult))
        return result
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    if len(macd_line) < 2 or len(signal_line) < 2:
        return False
    return macd_line[-2] <= signal_line[-2] and macd_line[-1] > signal_line[-1]


def check_volume_spike(candles: List[dict], multiplier=1.5) -> bool:
    """检查成交量放量"""
    if len(candles) < 20:
        return False
    volumes = [c["volume"] for c in candles]
    avg_vol = sum(volumes[-20:]) / 20
    return volumes[-1] >= avg_vol * multiplier


def check_liquidation_signal(candles: List[dict], min_strength: int = 2) -> bool:
    """回测简化版清算热力图信号：基于价格剧烈波动"""
    if len(candles) < 24:
        return False
    highs = [c["high"] for c in candles[-24:]]
    lows = [c["low"] for c in candles[-24:]]
    range_pct = (max(highs) - min(lows)) / max(highs) if max(highs) > 0 else 0
    # 24h 内波动超过 4% 认为有清算压力
    threshold = 0.02 + min_strength * 0.015
    return range_pct >= threshold


def check_macro_risk(candles: List[dict], current_positions: int = 0) -> tuple:
    """回测简化版宏观风险：基于波动率和持仓数"""
    if len(candles) < 20:
        return False, "数据不足"
    closes = [c["close"] for c in candles[-20:]]
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    volatility = (sum(r**2 for r in returns) / len(returns)) ** 0.5
    # 波动率过高认为有风险
    if volatility > 0.05:
        return True, "波动率过高"
    if current_positions >= 5:
        return True, "持仓数已达上限"
    return False, ""


def evaluate_factors_backtest(candles: List[dict], current_price: float, factors_config: dict):
    """回测专用因子评估（同步版）"""
    score = 0.0
    results = []
    enabled_flags = []
    
    # 0. 宏观过滤（硬 gate，weight=0 时阻断但不计分）
    macro_cfg = factors_config.get("macro_filter", {})
    macro_enabled = macro_cfg.get("enabled", False)
    macro_blocked = False
    macro_reason = ""
    if macro_enabled:
        macro_blocked, macro_reason = check_macro_risk(
            candles, macro_cfg.get("current_positions", 0)
        )
        if macro_blocked:
            return 0, [{"name": "宏观过滤", "triggered": False, "score_added": 0, "detail": f"阻断: {macro_reason}"}], []
        results.append({"name": "宏观过滤", "triggered": True, "score_added": 0, "detail": "宏观通过"})
    
    # 1. 缠论背驰
    div_cfg = factors_config.get("divergence", {})
    if div_cfg.get("enabled", False):
        triggered = check_divergence(candles)
        w = div_cfg.get("weight", 2)
        if triggered:
            score += w
        results.append({"name": "缠论背驰", "triggered": triggered, "score_added": w if triggered else 0})
        enabled_flags.append(triggered)
    
    # 2. 均线金叉
    ma_cfg = factors_config.get("ma_cross", {})
    if ma_cfg.get("enabled", False):
        triggered = check_ma_cross(candles)
        w = ma_cfg.get("weight", 1)
        if triggered:
            score += w
        results.append({"name": "均线金叉", "triggered": triggered, "score_added": w if triggered else 0})
        enabled_flags.append(triggered)
    
    # 3. RSI超卖
    rsi_cfg = factors_config.get("rsi", {})
    if rsi_cfg.get("enabled", False):
        closes = [c["close"] for c in candles]
        rsi = calculate_rsi_sync(closes)
        triggered = rsi < rsi_cfg.get("oversold", 30)
        w = rsi_cfg.get("weight", 1)
        if triggered:
            score += w
        results.append({"name": "RSI超卖", "triggered": triggered, "score_added": w if triggered else 0, "raw": {"rsi": round(rsi, 2)}})
        enabled_flags.append(triggered)
    
    # 4. 反弹检测
    reb_cfg = factors_config.get("rebound", {})
    if reb_cfg.get("enabled", False):
        triggered = check_rebound(candles, reb_cfg.get("threshold", 0.03))
        w = reb_cfg.get("weight", 1)
        if triggered:
            score += w
        results.append({"name": "24h反弹", "triggered": triggered, "score_added": w if triggered else 0})
        enabled_flags.append(triggered)
    
    # 5. 布林带
    boll_cfg = factors_config.get("bollinger", {})
    if boll_cfg.get("enabled", False):
        triggered = check_bollinger(candles)
        w = boll_cfg.get("weight", 1)
        if triggered:
            score += w
        results.append({"name": "布林带下轨", "triggered": triggered, "score_added": w if triggered else 0})
        enabled_flags.append(triggered)
    
    # 6. MACD金叉
    macd_cfg = factors_config.get("macd_cross", {})
    if macd_cfg.get("enabled", False):
        closes = [c["close"] for c in candles]
        triggered = check_macd_cross(closes)
        w = macd_cfg.get("weight", 1)
        if triggered:
            score += w
        results.append({"name": "MACD金叉", "triggered": triggered, "score_added": w if triggered else 0})
        enabled_flags.append(triggered)
    
    # 7. 成交量放量
    vol_cfg = factors_config.get("volume_spike", {})
    if vol_cfg.get("enabled", False):
        triggered = check_volume_spike(candles, vol_cfg.get("multiplier", 1.5))
        w = vol_cfg.get("weight", 1)
        if triggered:
            score += w
        results.append({"name": "成交量放量", "triggered": triggered, "score_added": w if triggered else 0})
        enabled_flags.append(triggered)
    
    # 8. 清算热力图
    liq_cfg = factors_config.get("liquidation_map", {})
    if liq_cfg.get("enabled", False):
        triggered = check_liquidation_signal(candles, liq_cfg.get("min_strength", 2))
        w = liq_cfg.get("weight", 2)
        if triggered:
            score += w
        results.append({"name": "清算热力图", "triggered": triggered, "score_added": w if triggered else 0})
        enabled_flags.append(triggered)
    
    return score, results, enabled_flags


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
            trades = self._simulate_trades(candles, run.strategy_type, run.leverage, run.position_percent, run.params)
            
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
    
    def _simulate_trades(self, candles: List[dict], strategy_type: str, leverage: int, position_pct: float, params: dict = None) -> List[dict]:
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
                elif strategy_type in ("multi_factor", "white_dove"):
                    factors_config = params.get("factors", {}) if params else {}
                    logic_mode = params.get("logic_mode", "weighted_sum") if params else "weighted_sum"
                    min_score = params.get("min_score", 5) if params else 5
                    
                    score, _, enabled_flags = evaluate_factors_backtest(
                        window, current["close"], factors_config
                    )
                    
                    if logic_mode == "all_required":
                        signal = enabled_flags and all(enabled_flags)
                        reason = f"多因子全部满足(得分{score:.1f})"
                    elif logic_mode == "any_one":
                        signal = any(enabled_flags)
                        reason = f"多因子任一满足(得分{score:.1f})"
                    else:
                        signal = score >= min_score
                        reason = f"多因子加权{score:.1f}/{min_score}"
                
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
    
    async def optimize_weights(self, symbol: str, strategy_type: str, days: int, leverage: int, position_pct: float, params: dict, metric: str = "total_return") -> dict:
        """权重自动优化 - 网格搜索最优权重组合"""
        candles = await fetch_historical_candles(symbol, "4H", days)
        if len(candles) < 50:
            raise ValueError(f"历史数据不足: 仅获取 {len(candles)} 根K线")

        factors_config = params.get("factors", {})
        enabled_factors = [k for k, v in factors_config.items() if v.get("enabled", False) and k != "macro_filter"]

        if len(enabled_factors) == 0:
            raise ValueError("没有启用的因子可供优化")

        if len(enabled_factors) > 6:
            enabled_factors = enabled_factors[:6]  # 限制优化数量

        # 往下是纯 CPU：6^6=46656 组网格，每组跑一次完整回测。此前直接跑在事件循环上，
        # 期间引擎的软件止损轮询、离场检查、wait_for 计时器全部停摆——任意登录用户点
        # 一次"权重优化"就能冻结事件循环数分钟（Phase 2.5d / E14）。
        # 丢进线程池后 CPython 每 ~5ms 释放一次 GIL，事件循环仍能按节拍推进（会变慢，
        # 但不再冻结）——这正是"止损停摆数分钟"与"止损略有延迟"的区别。
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(
                self._optimize_weights_blocking,
                candles, strategy_type, leverage, position_pct,
                params, factors_config, enabled_factors, metric,
            ),
        )

    def _optimize_weights_blocking(
        self, candles, strategy_type: str, leverage: int, position_pct: float,
        params: dict, factors_config: dict, enabled_factors: list, metric: str,
    ) -> dict:
        """optimize_weights 的纯 CPU 部分。**必须在 executor 里跑**，不得占用事件循环。"""

        def _eval_metrics(trades):
            closed = [t for t in trades if t.get("pnl_usdt") is not None]
            if not closed:
                return None
            total_pnl = sum(t["pnl_usdt"] for t in closed)
            wins = sum(1 for t in closed if t["pnl_usdt"] > 0)
            win_rate = wins / len(closed) * 100
            gross_profit = sum(t["pnl_usdt"] for t in closed if t["pnl_usdt"] > 0)
            gross_loss = abs(sum(t["pnl_usdt"] for t in closed if t["pnl_usdt"] < 0))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
            return {
                "total_return": round(total_pnl, 2),
                "win_rate": round(win_rate, 2),
                "profit_factor": round(profit_factor, 2),
                "total_trades": len(closed),
                "win_count": wins,
                "loss_count": len(closed) - wins,
            }
        
        # 基准回测（使用原始权重）
        baseline_trades = self._simulate_trades(candles, strategy_type, leverage, position_pct, params)
        baseline_metrics = _eval_metrics(baseline_trades)
        
        weight_candidates = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
        best_score = float('-inf')
        best_metrics = None
        best_weights = None
        tested = 0
        
        # 生成所有权重组合
        for weights in itertools.product(weight_candidates, repeat=len(enabled_factors)):
            test_config = {k: dict(v) for k, v in factors_config.items()}
            for i, key in enumerate(enabled_factors):
                test_config[key]["weight"] = weights[i]
            
            test_params = dict(params)
            test_params["factors"] = test_config
            
            trades = self._simulate_trades(candles, strategy_type, leverage, position_pct, test_params)
            metrics = _eval_metrics(trades)
            if not metrics:
                continue
            
            if metric == "win_rate":
                score = metrics["win_rate"]
            elif metric == "profit_factor":
                score = metrics["profit_factor"] * 10
            else:
                score = metrics["total_return"]
            
            if score > best_score:
                best_score = score
                best_metrics = metrics
                best_weights = {k: test_config[k]["weight"] for k in enabled_factors}
            
            tested += 1
        
        # 构造完整权重表（包含未优化的因子）
        optimized_weights = {}
        for k, v in factors_config.items():
            optimized_weights[k] = best_weights.get(k, v.get("weight", 0)) if best_weights else v.get("weight", 0)
        
        return {
            "tested_combinations": tested,
            "baseline_metrics": baseline_metrics or {"total_return": 0, "win_rate": 0, "profit_factor": 0, "total_trades": 0},
            "optimized_metrics": best_metrics or {"total_return": 0, "win_rate": 0, "profit_factor": 0, "total_trades": 0},
            "optimized_weights": optimized_weights,
            "metric": metric,
        }


backtest_service = BacktestService()
