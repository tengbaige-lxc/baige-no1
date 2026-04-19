import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime, timezone
from app.db.base import AsyncSessionLocal
from app.models.trading_strategy import TradingStrategy
from app.models.strategy_log import StrategyLog
from app.models.exchange_config import ExchangeConfig
from app.services.okx_client import okx_manager, decrypt_text
from app.services.trade_service import trade_service


class StrategyEngine:
    """Automated trading strategy engine."""
    
    def __init__(self):
        self._running = False
        self._task = None
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        print("✅ 策略引擎已启动")
    
    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        print("🛑 策略引擎已停止")
    
    async def _run_loop(self):
        while self._running:
            try:
                async with AsyncSessionLocal() as db:
                    await self._process_strategies(db)
            except Exception as e:
                print(f"策略引擎错误: {e}")
            await asyncio.sleep(10)
    
    async def _process_strategies(self, db):
        result = await db.execute(
            select(TradingStrategy).where(TradingStrategy.is_active == True)
        )
        strategies = result.scalars().all()
        
        for strategy in strategies:
            if strategy.last_run_at:
                elapsed = (datetime.now(timezone.utc) - strategy.last_run_at).total_seconds()
                if elapsed < strategy.interval_seconds:
                    continue
            
            await self._execute_strategy(db, strategy)
    
    async def _execute_strategy(self, db: AsyncSession, strategy: TradingStrategy):
        config_result = await db.execute(
            select(ExchangeConfig).where(ExchangeConfig.is_active == True)
        )
        config = config_result.scalar_one_or_none()
        if not config:
            return
        
        try:
            symbol = strategy.symbol
            if "-" not in symbol:
                symbol = symbol[:-4] + "-USDT" if symbol.endswith("USDT") else symbol + "-USDT"
            
            ticker = await okx_manager.get_ticker(symbol)
            current_price = float(ticker.get("last", 0))
            
            signal = "HOLD"
            reason = "暂无信号"
            
            if strategy.strategy_type == "grid":
                signal, reason = self._grid_strategy(strategy, current_price)
            elif strategy.strategy_type == "rsi":
                signal, reason = await self._rsi_strategy(strategy, current_price, symbol)
            elif strategy.strategy_type == "ma_cross":
                signal, reason = await self._ma_cross_strategy(strategy, current_price, symbol)
            
            if signal in ("BUY", "SELL"):
                await self._execute_trade(db, strategy, config, signal, current_price)
            
            strategy.last_run_at = datetime.now(timezone.utc)
            strategy.last_signal = signal
            
            log = StrategyLog(
                strategy_id=strategy.id,
                user_id=strategy.user_id,
                symbol=strategy.symbol,
                signal=signal,
                price=current_price,
                reason=reason,
            )
            db.add(log)
            await db.commit()
            
        except Exception as e:
            print(f"策略 {strategy.name} 执行失败: {e}")
    
    def _grid_strategy(self, strategy: TradingStrategy, current_price: float) -> tuple:
        params = strategy.params or {}
        grid_low = params.get("grid_low", current_price * 0.95)
        grid_high = params.get("grid_high", current_price * 1.05)
        
        if current_price <= grid_low:
            return "BUY", f"价格 {current_price} 触及网格下限 {grid_low}"
        elif current_price >= grid_high:
            return "SELL", f"价格 {current_price} 触及网格上限 {grid_high}"
        return "HOLD", f"价格 {current_price} 在网格范围内"
    
    async def _rsi_strategy(self, strategy, current_price, symbol):
        klines = await okx_manager.get_candles(symbol, "1H", 15)
        if len(klines) < 14:
            return "HOLD", "数据不足"
        
        closes = [float(k[4]) for k in klines]
        rsi = self._calculate_rsi(closes)
        
        params = strategy.params or {}
        oversold = params.get("oversold", 30)
        overbought = params.get("overbought", 70)
        
        if rsi < oversold:
            return "BUY", f"RSI {rsi:.2f} 超卖 (< {oversold})"
        elif rsi > overbought:
            return "SELL", f"RSI {rsi:.2f} 超买 (> {overbought})"
        return "HOLD", f"RSI {rsi:.2f} 中性"
    
    async def _ma_cross_strategy(self, strategy, current_price, symbol):
        klines = await okx_manager.get_candles(symbol, "1H", 50)
        if len(klines) < 20:
            return "HOLD", "数据不足"
        
        closes = [float(k[4]) for k in klines]
        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20
        
        if ma5 > ma20 and strategy.last_signal != "BUY":
            return "BUY", f"MA5({ma5:.2f}) 上穿 MA20({ma20:.2f})"
        elif ma5 < ma20 and strategy.last_signal != "SELL":
            return "SELL", f"MA5({ma5:.2f}) 下穿 MA20({ma20:.2f})"
        return "HOLD", f"MA5({ma5:.2f}) vs MA20({ma20:.2f})"
    
    def _calculate_rsi(self, prices, period=14):
        gains = []
        losses = []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i-1]
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
    
    async def _execute_trade(self, db, strategy, config, signal, price):
        params = strategy.params or {}
        quantity = params.get("quantity", 0.001)
        
        try:
            from app.schemas.trading import OrderCreate
            order_data = OrderCreate(
                symbol=strategy.symbol,
                side=signal,
                order_type="MARKET",
                quantity=quantity,
                market_type=strategy.market_type,
                remark=f"策略自动下单: {strategy.name}",
            )
            await trade_service.place_order(db, strategy.user_id, config, order_data)
            strategy.total_trades += 1
        except Exception as e:
            print(f"策略下单失败: {e}")


strategy_engine = StrategyEngine()
