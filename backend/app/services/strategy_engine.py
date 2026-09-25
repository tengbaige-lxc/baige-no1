import asyncio
import json
import math
import logging
import statistics
import sys
import time
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text
from datetime import datetime, timezone, timedelta
from app.db.base import AsyncSessionLocal
from app.models.news_factor import NewsFactor
from app.models.message import Message, MessageStatus
from app.models.trading_strategy import TradingStrategy
from app.models.strategy_log import StrategyLog
from app.models.exchange_config import ExchangeConfig
from app.models.trade_record import TradeRecord
from app.services.okx_client import (
    NATIVE_STOP_ALGO_PREFIX,
    OkxTransientNetworkError,
    decrypt_text,
    okx_manager,
)
from app.services.contract_specs import get_static_ct_val
from app.services.monitor_service import monitor_service
from app.services.trade_service import trade_service
from app.services.exit_policy import (
    TrailingPositionState,
    cap_trend_runner_reduce_quantity,
    evaluate_extreme_volume_followthrough,
    filter_confirmed_klines,
    is_confirmed_third_sell_exit,
    is_tighter_profit_floor,
    resolve_profit_lock_price,
    resolve_runner_add_stop_price,
    resolve_trailing_callback,
    resolve_trailing_profit_lock_ratio,
    resolve_trendline_break_signal,
)
from app.services.position_sizing import (
    classify_transition_replacement,
    is_replaceable_transition_tail,
    resolve_directional_ma_extension,
    resolve_global_entry_allocation,
    rotation_score_gap,
    rotation_weak_closed_bars,
    transition_slot_allows,
)
from app.services.trading_execution import (
    NativeStopRequest,
    OpenPositionRequest,
    ReducePositionRequest,
    TradingExecutionGateway,
)
from app.services.feishu_notify import feishu_openclaw_notifier
from app.services.chanlun_bridge import calc_macd, summarize_chanlun_factors, detect_black_candle
from app.services.liq_updater import LiqDataUpdater
from app.services.derivatives_market_data import derivatives_market_data
from app.services.market_regime import evaluate_adx_atr_regime
from app.services.signal_quality import confirmed_candles, assess_signal_quality, entry_timing
from app.services.moer_structure import (
    evaluate_moer_long_structure,
    evaluate_moer_short_structure,
)
from app.services.trend_v3 import Action as TrendV3Action
from app.services.trend_v3 import Direction as TrendV3Direction
from app.services.trend_v3 import (
    TrendContext,
    TrendV3,
    has_structural_confirmation,
    oil_fundamental_applies_to,
)
from app.services.macro_event_radar import load_active_event_context
from app.services.news_feedback import check_news_accuracy, auto_fetch_and_analyze_news

try:
    from app.services.elliott_wave_signal import SignalEngine as ElliottWaveSignalEngine
except Exception:
    ElliottWaveSignalEngine = None


def resolve_trend_v3_5m_trigger(
    directional_move: bool,
    structure_trigger: bool,
    volume_ratio: float,
    speed_ratio: float,
    min_volume_ratio: float,
    min_speed_ratio: float,
    require_volume_speed: bool = False,
) -> tuple[bool, bool]:
    """Return (entry_trigger, volume_speed_trigger) for the 5m timing layer."""
    volume_speed_trigger = bool(
        directional_move
        and volume_ratio >= min_volume_ratio
        and speed_ratio >= min_speed_ratio
    )
    if require_volume_speed:
        return volume_speed_trigger, volume_speed_trigger
    return bool(directional_move and (structure_trigger or volume_speed_trigger)), volume_speed_trigger


def resolve_tradfi_macro_event_bonus(
    params: dict,
    symbol: str,
    direction: str,
) -> dict:
    """Return a temporary score boost for confirmed macro tech rotations only.

    A missing or expired context is deliberately neutral.  The factor cannot
    relax structural gates, reverse a direction, or change a position size.
    """
    config = params.get("macro_event_overlay") or {}
    if not isinstance(config, dict) or not bool(config.get("enabled", False)):
        return {"enabled": False, "triggered": False, "score": 0.0, "detail": "macro_event_overlay_disabled", "raw": {}}
    tech_symbols = {str(item).upper() for item in (config.get("tech_symbols") or [])}
    normalized_symbol = str(symbol).upper()
    if normalized_symbol not in tech_symbols:
        return {"enabled": True, "triggered": False, "score": 0.0, "detail": "macro_event_symbol_not_technology", "raw": {"symbol": normalized_symbol}}
    context_path = str(config.get("context_path") or "/root/baige-no3/backend/data/macro_event_context.json")
    context = load_active_event_context(context_path)
    if not context:
        return {"enabled": True, "triggered": False, "score": 0.0, "detail": "macro_event_context_inactive", "raw": {"context_path": context_path}}
    expected_direction = str(context.get("direction") or "").upper()
    if expected_direction != str(direction).upper():
        return {"enabled": True, "triggered": False, "score": 0.0, "detail": "macro_event_direction_opposes", "raw": context}
    try:
        bonus = max(0.0, min(2.0, float(config.get("score_bonus", 1.0) or 0.0)))
    except (TypeError, ValueError):
        bonus = 0.0
    return {
        "enabled": True,
        "triggered": bonus > 0,
        "score": bonus,
        "detail": f"confirmed_macro_event={context.get('event_id', 'event')} direction={expected_direction}",
        "raw": context,
    }


def oil_fundamental_confidence_is_sufficient(
    confidence: object,
    minimum_confidence: object,
) -> bool:
    try:
        return float(confidence) >= float(minimum_confidence)
    except (TypeError, ValueError):
        return False


def _init_divergence_system():
    """?????????????????"""
    try:
        def _get_divergence_summary(candles: list, timeframe: str = '5m'):
            """????????????????????"""
            if len(candles) < 34:
                return False, None, "K???34??????MA34??"

            rows = []
            for i, c in enumerate(candles):
                ts = c.get('time', str(i))
                if isinstance(ts, (int, float)):
                    from datetime import datetime
                    dt = datetime.fromtimestamp(ts / 1000)
                    date_str = dt.strftime('%Y%m%d')
                else:
                    date_str = str(ts).replace('-', '').replace('T', '').replace(':', '')[:8]
                    if len(date_str) < 8:
                        date_str = str(i)
                rows.append({
                    'trade_date': date_str,
                    'open': float(c.get('open', 0)),
                    'high': float(c.get('high', 0)),
                    'low': float(c.get('low', 0)),
                    'close': float(c.get('close', 0)),
                    'vol': float(c.get('volume', c.get('vol', 0))),
                })

            summary = summarize_chanlun_factors(rows, big_yang_threshold=0.03)
            best_long = summary.get('bullish_divergence') or summary.get('best_long_signal')
            best_short = summary.get('best_short_signal')

            if best_long:
                info = {
                    'type': best_long.get('label', '????'),
                    'direction': 'bullish',
                    'confidence': round(best_long.get('confidence', 70)),
                    'price': best_long.get('price'),
                    'stype': best_long.get('stype'),
                }
                return True, info, f"??{best_long.get('label', best_long.get('stype'))}: {best_long.get('reason', '')}"

            if best_short:
                info = {
                    'type': best_short.get('label', '???'),
                    'direction': 'bearish',
                    'confidence': round(best_short.get('confidence', 70)),
                    'price': rows[-1]['close'],
                    'stype': best_short.get('stype'),
                }
                return True, info, best_short.get('reason', '???')

            return False, None, "??????????"

        def _scan_both_directions(candles: list, timeframe: str = '5m'):
            """??????????????????????????"""
            if len(candles) < 34:
                msg = "K???34??????MA34??"
                return (False, None, msg), (False, None, msg)

            rows = []
            for i, c in enumerate(candles):
                ts = c.get('time', str(i))
                if isinstance(ts, (int, float)):
                    from datetime import datetime
                    dt = datetime.fromtimestamp(ts / 1000)
                    date_str = dt.strftime('%Y%m%d')
                else:
                    date_str = str(ts).replace('-', '').replace('T', '').replace(':', '')[:8]
                    if len(date_str) < 8:
                        date_str = str(i)
                rows.append({
                    'trade_date': date_str,
                    'open': float(c.get('open', 0)),
                    'high': float(c.get('high', 0)),
                    'low': float(c.get('low', 0)),
                    'close': float(c.get('close', 0)),
                    'vol': float(c.get('volume', c.get('vol', 0))),
                })

            summary = summarize_chanlun_factors(rows, big_yang_threshold=0.03)
            best_long = summary.get('bullish_divergence') or summary.get('best_long_signal')
            best_short = summary.get('best_short_signal')

            long_result = (False, None, "??????????")
            short_result = (False, None, "??????????")

            if best_long:
                info = {
                    'type': best_long.get('label', '????'),
                    'direction': 'bullish',
                    'confidence': round(best_long.get('confidence', 70)),
                    'price': best_long.get('price'),
                    'stype': best_long.get('stype'),
                }
                long_result = (True, info, f"??{best_long.get('label', best_long.get('stype'))}: {best_long.get('reason', '')}")

            if best_short:
                info = {
                    'type': best_short.get('label', '???'),
                    'direction': 'bearish',
                    'confidence': round(best_short.get('confidence', 70)),
                    'price': rows[-1]['close'],
                    'stype': best_short.get('stype'),
                }
                short_result = (True, info, best_short.get('reason', '???'))

            return long_result, short_result

        return {
            'get_divergence_summary': _get_divergence_summary,
            'scan_both_directions': _scan_both_directions,
            'check_multi_timeframe': lambda *a, **k: (False, None, "??????????????"),
        }
    except Exception as e:
        print(f"??????????: {e}")
        import traceback
        traceback.print_exc()
        return None


def _init_liq_signal():
    try:
        from app.vendor.liq_signal_module import LiquidationSignal
        liq = LiquidationSignal()
        # ?????????
        liq.update_zones({
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
        return liq
    except Exception as e:
        print(f"??????????: {e}")
        return None


def _init_macro_filter():
    try:
        from app.vendor.macro_filter import MacroFilter
        return MacroFilter()
    except Exception as e:
        print(f"?????????: {e}")
        return None


# 关键失败的降级落点（Phase 2.5f）。本文件其余部分仍是 print——全库 print →
# 结构化日志属 Phase 6，这里只保证"告警送不出去时至少留下 error 级证据"。
logger = logging.getLogger(__name__)


class StrategyEngine:
    """Automated trading strategy engine."""

    MAX_AUTO_LEVERAGE = 125
    MAX_AUTO_POSITION_PERCENT = 1.0
    MAX_AUTO_SYMBOL_MARGIN_PERCENT = 1.0
    MICRO_SCALP_MAX_AUTO_POSITION_PERCENT = 0.15
    MICRO_SCALP_MAX_AUTO_SYMBOL_MARGIN_PERCENT = 0.15
    # 交易所侧兜底止损（docs/05 Phase 2c，2026-07-17 用户确认的距离规则）
    NATIVE_STOP_DEFAULT_PCT = 0.10   # hard_stop 未启用时的兜底距离（价格百分比）
    NATIVE_STOP_MULTIPLIER = 1.5     # 兜底线 = 软件止损 stop_pct × 该系数（正常时软件先触发）
    
    def __init__(self):
        self._running = False
        self._task = None
        self._div_system = _init_divergence_system()
        self._liq_signal = _init_liq_signal()
        self._macro_filter = _init_macro_filter()
        self._pending_trade_quantity = None
        self._pending_trade_context = None
        self._last_trade_quantity = None
        self._last_trade_error = None
        self._order_guard_lock = asyncio.Lock()
        self._execution_gateway = TradingExecutionGateway(
            trade_service,
            self._refresh_native_stop_after_reduce,
        )
        self._live_instrument_ids = set()
        self._live_instrument_ids_by_type: dict[str, set[str]] = {}
        self._instrument_cache_ts = 0
        # ???????????
        self._liq_updater = LiqDataUpdater(self._liq_signal, interval_seconds=3600)
        self._derivatives_market_data = derivatives_market_data
        # Peak state is scoped to one exchange position, not merely a symbol.
        self._trailing_peaks: dict = {}
        # A trailing-stop partial exit is allowed once per live position.  The
        # database check below restores this guard after an engine restart.
        self._trailing_exit_hits: set[str] = set()
        self._trailing_state_schema_ready = False
        self._account_scope_schema_ready = False
        # ????????????????????????????????
        self._profit_tier_hits: set[str] = set()
        # One-time staged divergence exits, scoped to the live position creation time.
        self._divergence_exit_hits: set[str] = set()
        # Entry timing state: first-timeframe divergence must precede confirmation.
        self._divergence_sequence_arms: dict[tuple[int, str, str], float] = {}
        # Large watchlists are scanned in a rotating batch.  This keeps a slow
        # public-data response from starving the entire entry loop.
        self._entry_scan_cursors: dict[int, int] = {}
        # One-off macro-event entries lock to the first confirmed market
        # direction.  A later noisy reversal must not open the opposite side.
        self._event_follow_direction_locks: dict[str, str] = {}
        self._last_cleanup_time = 0
        # ---- 引擎心跳与关键失败可观测（Phase 2.5f）----
        # 此前引擎死亡、兜底止损挂单失败、成交回填失败没有任何一条路径能到达人：
        # 全是 print；唯一告警通道飞书未配置/异常时只 return False，连"告警丢了"
        # 本身都无人知道；引擎死亡后唯一线索在无上限增长的 /tmp/strategy_debug.log。
        self._heartbeat: dict = {"loop_count": 0, "at": None, "started_at": None}
        self._last_trade_ok_at: float | None = None
        # 主循环各段（开仓/离场）各自的最后一次结果——2.5h 解耦后两段独立成败
        self._segment_status: dict[str, dict] = {}
        self._alert_stats: dict = {
            "delivered": 0, "undelivered": 0, "suppressed": 0, "last_undelivered": None,
        }
        self._native_stop_stats: dict = {"placed": 0, "failed": 0}
        self._alert_cooldown: dict[str, float] = {}

    @staticmethod
    def _summarize_exit_divergence(candles: list) -> dict:
        rows = [{
            "trade_date": str(c.get("time", "")),
            "open": float(c.get("open", 0)),
            "high": float(c.get("high", 0)),
            "low": float(c.get("low", 0)),
            "close": float(c.get("close", 0)),
            "vol": float(c.get("volume", c.get("vol", 0))),
        } for c in candles]
        summary = summarize_chanlun_factors(rows, big_yang_threshold=0.03)
        closes = [row["close"] for row in rows]
        dif, dea, hist = calc_macd(closes)
        bearish_momentum = False
        bullish_momentum = False
        if len(hist) >= 3 and dif and dea:
            bearish_momentum = bool(
                dif[-1] < dea[-1] or (hist[-1] < hist[-2] <= hist[-3])
            )
            bullish_momentum = bool(
                dif[-1] > dea[-1] or (hist[-1] > hist[-2] >= hist[-3])
            )
        return {
            "bearish_divergence": summary.get("bearish_divergence"),
            "bullish_divergence": summary.get("bullish_divergence"),
            "third_sell": summary.get("third_sell"),
            "bearish_momentum_confirmed": bearish_momentum,
            "bullish_momentum_confirmed": bullish_momentum,
        }

    def _get_max_auto_position_percent(self, strategy: TradingStrategy) -> float:
        if getattr(strategy, "strategy_type", "") == "micro_scalp":
            return self.MICRO_SCALP_MAX_AUTO_POSITION_PERCENT
        return self.MAX_AUTO_POSITION_PERCENT

    def _get_max_auto_symbol_margin_percent(self, strategy: TradingStrategy) -> float:
        if getattr(strategy, "strategy_type", "") == "micro_scalp":
            return self.MICRO_SCALP_MAX_AUTO_SYMBOL_MARGIN_PERCENT
        return self.MAX_AUTO_SYMBOL_MARGIN_PERCENT

    def _format_strategy_marker(self, strategy: TradingStrategy) -> str:
        strategy_id = getattr(strategy, "id", None)
        strategy_name = getattr(strategy, "name", "") or getattr(strategy, "strategy_type", "") or "unknown"
        marker = f"#{strategy_id} {strategy_name}" if strategy_id is not None else strategy_name
        return marker[:50]

    @staticmethod
    def _strategy_side_matches_position(strategy_side: str, is_long: bool) -> bool:
        side = (strategy_side or "").strip().upper()
        if side in {"BUY", "LONG"}:
            return is_long
        if side in {"SELL", "SHORT"}:
            return not is_long
        return True

    async def _strategy_owns_ledger_position(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
        symbol: str,
        direction: str,
    ) -> bool:
        async with AsyncSessionLocal() as ownership_db:
            result = await ownership_db.execute(
                select(TradeRecord.strategy_tag).where(
                    TradeRecord.user_id == strategy.user_id,
                    TradeRecord.exchange_config_id == config.id,
                    TradeRecord.symbol == symbol,
                    TradeRecord.direction == direction,
                    TradeRecord.is_closed == False,
                ).distinct()
            )
            owner_tags = {tag for tag in result.scalars().all() if tag}
        if not owner_tags:
            return False
        return self._format_strategy_marker(strategy) in owner_tags

    async def _load_persisted_exit_stages(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
        symbol: str,
        open_time_ms: str,
    ) -> tuple[set, set]:
        filters = [
            StrategyLog.strategy_id == strategy.id,
            StrategyLog.user_id == strategy.user_id,
            StrategyLog.exchange_config_id == config.id,
            StrategyLog.symbol == symbol,
            StrategyLog.signal.in_(("BUY", "SELL")),
        ]
        allow_legacy_reasons = False
        try:
            opened_at = datetime.fromtimestamp(
                float(open_time_ms) / 1000, timezone.utc
            ).replace(tzinfo=None)
            filters.append(StrategyLog.created_at >= opened_at)
            allow_legacy_reasons = True
        except (TypeError, ValueError, OverflowError):
            pass

        async with AsyncSessionLocal() as exit_log_db:
            result = await exit_log_db.execute(
                select(StrategyLog.details).where(*filters)
            )
            details_rows = result.scalars().all()

        stage_keys = set()
        legacy_reasons = set()
        for details in details_rows:
            if not isinstance(details, dict):
                continue
            if details.get("exit_stage_key"):
                stage_keys.add(details["exit_stage_key"])
            if allow_legacy_reasons and details.get("exit_reason"):
                legacy_reasons.add(details["exit_reason"])
        return stage_keys, legacy_reasons

    def _normalize_strategy_symbol(self, symbol: str) -> str:
        normalized = (symbol or "").strip().upper()
        if not normalized:
            return ""
        parts = normalized.split("-")
        if len(parts) >= 3 and parts[-1].isdigit():
            return normalized
        if normalized.endswith("-SWAP"):
            return normalized
        if normalized.endswith("-USDT"):
            return f"{normalized}-SWAP"
        if normalized.endswith("USDTSWAP"):
            return f"{normalized[:-8]}-USDT-SWAP"
        if normalized.endswith("USDT"):
            return f"{normalized[:-4]}-USDT-SWAP"
        return f"{normalized}-USDT-SWAP"

    def _normalize_strategy_symbol_for_market(self, symbol: str, market_type: str = "SWAP") -> str:
        normalized = (symbol or "").strip().upper()
        if not normalized:
            return ""

        market_type = (market_type or "SWAP").upper()
        if market_type == "FUTURES":
            return normalized
        if market_type == "SPOT":
            if "-" in normalized:
                return normalized
            if normalized.endswith("USDT"):
                return f"{normalized[:-4]}-USDT"
            return f"{normalized}-USDT"
        return self._normalize_strategy_symbol(normalized)

    def _okx_inst_type_for_market(self, market_type: str = "SWAP") -> str:
        normalized = (market_type or "SWAP").upper()
        if normalized in {"SPOT", "SWAP", "FUTURES", "OPTION"}:
            return normalized
        return "SWAP"

    def _get_excluded_strategy_symbols(self, strategy: TradingStrategy) -> set[str]:
        params = strategy.params or {}
        return {
            self._normalize_strategy_symbol(symbol)
            for symbol in (params.get("excluded_symbols") or [])
            if symbol
        }

    def _get_allowed_micro_scalp_signals(self, strategy: TradingStrategy, symbol: str) -> set[str] | None:
        params = strategy.params or {}
        overrides = params.get("symbol_direction_overrides") or {}
        normalized_symbol = self._normalize_strategy_symbol(symbol)
        base_symbol = normalized_symbol.split("-")[0] if normalized_symbol else ""
        raw_allowed = overrides.get(normalized_symbol) or overrides.get(base_symbol)
        if not raw_allowed:
            return None
        if isinstance(raw_allowed, str):
            raw_allowed = [raw_allowed]

        allowed = set()
        for item in raw_allowed:
            direction = (item or "").upper()
            if direction in ("BUY", "LONG"):
                allowed.add("BUY")
            elif direction in ("SELL", "SHORT"):
                allowed.add("SELL")
        return allowed or None

    def _is_transient_market_error(self, exc: Exception) -> bool:
        if isinstance(exc, OkxTransientNetworkError):
            return True
        message = str(exc)
        transient_markers = (
            "Temporary failure in name resolution",
            "Name or service not known",
            "nodename nor servname provided",
            "ConnectError",
            "TimeoutException",
            "ReadTimeout",
            "ConnectTimeout",
        )
        return any(marker in message for marker in transient_markers)

    async def _get_live_instrument_ids(self, inst_type: str = "SWAP") -> set:
        inst_type = (inst_type or "SWAP").upper()
        now = time.time()
        if inst_type in self._live_instrument_ids_by_type and now - self._instrument_cache_ts < 3600:
            return self._live_instrument_ids_by_type[inst_type]

        instruments = await okx_manager.get_instruments(inst_type)
        live_ids = {
            item.get("instId", "").upper()
            for item in instruments
            if item.get("state") == "live"
        }
        self._live_instrument_ids_by_type[inst_type] = live_ids
        if inst_type == "SWAP":
            self._live_instrument_ids = live_ids
        self._instrument_cache_ts = now
        return live_ids

    async def _is_live_instrument(self, symbol: str, market_type: str = "SWAP") -> bool:
        inst_type = "FUTURES" if (market_type or "").upper() == "FUTURES" else "SWAP"
        live_ids = await self._get_live_instrument_ids(inst_type)
        return symbol in live_ids

    async def _should_write_strategy_log(
        self,
        db: AsyncSession,
        strategy: TradingStrategy,
        config: ExchangeConfig,
        symbol: str,
        signal: str,
        reason: str,
    ) -> bool:
        return await self._should_write_strategy_log_snapshot(
            db,
            strategy_id=strategy.id,
            exchange_config_id=config.id,
            strategy_params=strategy.params or {},
            symbol=symbol,
            signal=signal,
            reason=reason,
        )

    async def _should_write_strategy_log_snapshot(
        self,
        db: AsyncSession,
        *,
        strategy_id: int,
        exchange_config_id: int | None,
        strategy_params: dict,
        symbol: str,
        signal: str,
        reason: str,
    ) -> bool:
        if signal != "HOLD":
            return True

        params = strategy_params or {}
        interval_seconds = int(params.get("hold_log_interval_seconds", 1800) or 1800)
        if interval_seconds <= 0:
            return False

        filters = [
            StrategyLog.strategy_id == strategy_id,
            StrategyLog.symbol == symbol,
            StrategyLog.signal == "HOLD",
        ]
        if exchange_config_id is not None:
            filters.append(StrategyLog.exchange_config_id == exchange_config_id)
        result = await db.execute(
            select(StrategyLog)
            .where(*filters)
            .order_by(StrategyLog.created_at.desc(), StrategyLog.id.desc())
            .limit(1)
        )
        last_log = result.scalar_one_or_none()
        if not last_log:
            return True
        log_on_reason_change = params.get("hold_log_on_reason_change", True)
        if isinstance(log_on_reason_change, str):
            log_on_reason_change = log_on_reason_change.strip().lower() not in {"0", "false", "no", "off"}
        if log_on_reason_change and (last_log.reason or "") != (reason or ""):
            return True

        last_created = last_log.created_at
        if last_created and last_created.tzinfo is None:
            last_created = last_created.replace(tzinfo=timezone.utc)
        if not last_created:
            return True

        elapsed = (datetime.now(timezone.utc) - last_created).total_seconds()
        return elapsed >= interval_seconds

    async def _get_symbol_last_trade_signal(
        self,
        db: AsyncSession,
        strategy: TradingStrategy,
        config: ExchangeConfig,
        raw_symbol: str,
        normalized_symbol: str,
    ) -> str:
        """Return the latest BUY/SELL for this strategy+symbol, not the strategy-wide signal.
        ?????????????/?????????????????? HOLD ???
        """
        candidate_symbols = {
            s for s in (
                raw_symbol,
                normalized_symbol,
                self._normalize_strategy_symbol_for_market(raw_symbol, strategy.market_type),
            )
            if s
        }
        result = await db.execute(
            select(StrategyLog)
            .where(
                StrategyLog.strategy_id == strategy.id,
                StrategyLog.exchange_config_id == config.id,
                StrategyLog.symbol.in_(candidate_symbols),
                StrategyLog.signal.in_(("BUY", "SELL")),
            )
            .order_by(StrategyLog.created_at.desc(), StrategyLog.id.desc())
            .limit(1)
        )
        last_log = result.scalar_one_or_none()
        if last_log and self._is_exit_strategy_log(last_log):
            return "HOLD"
        return last_log.signal if last_log else "HOLD"

    def _is_exit_strategy_log(self, log: StrategyLog | None) -> bool:
        reason = (getattr(log, "reason", None) or "").strip()
        return reason.startswith(("[??]", "[????]", "[??]"))
    
    def _debug_log(self, msg):
        import os
        with open('/tmp/strategy_debug.log', 'a') as f:
            f.write(f'{datetime.now().isoformat()} {msg}\n')

    # 基础节拍 ~10s；留足开仓段(180s)+离场段(60s)超时的余量后判定心跳过期
    HEARTBEAT_STALE_SECONDS = 300
    ALERT_COOLDOWN_SECONDS = 300

    async def _alert(self, title: str, detail: str, *, key: str | None = None) -> bool:
        """关键失败的统一出口（Phase 2.5f）。

        先走飞书；未配置/发送失败/通道自身抛异常时，一律降级到本地 error 日志，
        并把"没送达"计入 _alert_stats 供 /health/engine 查询——此前 send_text 未配置
        直接 return False、异常也只 print，告警丢失本身是静默的。
        同一 key 在冷却期内只发一次：主循环 ~10s 一轮，OKX 故障时不做节流会刷屏。
        """
        key = key or title
        now = time.time()
        if now - self._alert_cooldown.get(key, 0.0) < self.ALERT_COOLDOWN_SECONDS:
            self._alert_stats["suppressed"] += 1
            return False
        self._alert_cooldown[key] = now

        delivered = False
        try:
            delivered = bool(await feishu_openclaw_notifier.send_text(
                f"[baige-no2][ALERT] {title}\n{detail}"
            ))
        except Exception as exc:
            # 通道自身炸了也不能把主循环带走
            logger.error("告警通道异常 | %s | %s", title, exc)
            delivered = False

        if delivered:
            self._alert_stats["delivered"] += 1
        else:
            self._alert_stats["undelivered"] += 1
            self._alert_stats["last_undelivered"] = {"title": title, "at": now}
            logger.error("ALERT-UNDELIVERED | %s | %s", title, detail)
        return delivered

    def health_snapshot(self) -> dict:
        """引擎健康快照（GET /health/engine，Phase 2.5f）。

        心跳过期 = _run_loop 已死或被卡住——此前引擎死亡的唯一线索是
        /tmp/strategy_debug.log，没有任何主动路径能让人知道。
        """
        now = time.time()
        beat_at = self._heartbeat.get("at")
        age = (now - beat_at) if beat_at else None
        stale = (age is None) or (age > self.HEARTBEAT_STALE_SECONDS)

        placed = self._native_stop_stats["placed"]
        failed = self._native_stop_stats["failed"]
        attempted = placed + failed
        notification_config = feishu_openclaw_notifier._load_config()

        return {
            "running": self._running,
            "task_alive": bool(self._task and not self._task.done()),
            "heartbeat": {
                "loop_count": self._heartbeat.get("loop_count", 0),
                "at": beat_at,
                "age_seconds": round(age, 3) if age is not None else None,
                "stale": stale,
                "started_at": self._heartbeat.get("started_at"),
            },
            # 开仓/离场两段独立成败（2.5h）：开仓挂了不代表止损没跑，反之亦然
            "segments": self._segment_status,
            "last_trade_ok_at": self._last_trade_ok_at,
            "native_stop": {
                "placed": placed,
                "failed": failed,
                "success_rate": round(placed / attempted, 4) if attempted else None,
            },
            "alerts": {
                **self._alert_stats,
                "configured": bool(
                    notification_config.get("enabled") and notification_config.get("webhook")
                ),
            },
            "healthy": bool(self._running and not stale),
        }

    async def _run_loop_segment(self, name: str, handler, timeout: float) -> bool:
        """跑主循环的一段（开仓/离场），异常只影响本段。返回 True 表示应结束主循环。

        Phase 2.5h：此前开仓与离场共用一个 try，开仓一抛出，本轮离场检查
        （软件止损）根本轮不到执行。而"开仓失败"与"该不该止损"业务上完全无关，
        没有理由共命运——故两段各自独立 try、各自记账告警。
        """
        try:
            async with AsyncSessionLocal() as db:
                await asyncio.wait_for(handler(db), timeout=timeout)
            self._segment_status[name] = {"ok": True, "at": time.time(), "error": None}
            return False
        except asyncio.CancelledError:
            self._debug_log(f'_run_loop() CancelledError in {name}')
            raise
        except asyncio.TimeoutError as te:
            self._segment_status[name] = {"ok": False, "at": time.time(), "error": f"timeout>{timeout}s"}
            self._debug_log(f'{name} timeout: {te}')
            print(f"?? [{name}] ??: {te}")
            await self._alert(f"主循环 {name} 段超时", f"timeout > {timeout}s", key=f"segment-timeout-{name}")
            return False
        except Exception as e:
            error_msg = str(e)
            # 只有"确实在关停"才退出主循环。此前这里还匹配 `"no active connection"
            # in error_msg`——任何异常消息碰巧含这个子串就 break，而 _running 仍为 True：
            # 引擎看似"已启动"，实际所有开仓/离场永久停止，且 start() 会以"已在运行"
            # 拒绝重启（全库只有 lifespan 调 start()，等于必须重启进程）。
            # 关停路径不依赖这个子串：stop() 是先置 _running=False 再 cancel，
            # 故正常关停一定由下面的 not self._running 命中；DB 连接错误则是真实故障，
            # 应走正常失败分支（记账 + 告警 + 下一轮继续）。（Phase 2.5b / E13）
            if not self._running:
                self._debug_log(f'_run_loop() stopped during shutdown in {name}: {e}')
                return True
            self._segment_status[name] = {"ok": False, "at": time.time(), "error": error_msg}
            self._debug_log(f'{name} exception: {e}')
            logger.error("主循环 %s 段异常", name, exc_info=True)
            await self._alert(f"主循环 {name} 段异常", error_msg, key=f"segment-error-{name}")
            return False

    def start(self):
        self._debug_log(f'start() called, _running={self._running}, _task={self._task}')
        if self._running:
            self._debug_log('start() skipped because already running')
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        # ??????????
        self._liq_updater._liq_signal = self._liq_signal
        self._liq_updater.start(self._get_liquidation_reference_price)
        self._derivatives_market_data.start(self._get_active_derivatives_symbols)
        print("? ???????")
        self._debug_log(f'start() completed, _task={self._task}, _running={self._running}')
    
    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        self._liq_updater.stop()
        self._derivatives_market_data.stop()
        print("?? ???????")

    async def _get_liquidation_reference_price(self, coin: str) -> float:
        """Provide live prices for bucketing fresh realised liquidations."""
        ticker = await okx_manager.get_ticker(f"{coin}-USDT-SWAP")
        return float(ticker.get("last", 0) or 0)

    def _check_liquidation_risk_filter(
        self, symbol: str, current_price: float, signal: str, params: dict
    ) -> dict:
        cfg = params.get("liquidation_risk_filter") or {}
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
            return {"enabled": False, "blocked": False, "status": "disabled", "reason": "未启用"}
        if not self._liq_signal:
            return {"enabled": True, "blocked": False, "status": "unavailable", "reason": "清算模块不可用，按中性处理"}
        try:
            result = self._liq_signal.check_recent_cascade_risk(
                symbol.split("-", 1)[0].upper(),
                current_price,
                "LONG" if signal == "BUY" else "SHORT",
                distance_threshold=float(cfg.get("distance_threshold", 0.015) or 0.015),
                min_relative_zone_size=float(cfg.get("min_relative_zone_size", 0.40) or 0.40),
                max_data_age_seconds=int(cfg.get("max_data_age_seconds", 10 * 60) or 10 * 60),
            )
        except Exception as exc:
            return {"enabled": True, "blocked": False, "status": "error", "reason": f"清算风险计算失败，按中性处理: {exc}"}
        return {"enabled": True, **result}

    async def _get_active_derivatives_symbols(self) -> list[str]:
        """Union active trend symbols for public OI/funding collection."""
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(TradingStrategy).where(
                        TradingStrategy.is_active == True,
                        TradingStrategy.strategy_type == "white_dove",
                    )
                )
                symbols = set()
                for strategy in result.scalars():
                    params = strategy.params or {}
                    for raw_symbol in [strategy.symbol, *(params.get("symbols") or [])]:
                        symbol = self._normalize_strategy_symbol_for_market(raw_symbol, strategy.market_type)
                        if symbol and symbol.endswith("-SWAP"):
                            symbols.add(symbol)
                return sorted(symbols)
        except Exception as exc:
            print(f"OI/资金费率币种列表获取失败: {exc}")
            return []

    async def aclose(self):
        self.stop()
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                if "no active connection" not in str(exc):
                    raise
    
    async def _run_loop(self):
        self._debug_log('_run_loop() started')
        loop_count = 0
        self._heartbeat["started_at"] = time.time()
        # 心跳初值 = 启动时刻：启动对账最长 45s，若不先落一拍，这段窗口会被
        # /health/engine 误报为 stale（把"正在启动"当成"已死"）
        self._heartbeat["at"] = self._heartbeat["started_at"]
        try:
            await self._run_loop_body()
        except asyncio.CancelledError:
            # 正常关停：stop() 已置 _running=False 并 cancel
            self._debug_log('_run_loop() cancelled')
            raise
        except BaseException as exc:
            # 引擎"无声死亡"的最后入口（Phase 2.5b / E13）：主循环因未预期异常退出。
            # 此前这种情况下 _running 仍为 True——状态说谎，start() 拒绝重启，
            # 且没有任何信号告诉人引擎已经不在了。
            #
            # 顺序有讲究：死亡告警绝不能依赖 _debug_log。它写的是无上限增长的
            # /tmp/strategy_debug.log（N6），而"磁盘被它自己写满"恰恰是主循环会死的
            # 原因之一——那种情况下先调 _debug_log 会让本处理器自己抛出，告警永远发不出。
            logger.error("引擎主循环异常退出", exc_info=True)
            try:
                await self._alert(
                    "引擎主循环已退出",
                    f"{type(exc).__name__}: {exc}（开仓与离场已全部停止，需重启进程）",
                    key="engine-loop-died",
                )
            except Exception:
                pass
            try:
                self._debug_log(f'_run_loop() died: {exc}')
            except Exception:
                pass
            raise
        finally:
            # 循环不在跑了，状态就不许再自称在跑
            self._running = False

    async def _ensure_account_scoped_ledger_schema(self) -> None:
        """Add account ownership columns before a second account can trade.

        SQLite does not run ORM migrations by itself.  Historical rows belong
        to the first configured account; every new row is written with the
        concrete ExchangeConfig id and all execution guards filter on it.
        """
        if self._account_scope_schema_ready:
            return
        tables = (
            "trade_records",
            "trading_orders",
            "trading_positions",
            "strategy_logs",
        )
        try:
            async with AsyncSessionLocal() as schema_db:
                default_config_id = (await schema_db.execute(
                    select(ExchangeConfig.id).order_by(ExchangeConfig.id.asc()).limit(1)
                )).scalar_one_or_none()
                for table_name in tables:
                    columns_result = await schema_db.execute(
                        text(f"PRAGMA table_info({table_name})")
                    )
                    existing_columns = {str(row[1]) for row in columns_result.fetchall()}
                    if not existing_columns:
                        raise RuntimeError(f"missing required table: {table_name}")
                    if "exchange_config_id" not in existing_columns:
                        await schema_db.execute(text(
                            f"ALTER TABLE {table_name} ADD COLUMN exchange_config_id INTEGER"
                        ))
                    if default_config_id is not None:
                        await schema_db.execute(
                            text(
                                f"UPDATE {table_name} "
                                "SET exchange_config_id = :config_id "
                                "WHERE exchange_config_id IS NULL"
                            ),
                            {"config_id": default_config_id},
                        )
                await schema_db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_trade_records_config_open "
                    "ON trade_records(exchange_config_id, is_closed)"
                ))
                await schema_db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_strategy_logs_config_strategy_symbol "
                    "ON strategy_logs(exchange_config_id, strategy_id, symbol)"
                ))
                await schema_db.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_trading_orders_config_order "
                    "ON trading_orders(exchange_config_id, binance_order_id)"
                ))
                await schema_db.commit()
            self._account_scope_schema_ready = True
        except Exception:
            logger.exception("failed to initialize account-scoped ledger schema")
            raise

    async def _reconcile_ledger_once(self):
        """孤儿仓对账（Phase 2.5c/#10）：启动时 + 每 RECONCILE_EVERY_LOOPS 轮各跑一次。

        60s 周期节拍此前寄生在 openclaw_trading_journal 上（它是 sync_live_positions
        唯一的周期调用方）；OpenClaw 脱钩（2026-07-17）删除 journal 后，节拍由引擎
        自己持有——这也顺带解决了"journal 挂了对账就停"的隐患（它在本地正是每轮崩）。
        用户/配置选择沿用原 journal 口径（首个活跃用户、超管优先；活跃 OKX 配置）。
        无配置（如本地开发）静默跳过。
        """
        from app.models.user import User
        from app.services.analyzer_service import analyzer_service

        async with AsyncSessionLocal() as db:
            user = (await db.execute(
                select(User)
                .where(User.is_active == True)
                .order_by(User.is_superuser.desc(), User.id)
                .limit(1)
            )).scalar_one_or_none()
            configs = (await db.execute(
                select(ExchangeConfig)
                .where(
                    ExchangeConfig.is_active == True,
                    ExchangeConfig.exchange.ilike("okx"),
                )
                .order_by(ExchangeConfig.id.asc())
            )).scalars().all()
            if not user or not configs:
                return None
            summaries = []
            for config in configs:
                summary = await analyzer_service.sync_live_positions(db, user.id, config)
                summaries.append({"exchange_config_id": config.id, "name": config.name, **summary})
            self._debug_log(f'startup reconcile: {summaries}')
            return {"accounts": summaries}

    # 对账的硬顶：正常一次持仓查询（含 okx_client 重试链 ~12s）远低于此；
    # OKX 不可达时 httpx 的连接/DNS 重试可拖到分钟级，而"对账没跑完"绝不能
    # 推迟主循环——推迟主循环 = 推迟止损轮询（出口冒烟实测抓到的问题）。
    STARTUP_RECONCILE_TIMEOUT = 45
    # 基础节拍 ~10s → 每 6 轮 ≈ 60s，接替被删 journal 的周期对账节拍
    RECONCILE_EVERY_LOOPS = 6

    async def _run_reconcile_guarded(self, trigger: str) -> None:
        """跑一次对账，失败/超时 → 告警 + 放行，绝不拖垮主循环。"""
        try:
            await asyncio.wait_for(self._reconcile_ledger_once(), timeout=self.STARTUP_RECONCILE_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.error("%s对账超时（>%ss），跳过", trigger, self.STARTUP_RECONCILE_TIMEOUT)
            await self._alert(
                f"{trigger}对账超时",
                f">{self.STARTUP_RECONCILE_TIMEOUT}s（OKX 可能不可达）。已跳过，下个周期重试",
                key=f"reconcile-{trigger}-timeout",
            )
        except Exception as exc:
            logger.error("%s对账失败", trigger, exc_info=True)
            await self._alert(
                f"{trigger}对账失败",
                f"{type(exc).__name__}: {exc}（已跳过，下个周期重试）",
                key=f"reconcile-{trigger}-failed",
            )

    async def _run_loop_body(self):
        await self._ensure_account_scoped_ledger_schema()
        # 启动对账：失败/超时不阻塞引擎启动
        await self._run_reconcile_guarded("启动")

        loop_count = 0
        while self._running:
            loop_count += 1
            self._debug_log(f'_run_loop() iteration {loop_count}')
            # 心跳：每轮起点。过期即代表本循环已死或被卡住（Phase 2.5f）
            self._heartbeat["loop_count"] = loop_count
            self._heartbeat["at"] = time.time()

            # 开仓与离场各自独立：开仓段无论成败，离场段（软件止损）本轮都必须跑（2.5h）
            if await self._run_loop_segment("开仓", self._process_strategies, 180):
                break
            if not self._running:
                break
            if await self._run_loop_segment("离场", self._process_exit_checks, 60):
                break

            if not self._running:
                break

            # 周期对账（≈60s）：接替被删 journal 的节拍（#10 · OpenClaw 脱钩 2026-07-17）
            if loop_count % self.RECONCILE_EVERY_LOOPS == 0:
                await self._run_reconcile_guarded("周期")

            # ??????7???HOLD??
            now = time.time()
            if now - self._last_cleanup_time > 86400:
                try:
                    async with AsyncSessionLocal() as db:
                        result = await db.execute(
                            text("DELETE FROM strategy_logs WHERE signal = 'HOLD' AND created_at < datetime('now', '-7 days')")
                        )
                        await db.commit()
                        self._last_cleanup_time = now
                        print(f"?? ??????: ??7??HOLD??")
                except Exception as e:
                    print(f"?? ??????: {e}")
            
            # ??????????
            if loop_count % 360 == 0:  # ??1??
                try:
                    await auto_fetch_and_analyze_news()
                except Exception as e:
                    print(f"?? ??????: {e}")
            
            if loop_count % 360 == 180:  # ???????1??????
                try:
                    await check_news_accuracy(lookback_hours=4)
                except Exception as e:
                    print(f"?? ??????: {e}")
            
            await asyncio.sleep(10)
    
    async def _process_exit_checks(
        self,
        db: AsyncSession,
        configs: list[ExchangeConfig] | None = None,
    ):
        """???????????????????/????"""
        result = await db.execute(
            select(TradingStrategy).where(TradingStrategy.is_active == True)
        )
        strategies = result.scalars().all()
        if configs is None:
            config_result = await db.execute(
                select(ExchangeConfig)
                .where(ExchangeConfig.is_active == True)
                .order_by(ExchangeConfig.id.asc())
            )
            configs = list(config_result.scalars().all())
        if not configs:
            return
        for config in configs:
            # Do not let a reduction on account A suppress the matching exit on B.
            reduced_symbols: set = set()
            for strategy in strategies:
                params = strategy.params or {}
                exit_factors = params.get("exit_factors")
                if not exit_factors:
                    continue
                try:
                    await self._check_exit_for_strategy(strategy, config, exit_factors, reduced_symbols)
                except Exception as e:
                    print(f"?????? [{strategy.name}] account={config.id}: {e}")

    async def _check_exit_for_strategy(
        self, strategy: TradingStrategy,
        config: ExchangeConfig, exit_factors: dict,
        reduced_symbols: set = None
    ):
        if reduced_symbols is None:
            reduced_symbols = set()
        positions = await monitor_service.get_positions(config)
        base_params = strategy.params or {}
        watched_symbols = {self._normalize_strategy_symbol(s) for s in ([strategy.symbol] + (base_params.get("symbols") or []))}

        for pos in positions:
            try:
                params = dict(base_params)
                live_margin_mode = str(pos.get("mgnMode") or "").lower()
                if live_margin_mode in {"cross", "isolated"}:
                    # Existing positions retain their actual OKX mode during a
                    # cross/isolated migration. New entries use strategy params.
                    params["margin_mode"] = live_margin_mode
                symbol = pos.get("instId", "")
                pos_size = float(pos.get("pos", 0) or 0)
                if pos_size == 0:
                    # ????????????????
                    empty_pos_side = (pos.get('posSide','long') or 'long').lower()
                    tier_prefix = f"cfg{config.id}_{strategy.id}_{symbol}_{empty_pos_side}_"
                    self._trailing_peaks = {
                        key: value
                        for key, value in self._trailing_peaks.items()
                        if not key.startswith(tier_prefix)
                    }
                    self._trailing_exit_hits = {
                        key for key in self._trailing_exit_hits
                        if not key.startswith(tier_prefix)
                    }
                    self._profit_tier_hits = {
                        key for key in self._profit_tier_hits
                        if not key.startswith(tier_prefix)
                    }
                    self._divergence_exit_hits = {
                        key for key in self._divergence_exit_hits
                        if not key.startswith(tier_prefix)
                    }
                    await self._clear_trailing_position_state(tier_prefix)
                    continue
                if symbol not in watched_symbols:
                    continue
                # ???????????????
                if symbol in reduced_symbols:
                    continue

                entry_px = float(pos.get("avgPx", 0) or 0)
                mark_px = float(pos.get("markPx", 0) or pos.get("last", 0) or entry_px)
                upl_ratio = float(pos.get("uplRatio", 0) or 0)
                pos_side = (pos.get("posSide", "long") or "long").lower()
                open_time_ms = str(pos.get("cTime", "") or "")
                position_state_key = f"cfg{config.id}_{strategy.id}_{symbol}_{pos_side}_{open_time_ms or entry_px}"
                is_long = pos_side == "long" or (pos_side == "net" and pos_size > 0)
                if not self._strategy_side_matches_position(strategy.side, is_long):
                    continue
                position_direction = "LONG" if is_long else "SHORT"
                if not await self._strategy_owns_ledger_position(
                    config, strategy, symbol, position_direction
                ):
                    continue
                close_side = "SELL" if is_long else "BUY"
                # ????????? posSide????????
                okx_pos_side = pos_side if pos_side in ("long", "short") else None

                if entry_px <= 0 or mark_px <= 0:
                    continue

                (
                    trend_runner_key,
                    trend_runner_state,
                    trend_runner_core_qty,
                ) = await self._resolve_trend_runner_core_quantity(
                    strategy,
                    config,
                    symbol,
                    position_direction,
                    open_time_ms,
                    abs(pos_size),
                )

                trend_runner_state = await self._hydrate_extreme_volume_event_state(
                    trend_runner_key,
                    trend_runner_state,
                    strategy,
                    config,
                    symbol,
                    position_direction,
                    open_time_ms,
                )
                followthrough = await self._resolve_extreme_volume_followthrough(
                    trend_runner_state,
                    symbol,
                    position_direction,
                    entry_px,
                    params,
                )
                if followthrough != trend_runner_state.extreme_event_status:
                    trend_runner_state.extreme_event_status = followthrough
                    await self._save_trailing_position_state(
                        trend_runner_key,
                        strategy,
                        symbol,
                        position_direction,
                        open_time_ms,
                        trend_runner_state,
                    )
                if followthrough == "failed":
                    reduced = await self._do_reduce(
                        strategy,
                        config,
                        symbol,
                        abs(pos_size),
                        close_side,
                        mark_px,
                        (
                            "extreme-volume follow-through failed on first closed 5m "
                            f"(entry volume {trend_runner_state.extreme_volume_ratio:.1f}x)"
                        ),
                        params,
                        okx_pos_side,
                        reduced_symbols,
                    )
                    if reduced:
                        continue

                # A continuation add is isolated from the protected core.  Its
                # initial stop is tight; after a 1% favorable price move it is
                # promoted to break-even and can no longer give back locked PnL.
                live_runner_qty = cap_trend_runner_reduce_quantity(
                    pos_size,
                    trend_runner_state.runner_add_quantity,
                    trend_runner_core_qty,
                )
                if live_runner_qty > 0 and trend_runner_state.runner_add_entry_price > 0:
                    runner_move = (
                        mark_px / trend_runner_state.runner_add_entry_price - 1
                        if is_long
                        else trend_runner_state.runner_add_entry_price / mark_px - 1
                    )
                    break_even_activation = max(
                        0.0,
                        float(params.get("trend_runner_add_break_even_pct", 0.01) or 0.01),
                    )
                    if (
                        not trend_runner_state.runner_add_break_even_armed
                        and runner_move >= break_even_activation
                    ):
                        trend_runner_state.runner_add_stop_price = trend_runner_state.runner_add_entry_price
                        trend_runner_state.runner_add_break_even_armed = True
                        await self._save_trailing_position_state(
                            trend_runner_key,
                            strategy,
                            symbol,
                            position_direction,
                            open_time_ms,
                            trend_runner_state,
                        )
                    runner_stop_hit = (
                        mark_px <= trend_runner_state.runner_add_stop_price
                        if is_long
                        else mark_px >= trend_runner_state.runner_add_stop_price
                    )
                    if trend_runner_state.runner_add_stop_price > 0 and runner_stop_hit:
                        reduced = await self._do_reduce(
                            strategy,
                            config,
                            symbol,
                            live_runner_qty,
                            close_side,
                            mark_px,
                            "trend runner add stop (isolated continuation tranche)",
                            params,
                            okx_pos_side,
                            reduced_symbols,
                        )
                        if reduced:
                            trend_runner_state.runner_add_quantity = max(
                                0.0,
                                trend_runner_state.runner_add_quantity - live_runner_qty,
                            )
                            trend_runner_state.runner_add_entry_price = 0.0
                            trend_runner_state.runner_add_stop_price = 0.0
                            trend_runner_state.runner_add_break_even_armed = False
                            await self._save_trailing_position_state(
                                trend_runner_key,
                                strategy,
                                symbol,
                                position_direction,
                                open_time_ms,
                                trend_runner_state,
                            )
                            continue

                # ????????????
                leverage = int(params.get("leverage", 1) or 1)
                price_change_ratio = (mark_px - entry_px) / entry_px if is_long else (entry_px - mark_px) / entry_px
                # OKX uplRatio is leveraged margin return. Exit rules can opt into it explicitly.
                upl_ratio = float(pos.get("uplRatio", 0) or 0)

                # 0a. 残仓清尾（2026-08-05, docs/09 · P2）
                #     所有减仓规则都按"剩余仓位"比例（trailing 0.4、profit_tiers 0.2~0.25），
                #     仓位指数衰减却永不归零；留下的零头仍完整占用 global_max_open_symbols
                #     名额，实测导致 14 天零建仓（TAO 剩 0.49 USDT 保证金占着 1/4 名额）。
                #     阈值取单笔最小保证金的一半，并要求持仓满 min_age_minutes，
                #     避免刚建的小仓被误清。
                residual_cfg = exit_factors.get("residual_clear", {})
                if residual_cfg.get("enabled", True):
                    min_order_margin = float(params.get("min_order_margin_usdt", 5.0) or 5.0)
                    margin_ratio = float(residual_cfg.get("margin_ratio", 0.5))
                    min_age_minutes = float(residual_cfg.get("min_age_minutes", 30))
                    residual_floor = min_order_margin * margin_ratio
                    pos_imr = float(pos.get("imr", 0) or 0)
                    open_ms = float(open_time_ms) if str(open_time_ms).isdigit() else 0.0
                    age_minutes = (time.time() * 1000 - open_ms) / 60000.0 if open_ms else 1e9
                    residual_qty = cap_trend_runner_reduce_quantity(
                        pos_size, abs(pos_size), trend_runner_core_qty
                    )
                    if (
                        residual_qty > 0
                        and 0 < pos_imr < residual_floor
                        and age_minutes >= min_age_minutes
                    ):
                        await self._do_reduce(
                            strategy,
                            config,
                            symbol,
                            residual_qty,
                            close_side,
                            mark_px,
                            f"residual clear (margin {pos_imr:.2f} < {residual_floor:.2f} USDT, "
                            f"age {age_minutes:.0f}m) - free up open-symbol slot",
                            params,
                            okx_pos_side,
                            reduced_symbols,
                        )
                        continue

                # 0. 硬止损（2026-08-05, docs/09 · P1）
                #    metric 可选 price_change（默认，向后兼容）或 upl_ratio（保证金收益率）。
                #    此前写死 price_change_ratio，而 profit_tiers / trailing_stop 都用
                #    upl_ratio——20x 下等于"赚 0.75% 价格就减仓、亏 2.5% 价格才砍仓"，
                #    实测近 30 天盈亏比 0.32（盈利单均 +0.86/9.4h，亏损单均 -2.68/21.3h）。
                #    加开关后可把止盈止损统一到同一把尺子。
                hard_stop_cfg = exit_factors.get("hard_stop", {})
                if hard_stop_cfg.get("enabled", False):
                    stop_pct = float(hard_stop_cfg.get("stop_pct", 0.07))
                    hs_metric_mode = str(hard_stop_cfg.get("metric", "price_change")).lower()
                    hs_use_upl = hs_metric_mode in {"upl_ratio", "margin_return", "roe"}
                    hs_metric = upl_ratio if hs_use_upl else price_change_ratio
                    hs_label = "margin return" if hs_use_upl else "price change"
                    if hs_metric <= -stop_pct:
                        await self._do_reduce(
                            strategy,
                            config,
                            symbol,
                            abs(pos_size),
                            close_side,
                            mark_px,
                            f"hard stop ({hs_label} {abs(hs_metric)*100:.1f}% >= {stop_pct*100:.1f}%)",
                            params,
                            okx_pos_side,
                            reduced_symbols,
                        )
                        continue

                # 1. 移动止盈：峰值按整笔交易持久化，减仓后可重新启动。
                ts_cfg = exit_factors.get("trailing_stop", {})
                if ts_cfg.get("enabled", False):
                    activation = float(ts_cfg.get("activation", ts_cfg.get("activation_pct", 0.04)))
                    metric_mode = str(ts_cfg.get("metric", "price_change")).lower()
                    current_metric = upl_ratio if metric_mode in {"upl_ratio", "margin_return", "roe"} else price_change_ratio
                    metric_label = "margin return" if metric_mode in {"upl_ratio", "margin_return", "roe"} else "price change"
                    trailing_key = f"{position_state_key}_trailing"
                    state = trend_runner_state
                    historical_exit = await self._trailing_stop_already_triggered(
                        config,
                        strategy,
                        symbol,
                        position_direction,
                        open_time_ms,
                        trailing_key,
                    )
                    peak_key = f"{position_state_key}_trailing_peak"
                    current_peak = max(
                        self._trailing_peaks.get(peak_key, 0.0),
                        state.peak_metric,
                        current_metric,
                    )
                    self._trailing_peaks[peak_key] = current_peak

                    # `realizedPnl` is exchange-side realized PnL from prior partial exits;
                    # `upl` is the live residual PnL.  Together they are the only reliable
                    # whole-trade measurement after a partial close or service restart.
                    try:
                        realized_pnl = float(pos.get("realizedPnl", 0) or 0)
                        current_total_pnl = realized_pnl + float(pos.get("upl", 0) or 0)
                    except (TypeError, ValueError):
                        realized_pnl = 0.0
                        current_total_pnl = 0.0
                    state.peak_metric = current_peak
                    state.peak_total_pnl = max(state.peak_total_pnl, current_total_pnl)

                    # Older trailing partials have records but no persisted state.  Import
                    # them once so they receive a profit floor instead of becoming exempt.
                    if historical_exit and state.last_exit_peak_metric <= 0:
                        state.exit_taken = True
                        state.last_exit_peak_metric = max(current_peak, activation)

                    rearm_ratio = max(0.0, float(ts_cfg.get("rearm_new_peak_ratio", 0.15)))
                    if state.exit_taken and current_peak >= max(
                        activation,
                        state.last_exit_peak_metric * (1 + rearm_ratio),
                    ):
                        state.exit_taken = False

                    trailing_history = bool(
                        historical_exit or state.exit_taken or state.last_exit_peak_metric > 0
                    )
                    if (
                        trailing_history
                        and state.profit_floor_taken
                        and current_total_pnl > 0
                        and current_total_pnl >= state.profit_floor_peak_total_pnl * (1 + rearm_ratio)
                    ):
                        state.profit_floor_taken = False
                        state.profit_floor_peak_total_pnl = 0.0

                    # After a partial take-profit, protect a tiered fraction of the peak
                    # *whole-trade* PnL.  This is a software exit on purpose: the exchange
                    # native order remains the full hard-stop backstop, while profit locks
                    # only trim the configured fraction instead of flattening the trend leg.
                    if trailing_history and not state.profit_floor_taken and state.peak_total_pnl > 0:
                        lock_ratio = resolve_trailing_profit_lock_ratio(current_peak, ts_cfg)
                        # Profit floors must never use the approximate static contract table.
                        contract_value = await self._get_profit_lock_contract_value(symbol)
                        proposed_floor = resolve_profit_lock_price(
                            position_direction,
                            entry_px,
                            abs(pos_size),
                            contract_value,
                            realized_pnl,
                            state.peak_total_pnl,
                            lock_ratio,
                        )
                        min_step = max(0.0, float(ts_cfg.get("profit_lock_min_step_pct", 0.0025)))
                        should_update_floor = proposed_floor is not None and (
                            state.profit_floor_price <= 0
                            or (
                                is_tighter_profit_floor(
                                    position_direction, proposed_floor, state.profit_floor_price
                                )
                                and abs(proposed_floor / state.profit_floor_price - 1) >= min_step
                            )
                        )
                        if should_update_floor:
                            state.profit_floor_price = proposed_floor

                        floor_breached = (
                            contract_value > 0
                            and state.profit_floor_price > 0
                            and (
                                mark_px <= state.profit_floor_price
                                if is_long else mark_px >= state.profit_floor_price
                            )
                        )
                        if floor_breached:
                            reduce_ratio = max(
                                0.0,
                                min(1.0, float(ts_cfg.get("profit_lock_reduce_ratio", ts_cfg.get("reduce_ratio", 0.25)))),
                            )
                            reduce_qty = cap_trend_runner_reduce_quantity(
                                pos_size,
                                abs(pos_size) * reduce_ratio,
                                trend_runner_core_qty,
                            )
                            if reduce_qty > 0:
                                state.profit_floor_taken = True
                                state.profit_floor_peak_total_pnl = state.peak_total_pnl
                                await self._save_trailing_position_state(
                                    position_state_key, strategy, symbol, position_direction,
                                    open_time_ms, state,
                                )
                                reduced = await self._do_reduce(
                                    strategy, config, symbol, reduce_qty, close_side,
                                    mark_px,
                                    f"dynamic profit lock (whole-trade peak {state.peak_total_pnl:.2f} USDT; "
                                    f"lock {lock_ratio*100:.0f}%; floor {state.profit_floor_price:.8g})",
                                    params, okx_pos_side, reduced_symbols,
                                )
                                if reduced:
                                    state.runner_add_quantity = max(
                                        0.0, state.runner_add_quantity - reduce_qty
                                    )
                                    await self._save_trailing_position_state(
                                        position_state_key, strategy, symbol, position_direction,
                                        open_time_ms, state,
                                    )
                                    continue
                                state.profit_floor_taken = False
                                state.profit_floor_peak_total_pnl = 0.0
                            else:
                                # The protected core has absorbed every allowed trim.
                                state.profit_floor_taken = True
                                state.profit_floor_peak_total_pnl = state.peak_total_pnl

                    await self._save_trailing_position_state(
                        position_state_key, strategy, symbol, position_direction, open_time_ms, state,
                    )
                    if not state.exit_taken and current_peak >= activation:
                        callback = resolve_trailing_callback(current_peak, ts_cfg)
                        callback_mode = str(ts_cfg.get("callback_mode", "absolute_price")).lower()
                        if callback_mode in {"peak_profit_ratio", "peak_ratio"}:
                            drawdown = ((current_peak - current_metric) / current_peak if current_peak > 0 else 0)
                            drawdown_label = "peak-profit drawdown"
                        else:
                            drawdown = ((current_peak - current_metric) / (1 + current_peak) if current_peak > 0 else 0)
                            drawdown_label = "price drawdown"
                        if drawdown >= callback:
                            reduce_ratio = max(0.0, min(1.0, float(ts_cfg.get("reduce_ratio", 1.0))))
                            reduce_qty = cap_trend_runner_reduce_quantity(
                                pos_size,
                                abs(pos_size) * reduce_ratio,
                                trend_runner_core_qty,
                            )
                            if reduce_qty > 0:
                                reduced = await self._do_reduce(
                                    strategy, config, symbol, reduce_qty, close_side,
                                    mark_px,
                                    f"trailing stop ({metric_label}; {drawdown_label}; peak {current_peak*100:.1f}%, drawdown {drawdown*100:.1f}%, callback {callback*100:.1f})",
                                    params, okx_pos_side, reduced_symbols,
                                )
                            else:
                                reduced = False
                            if reduced:
                                self._trailing_exit_hits.add(trailing_key)
                                state.exit_taken = True
                                state.last_exit_peak_metric = current_peak
                                state.runner_add_quantity = max(
                                    0.0, state.runner_add_quantity - reduce_qty
                                )
                                await self._save_trailing_position_state(
                                    position_state_key, strategy, symbol, position_direction,
                                    open_time_ms, state,
                                )
                                continue
                            if reduce_qty <= 0:
                                state.exit_taken = True
                                state.last_exit_peak_metric = current_peak
                                await self._save_trailing_position_state(
                                    position_state_key, strategy, symbol, position_direction,
                                    open_time_ms, state,
                                )

                # 2. ???????????????????
                pt_cfg = exit_factors.get("profit_tiers", {})
                if pt_cfg.get("enabled", False):
                    metric_mode = str(pt_cfg.get("metric", "price_change")).lower()
                    profit_metric = upl_ratio if metric_mode in {"upl_ratio", "margin_return", "roe"} else price_change_ratio
                    metric_label = "margin return" if metric_mode in {"upl_ratio", "margin_return", "roe"} else "price change"
                    tiers = sorted(pt_cfg.get("tiers", []), key=lambda x: x.get("profit", x.get("profit_pct", 0)))
                    for tier in tiers:
                        tier_profit = float(tier.get("profit", tier.get("profit_pct", 999)))
                        tier_key = f"{position_state_key}_tier_{metric_mode}_{tier_profit}"
                        if profit_metric >= tier_profit and tier_key not in self._profit_tier_hits and tier_key not in reduced_symbols:
                            reduce_ratio = float(tier.get("reduce", tier.get("reduce_ratio", 0)))
                            reduce_qty = cap_trend_runner_reduce_quantity(
                                pos_size,
                                abs(pos_size) * reduce_ratio,
                                trend_runner_core_qty,
                            )
                            if reduce_qty > 0:
                                reduced = await self._do_reduce(
                                    strategy, config, symbol, reduce_qty, close_side, mark_px,
                                    f"profit tier ({metric_label} {profit_metric*100:.1f}%)",
                                    params, okx_pos_side, reduced_symbols,
                                )
                                if reduced:
                                    trend_runner_state.runner_add_quantity = max(
                                        0.0, trend_runner_state.runner_add_quantity - reduce_qty
                                    )
                                    await self._save_trailing_position_state(
                                        position_state_key, strategy, symbol, position_direction,
                                        open_time_ms, trend_runner_state,
                                    )
                                    self._profit_tier_hits.add(tier_key)
                                    reduced_symbols.add(tier_key)
                            break

                # 3. Staged divergence exits; each stage triggers once per live position.
                td_cfg = exit_factors.get("top_divergence", {})
                if td_cfg.get("enabled", False) and self._div_system:
                    bar = td_cfg.get("timeframe", "5m")
                    klines = filter_confirmed_klines(
                        await okx_manager.get_candles(symbol, bar, 100)
                    )
                    if len(klines) >= 30:
                        candles = [{
                            'open': float(k[1]), 'high': float(k[2]),
                            'low': float(k[3]), 'close': float(k[4]),
                            'volume': float(k[5]), 'time': k[0]
                        } for k in klines]
                        base_state = self._summarize_exit_divergence(candles)
                        base_signal = (
                            base_state["bearish_divergence"] if is_long
                            else base_state["bullish_divergence"]
                        )
                        momentum_confirmed = (
                            base_state["bearish_momentum_confirmed"] if is_long
                            else base_state["bullish_momentum_confirmed"]
                        )

                        confirm_bar = td_cfg.get("confirm_timeframe", "30m")
                        confirm_klines = filter_confirmed_klines(
                            await okx_manager.get_candles(symbol, confirm_bar, 100)
                        )
                        confirm_state = None
                        if len(confirm_klines) >= 30:
                            confirm_candles = [{
                                'open': float(k[1]), 'high': float(k[2]),
                                'low': float(k[3]), 'close': float(k[4]),
                                'volume': float(k[5]), 'time': k[0]
                            } for k in confirm_klines]
                            confirm_state = self._summarize_exit_divergence(confirm_candles)

                        persisted_stage_keys, persisted_exit_reasons = (
                            await self._load_persisted_exit_stages(
                                config, strategy, symbol, open_time_ms
                            )
                        )

                        if is_long and is_confirmed_third_sell_exit(base_state, confirm_state):
                            third_sell_key = (
                                f"{position_state_key}_third_sell_{bar}_{confirm_bar}"
                            )
                            third_sell_reason = (
                                f"third sell strong exit ({bar}+{confirm_bar} closed): "
                                f"{confirm_state['third_sell'].get('reason', '')}"
                            )
                            third_sell_seen = (
                                third_sell_key in self._divergence_exit_hits
                                or third_sell_key in persisted_stage_keys
                                or any(
                                    reason.startswith("third sell strong exit")
                                    for reason in persisted_exit_reasons
                                )
                            )
                            if not third_sell_seen:
                                third_sell_ratio = max(0.0, min(1.0, float(
                                    td_cfg.get("third_sell_reduce_ratio", 1.0)
                                )))
                                reduced = await self._do_reduce(
                                    strategy, config, symbol, abs(pos_size) * third_sell_ratio,
                                    close_side, mark_px,
                                    third_sell_reason,
                                    params, okx_pos_side, reduced_symbols,
                                    exit_stage_key=third_sell_key,
                                )
                                if reduced:
                                    self._divergence_exit_hits.add(third_sell_key)
                                    continue

                        if base_signal:
                            confluence_signal = bool(
                                confirm_state and (
                                    confirm_state["bearish_divergence"] if is_long
                                    else confirm_state["bullish_divergence"]
                                )
                            )
                            side_name = "bearish" if is_long else "bullish"
                            base_key = f"{position_state_key}_{side_name}_div_{bar}"
                            confluence_key = f"{base_key}_{confirm_bar}"
                            base_reason = f"first {bar} {side_name} divergence exit"
                            confluence_reason = (
                                f"{bar}+{confirm_bar} {side_name} divergence confluence"
                            )
                            if base_reason in persisted_exit_reasons:
                                persisted_stage_keys.add(base_key)
                            if confluence_reason in persisted_exit_reasons:
                                persisted_stage_keys.update({base_key, confluence_key})
                            seen_stage_keys = self._divergence_exit_hits | persisted_stage_keys

                            if confluence_signal and confluence_key not in seen_stage_keys:
                                confluence_ratio = max(0.0, min(1.0, float(
                                    td_cfg.get("confluence_reduce_ratio", 0.50)
                                )))
                                reduced = await self._do_reduce(
                                    strategy, config, symbol, abs(pos_size) * confluence_ratio,
                                    close_side, mark_px,
                                    confluence_reason,
                                    params, okx_pos_side, reduced_symbols,
                                    exit_stage_key=confluence_key,
                                )
                                if reduced:
                                    self._divergence_exit_hits.update({base_key, confluence_key})
                                    continue

                            require_momentum = bool(
                                td_cfg.get("require_momentum_confirmation", True)
                            )
                            if (
                                base_key not in seen_stage_keys
                                and (momentum_confirmed or not require_momentum)
                            ):
                                base_ratio = max(0.0, min(1.0, float(
                                    td_cfg.get("reduce_ratio", 0.25)
                                )))
                                reduce_qty = cap_trend_runner_reduce_quantity(
                                    pos_size,
                                    abs(pos_size) * base_ratio,
                                    trend_runner_core_qty,
                                )
                                reduced = reduce_qty > 0 and await self._do_reduce(
                                    strategy, config, symbol, reduce_qty,
                                    close_side, mark_px,
                                    base_reason,
                                    params, okx_pos_side, reduced_symbols,
                                    exit_stage_key=base_key,
                                )
                                if reduced:
                                    trend_runner_state.runner_add_quantity = max(
                                        0.0, trend_runner_state.runner_add_quantity - reduce_qty
                                    )
                                    await self._save_trailing_position_state(
                                        position_state_key, strategy, symbol, position_direction,
                                        open_time_ms, trend_runner_state,
                                    )
                                    self._divergence_exit_hits.add(base_key)
                                    continue

                # 3.5 ?????/????
                tb_cfg = exit_factors.get("trendline_break", {})
                if tb_cfg.get("enabled", False):
                    bar = tb_cfg.get("timeframe", "1H")
                    break_pct = float(tb_cfg.get("break_pct", 0.005))
                    klines_tb = await okx_manager.get_candles(symbol, bar, 20)
                    require_closed_candle = bool(tb_cfg.get("require_closed_candle", False))
                    if resolve_trendline_break_signal(
                        klines_tb,
                        is_long,
                        break_pct,
                        mark_px,
                        require_closed_candle=require_closed_candle,
                    ):
                        reduce_ratio = float(tb_cfg.get("reduce_ratio", 0.5))
                        reduce_qty = abs(pos_size) * reduce_ratio
                        confirmation = "closed candle confirmed" if require_closed_candle else "live price"
                        await self._do_reduce(
                            strategy,
                            config,
                            symbol,
                            reduce_qty,
                            close_side,
                            mark_px,
                            f"trendline break ({bar}; {confirmation})",
                            params,
                            okx_pos_side,
                            reduced_symbols,
                        )
                        continue

                # 4. ????
                time_cfg = exit_factors.get("time_stop", {})
                if time_cfg.get("enabled", False):
                    open_time_ms = float(pos.get("cTime", 0) or 0)
                    if open_time_ms > 0:
                        hold_hours = (time.time() * 1000 - open_time_ms) / 3600000
                        loss_exit_after = time_cfg.get("loss_exit_after_hours")
                        if loss_exit_after is not None:
                            loss_exit_after = float(loss_exit_after or 0)
                            loss_exit_threshold = abs(float(time_cfg.get("loss_exit_max_loss_pct", 0) or 0))
                            if loss_exit_after > 0 and hold_hours >= loss_exit_after and price_change_ratio <= -loss_exit_threshold:
                                reduce_ratio = float(time_cfg.get("loss_exit_reduce_ratio", time_cfg.get("reduce_ratio", 1)))
                                reduce_qty = abs(pos_size) * reduce_ratio
                                await self._do_reduce(
                                    strategy,
                                    config,
                                    symbol,
                                    reduce_qty,
                                    close_side,
                                    mark_px,
                                    f"????????{hold_hours:.1f}h>{loss_exit_after}h???{abs(price_change_ratio)*100:.2f}%",
                                    params,
                                    okx_pos_side,
                                    reduced_symbols,
                                )
                                continue
                        max_hours = float(time_cfg.get("max_hold_hours", 48))
                        if hold_hours >= max_hours:
                            reduce_ratio = float(time_cfg.get("reduce_ratio", 0.5))
                            reduce_qty = abs(pos_size) * reduce_ratio
                            await self._do_reduce(strategy, config, symbol, reduce_qty, close_side,
                                                  mark_px, f"????{hold_hours:.1f}h>{max_hours}h", params, okx_pos_side, reduced_symbols)

            except Exception as e:
                print(f"????????? [{pos.get('instId')}]: {e}")

    async def _trailing_stop_already_triggered(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
        symbol: str,
        direction: str,
        open_time_ms: str,
        trailing_key: str,
    ) -> bool:
        """Return whether this live position has already taken its trailing partial exit."""
        if trailing_key in self._trailing_exit_hits:
            return True

        filters = [
            TradeRecord.user_id == strategy.user_id,
            TradeRecord.exchange_config_id == config.id,
            TradeRecord.symbol == symbol,
            TradeRecord.direction == direction,
            TradeRecord.strategy_tag == self._format_strategy_marker(strategy),
            TradeRecord.is_closed == True,
            TradeRecord.notes.ilike("%trailing stop%"),
        ]
        if str(open_time_ms).isdigit():
            opened_at = datetime.fromtimestamp(int(open_time_ms) / 1000, timezone.utc).replace(tzinfo=None)
            filters.append(TradeRecord.created_at >= opened_at - timedelta(minutes=1))

        try:
            async with AsyncSessionLocal() as state_db:
                result = await state_db.execute(
                    select(TradeRecord.id).where(and_(*filters)).limit(1)
                )
                already_triggered = result.scalar_one_or_none() is not None
        except Exception:
            logger.exception("failed to restore trailing-stop state for %s", symbol)
            return False

        if already_triggered:
            self._trailing_exit_hits.add(trailing_key)
        return already_triggered

    async def _ensure_trailing_position_state_schema(self) -> None:
        if self._trailing_state_schema_ready:
            return
        try:
            async with AsyncSessionLocal() as state_db:
                await state_db.execute(text("""
                    CREATE TABLE IF NOT EXISTS strategy_trailing_position_states (
                        position_key TEXT PRIMARY KEY,
                        strategy_id INTEGER NOT NULL,
                        symbol VARCHAR(30) NOT NULL,
                        direction VARCHAR(10) NOT NULL,
                        open_time_ms VARCHAR(32),
                        peak_metric REAL NOT NULL DEFAULT 0,
                        exit_taken BOOLEAN NOT NULL DEFAULT 0,
                        peak_total_pnl REAL NOT NULL DEFAULT 0,
                        last_exit_peak_metric REAL NOT NULL DEFAULT 0,
                        profit_floor_price REAL NOT NULL DEFAULT 0,
                        profit_floor_taken BOOLEAN NOT NULL DEFAULT 0,
                        profit_floor_peak_total_pnl REAL NOT NULL DEFAULT 0,
                        core_quantity REAL NOT NULL DEFAULT 0,
                        runner_add_quantity REAL NOT NULL DEFAULT 0,
                        runner_add_entry_price REAL NOT NULL DEFAULT 0,
                        runner_add_stop_price REAL NOT NULL DEFAULT 0,
                        runner_add_break_even_armed BOOLEAN NOT NULL DEFAULT 0,
                        runner_add_used BOOLEAN NOT NULL DEFAULT 0,
                        extreme_volume_ratio REAL NOT NULL DEFAULT 0,
                        extreme_event_candle_ts VARCHAR(32) NOT NULL DEFAULT '',
                        extreme_event_status VARCHAR(20) NOT NULL DEFAULT '',
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                columns_result = await state_db.execute(
                    text("PRAGMA table_info(strategy_trailing_position_states)")
                )
                existing_columns = {str(row[1]) for row in columns_result.fetchall()}
                migrations = {
                    "peak_total_pnl": "REAL NOT NULL DEFAULT 0",
                    "last_exit_peak_metric": "REAL NOT NULL DEFAULT 0",
                    "profit_floor_price": "REAL NOT NULL DEFAULT 0",
                    "profit_floor_taken": "BOOLEAN NOT NULL DEFAULT 0",
                    "profit_floor_peak_total_pnl": "REAL NOT NULL DEFAULT 0",
                    "core_quantity": "REAL NOT NULL DEFAULT 0",
                    "runner_add_quantity": "REAL NOT NULL DEFAULT 0",
                    "runner_add_entry_price": "REAL NOT NULL DEFAULT 0",
                    "runner_add_stop_price": "REAL NOT NULL DEFAULT 0",
                    "runner_add_break_even_armed": "BOOLEAN NOT NULL DEFAULT 0",
                    "runner_add_used": "BOOLEAN NOT NULL DEFAULT 0",
                    "extreme_volume_ratio": "REAL NOT NULL DEFAULT 0",
                    "extreme_event_candle_ts": "VARCHAR(32) NOT NULL DEFAULT ''",
                    "extreme_event_status": "VARCHAR(20) NOT NULL DEFAULT ''",
                }
                for column, ddl in migrations.items():
                    if column not in existing_columns:
                        await state_db.execute(text(
                            f"ALTER TABLE strategy_trailing_position_states ADD COLUMN {column} {ddl}"
                        ))
                await state_db.commit()
            self._trailing_state_schema_ready = True
        except Exception:
            logger.exception("failed to initialize trailing position state storage")

    async def _load_trailing_position_state(self, position_key: str) -> TrailingPositionState:
        await self._ensure_trailing_position_state_schema()
        try:
            async with AsyncSessionLocal() as state_db:
                result = await state_db.execute(text("""
                    SELECT peak_metric, exit_taken, peak_total_pnl,
                           last_exit_peak_metric, profit_floor_price,
                           profit_floor_taken, profit_floor_peak_total_pnl,
                            core_quantity, runner_add_quantity,
                            runner_add_entry_price, runner_add_stop_price,
                            runner_add_break_even_armed, runner_add_used,
                            extreme_volume_ratio, extreme_event_candle_ts,
                            extreme_event_status
                    FROM strategy_trailing_position_states
                    WHERE position_key = :position_key
                """), {"position_key": position_key})
                row = result.first()
        except Exception:
            logger.exception("failed to load trailing position state for %s", position_key)
            return TrailingPositionState()
        if not row:
            return TrailingPositionState()
        return TrailingPositionState(
            peak_metric=float(row[0] or 0.0),
            exit_taken=bool(row[1]),
            peak_total_pnl=float(row[2] or 0.0),
            last_exit_peak_metric=float(row[3] or 0.0),
            profit_floor_price=float(row[4] or 0.0),
            profit_floor_taken=bool(row[5]),
            profit_floor_peak_total_pnl=float(row[6] or 0.0),
            core_quantity=float(row[7] or 0.0),
            runner_add_quantity=float(row[8] or 0.0),
            runner_add_entry_price=float(row[9] or 0.0),
            runner_add_stop_price=float(row[10] or 0.0),
            runner_add_break_even_armed=bool(row[11]),
            runner_add_used=bool(row[12]),
            extreme_volume_ratio=float(row[13] or 0.0),
            extreme_event_candle_ts=str(row[14] or ""),
            extreme_event_status=str(row[15] or ""),
        )

    async def _save_trailing_position_state(
        self,
        position_key: str,
        strategy: TradingStrategy,
        symbol: str,
        direction: str,
        open_time_ms: str,
        state: TrailingPositionState,
    ) -> None:
        await self._ensure_trailing_position_state_schema()
        try:
            async with AsyncSessionLocal() as state_db:
                await state_db.execute(text("""
                    INSERT INTO strategy_trailing_position_states (
                        position_key, strategy_id, symbol, direction, open_time_ms,
                        peak_metric, exit_taken, peak_total_pnl, last_exit_peak_metric,
                        profit_floor_price, profit_floor_taken, profit_floor_peak_total_pnl,
                         core_quantity, runner_add_quantity, runner_add_entry_price,
                         runner_add_stop_price, runner_add_break_even_armed, runner_add_used,
                         extreme_volume_ratio, extreme_event_candle_ts, extreme_event_status,
                        updated_at
                    ) VALUES (
                        :position_key, :strategy_id, :symbol, :direction, :open_time_ms,
                        :peak_metric, :exit_taken, :peak_total_pnl, :last_exit_peak_metric,
                        :profit_floor_price, :profit_floor_taken, :profit_floor_peak_total_pnl,
                         :core_quantity, :runner_add_quantity, :runner_add_entry_price,
                         :runner_add_stop_price, :runner_add_break_even_armed, :runner_add_used,
                         :extreme_volume_ratio, :extreme_event_candle_ts, :extreme_event_status,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT(position_key) DO UPDATE SET
                        peak_metric = excluded.peak_metric,
                        exit_taken = excluded.exit_taken,
                        peak_total_pnl = excluded.peak_total_pnl,
                        last_exit_peak_metric = excluded.last_exit_peak_metric,
                        profit_floor_price = excluded.profit_floor_price,
                        profit_floor_taken = excluded.profit_floor_taken,
                        profit_floor_peak_total_pnl = excluded.profit_floor_peak_total_pnl,
                        core_quantity = excluded.core_quantity,
                        runner_add_quantity = excluded.runner_add_quantity,
                         runner_add_entry_price = excluded.runner_add_entry_price,
                         runner_add_stop_price = excluded.runner_add_stop_price,
                         runner_add_break_even_armed = excluded.runner_add_break_even_armed,
                         runner_add_used = excluded.runner_add_used,
                        extreme_volume_ratio = excluded.extreme_volume_ratio,
                        extreme_event_candle_ts = excluded.extreme_event_candle_ts,
                        extreme_event_status = excluded.extreme_event_status,
                        updated_at = CURRENT_TIMESTAMP
                """), {
                    "position_key": position_key,
                    "strategy_id": strategy.id,
                    "symbol": symbol,
                    "direction": direction,
                    "open_time_ms": open_time_ms,
                    "peak_metric": state.peak_metric,
                    "exit_taken": state.exit_taken,
                    "peak_total_pnl": state.peak_total_pnl,
                    "last_exit_peak_metric": state.last_exit_peak_metric,
                    "profit_floor_price": state.profit_floor_price,
                    "profit_floor_taken": state.profit_floor_taken,
                    "profit_floor_peak_total_pnl": state.profit_floor_peak_total_pnl,
                    "core_quantity": state.core_quantity,
                    "runner_add_quantity": state.runner_add_quantity,
                    "runner_add_entry_price": state.runner_add_entry_price,
                    "runner_add_stop_price": state.runner_add_stop_price,
                    "runner_add_break_even_armed": state.runner_add_break_even_armed,
                    "runner_add_used": state.runner_add_used,
                    "extreme_volume_ratio": state.extreme_volume_ratio,
                    "extreme_event_candle_ts": state.extreme_event_candle_ts,
                    "extreme_event_status": state.extreme_event_status,
                })
                await state_db.commit()
        except Exception:
            logger.exception("failed to save trailing position state for %s", position_key)

    async def _clear_trailing_position_state(self, position_key_prefix: str) -> None:
        await self._ensure_trailing_position_state_schema()
        try:
            async with AsyncSessionLocal() as state_db:
                await state_db.execute(text("""
                    DELETE FROM strategy_trailing_position_states
                    WHERE position_key LIKE :position_key_prefix
                """), {"position_key_prefix": f"{position_key_prefix}%"})
                await state_db.commit()
        except Exception:
            logger.exception("failed to clear trailing position state for %s", position_key_prefix)

    async def _resolve_trend_runner_core_quantity(
        self,
        strategy: TradingStrategy,
        config: ExchangeConfig,
        symbol: str,
        direction: str,
        open_time_ms: str,
        current_quantity: float,
    ) -> tuple[str, TrailingPositionState, float]:
        """Persist the protected core from the original effective entry size."""
        params = strategy.params or {}
        try:
            core_ratio = max(0.0, min(1.0, float(
                params.get("trend_runner_min_remaining_ratio", 0.0) or 0.0
            )))
        except (TypeError, ValueError):
            core_ratio = 0.0
        pos_side = "long" if str(direction).upper() == "LONG" else "short"
        position_key = f"cfg{config.id}_{strategy.id}_{symbol}_{pos_side}_{open_time_ms}"
        state = await self._load_trailing_position_state(position_key)
        if core_ratio <= 0:
            return position_key, state, 0.0
        if state.core_quantity > 0:
            return position_key, state, state.core_quantity

        effective_quantity = abs(float(current_quantity or 0.0))
        if str(open_time_ms).isdigit():
            opened_at = datetime.fromtimestamp(
                int(open_time_ms) / 1000, timezone.utc
            ).replace(tzinfo=None)
            try:
                async with AsyncSessionLocal() as state_db:
                    rows = await state_db.execute(
                        select(
                            TradeRecord.position_size,
                            TradeRecord.is_closed,
                            TradeRecord.notes,
                        ).where(
                            TradeRecord.user_id == strategy.user_id,
                            TradeRecord.exchange_config_id == config.id,
                            TradeRecord.symbol == symbol,
                            TradeRecord.direction == direction,
                            TradeRecord.strategy_tag == self._format_strategy_marker(strategy),
                            TradeRecord.created_at >= opened_at - timedelta(minutes=2),
                            TradeRecord.created_at <= opened_at + timedelta(minutes=2),
                        )
                    )
                    recorded_quantity = 0.0
                    for quantity, is_closed, notes in rows.all():
                        if bool(is_closed) and "manual sizing correction" in str(notes or "").lower():
                            continue
                        recorded_quantity += abs(float(quantity or 0.0))
                    effective_quantity = max(effective_quantity, recorded_quantity)
            except Exception:
                logger.exception("failed to recover trend runner base size for %s", symbol)

        state.core_quantity = effective_quantity * core_ratio
        await self._save_trailing_position_state(
            position_key, strategy, symbol, direction, open_time_ms, state
        )
        return position_key, state, state.core_quantity

    async def _record_trend_runner_add(
        self,
        metadata: dict,
        strategy: TradingStrategy,
        config: ExchangeConfig,
        executed_quantity: float,
        executed_price: float,
    ) -> None:
        """Store a separately protected continuation add after the exchange fill."""
        try:
            position_key = str(metadata["position_key"])
            symbol = str(metadata["symbol"])
            direction = str(metadata["direction"])
            open_time_ms = str(metadata["open_time_ms"])
            stop_pct = float(metadata["stop_pct"])
            core_quantity = float(metadata["core_quantity"])
        except (KeyError, TypeError, ValueError):
            return
        state = await self._load_trailing_position_state(position_key)
        # A historical runner may already have been trimmed below its intended
        # core before this feature existed.  The live remainder becomes the
        # protected core for the continuation cycle; it must not be re-sold.
        state.core_quantity = max(0.0, core_quantity)
        state.runner_add_quantity += abs(float(executed_quantity or 0.0))
        state.runner_add_entry_price = float(executed_price or 0.0)
        state.runner_add_stop_price = resolve_runner_add_stop_price(
            direction, state.runner_add_entry_price, stop_pct
        )
        state.runner_add_break_even_armed = False
        state.runner_add_used = True
        await self._save_trailing_position_state(
            position_key, strategy, symbol, direction, open_time_ms, state
        )

    async def _hydrate_extreme_volume_event_state(
        self,
        position_key: str,
        state: TrailingPositionState,
        strategy: TradingStrategy,
        config: ExchangeConfig,
        symbol: str,
        direction: str,
        open_time_ms: str,
    ) -> TrailingPositionState:
        """Restore extreme-volume entry metadata from the durable entry log."""
        if state.extreme_event_status:
            return state
        params = strategy.params or {}
        if not bool(params.get("extreme_volume_followthrough_enabled", False)):
            state.extreme_event_status = "not_applicable"
        elif not str(open_time_ms).isdigit():
            return state
        elif int(open_time_ms) < int(
            params.get("extreme_volume_followthrough_apply_after_ms", 0) or 0
        ):
            state.extreme_event_status = "not_applicable"
        else:
            threshold = max(
                1.0,
                float(params.get("extreme_volume_ratio_threshold", 20.0) or 20.0),
            )
            opened_at = datetime.fromtimestamp(
                int(open_time_ms) / 1000, timezone.utc
            ).replace(tzinfo=None)
            entry_signal = "BUY" if direction == "LONG" else "SELL"
            try:
                async with AsyncSessionLocal() as event_db:
                    result = await event_db.execute(
                        select(StrategyLog.details)
                        .where(
                            StrategyLog.strategy_id == strategy.id,
                            StrategyLog.exchange_config_id == config.id,
                            StrategyLog.symbol == symbol,
                            StrategyLog.signal == entry_signal,
                            StrategyLog.created_at >= opened_at - timedelta(minutes=2),
                            StrategyLog.created_at <= opened_at + timedelta(minutes=10),
                        )
                        .order_by(StrategyLog.created_at.asc())
                    )
                    detail_rows = result.scalars().all()
            except Exception:
                logger.exception("failed to restore extreme-volume entry for %s", symbol)
                return state
            event_raw = None
            for raw_details in detail_rows:
                try:
                    details = (
                        raw_details
                        if isinstance(raw_details, dict)
                        else json.loads(raw_details or "{}")
                    )
                except Exception:
                    continue
                for factor in details.get("factors") or []:
                    if factor.get("key") == "trend_v3_5m":
                        event_raw = factor.get("raw") or {}
                        break
                if event_raw:
                    break
            try:
                ratio = float((event_raw or {}).get("volume_ratio", 0) or 0)
            except (TypeError, ValueError):
                ratio = 0.0
            candle_ts = str((event_raw or {}).get("candle_ts", "") or "")
            if ratio >= threshold and candle_ts.isdigit():
                state.extreme_volume_ratio = ratio
                state.extreme_event_candle_ts = candle_ts
                state.extreme_event_status = "pending"
            else:
                state.extreme_event_status = "not_applicable"
        await self._save_trailing_position_state(
            position_key, strategy, symbol, direction, open_time_ms, state
        )
        return state

    async def _resolve_extreme_volume_followthrough(
        self,
        state: TrailingPositionState,
        symbol: str,
        direction: str,
        entry_price: float,
        params: dict,
    ) -> str:
        if state.extreme_event_status != "pending":
            return state.extreme_event_status
        if not state.extreme_event_candle_ts.isdigit():
            return "pending"
        try:
            candles = await okx_manager.get_candles(symbol, "5m", 12)
        except Exception:
            return "pending"
        event_ts = int(state.extreme_event_candle_ts)
        subsequent = []
        for candle in candles or []:
            try:
                candle_ts = int(candle[0])
                confirmed = len(candle) <= 8 or str(candle[8]) == "1"
            except (TypeError, ValueError, IndexError):
                continue
            if candle_ts > event_ts and confirmed:
                subsequent.append(candle)
        if not subsequent:
            return "pending"
        candle = min(subsequent, key=lambda item: int(item[0]))
        return evaluate_extreme_volume_followthrough(
            direction,
            entry_price,
            candle[1],
            candle[2],
            candle[3],
            candle[4],
            float(params.get("extreme_volume_failure_tolerance", 0.003) or 0.003),
            float(params.get("extreme_volume_max_rejection_wick_ratio", 0.45) or 0.45),
        )

    async def _get_trend_runner_add_context(
        self,
        strategy: TradingStrategy,
        config: ExchangeConfig,
        symbol: str,
        direction: str,
    ) -> dict:
        """Return a bounded continuation-add plan after a runner has been trimmed."""
        params = strategy.params or {}
        if not bool(params.get("trend_runner_add_enabled", False)):
            return {"allowed": False, "reason": "trend_runner_add_disabled"}
        try:
            positions = await monitor_service.get_positions(config)
        except Exception as exc:
            return {"allowed": False, "reason": f"position_lookup_failed:{exc}"}

        wanted_side = "long" if direction == "LONG" else "short"
        matched = None
        for position in positions or []:
            if str(position.get("instId") or "").upper() != str(symbol).upper():
                continue
            side = str(position.get("posSide") or "net").lower()
            size = float(position.get("pos", 0) or 0)
            if size == 0:
                continue
            is_long = side == "long" or (side == "net" and size > 0)
            if (wanted_side == "long") == is_long:
                matched = position
                break
        if not matched:
            return {"allowed": False, "reason": "no_matching_live_position"}

        current_quantity = abs(float(matched.get("pos", 0) or 0))
        open_time_ms = str(matched.get("cTime", "") or "")
        position_key, state, core_quantity = await self._resolve_trend_runner_core_quantity(
            strategy, config, symbol, direction, open_time_ms, current_quantity
        )
        state = await self._hydrate_extreme_volume_event_state(
            position_key,
            state,
            strategy,
            config,
            symbol,
            direction,
            open_time_ms,
        )
        if core_quantity <= 0:
            return {"allowed": False, "reason": "runner_core_unavailable"}
        if state.runner_add_used:
            return {"allowed": False, "reason": "runner_add_already_used"}
        extreme_confirmation_add = bool(
            params.get("extreme_volume_confirmation_add_enabled", False)
            and state.extreme_event_status == "confirmed"
        )
        target_core_quantity = core_quantity
        active_core_quantity = min(target_core_quantity, current_quantity)
        if not extreme_confirmation_add and current_quantity > active_core_quantity * 1.02:
            return {"allowed": False, "reason": "runner_not_yet_trimmed"}

        try:
            realized_pnl = float(matched.get("realizedPnl", 0) or 0)
        except (TypeError, ValueError):
            realized_pnl = 0.0
        min_realized = max(0.0, float(params.get("trend_runner_add_min_realized_pnl", 1.0) or 0.0))
        if not extreme_confirmation_add and realized_pnl < min_realized:
            return {"allowed": False, "reason": "locked_profit_insufficient"}

        core_ratio = max(0.01, min(1.0, float(
            params.get("trend_runner_min_remaining_ratio", 0.50) or 0.50
        )))
        original_quantity = target_core_quantity / core_ratio
        if extreme_confirmation_add:
            add_ratio = max(0.01, min(0.50, float(
                params.get("extreme_volume_confirmation_add_max_ratio", 0.25) or 0.25
            )))
            quantity_cap = original_quantity * add_ratio
        else:
            add_ratio = max(0.01, min(1.0, float(
                params.get("trend_runner_add_max_ratio", 0.25) or 0.25
            )))
            quantity_cap = min(
                original_quantity - current_quantity,
                original_quantity * add_ratio,
            )
        try:
            contract_value = float(matched.get("ctVal", 0) or 0)
        except (TypeError, ValueError):
            contract_value = 0.0
        if contract_value <= 0:
            contract_value = self._get_contract_value(symbol)
        try:
            stop_pct = max(0.001, min(0.05, float(
                params.get("trend_runner_add_stop_pct", 0.006) or 0.006
            )))
            risk_ratio = max(0.01, min(0.50, float(
                params.get("trend_runner_add_profit_risk_ratio", 0.25) or 0.25
            )))
            mark_price = float(matched.get("markPx", 0) or matched.get("last", 0) or 0)
        except (TypeError, ValueError):
            return {"allowed": False, "reason": "runner_add_inputs_invalid"}
        risk_per_contract = mark_price * contract_value * stop_pct
        if risk_per_contract <= 0:
            return {"allowed": False, "reason": "runner_add_risk_unavailable"}
        if not extreme_confirmation_add:
            quantity_cap = min(
                quantity_cap,
                realized_pnl * risk_ratio / risk_per_contract,
            )
        if quantity_cap <= 0:
            return {"allowed": False, "reason": "runner_add_capacity_zero"}
        risk_budget = (
            quantity_cap * risk_per_contract
            if extreme_confirmation_add
            else realized_pnl * risk_ratio
        )
        return {
            "allowed": True,
            "position_key": position_key,
            "open_time_ms": open_time_ms,
            "core_quantity": active_core_quantity,
            "quantity_cap": quantity_cap,
            "stop_pct": stop_pct,
            "realized_pnl": realized_pnl,
            "risk_budget": risk_budget,
            "add_mode": (
                "extreme_volume_followthrough"
                if extreme_confirmation_add
                else "locked_profit_reentry"
            ),
        }

    async def _do_reduce(
        self, strategy: TradingStrategy, config: ExchangeConfig,
        symbol: str, quantity: float, side: str, price: float, reason: str, params: dict,
        pos_side: str = None, reduced_symbols: set = None,
        exit_stage_key: str = None,
    ):
        """???????????"""
        quantity = max(round(quantity, 2), 0.01)
        position_direction = "LONG" if side == "SELL" else "SHORT"
        try:
            request = ReducePositionRequest(
                symbol=symbol,
                direction=position_direction,
                quantity=quantity,
                market_type=strategy.market_type,
                margin_mode=(params or {}).get("margin_mode", "cross") or "cross",
                leverage=int(params.get("leverage", 20) or 20),
                pos_side=pos_side,
                remark=f"????: {reason}",
            )
            async with AsyncSessionLocal() as new_db:
                db_order = await self._execution_gateway.reduce_position(
                    new_db,
                    strategy.user_id,
                    config,
                    request,
                )
                log_details = {"exit_reason": reason, "reduce_qty": quantity}
                if exit_stage_key:
                    log_details["exit_stage_key"] = exit_stage_key
                new_db.add(StrategyLog(
                    strategy_id=strategy.id,
                    user_id=strategy.user_id,
                    exchange_config_id=config.id,
                    symbol=symbol,
                    signal="SELL" if side == "SELL" else "BUY",
                    price=price,
                    quantity=quantity,
                    reason=f"[??] {reason}",
                    details=log_details,
                ))
                
                # ?? TradeRecord?? FIFO ??????????????????
                try:
                    from app.services.trade_record_ledger import apply_reduce_to_trade_records
                    close_direction = position_direction  # SELL?? = ???BUY?? = ??
                    exit_price = getattr(db_order, "executed_price", None) or price
                    ledger_result = await apply_reduce_to_trade_records(
                        new_db,
                        user_id=strategy.user_id,
                        exchange_config_id=config.id,
                        symbol=symbol,
                        direction=close_direction,
                        exit_price=exit_price,
                        quantity=getattr(db_order, "executed_qty", None) or quantity,
                        strategy_tag=self._format_strategy_marker(strategy),
                        closed_at=datetime.now(timezone.utc),
                        note=reason,
                    )
                    if ledger_result["closed_qty"] > 0:
                        print(
                            f"[????] {symbol} {close_direction} "
                            f"qty={ledger_result['closed_qty']} pnl={ledger_result['realized_pnl']:.4f} "
                            f"partial={ledger_result['partial_records']} closed={ledger_result['closed_records']}"
                        )
                    elif ledger_result["remaining_qty"] > 0:
                        print(f"[????] ????????: {symbol} {close_direction} qty={ledger_result['remaining_qty']}")
                    else:
                        print(f"[????] ????????: {symbol} {close_direction}")
                except Exception as te:
                    print(f"[??????] {symbol}: {te}")

                await new_db.commit()
            # 兜底止损同步：撤旧，剩余仓位>0 则按剩余数量重挂；内部吞错不影响减仓结果
            remaining_size = await self._execution_gateway.refresh_native_stop(
                config,
                NativeStopRequest(symbol, position_direction, params),
            )
            post_reduce_callback = getattr(self, "_post_reduce_callback", None)
            if post_reduce_callback is not None:
                try:
                    await post_reduce_callback(
                        strategy=strategy,
                        account=config,
                        symbol=symbol,
                        direction="LONG" if side == "SELL" else "SHORT",
                        reason=reason,
                        remaining_size=remaining_size,
                    )
                except Exception as callback_error:
                    print(
                        f"post reduce callback failed [{symbol}]: "
                        f"{type(callback_error).__name__}"
                    )
            print(f"? ???? [{symbol}] {quantity}? ??: {reason}")
            if reduced_symbols is not None:
                reduced_symbols.add(symbol)
            return True
        except Exception as e:
            error_msg = str(e)
            print(f"? ???? [{symbol}]: {error_msg}")
            try:
                async with AsyncSessionLocal() as err_db:
                    err_db.add(StrategyLog(
                        strategy_id=strategy.id,
                        user_id=strategy.user_id,
                        exchange_config_id=config.id,
                        symbol=symbol,
                        signal="HOLD",
                        price=price,
                        quantity=quantity,
                        reason=f"[????] {reason}: {error_msg}",
                        details={"exit_reason": reason, "reduce_qty": quantity, "error": error_msg},
                    ))
                    await err_db.commit()
            except Exception as log_error:
                print(f"? ?????????? [{symbol}]: {log_error}")
            return False

    async def _process_strategies(self, db):
        # 只取纯量而不是 ORM 实体：某个策略失败后的 db.rollback() 会让本 session 中
        # 所有已加载 ORM 对象过期，后续策略再读属性就会触发同步 IO 刷新 →
        # MissingGreenlet 抛出本函数，导致同轮其余策略全灭、且 _run_loop 里同一个
        # try 中的离场检查（软件止损）被整段跳过。纯量不受回滚影响。（Phase 2.5a / E11）
        result = await db.execute(
            select(
                TradingStrategy.id,
                TradingStrategy.name,
                TradingStrategy.interval_seconds,
                TradingStrategy.last_run_at,
            )
            .where(TradingStrategy.is_active == True)
            .order_by(TradingStrategy.interval_seconds.asc(), TradingStrategy.id.asc())
        )
        strategies = result.all()
        configs_result = await db.execute(
            select(ExchangeConfig)
            .where(ExchangeConfig.is_active == True)
            .order_by(ExchangeConfig.id.asc())
        )
        config_ids = [int(config.id) for config in configs_result.scalars().all()]
        if not config_ids:
            return

        for strategy_id, strategy_name, interval_seconds, last_run_at in strategies:
            if last_run_at:
                last_run = last_run_at
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last_run).total_seconds()
                if elapsed < (interval_seconds or 0):
                    continue

            for config_id in config_ids:
                try:
                # 逐个重新加载：上一个策略失败触发的 rollback 会让先前加载的对象过期，
                # 必须在 try 内经 await 路径取回新鲜对象，否则 _execute_strategy
                # 读 ORM 属性时同样会炸（只是从"逃出本函数"变成"每个都失败"）。
                    strategy = await db.get(TradingStrategy, strategy_id)
                    config = await db.get(ExchangeConfig, config_id)
                    if strategy is None or config is None or not config.is_active:
                        continue
                    await self._execute_strategy(db, strategy, config)
                except Exception as e:
                # fail-closed：单个策略执行失败（含持仓/余额查询失败上抛）只跳过该策略本轮，
                # 不放行降级决策，也不拖垮同一轮的其他策略。
                # 日志只用纯量 strategy_name——此刻 ORM 对象可能已过期，读它会让本
                # except 块自己抛出并逃逸，正是要防的那条路径。
                    print(f"[策略执行失败] {strategy_name} account={config_id}: {e}")
                    try:
                        await db.rollback()
                    except Exception:
                        pass
    
    @staticmethod
    def _entry_candidate_sort_key(candidate: dict) -> tuple:
        """Rank qualified entries by signal quality before symbol preference."""
        def number(key: str, default: float = 0.0) -> float:
            try:
                return float(candidate.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        return (
            number("score"),
            number("regime_rank"),
            number("support_count"),
            -number("negative_total"),
            number("leader_rank"),
            number("turnover_24h"),
        )

    @classmethod
    def _select_entry_candidate(
        cls,
        candidates: list[dict],
        leader_score_tolerance: float,
        require_leader_or_liquidity: bool = False,
        min_quote_turnover_24h: float = 0.0,
    ) -> tuple[dict, list[dict], str]:
        ranked = sorted(
            candidates,
            key=cls._entry_candidate_sort_key,
            reverse=True,
        )
        top_score = float(ranked[0].get("score", 0) or 0)
        try:
            tolerance = max(0.0, float(leader_score_tolerance or 0))
        except (TypeError, ValueError):
            tolerance = 0.0
        shortlist = [
            candidate
            for candidate in ranked
            if float(candidate.get("score", 0) or 0) >= top_score - tolerance
        ]
        leaders = [
            candidate
            for candidate in shortlist
            if float(candidate.get("leader_rank", 0) or 0) > 0
        ]
        if leaders:
            best = max(
                leaders,
                key=lambda candidate: (
                    float(candidate.get("leader_rank", 0) or 0),
                    *cls._entry_candidate_sort_key(candidate),
                ),
            )
            return best, ranked, "leader_within_score_tolerance"
        liquid = []
        if require_leader_or_liquidity or float(min_quote_turnover_24h or 0) > 0:
            liquid = [
                candidate
                for candidate in shortlist
                if float(candidate.get("quote_turnover_24h", 0) or 0)
                >= max(0.0, float(min_quote_turnover_24h or 0))
            ]
        if liquid:
            return max(liquid, key=cls._entry_candidate_sort_key), ranked, "liquidity_confirmed"
        if require_leader_or_liquidity:
            blocked = dict(ranked[0])
            blocked["_selection_blocked"] = True
            return blocked, ranked, "rejected_no_leader_or_liquidity"
        return ranked[0], ranked, "score_first_no_leader"

    async def _prefetch_white_dove_candles(self, symbol: str, params: dict) -> None:
        """Warm each required candle interval once before factor evaluation.

        White-dove factors use overlapping windows. Fetching the largest
        required lookback concurrently lets smaller factor windows reuse the
        public candle cache and prevents a 40-symbol scan from timing out.
        Failures stay non-fatal because normal factor handling can retry.
        """
        requirements: dict[str, int] = {}

        def require(bar: str, limit: int) -> None:
            normalized = str(bar or "").strip()
            if not normalized:
                return
            requirements[normalized] = max(
                requirements.get(normalized, 0), max(20, int(limit))
            )

        for item in params.get("divergence_timeframes") or []:
            if isinstance(item, dict):
                require(item.get("bar", "5m"), 100)
        require(params.get("divergence_timeframe", "5m"), 100)

        slow_period = int(params.get("trend_regime_ma_slow", 170) or 170)
        require(
            params.get("trend_regime_higher_timeframe", "4H"),
            slow_period + int(params.get("trend_regime_higher_slope_bars", 8) or 8) + 8,
        )
        require(
            params.get("trend_regime_entry_timeframe", "30m"),
            slow_period + int(params.get("trend_regime_entry_slope_bars", 12) or 12) + 8,
        )

        if params.get("ma_cross_enabled", False):
            require(params.get("ma_cross_timeframe", "1H"), 55)
        for item in params.get("elliott_wave_timeframes") or []:
            if isinstance(item, dict):
                require(item.get("bar", "30m"), item.get("limit", 220))
        if params.get("harmonic_filter_enabled", False):
            for item in params.get("harmonic_timeframes") or []:
                if isinstance(item, dict):
                    require(item.get("bar", "4H"), item.get("limit", 240))
            if params.get("allow_harmonic_rebound_long_entry", False):
                require(params.get("harmonic_rebound_confirm_timeframe", "5m"), 100)

        moer_cfg = (
            params.get("moer_long_structure")
            or params.get("moer_short_structure")
            or {}
        )
        if isinstance(moer_cfg, dict) and bool(moer_cfg.get("enabled", False)):
            require(moer_cfg.get("higher_timeframe", "4H"), 200)
            require(moer_cfg.get("entry_timeframe", "30m"), 240)

        if not requirements:
            return
        results = await asyncio.gather(
            *(
                okx_manager.get_candles(symbol, bar, limit)
                for bar, limit in requirements.items()
            ),
            return_exceptions=True,
        )
        failed = sum(isinstance(result, Exception) for result in results)
        if failed:
            logger.debug(
                "white-dove candle prefetch partial failure: %s %s/%s",
                symbol,
                failed,
                len(requirements),
            )

    async def _execute_strategy(
        self,
        db: AsyncSession,
        strategy: TradingStrategy,
        config: ExchangeConfig,
    ):
        strategy_id = strategy.id
        strategy_user_id = strategy.user_id
        strategy_name = strategy.name
        strategy_type = strategy.strategy_type
        params = strategy.params or {}
        # ??????? symbol + params.symbols ??
        symbols = [strategy.symbol]
        extra_symbols = params.get("symbols") or []
        if isinstance(extra_symbols, list):
            for s in extra_symbols:
                if s and s not in symbols:
                    symbols.append(s)

        # A 40-symbol white-dove scan evaluates several candle-based factors per
        # symbol.  Run the liquid leaders every cycle and rotate the remainder so
        # the 180s entry watchdog never suppresses an entire scan cycle.
        if strategy_type == "white_dove":
            try:
                batch_size = int(params.get("entry_scan_batch_size", 0) or 0)
            except (TypeError, ValueError):
                batch_size = 0
            if 0 < batch_size < len(symbols):
                configured_priority = params.get("entry_scan_priority_symbols") or []
                priority = [
                    raw_symbol for raw_symbol in configured_priority
                    if raw_symbol in symbols
                ]
                if not priority:
                    priority = symbols[:min(5, batch_size)]
                priority = priority[:batch_size]
                rotating_pool = [raw_symbol for raw_symbol in symbols if raw_symbol not in priority]
                rotating_slots = max(0, batch_size - len(priority))
                if rotating_pool and rotating_slots:
                    scan_cursor_key = (strategy_id, config.id)
                    cursor = self._entry_scan_cursors.get(scan_cursor_key, 0) % len(rotating_pool)
                    rotating = [
                        rotating_pool[(cursor + index) % len(rotating_pool)]
                        for index in range(min(rotating_slots, len(rotating_pool)))
                    ]
                    self._entry_scan_cursors[scan_cursor_key] = (
                        cursor + len(rotating)
                    ) % len(rotating_pool)
                    symbols = priority + rotating
                else:
                    symbols = priority

        ticker_by_symbol = {}
        try:
            inst_type = self._okx_inst_type_for_market(strategy.market_type)
            tickers = await okx_manager.get_tickers(inst_type)
            ticker_by_symbol = {
                item.get("instId"): item
                for item in tickers
                if item.get("instId")
            }
        except Exception as exc:
            print(f"?? {strategy_name} ??????????????: {exc}")
        
        any_trade = False
        last_signal = "HOLD"
        rank_entry_candidates = bool(
            strategy_type == "white_dove" and params.get("rank_entry_candidates", False)
        )
        entry_candidates = []
        # A 40-symbol score scan may take minutes.  Check owned live positions during
        # the scan so a trailing exit is not delayed until every candidate is evaluated.
        last_intra_scan_exit_at = time.monotonic() - 15.0
        intra_scan_exit_interval = 15.0
        leader_priority = params.get("leader_priority") or {
            "BTC": 5.0,
            "ETH": 4.0,
            "SOL": 3.0,
            "XRP": 2.0,
            "DOGE": 1.0,
        }
        leader_score_tolerance_value = params.get("leader_score_tolerance", 0.50)
        if leader_score_tolerance_value is None:
            leader_score_tolerance_value = 0.50
        try:
            leader_score_tolerance = max(0.0, float(leader_score_tolerance_value))
        except (TypeError, ValueError):
            leader_score_tolerance = 0.50
        require_leader_or_liquidity = bool(
            params.get("entry_require_leader_or_liquidity", False)
        )
        try:
            min_quote_turnover_24h = max(
                0.0,
                float(params.get("entry_min_quote_turnover_24h", 0) or 0),
            )
        except (TypeError, ValueError):
            min_quote_turnover_24h = 0.0
        
        for raw_symbol in symbols:
            if time.monotonic() - last_intra_scan_exit_at >= intra_scan_exit_interval:
                try:
                    await self._process_exit_checks(db, [config])
                except Exception as exc:
                    print(f"[intra-scan exit check failed] {strategy_name}: {exc}")
                last_intra_scan_exit_at = time.monotonic()
            try:
                symbol = self._normalize_strategy_symbol_for_market(raw_symbol, strategy.market_type)
                if not symbol:
                    continue
                if symbol in self._get_excluded_strategy_symbols(strategy):
                    reason = f"??????: ????????????: {symbol}"
                    should_log = await self._should_write_strategy_log(db, strategy, config, raw_symbol, "HOLD", reason)
                    if should_log:
                        db.add(StrategyLog(
                            strategy_id=strategy_id,
                            user_id=strategy_user_id,
                            exchange_config_id=config.id,
                            symbol=raw_symbol,
                            signal="HOLD",
                            price=0,
                            quantity=None,
                            reason=reason,
                            details={"strategy_type": strategy_type, "normalized_symbol": symbol},
                        ))
                        await db.commit()
                    continue
                if not await self._is_live_instrument(symbol, strategy.market_type):
                    reason = f"????????????: {symbol}"
                    print(f"?? {strategy_name} ?? {raw_symbol} ??: {reason}")
                    should_log = await self._should_write_strategy_log(db, strategy, config, raw_symbol, "HOLD", reason)
                    if should_log:
                        db.add(StrategyLog(
                            strategy_id=strategy_id,
                            user_id=strategy_user_id,
                            exchange_config_id=config.id,
                            symbol=raw_symbol,
                            signal="HOLD",
                            price=0,
                            quantity=None,
                            reason=reason,
                            details={"strategy_type": strategy_type, "normalized_symbol": symbol},
                        ))
                        await db.commit()
                    continue
                
                ticker = ticker_by_symbol.get(symbol)
                if ticker is None:
                    ticker = await okx_manager.get_ticker(symbol)
                current_price = float(ticker.get("last", 0))
                
                signal = "HOLD"
                reason = "????"
                log_details = {}
                strategy_last_signal = strategy.last_signal
                strategy.last_signal = await self._get_symbol_last_trade_signal(
                    db, strategy, config, raw_symbol, symbol
                )

                try:
                    if strategy.strategy_type == "grid":
                        signal, reason = self._grid_strategy(strategy, current_price)
                    elif strategy.strategy_type == "rsi":
                        signal, reason = await self._rsi_strategy(strategy, current_price, symbol)
                    elif strategy.strategy_type == "ma_cross":
                        signal, reason = await self._ma_cross_strategy(strategy, current_price, symbol)
                    elif strategy.strategy_type == "divergence":
                        signal, reason = await self._divergence_strategy(strategy, current_price, symbol)
                    elif strategy.strategy_type == "liquidation_map":
                        signal, reason = await self._liquidation_map_strategy(strategy, current_price, symbol)
                    elif strategy.strategy_type == "macro_filtered":
                        signal, reason = await self._macro_filtered_strategy(strategy, current_price, symbol)
                    elif strategy.strategy_type == "white_dove":
                        await self._prefetch_white_dove_candles(symbol, params)
                        signal, reason, factors = await self._white_dove_strategy(strategy, current_price, symbol, config)
                        log_details["strategy_type"] = "white_dove"
                        log_details["raw_reason"] = reason
                        log_details["min_score"] = params.get("min_score", 3)
                        log_details["factors"] = factors
                        dynamic_factor = next((f for f in factors if f.get("key") == "dynamic_score"), None)
                        trend_v3_factor = next((f for f in factors if f.get("key") == "trend_v3_score"), None)
                        dynamic_raw = (dynamic_factor or {}).get("raw") or {}
                        trend_v3_raw = (trend_v3_factor or {}).get("raw") or {}
                        log_details["score"] = trend_v3_raw.get(
                            "score",
                            dynamic_raw.get(
                                "base_score",
                                sum(f.get("score_added", 0) for f in factors),
                            ),
                        )
                        if dynamic_raw:
                            log_details["dynamic_score"] = dynamic_raw
                    elif strategy.strategy_type == "multi_factor":
                        signal, reason = await self._multi_factor_strategy(strategy, current_price, symbol)
                        if signal in ("BUY", "SELL"):
                            eval_result = await self._evaluate_factors(current_price, symbol, params.get("factors", {}))
                            log_details["strategy_type"] = "multi_factor"
                            log_details["factors"] = eval_result.get("factors", [])
                            log_details["score"] = eval_result.get("score", 0)
                            log_details["min_score"] = params.get("min_score", 5)
                            log_details["macro_blocked"] = eval_result.get("macro_blocked", False)
                            log_details["macro_reason"] = eval_result.get("macro_reason", "")
                    elif strategy.strategy_type == "micro_scalp":
                        signal, reason, micro_details = await self._micro_scalp_strategy(
                            strategy, current_price, symbol, config
                        )
                        log_details.update(micro_details)
                finally:
                    strategy.last_signal = strategy_last_signal
                
                if signal in ("BUY", "SELL") and strategy.strategy_type == "white_dove":
                    liquidation_risk = self._check_liquidation_risk_filter(
                        symbol, current_price, signal, params
                    )
                    log_details["liquidation_risk_filter"] = liquidation_risk
                    if liquidation_risk.get("blocked", False):
                        reason = (
                            f"近期清算瀑布风险过滤拦截: {liquidation_risk.get('reason', '')}"
                            f" | 原信号: {reason}"
                        )
                        signal = "HOLD"
                        self._pending_trade_quantity = None
                        self._pending_trade_context = None

                if signal in ("BUY", "SELL") and rank_entry_candidates:
                    dynamic_payload = log_details.get("dynamic_score") or {}
                    dynamic_score = dynamic_payload.get("decision_score")
                    try:
                        candidate_score = float(
                            dynamic_score if dynamic_score is not None else log_details.get("score", 0)
                        )
                    except (TypeError, ValueError):
                        candidate_score = 0.0
                    try:
                        min_score = float(log_details.get("min_score", 0) or 0)
                    except (TypeError, ValueError):
                        min_score = 0.0
                    score_margin = candidate_score - min_score
                    trend_factor = next(
                        (
                            factor for factor in log_details.get("factors", [])
                            if factor.get("key") == "trend_filter"
                        ),
                        {},
                    )
                    trend_raw = trend_factor.get("raw") or {}
                    regime = str(trend_raw.get("regime") or "unknown")
                    regime_rank = {
                        "strong_long": 3.0,
                        "strong_short": 3.0,
                        "weak_long": 2.0,
                        "weak_short": 2.0,
                        "range": 1.0,
                    }.get(regime, 0.0)
                    try:
                        support_count = int(dynamic_payload.get("support_count", 0) or 0)
                    except (TypeError, ValueError):
                        support_count = 0
                    negative_total = 0.0
                    for factor in dynamic_payload.get("negative_factors", []) or []:
                        try:
                            negative_total += abs(float(factor.get("score", 0) or 0))
                        except (TypeError, ValueError):
                            continue
                    base_symbol = symbol.split("-", 1)[0].upper()
                    try:
                        leader_rank = float(leader_priority.get(base_symbol, 0) or 0)
                    except (TypeError, ValueError):
                        leader_rank = 0.0
                    try:
                        turnover_24h = float(ticker.get("volCcy24h", 0) or 0)
                    except (TypeError, ValueError):
                        turnover_24h = 0.0
                    quote_turnover_24h = turnover_24h * current_price
                    entry_candidates.append({
                        "raw_symbol": raw_symbol,
                        "symbol": symbol,
                        "signal": signal,
                        "price": current_price,
                        "reason": reason,
                        "details": dict(log_details),
                        "quantity": self._pending_trade_quantity,
                        "trade_context": self._pending_trade_context,
                        "score": candidate_score,
                        "score_margin": score_margin,
                        "regime": regime,
                        "regime_rank": regime_rank,
                        "support_count": support_count,
                        "negative_total": negative_total,
                        "leader_rank": leader_rank,
                        "turnover_24h": turnover_24h,
                        "quote_turnover_24h": quote_turnover_24h,
                    })
                    log_details["entry_ranking"] = {
                        "status": "candidate",
                        "score": candidate_score,
                        "score_margin": score_margin,
                        "regime": regime,
                        "support_count": support_count,
                        "negative_total": negative_total,
                        "leader_rank": leader_rank,
                        "turnover_24h": turnover_24h,
                        "quote_turnover_24h": quote_turnover_24h,
                    }
                    reason = f"候选待全市场择优: {reason}"
                    signal = "HOLD"
                    self._pending_trade_quantity = None
                    self._pending_trade_context = None

                if signal in ("BUY", "SELL"):
                    trade_context = self._pending_trade_context
                    self._last_trade_error = None
                    trade_success = await self._execute_trade(db, strategy, config, signal, current_price, symbol)
                    if not trade_success:
                        refreshed_strategy = await db.get(TradingStrategy, strategy_id)
                        if refreshed_strategy is not None:
                            strategy = refreshed_strategy
                    if trade_context:
                        log_details["trade_context"] = trade_context
                    if not trade_success:
                        log_details["trade_error"] = self._last_trade_error or "unknown error"
                        reason = f"??????????: {reason}"
                        signal = "HOLD"
                
                if signal in ("BUY", "SELL"):
                    any_trade = True
                    last_signal = signal
                
                should_log = await self._should_write_strategy_log(db, strategy, config, raw_symbol, signal, reason)
                if should_log:
                    log = StrategyLog(
                        strategy_id=strategy_id,
                        user_id=strategy_user_id,
                        exchange_config_id=config.id,
                        symbol=raw_symbol,
                        signal=signal,
                        price=current_price,
                        quantity=self._last_trade_quantity if signal in ("BUY", "SELL") else None,
                        reason=reason,
                        details=log_details if log_details else None,
                    )
                    db.add(log)
                    await db.commit()
                self._last_trade_quantity = None
                
            except Exception as e:
                await db.rollback()
                error_msg = str(e)
                if self._is_transient_market_error(e):
                    reason = f"?????????????????: {error_msg}"
                    print(f"?? {strategy_name} ?? {raw_symbol} ??????: {error_msg}")
                    should_log = await self._should_write_strategy_log_snapshot(
                        db,
                        strategy_id=strategy_id,
                        exchange_config_id=config.id,
                        strategy_params=params,
                        symbol=raw_symbol,
                        signal="HOLD",
                        reason=reason,
                    )
                    if should_log:
                        db.add(StrategyLog(
                            strategy_id=strategy_id,
                            user_id=strategy_user_id,
                            exchange_config_id=config.id,
                            symbol=raw_symbol,
                            signal="HOLD",
                            price=None,
                            quantity=None,
                            reason=reason,
                            details={
                                "strategy_type": strategy_type,
                                "error_type": "transient_market_network",
                                "error": error_msg,
                            },
                        ))
                        await db.commit()
                    continue
                print(f"?? {strategy_name} ?? {raw_symbol} ????: {error_msg}")
                db.add(StrategyLog(
                    strategy_id=strategy_id,
                    user_id=strategy_user_id,
                    exchange_config_id=config.id,
                    symbol=raw_symbol,
                    signal="HOLD",
                    price=None,
                    quantity=None,
                    reason=f"???????????????: {error_msg}",
                    details={
                        "strategy_type": strategy_type,
                        "error": error_msg,
                    },
                ))
                await db.commit()

        if rank_entry_candidates and entry_candidates:
            top_score = max(candidate["score"] for candidate in entry_candidates)
            best, ranked_candidates, selection_mode = self._select_entry_candidate(
                entry_candidates,
                leader_score_tolerance,
                require_leader_or_liquidity,
                min_quote_turnover_24h,
            )
            compared_candidates = [
                {
                    "rank": index,
                    "symbol": candidate["symbol"],
                    "signal": candidate["signal"],
                    "score": candidate["score"],
                    "score_margin": candidate["score_margin"],
                    "regime": candidate["regime"],
                    "support_count": candidate["support_count"],
                    "negative_total": candidate["negative_total"],
                    "leader_rank": candidate["leader_rank"],
                    "turnover_24h": candidate["turnover_24h"],
                    "quote_turnover_24h": candidate["quote_turnover_24h"],
                    "within_score_tolerance": (
                        candidate["score"] >= top_score - leader_score_tolerance
                    ),
                    "is_leader": candidate["leader_rank"] > 0,
                }
                for index, candidate in enumerate(ranked_candidates[:10], start=1)
            ]
            selection_blocked = bool(best.get("_selection_blocked", False))
            self._pending_trade_quantity = best["quantity"]
            self._pending_trade_context = best["trade_context"]
            self._last_trade_error = None
            if selection_blocked:
                trade_success = False
                self._last_trade_error = "entry_requires_leader_or_minimum_liquidity"
            else:
                trade_success = await self._execute_trade(
                    db, strategy, config, best["signal"], best["price"], best["symbol"]
                )
            rank_details = dict(best["details"])
            rank_details["trade_context"] = best["trade_context"]
            rank_details["entry_ranking"] = {
                "status": "selected" if trade_success else "rejected",
                "selected_symbol": best["symbol"],
                "scanned_symbol_count": len(symbols),
                "candidate_count": len(entry_candidates),
                "top_score": top_score,
                "leader_score_tolerance": leader_score_tolerance,
                "selected_score": best["score"],
                "selected_score_margin": best["score_margin"],
                "selected_regime": best["regime"],
                "selected_support_count": best["support_count"],
                "selected_negative_total": best["negative_total"],
                "leader_rank": best["leader_rank"],
                "turnover_24h": best["turnover_24h"],
                "quote_turnover_24h": best["quote_turnover_24h"],
                "minimum_quote_turnover_24h": min_quote_turnover_24h,
                "selection_mode": selection_mode,
                "ranking_rule": (
                    "score_shortlist,leader_within_tolerance,"
                    "score,regime,support,negative,turnover"
                ),
                "compared_candidates": compared_candidates,
            }
            selected_signal = best["signal"] if trade_success else "HOLD"
            selected_reason = (
                f"全市场评分对比[{len(symbols)}扫描/{len(entry_candidates)}合格] "
                f"选择 {best['symbol']}({best['score']:.2f}分/{selection_mode}): "
                f"{best['reason']}"
            )
            if not trade_success:
                rank_details["trade_error"] = self._last_trade_error or "unknown error"
                selected_reason = f"择优下单失败: {selected_reason}"
            else:
                any_trade = True
                last_signal = selected_signal
            db.add(StrategyLog(
                strategy_id=strategy_id,
                user_id=strategy_user_id,
                exchange_config_id=config.id,
                symbol=best["raw_symbol"],
                signal=selected_signal,
                price=best["price"],
                quantity=self._last_trade_quantity if trade_success else None,
                reason=selected_reason,
                details=rank_details,
            ))
            await db.commit()
            self._last_trade_quantity = None
            self._pending_trade_quantity = None
            self._pending_trade_context = None
        
        current_strategy = await db.get(TradingStrategy, strategy_id)
        if current_strategy is not None:
            current_strategy.last_run_at = datetime.now(timezone.utc)
            current_strategy.last_signal = last_signal if any_trade else "HOLD"
            await db.commit()
    
    def _grid_strategy(self, strategy: TradingStrategy, current_price: float) -> tuple:
        params = strategy.params or {}
        grid_low = params.get("grid_low", current_price * 0.95)
        grid_high = params.get("grid_high", current_price * 1.05)
        
        if current_price <= grid_low:
            return "BUY", f"?? {current_price} ?????? {grid_low}"
        elif current_price >= grid_high:
            return "SELL", f"?? {current_price} ?????? {grid_high}"
        return "HOLD", f"?? {current_price} ??????"
    
    async def _rsi_strategy(self, strategy, current_price, symbol):
        klines = await okx_manager.get_candles(symbol, "1H", 15)
        if len(klines) < 14:
            return "HOLD", "????"
        
        closes = [float(k[4]) for k in klines]
        rsi = self._calculate_rsi(closes)
        
        params = strategy.params or {}
        oversold = params.get("oversold", 30)
        overbought = params.get("overbought", 70)
        
        if rsi < oversold:
            return "BUY", f"RSI {rsi:.2f} ?? (< {oversold})"
        elif rsi > overbought:
            return "SELL", f"RSI {rsi:.2f} ?? (> {overbought})"
        return "HOLD", f"RSI {rsi:.2f} ??"
    
    async def _ma_cross_strategy(self, strategy, current_price, symbol):
        klines = await okx_manager.get_candles(symbol, "1H", 50)
        if len(klines) < 20:
            return "HOLD", "????"
        
        closes = [float(k[4]) for k in klines]
        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20
        
        if ma5 > ma20 and strategy.last_signal != "BUY":
            return "BUY", f"MA5({ma5:.2f}) ?? MA20({ma20:.2f})"
        elif ma5 < ma20 and strategy.last_signal != "SELL":
            return "SELL", f"MA5({ma5:.2f}) ?? MA20({ma20:.2f})"
        return "HOLD", f"MA5({ma5:.2f}) vs MA20({ma20:.2f})"

    async def _micro_scalp_strategy(self, strategy, current_price, symbol, config):
        """1?????????????????5???????"""
        params = strategy.params or {}
        timeframe = params.get("timeframe", "1m")
        confirm_timeframe = params.get("confirm_timeframe", "5m")
        lookback = max(30, int(params.get("lookback", 40) or 40))
        min_score = float(params.get("min_score", 4) or 4)
        min_move_pct = float(params.get("min_move_pct", 0.0008) or 0.0008)
        volume_multiplier = float(params.get("volume_multiplier", 1.25) or 1.25)
        ma_fast_len = max(2, int(params.get("ma_fast", 3) or 3))
        ma_slow_len = max(ma_fast_len + 1, int(params.get("ma_slow", 9) or 9))
        momentum_window = max(2, int(params.get("momentum_window", 3) or 3))
        rsi_overbought = float(params.get("rsi_overbought", 72) or 72)
        rsi_oversold = float(params.get("rsi_oversold", 28) or 28)
        position_factor = float(params.get("micro_position_factor", 1) or 1)
        base_qty = float(params.get("quantity", 0.01) or 0.01)

        strategy_side = (getattr(strategy, "side", "BUY") or "BUY").upper()
        allow_long = bool(params.get("allow_long", strategy_side in ("BUY", "BOTH")))
        allow_short = bool(params.get("allow_short", strategy_side in ("SELL", "BOTH")))

        details = {
            "strategy_type": "micro_scalp",
            "timeframe": timeframe,
            "confirm_timeframe": confirm_timeframe,
            "min_score": min_score,
        }

        normalized_symbol = self._normalize_strategy_symbol(symbol)
        if normalized_symbol in self._get_excluded_strategy_symbols(strategy):
            return "HOLD", f"??1m: ?????????????({normalized_symbol})", details

        max_open_symbols = int(params.get("max_open_symbols", 1) or 1)
        strategy_position_keys = await self._get_strategy_live_position_keys(config, strategy)
        if len(strategy_position_keys) >= max_open_symbols:
            return "HOLD", f"??1m: ????????????({len(strategy_position_keys)}/{max_open_symbols})", details

        cooldown_minutes = float(params.get("open_cooldown_minutes", 3) or 3)
        if cooldown_minutes > 0:
            try:
                async with AsyncSessionLocal() as cd_db:
                    result = await cd_db.execute(
                        select(StrategyLog)
                        .where(
                            StrategyLog.strategy_id == strategy.id,
                            StrategyLog.exchange_config_id == config.id,
                            StrategyLog.symbol == symbol,
                            StrategyLog.signal.in_(("BUY", "SELL")),
                            ~StrategyLog.reason.like("[??]%"),
                            ~StrategyLog.reason.like("[????]%"),
                        )
                        .order_by(StrategyLog.created_at.desc())
                        .limit(1)
                    )
                    last_open_log = result.scalar_one_or_none()
                    if last_open_log and last_open_log.created_at:
                        last_ts = last_open_log.created_at
                        if last_ts.tzinfo is None:
                            last_ts = last_ts.replace(tzinfo=timezone.utc)
                        elapsed_min = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
                        if elapsed_min < cooldown_minutes:
                            return "HOLD", f"??1m: ?????({elapsed_min:.1f}/{cooldown_minutes:g}??)", details
            except Exception:
                pass

        klines = await okx_manager.get_candles(symbol, timeframe, lookback)
        if len(klines) < max(20, ma_slow_len + momentum_window + 2):
            return "HOLD", f"??1m: {timeframe} K???", details

        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        ma_fast = sum(closes[-ma_fast_len:]) / ma_fast_len
        ma_slow = sum(closes[-ma_slow_len:]) / ma_slow_len
        prev_price = closes[-momentum_window - 1]
        momentum = (closes[-1] - prev_price) / prev_price if prev_price > 0 else 0
        avg_volume_window = volumes[-21:-1] if len(volumes) >= 21 else volumes[:-1]
        avg_volume = sum(avg_volume_window) / len(avg_volume_window) if avg_volume_window else 0
        volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 0
        rsi = self._calculate_rsi(closes[-15:], period=14) if len(closes) >= 15 else 50

        confirm_trend = "flat"
        confirm_klines = await okx_manager.get_candles(symbol, confirm_timeframe, 24)
        if len(confirm_klines) >= 13:
            confirm_closes = [float(k[4]) for k in confirm_klines]
            confirm_fast = sum(confirm_closes[-5:]) / 5
            confirm_slow = sum(confirm_closes[-13:]) / 13
            if confirm_fast > confirm_slow:
                confirm_trend = "up"
            elif confirm_fast < confirm_slow:
                confirm_trend = "down"

        long_score = 0.0
        short_score = 0.0
        long_reasons = []
        short_reasons = []

        if momentum >= min_move_pct:
            long_score += 1
            long_reasons.append(f"1m??+{momentum * 100:.2f}%")
        if momentum <= -min_move_pct:
            short_score += 1
            short_reasons.append(f"1m??{momentum * 100:.2f}%")
        if ma_fast > ma_slow:
            long_score += 1
            long_reasons.append(f"MA{ma_fast_len}>{ma_slow_len}")
        if ma_fast < ma_slow:
            short_score += 1
            short_reasons.append(f"MA{ma_fast_len}<MA{ma_slow_len}")
        if volume_ratio >= volume_multiplier:
            long_score += 1
            short_score += 1
            long_reasons.append(f"??x{volume_ratio:.2f}")
            short_reasons.append(f"??x{volume_ratio:.2f}")
        if confirm_trend == "up":
            long_score += 1
            long_reasons.append(f"{confirm_timeframe}????")
        elif confirm_trend == "down":
            short_score += 1
            short_reasons.append(f"{confirm_timeframe}????")
        if rsi_oversold < rsi < rsi_overbought:
            long_score += 1
            short_score += 1
            long_reasons.append(f"RSI{rsi:.1f}???")
            short_reasons.append(f"RSI{rsi:.1f}???")

        details.update({
            "long_score": long_score,
            "short_score": short_score,
            "momentum_pct": round(momentum * 100, 4),
            "volume_ratio": round(volume_ratio, 4),
            "rsi": round(rsi, 2),
            "ma_fast": round(ma_fast, 6),
            "ma_slow": round(ma_slow, 6),
            "confirm_trend": confirm_trend,
        })

        signal = "HOLD"
        reasons = []
        if allow_long and long_score >= min_score and long_score > short_score:
            signal = "BUY"
            reasons = long_reasons
        elif allow_short and short_score >= min_score and short_score > long_score:
            signal = "SELL"
            reasons = short_reasons
        else:
            reason = (
                f"??1m: ?{long_score:g}/{min_score:g} ?{short_score:g}/{min_score:g} | "
                f"??{momentum * 100:.2f}% ??x{volume_ratio:.2f} RSI{rsi:.1f} {confirm_timeframe}:{confirm_trend}"
            )
            return "HOLD", reason, details

        allowed_signals = self._get_allowed_micro_scalp_signals(strategy, normalized_symbol)
        if allowed_signals is not None and signal not in allowed_signals:
            allowed_text = "/".join(sorted(allowed_signals))
            return "HOLD", f"??1m: {normalized_symbol} ??? {allowed_text}???{signal}???", details

        if strategy.last_signal == signal:
            side_text = "?" if signal == "BUY" else "?"
            return "HOLD", f"??1m: ??{side_text}???????????", details

        target_direction = "LONG" if signal == "BUY" else "SHORT"
        same_symbol_positions = {
            direction
            for pos_symbol, direction in strategy_position_keys
            if pos_symbol == normalized_symbol
        }
        if same_symbol_positions:
            if not bool(params.get("allow_add_existing_position", False)):
                return "HOLD", f"??1m: {normalized_symbol} ???????????/??", details
            if target_direction not in same_symbol_positions and not bool(params.get("allow_hedge_same_symbol", False)):
                return "HOLD", f"??1m: {normalized_symbol} ?????????????", details

        qty = 0.0
        trade_context = None
        if params.get("margin_sizing_enabled", True):
            try:
                trade_context = await self._calc_white_dove_margin_plan(
                    strategy=strategy,
                    config=config,
                    current_price=current_price,
                    position_factor=position_factor,
                    symbol=symbol,
                )
                qty = float(trade_context.get("quantity", 0) or 0)
            except Exception as e:
                reasons.append(f"????????????????: {e}")
            if trade_context and qty <= 0:
                details["trade_context"] = trade_context
                return "HOLD", f"??1m: ???????????????{self._format_trade_context(trade_context)}", details
        if qty <= 0:
            qty = base_qty
            trade_context = None

        self._pending_trade_quantity = max(qty, 0)
        self._pending_trade_context = trade_context
        details["trade_context"] = trade_context
        side_text = "??" if signal == "BUY" else "??"
        reason = f"??1m{side_text}??{max(long_score, short_score):g}/{min_score:g}: {' | '.join(reasons)}"
        return signal, f"{reason}{self._format_trade_context(trade_context)}", details
    
    async def _divergence_strategy(self, strategy, current_price, symbol):
        """??????"""
        if not self._div_system:
            return "HOLD", "???????"
        
        params = strategy.params or {}
        timeframe = params.get("timeframe", "5m")
        bar_map = {"5m": "5m", "15m": "15m", "30m": "30m", "1H": "1H", "4H": "4H"}
        bar = bar_map.get(timeframe, "5m")
        
        klines = await okx_manager.get_candles(symbol, bar, 100)
        if len(klines) < 30:
            return "HOLD", "K?????"
        
        candles = []
        for k in klines:
            candles.append({
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5]),
                'time': k[0]
            })
        
        has_div, info, msg = self._div_system['get_divergence_summary'](candles, timeframe)
        
        if has_div and info and info.get('direction') == 'bullish':
            return "BUY", f"{msg} | ??? {current_price}"
        if has_div and info and info.get('direction') == 'bearish':
            return "SELL", f"{msg} | ??? {current_price}"
        
        return "HOLD", f"{msg} | ??? {current_price}"
    
    async def _liquidation_map_strategy(self, strategy, current_price, symbol):
        """???????"""
        if not self._liq_signal:
            return "HOLD", "?????????"
        
        params = strategy.params or {}
        # ?????????????
        custom_zones = params.get("zones")
        if custom_zones:
            self._liq_signal.update_zones(custom_zones)
        
        coin = symbol.split("-")[0]
        result, reason = self._liq_signal.check_signal(coin, current_price)
        
        if not result:
            return "HOLD", reason
        
        sig = result.get('signal')
        strength = result.get('strength', 0)
        
        if sig in ('LONG', 'LONG_BIAS') and strength >= params.get("min_strength", 2):
            return "BUY", result.get('reason', reason)
        elif sig == 'SHORT_BIAS' and strength >= params.get("min_strength", 2):
            return "SELL", result.get('reason', reason)
        elif sig == 'CAUTION':
            return "HOLD", f"?? {result.get('reason', reason)}"
        
        return "HOLD", result.get('reason', reason)
    
    async def _macro_filtered_strategy(self, strategy, current_price, symbol):
        """??????"""
        if not self._macro_filter:
            return "HOLD", "????????"
        
        params = strategy.params or {}
        can_open, reason = self._macro_filter.check_can_open_position(
            current_positions=params.get("current_positions", 0)
        )
        
        if not can_open:
            return "HOLD", f"????: {reason}"
        
        # ????????????????
        klines = await okx_manager.get_candles(symbol, "1H", 20)
        if len(klines) < 10:
            return "HOLD", "????"
        
        closes = [float(k[4]) for k in klines]
        ma10 = sum(closes[-10:]) / 10
        
        if current_price > ma10 * 1.01:
            return "BUY", f"?????????{current_price}??MA10({ma10:.2f})"
        elif current_price < ma10 * 0.99:
            return "SELL", f"?????????{current_price}??MA10({ma10:.2f})"
        
        return "HOLD", f"???????????MA10({ma10:.2f})??"

    def _harmonic_classify_pattern(
        self,
        x_price: float,
        a_price: float,
        b_price: float,
        c_price: float,
        d_price: float,
        tol: float,
    ) -> str | None:
        """Classify an XABCD candidate using the SkillHub harmonic ratios."""
        xa = abs(a_price - x_price)
        ab = abs(b_price - a_price)
        bc = abs(c_price - b_price)
        cd = abs(d_price - c_price)
        if xa == 0 or ab == 0 or bc == 0:
            return None

        b_retrace = ab / xa
        d_retrace = abs(d_price - a_price) / xa
        cd_ratio = cd / bc if bc else 0
        rules = {
            "Gartley": ((0.55, 0.68), (0.72, 0.84), (1.27, 1.618)),
            "Bat": ((0.33, 0.55), (0.82, 0.94), (1.618, 2.618)),
            "Butterfly": ((0.72, 0.84), (1.20, 1.38), (1.618, 2.618)),
            "Crab": ((0.33, 0.68), (1.52, 1.72), (2.24, 3.618)),
        }
        for name, (b_range, d_range, cd_range) in rules.items():
            b_ok = b_range[0] - tol <= b_retrace <= b_range[1] + tol
            d_ok = d_range[0] - tol <= d_retrace <= d_range[1] + tol
            cd_ok = cd_range[0] - tol <= cd_ratio <= cd_range[1] + tol
            # B and D define the reversal geometry; CD is a soft sanity check.
            if b_ok and d_ok and (cd_ok or tol >= 0.1):
                return name
        return None

    def _detect_harmonic_patterns(
        self,
        rows: list[dict],
        swing_window: int = 8,
        tol: float = 0.12,
    ) -> list[dict]:
        """Detect XABCD harmonic patterns without pandas/pyharmonics."""
        if len(rows) < swing_window * 4:
            return []

        swings = []
        for idx in range(swing_window, len(rows) - swing_window):
            row = rows[idx]
            neighborhood = rows[idx - swing_window: idx + swing_window + 1]
            high = float(row["high"])
            low = float(row["low"])
            if high >= max(float(r["high"]) for r in neighborhood):
                swings.append({"idx": idx, "time": row.get("time"), "price": high, "type": "H"})
            if low <= min(float(r["low"]) for r in neighborhood):
                swings.append({"idx": idx, "time": row.get("time"), "price": low, "type": "L"})

        swings.sort(key=lambda item: item["idx"])
        merged = []
        for swing in swings:
            if not merged or merged[-1]["type"] != swing["type"]:
                merged.append(swing)
                continue
            prev = merged[-1]
            if swing["type"] == "H" and swing["price"] > prev["price"]:
                merged[-1] = swing
            elif swing["type"] == "L" and swing["price"] < prev["price"]:
                merged[-1] = swing

        found = []
        for idx in range(len(merged) - 4):
            pts = merged[idx: idx + 5]
            if any(pts[i]["type"] == pts[i + 1]["type"] for i in range(4)):
                continue
            pattern = self._harmonic_classify_pattern(
                pts[0]["price"], pts[1]["price"], pts[2]["price"],
                pts[3]["price"], pts[4]["price"], tol,
            )
            if not pattern:
                continue
            direction = "bullish" if pts[0]["type"] == "L" else "bearish"
            found.append({
                "pattern": pattern,
                "direction": direction,
                "d_pos": pts[4]["idx"],
                "d_time": pts[4].get("time"),
                "d_price": pts[4]["price"],
            })
        return found

    async def _get_harmonic_filter_signal(self, symbol: str, params: dict) -> dict:
        """Return the latest fresh harmonic signal for white-dove filtering."""
        tf_configs = params.get("harmonic_timeframes") or [
            {"bar": "4H", "weight": 1, "swing_window": 6, "max_age_bars": 12, "limit": 240},
            {"bar": "1D", "weight": 1, "swing_window": 8, "max_age_bars": 16, "limit": 260},
        ]
        tol = float(params.get("harmonic_tolerance", 0.12) or 0.12)
        fresh = []
        details = []

        for cfg in tf_configs:
            bar = str(cfg.get("bar", "4H"))
            limit = int(cfg.get("limit", 240) or 240)
            swing_window = int(cfg.get("swing_window", 8) or 8)
            max_age_bars = int(cfg.get("max_age_bars", max(swing_window * 2, 12)) or 12)
            try:
                klines = await okx_manager.get_candles(symbol, bar, limit)
            except Exception as exc:
                details.append(f"{bar}????: {exc}")
                continue

            rows = [{
                "time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            } for k in klines]
            patterns = self._detect_harmonic_patterns(rows, swing_window=swing_window, tol=tol)
            recent = [
                p for p in patterns
                if len(rows) - 1 - int(p.get("d_pos", -9999)) <= max_age_bars
            ]
            if recent:
                latest = max(recent, key=lambda p: p["d_pos"])
                latest["bar"] = bar
                latest["age_bars"] = len(rows) - 1 - latest["d_pos"]
                latest["weight"] = int(cfg.get("weight", 1) or 1)
                fresh.append(latest)
                side_text = "??" if latest["direction"] == "bullish" else "??"
                details.append(
                    f"{bar}{latest['pattern']}{side_text}(D??{latest['age_bars']}?)"
                )
            elif patterns:
                latest = max(patterns, key=lambda p: p["d_pos"])
                age = len(rows) - 1 - latest["d_pos"]
                details.append(f"{bar}??{latest['pattern']}???(D??{age}?)")
            else:
                details.append(f"{bar}??????")

        if not fresh:
            return {
                "direction": "neutral",
                "signal": 0,
                "detail": " | ".join(details) or "??????",
                "raw": {"patterns": []},
            }

        latest = max(fresh, key=lambda p: (p["d_pos"], p.get("weight", 1)))
        signal = 1 if latest["direction"] == "bullish" else -1
        return {
            "direction": latest["direction"],
            "signal": signal,
            "detail": " | ".join(details),
            "raw": {"patterns": fresh, "selected": latest},
        }

    async def _get_btc_4h_market_gate(self, params: dict) -> dict:
        """Use BTC 4H structure as a market-wide directional permission gate."""
        symbol = str(params.get("btc_market_gate_symbol", "BTC-USDT-SWAP") or "BTC-USDT-SWAP")
        bar = str(params.get("btc_market_gate_timeframe", "4H") or "4H")
        fast_period = int(params.get("btc_market_gate_ma_fast", 34) or 34)
        slow_period = int(params.get("btc_market_gate_ma_slow", 170) or 170)
        slope_bars = int(params.get("btc_market_gate_slope_bars", 8) or 8)
        slope_threshold = float(params.get("btc_market_gate_slope_threshold", 0.0005) or 0.0005)
        try:
            klines = await okx_manager.get_candles(symbol, bar, max(slow_period + slope_bars + 8, 80))
            closes = [float(k[4]) for k in klines if len(k) > 4]
            if len(closes) < slow_period + slope_bars:
                return {"state": "unknown", "entry_allowed": True, "detail": f"BTC {bar} data insufficient"}
            price = closes[-1]
            ma_fast = sum(closes[-fast_period:]) / fast_period
            ma_slow = sum(closes[-slow_period:]) / slow_period
            fast_prev = sum(closes[-fast_period - slope_bars:-slope_bars]) / fast_period
            slope = (ma_fast - fast_prev) / fast_prev if fast_prev else 0.0
            if price > ma_fast > ma_slow and slope >= slope_threshold:
                state = "strong_long"
            elif price < ma_fast < ma_slow and slope <= -slope_threshold:
                state = "strong_short"
            else:
                state = "range"
            return {
                "state": state,
                "entry_allowed": True,
                "detail": f"BTC {bar}: price={price:.6g} MA{fast_period}={ma_fast:.6g} MA{slow_period}={ma_slow:.6g} slope={slope*100:.3f}% state={state}",
                "raw": {"symbol": symbol, "bar": bar, "price": price, "ma_fast": ma_fast, "ma_slow": ma_slow, "slope": slope},
            }
        except Exception as exc:
            return {"state": "unknown", "entry_allowed": True, "detail": f"BTC market gate unavailable: {exc}"}

    async def _get_trend_regime_signal(self, symbol: str, params: dict, *, closed_only: bool = False) -> dict:
        """Classify trend: higher timeframe sets direction, entry timeframe sets execution."""
        higher_bar = str(params.get("trend_regime_higher_timeframe", "4H") or "4H")
        entry_bar = str(params.get("trend_regime_entry_timeframe", "30m") or "30m")
        ma_fast_period = int(params.get("trend_regime_ma_fast", 34) or 34)
        ma_slow_period = int(params.get("trend_regime_ma_slow", 170) or 170)
        higher_slope_bars = int(params.get("trend_regime_higher_slope_bars", 8) or 8)
        entry_slope_bars = int(params.get("trend_regime_entry_slope_bars", 12) or 12)
        slope_threshold = float(params.get("trend_regime_slope_threshold", 0.0005) or 0.0005)

        async def _snapshot(bar: str, slope_bars: int) -> dict:
            limit = max(ma_slow_period + slope_bars + 8, 80)
            klines = await okx_manager.get_candles(symbol, bar, limit)
            if closed_only:
                klines = confirmed_candles(klines, bar)
            closes = [float(k[4]) for k in klines if len(k) > 4]
            if len(closes) < ma_slow_period + slope_bars:
                return {"ok": False, "bar": bar, "count": len(closes)}
            current = closes[-1]
            fast_now = sum(closes[-ma_fast_period:]) / ma_fast_period
            slow_now = sum(closes[-ma_slow_period:]) / ma_slow_period
            fast_prev = sum(closes[-ma_fast_period - slope_bars:-slope_bars]) / ma_fast_period
            fast_slope = (fast_now - fast_prev) / fast_prev if fast_prev else 0.0
            long_state = current > fast_now and fast_now > slow_now and fast_slope >= slope_threshold
            short_state = current < fast_now and fast_now < slow_now and fast_slope <= -slope_threshold
            return {
                "ok": True,
                "bar": bar,
                "price": current,
                "ma_fast": fast_now,
                "ma_slow": slow_now,
                "slope": fast_slope,
                "long_state": long_state,
                "short_state": short_state,
                "closed_only": closed_only,
            }

        higher = await _snapshot(higher_bar, higher_slope_bars)
        entry = await _snapshot(entry_bar, entry_slope_bars)
        if not higher.get("ok") or not entry.get("ok"):
            return {
                "regime": "unknown",
                "score": 0.0,
                "position_multiplier": float(params.get("trend_regime_unknown_position_multiplier", 0.0) or 0.0),
                "entry_allowed": False,
                "add_allowed": False,
                "detail": f"trend data insufficient higher={higher.get('count')} entry={entry.get('count')}",
                "raw": {"higher": higher, "entry": entry},
            }

        if higher["long_state"] and entry["long_state"]:
            regime = "strong_long"
            score = float(params.get("trend_regime_strong_score", 2.0) or 2.0)
            position_multiplier = 1.0
            entry_allowed = True
            add_allowed = True
        elif higher["short_state"] and entry["short_state"]:
            regime = "strong_short"
            score = -float(params.get("trend_regime_strong_score", 2.0) or 2.0)
            position_multiplier = 1.0
            entry_allowed = True
            add_allowed = True
        elif entry["long_state"] and not higher["short_state"]:
            regime = "weak_long"
            score = float(params.get("trend_regime_weak_score", 0.8) or 0.8)
            position_multiplier = float(
                params.get(
                    "trend_regime_rebound_long_position_multiplier",
                    params.get("trend_regime_weak_position_multiplier", 0.25),
                )
                or 0.25
            )
            entry_allowed = bool(params.get("trend_regime_allow_rebound_long", False))
            add_allowed = False
        elif entry["short_state"] and not higher["long_state"]:
            regime = "weak_short"
            score = -float(params.get("trend_regime_weak_score", 0.8) or 0.8)
            position_multiplier = float(params.get("trend_regime_weak_position_multiplier", 0.35) or 0.35)
            entry_allowed = bool(params.get("trend_regime_allow_weak_entry", False))
            add_allowed = False
        else:
            regime = "range"
            score = 0.0
            position_multiplier = float(params.get("trend_regime_range_position_multiplier", 0.0) or 0.0)
            entry_allowed = bool(params.get("trend_regime_allow_range_entry", False))
            add_allowed = False

        detail = (
            f"{higher_bar}: price={higher['price']:.6g} MA{ma_fast_period}={higher['ma_fast']:.6g} "
            f"MA{ma_slow_period}={higher['ma_slow']:.6g} slope={higher['slope']*100:.3f}% | "
            f"{entry_bar}: price={entry['price']:.6g} MA{ma_fast_period}={entry['ma_fast']:.6g} "
            f"MA{ma_slow_period}={entry['ma_slow']:.6g} slope={entry['slope']*100:.3f}% | regime={regime}"
        )
        return {
            "regime": regime,
            "score": score,
            "position_multiplier": max(0.0, min(1.0, position_multiplier)),
            "entry_allowed": entry_allowed,
            "add_allowed": add_allowed,
            "detail": detail,
            "raw": {"higher": higher, "entry": entry},
        }

    @staticmethod
    def _score_based_regime_entry_block(
        direction: str,
        trend_regime: str,
        factors: list,
        score_based_entry_mode: bool,
        params: dict,
    ) -> str | None:
        if not score_based_entry_mode or not bool(
            params.get("score_based_regime_filter_enabled", False)
        ):
            return None

        direction = (direction or "").upper()
        regime = (trend_regime or "unknown").lower()
        aligned = {
            "LONG": {"strong_long", "weak_long"},
            "SHORT": {"strong_short", "weak_short"},
        }.get(direction, set())
        opposite = {
            "LONG": {"strong_short", "weak_short"},
            "SHORT": {"strong_long", "weak_long"},
        }.get(direction, set())

        if regime in aligned:
            weak_regime = "weak_long" if direction == "LONG" else "weak_short"
            if regime == weak_regime and not bool(
                params.get("score_based_allow_weak_entry", True)
            ):
                return (
                    "score-based entry blocked because weak trend entry is disabled: "
                    f"{regime}"
                )
            return None
        if regime in opposite:
            return f"score-based entry blocked by opposite trend regime: {regime}"
        if regime in {"unknown", ""}:
            return "score-based entry blocked because trend regime is unknown"
        if regime == "range" and bool(
            params.get("score_based_range_require_divergence", True)
        ):
            has_divergence = any(
                isinstance(factor, dict)
                and factor.get("key") == "divergence"
                and bool(factor.get("triggered"))
                for factor in factors
            )
            if not has_divergence:
                return "score-based range entry requires real divergence structure"
        return None

    @staticmethod
    def _apply_score_based_regime_sizing(
        position_factor: float,
        base_position_factor: float,
        direction: str,
        trend_regime: str,
        dynamic_score: dict,
        score_based_entry_mode: bool,
        params: dict,
    ) -> tuple[float, str | None]:
        if not score_based_entry_mode or not bool(
            params.get("score_based_regime_filter_enabled", False)
        ):
            return position_factor, None

        direction = (direction or "").upper()
        regime = (trend_regime or "unknown").lower()
        base = max(0.0, float(base_position_factor or 0.0))
        factor = max(0.0, float(position_factor or 0.0))
        strong_regime = "strong_long" if direction == "LONG" else "strong_short"
        weak_regime = "weak_long" if direction == "LONG" else "weak_short"

        if regime == strong_regime and not (dynamic_score.get("negative_factors") or []):
            floor_multiplier = max(
                0.0,
                min(1.0, float(params.get(
                    "score_based_strong_trend_min_position_multiplier", 1.0
                ) or 1.0)),
            )
            factor = max(factor, base * floor_multiplier)
            return factor, f"regime sizing: {regime} floor x{floor_multiplier:g}"
        if regime == weak_regime:
            cap_multiplier = max(
                0.05,
                min(1.0, float(params.get(
                    "score_based_weak_trend_position_cap", 0.40
                ) or 0.40)),
            )
            factor = min(factor, base * cap_multiplier)
            return factor, f"regime sizing: {regime} cap x{cap_multiplier:g}"
        if regime == "range":
            cap_multiplier = max(
                0.05,
                min(1.0, float(params.get(
                    "score_based_range_position_cap", 0.25
                ) or 0.25)),
            )
            factor = min(factor, base * cap_multiplier)
            return factor, f"regime sizing: range cap x{cap_multiplier:g}"
        return factor, None

    async def _get_short_term_bullish_confirmation(self, symbol: str, params: dict) -> dict:
        """Confirm a rebound with 5m structure or a short-term MA reclaim."""
        bar = str(params.get("harmonic_rebound_confirm_timeframe", "5m") or "5m")
        limit = int(params.get("harmonic_rebound_confirm_limit", 100) or 100)
        klines = await okx_manager.get_candles(symbol, bar, limit)
        if len(klines) < 40:
            return {"triggered": False, "detail": f"{bar}K???({len(klines)})", "raw": {"bar": bar}}

        candles = [{
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "time": k[0],
        } for k in klines]

        allowed = set(params.get("harmonic_rebound_confirm_signals") or [
            "bottom_divergence", "jin1_ext", "jin1_std", "jin2_approx", "jin3_approx", "shou1", "shou2"
        ])
        if self._div_system:
            try:
                (has_long, long_info, long_msg), _ = self._div_system["scan_both_directions"](candles, bar)
                if has_long and long_info:
                    confidence = round(long_info.get("confidence") or 0)
                    stype = long_info.get("stype")
                    min_conf = int(params.get("harmonic_rebound_min_chanlun_confidence", 60) or 60)
                    if confidence >= min_conf and stype in allowed:
                        return {
                            "triggered": True,
                            "detail": f"{bar}????({stype}, ??{confidence}): {long_msg}",
                            "raw": {"bar": bar, "mode": "chanlun", "stype": stype, "confidence": confidence},
                        }
            except Exception as exc:
                pass

        closes = [float(c["close"]) for c in candles]
        ma5 = sum(closes[-5:]) / 5
        ma5_prev = sum(closes[-6:-1]) / 5
        ma34 = sum(closes[-34:]) / 34
        price = closes[-1]
        reclaim = price > ma34 and ma5 > ma5_prev
        if reclaim:
            return {
                "triggered": True,
                "detail": f"{bar}????MA34?MA5??: price={price:.6g} MA34={ma34:.6g} MA5={ma5:.6g}->{ma5_prev:.6g}",
                "raw": {"bar": bar, "mode": "ma_reclaim", "price": price, "ma34": ma34, "ma5": ma5, "ma5_prev": ma5_prev},
            }
        return {
            "triggered": False,
            "detail": f"{bar}?????: price={price:.6g} MA34={ma34:.6g} MA5={ma5:.6g}->{ma5_prev:.6g}",
            "raw": {"bar": bar, "mode": "ma_reclaim", "price": price, "ma34": ma34, "ma5": ma5, "ma5_prev": ma5_prev},
        }

    async def _get_elliott_wave_signal(self, symbol: str, params: dict) -> dict:
        """Return a fresh Elliott-wave directional signal.

        The installed SkillHub engine uses: 1 = long, -1 = short, 0 = neutral.
        """
        if ElliottWaveSignalEngine is None:
            return {"signal": 0, "score": 0, "detail": "elliott engine unavailable", "raw": {}}

        tf_configs = params.get("elliott_wave_timeframes") or [
            {"bar": "30m", "weight": 1.0, "max_age_bars": 18},
            {"bar": "4H", "weight": 1.0, "max_age_bars": 8},
        ]
        limit = int(params.get("elliott_wave_candle_limit", 220) or 220)
        swing_window = int(params.get("elliott_wave_swing_window", 8) or 8)
        fib_tolerance = float(params.get("elliott_wave_fib_tolerance", 0.18) or 0.18)
        min_wave_bars = int(params.get("elliott_wave_min_wave_bars", 4) or 4)
        engine = ElliottWaveSignalEngine(
            swing_window=swing_window,
            fib_tolerance=fib_tolerance,
            min_wave_bars=min_wave_bars,
        )

        total = 0.0
        details = []
        raw_signals = []
        min_rows = max(swing_window * 2 + 10, 60)

        for tf_cfg in tf_configs:
            bar = str(tf_cfg.get("bar", "30m"))
            weight = float(tf_cfg.get("weight", 1.0) or 1.0)
            max_age = int(tf_cfg.get("max_age_bars", params.get("elliott_wave_max_age_bars", 12)) or 12)
            tf_limit = int(tf_cfg.get("limit", limit) or limit)
            try:
                klines = await okx_manager.get_candles(symbol, bar, tf_limit)
            except Exception as exc:
                details.append(f"{bar}: fetch failed {exc}")
                continue
            if len(klines) < min_rows:
                details.append(f"{bar}: insufficient candles")
                continue

            rows = []
            for k in reversed(klines):
                try:
                    rows.append({
                        "ts": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    })
                except Exception:
                    continue
            if len(rows) < min_rows:
                details.append(f"{bar}: invalid candle rows")
                continue

            series = engine.generate({symbol: rows}).get(symbol)
            if not series:
                details.append(f"{bar}: no series")
                continue

            non_zero = [(idx, value) for idx, value in enumerate(series) if value != 0]
            if not non_zero:
                details.append(f"{bar}: neutral")
                raw_signals.append({"bar": bar, "signal": 0, "weight": weight})
                continue

            pos, sig = non_zero[-1]
            age = len(series) - 1 - int(pos)
            if age <= max_age:
                total += sig * weight
                side_text = "long" if sig > 0 else "short"
                details.append(f"{bar}: {side_text} signal age={age} weight={weight:g}")
                raw_signals.append({"bar": bar, "signal": sig, "age": age, "weight": weight, "pos": int(pos), "time": rows[pos].get("ts")})
            else:
                details.append(f"{bar}: stale signal age={age}>{max_age}")
                raw_signals.append({"bar": bar, "signal": sig, "age": age, "weight": weight, "stale": True, "pos": int(pos), "time": rows[pos].get("ts")})

        signal = 1 if total > 0 else -1 if total < 0 else 0
        return {
            "signal": signal,
            "score": total,
            "detail": " | ".join(details) or "elliott neutral",
            "raw": {
                "signals": raw_signals,
                "score": total,
                "swing_window": swing_window,
                "fib_tolerance": fib_tolerance,
                "min_wave_bars": min_wave_bars,
            },
        }
    
    async def _event_follow_gate(
        self,
        params: dict,
        direction: TrendV3Direction,
    ) -> tuple[bool, str, dict]:
        """Confirm an event move from two liquid index anchors before entry.

        The gate uses only closed one-minute candles.  It waits through the
        initial reaction, then requires SPY and QQQ to break the same side of
        their pre-event range with two confirming closes and abnormal volume.
        Once a direction is confirmed it is locked for this event.
        """
        config = params.get("event_follow") or {}
        if not isinstance(config, dict) or not config.get("enabled"):
            return True, "event_follow_disabled", {}

        event_id = str(config.get("event_id") or "event")
        start_raw = str(config.get("start_at") or "")
        end_raw = str(config.get("end_at") or "")
        try:
            start_at = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            end_at = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=timezone.utc)
            if end_at.tzinfo is None:
                end_at = end_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return False, "event_follow_invalid_window", {"event_id": event_id}

        now = datetime.now(timezone.utc)
        if now < start_at:
            return False, f"event_follow_waiting_until={start_at.isoformat()}", {"event_id": event_id}
        if now >= end_at:
            return False, f"event_follow_expired_at={end_at.isoformat()}", {"event_id": event_id}

        cooldown_seconds = max(0, int(config.get("cooldown_seconds", 180) or 0))
        if now < start_at + timedelta(seconds=cooldown_seconds):
            return False, "event_follow_initial_reaction_cooldown", {"event_id": event_id}

        anchors = config.get("anchors") or ["SPY-USDT-SWAP", "QQQ-USDT-SWAP"]
        if not isinstance(anchors, list) or len(anchors) < 2:
            return False, "event_follow_requires_two_anchors", {"event_id": event_id}
        anchors = [str(anchor) for anchor in anchors[:2]]
        pre_minutes = max(5, int(config.get("pre_event_range_minutes", 15) or 15))
        confirmations = max(1, int(config.get("confirmation_candles", 2) or 2))
        break_pct = max(0.0, float(config.get("break_pct", 0.0015) or 0.0015))
        min_move_pct = max(0.0, float(config.get("min_move_pct", 0.0035) or 0.0035))
        min_volume_ratio = max(1.0, float(config.get("min_volume_ratio", 2.0) or 2.0))
        start_ms = int(start_at.timestamp() * 1000)

        async def anchor_state(anchor: str) -> dict:
            rows = await okx_manager.get_candles(anchor, "1m", max(60, pre_minutes + confirmations + 10))
            closed = [
                row for row in rows
                if len(row) < 9 or str(row[8]) == "1"
            ]
            before = [row for row in closed if int(row[0]) < start_ms][-pre_minutes:]
            after = [row for row in closed if int(row[0]) >= start_ms]
            if len(before) < pre_minutes or len(after) < confirmations:
                return {"anchor": anchor, "direction": "WAIT", "reason": "insufficient_closed_candles"}
            recent = after[-confirmations:]
            pre_high = max(float(row[2]) for row in before)
            pre_low = min(float(row[3]) for row in before)
            baseline_volume = max(statistics.median(float(row[5]) for row in before), 1e-9)
            average_volume = statistics.mean(float(row[5]) for row in recent)
            volume_ratio = average_volume / baseline_volume
            last_close = float(recent[-1][4])
            reference_close = float(before[-1][4])
            move = last_close / reference_close - 1.0 if reference_close else 0.0
            long_ok = (
                all(float(row[4]) > pre_high * (1.0 + break_pct) for row in recent)
                and move >= min_move_pct
                and volume_ratio >= min_volume_ratio
            )
            short_ok = (
                all(float(row[4]) < pre_low * (1.0 - break_pct) for row in recent)
                and move <= -min_move_pct
                and volume_ratio >= min_volume_ratio
            )
            resolved = "LONG" if long_ok else "SHORT" if short_ok else "WAIT"
            return {
                "anchor": anchor, "direction": resolved, "pre_high": pre_high,
                "pre_low": pre_low, "last_close": last_close, "move": move,
                "volume_ratio": volume_ratio,
            }

        states = await asyncio.gather(*(anchor_state(anchor) for anchor in anchors))
        resolved = {state.get("direction") for state in states}
        if len(resolved) != 1 or "WAIT" in resolved:
            return False, "event_follow_anchor_disagreement_or_no_break", {
                "event_id": event_id, "anchors": states,
            }
        market_direction = resolved.pop()
        locked_direction = self._event_follow_direction_locks.get(event_id)
        if locked_direction and locked_direction != market_direction:
            return False, "event_follow_direction_locked", {
                "event_id": event_id, "locked_direction": locked_direction, "anchors": states,
            }
        self._event_follow_direction_locks.setdefault(event_id, market_direction)
        wanted = "LONG" if direction is TrendV3Direction.LONG else "SHORT"
        if market_direction != wanted:
            return False, f"event_follow_confirmed_{market_direction.lower()}", {
                "event_id": event_id, "anchors": states,
            }
        return True, f"event_follow_confirmed_{market_direction.lower()}", {
            "event_id": event_id, "anchors": states, "locked_direction": market_direction,
        }

    async def _v3_market_observation_factors(self, symbol, direction, params, adx_metrics, decision):
        # Shadow wiring must not silently turn a score bonus into a hard gate.
        adx_factor = {
            "name": "V3 ADX/ATR observation", "key": "trend_v3_adx_atr",
            "enabled": bool(adx_metrics.get("enabled", False)),
            "triggered": "4h_adx_confirms" in decision.reasons,
            "score_added": 1.0 if "4h_adx_confirms" in decision.reasons else 0.0,
            "detail": str(adx_metrics.get("reason", "")),
            "raw": {**adx_metrics, "mode": "observe", "enforced": False,
                    "would_block": adx_metrics.get("allowed") is False,
                    "scoring_threshold": float(params.get("trend_v3_strong_adx", 23.0) or 23.0)},
        }
        configured = params.get("oi_funding_filter") or {}
        observation_cfg = {"enabled": True, **configured, "mode": "observe"}
        try:
            observation = await asyncio.wait_for(
                self._derivatives_market_data.entry_observation(
                    symbol, direction, adx_metrics.get("atr_pct"), observation_cfg,
                ), timeout=2.0,
            )
        except Exception as exc:
            observation = {"enabled": bool(observation_cfg.get("enabled")),
                           "status": "unavailable", "would_block": False,
                           "reason": type(exc).__name__}
        return [adx_factor, {
            "name": "V3 OI/funding observation", "key": "oi_funding_context",
            "enabled": bool(observation.get("enabled", False)),
            "triggered": bool(observation.get("would_block", False)),
            "score_added": 0.0, "detail": str(observation.get("reason", "")),
            "raw": {**observation, "mode": "observe", "enforced": False,
                    "configured_mode": configured.get("mode", "observe"),
                    "affects_decision": False},
        }]

    async def _white_dove_v3_strategy(
        self,
        strategy: TradingStrategy,
        current_price: float,
        symbol: str,
        config: ExchangeConfig,
        has_current_pos: bool,
        score_only: bool = False,
    ) -> tuple[str, str, list[dict]]:
        """V3 trend decision, while reusing the proven White Dove executor."""
        params = strategy.params or {}
        side = (getattr(strategy, "side", "") or "").upper()
        if side not in {"BUY", "SELL"}:
            return "HOLD", "trend_v3_requires_one_direction_per_strategy", []
        direction = TrendV3Direction.LONG if side == "BUY" else TrendV3Direction.SHORT
        oil_fundamental = params.get("oil_fundamental") or {}
        fundamental_enabled = (
            oil_fundamental_applies_to(symbol)
            and bool(oil_fundamental.get("enabled", False))
        )
        fundamental_direction = None
        if fundamental_enabled:
            configured_direction = str(oil_fundamental.get("direction", "")).upper()
            if configured_direction not in {"LONG", "SHORT"}:
                return "HOLD", "oil_fundamental_direction_invalid", []
            if not oil_fundamental_confidence_is_sufficient(
                oil_fundamental.get("confidence"),
                oil_fundamental.get("min_confidence", 0.70),
            ):
                return "HOLD", "oil_fundamental_confidence_below_threshold", []
            fundamental_direction = TrendV3Direction(configured_direction)
        event_ok, event_reason, event_raw = await self._event_follow_gate(params, direction)
        event_factor = {
            "name": "Event follow confirmation", "key": "event_follow",
            "enabled": bool((params.get("event_follow") or {}).get("enabled")),
            "triggered": event_ok, "score_added": 0.0,
            "detail": event_reason, "raw": event_raw,
        }
        if not event_ok:
            return "HOLD", event_reason, [event_factor]
        trend = await self._get_trend_regime_signal(symbol, params, closed_only=True)
        trend_raw = trend.get("raw") or {}
        higher = trend_raw.get("higher") or {}
        entry = trend_raw.get("entry") or {}

        if direction is TrendV3Direction.LONG:
            trend_4h = 1 if higher.get("long_state") else -1 if higher.get("short_state") else 0
            structure_30m = 1 if entry.get("long_state") else -1 if entry.get("short_state") else 0
        else:
            trend_4h = 1 if higher.get("short_state") else -1 if higher.get("long_state") else 0
            structure_30m = 1 if entry.get("short_state") else -1 if entry.get("long_state") else 0

        if not higher.get("ok") or not entry.get("ok"):
            return "HOLD", "trend_v3_insufficient_confirmed_trend_candles", [event_factor]
        adx_metrics = await evaluate_adx_atr_regime(symbol, direction.value, params, closed_only=True)
        adx_4h = float(adx_metrics.get("adx", 0.0) or 0.0)
        candles_1m, candles_5m, candles_4h, candles_30m = await asyncio.gather(
            okx_manager.get_candles(symbol, "1m", 60),
            okx_manager.get_candles(symbol, "5m", 100),
            okx_manager.get_candles(symbol, "4H", 200),
            okx_manager.get_candles(symbol, "30m", 240),
        )
        quality_now_ms = int(time.time() * 1000)
        raw_candles_1m, raw_candles_5m = candles_1m, candles_5m
        candles_1m = confirmed_candles(candles_1m, "1m", now_ms=quality_now_ms)
        candles_5m = confirmed_candles(candles_5m, "5m", now_ms=quality_now_ms)
        candles_4h = confirmed_candles(candles_4h, "4H", now_ms=quality_now_ms)
        candles_30m = confirmed_candles(candles_30m, "30m", now_ms=quality_now_ms)
        if (
            len(candles_1m) < 25
            or len(candles_5m) < 35
            or len(candles_4h) < 170
            or len(candles_30m) < 40
        ):
            return "HOLD", "trend_v3_insufficient_closed_candles", []

        quality_1m = assess_signal_quality(raw_candles_1m, now_ms=quality_now_ms, period_ms=60000)
        activity_1m = quality_1m["intrabar_activity"] if quality_1m["intrabar_count"] else quality_1m["confirmed_activity"]
        current_move_1m = float(activity_1m["current_move"] or 0.0)
        volume_ratio_1m = float(activity_1m["volume_ratio"] or 0.0)
        speed_ratio_1m = float(activity_1m["speed_ratio"] or 0.0)
        directional_move_1m = (
            current_move_1m > 0
            if direction is TrendV3Direction.LONG
            else current_move_1m < 0
        )

        closes_5m = [float(row[4]) for row in candles_5m]
        ma34 = sum(closes_5m[-34:]) / 34
        min_volume_ratio = float(params.get("trend_v3_min_volume_ratio", 1.8) or 1.8)
        min_speed_ratio = float(params.get("trend_v3_min_speed_ratio", 1.5) or 1.5)
        require_volume_speed = params.get("trend_v3_require_volume_speed_for_entry", False)
        if isinstance(require_volume_speed, str):
            require_volume_speed = require_volume_speed.strip().lower() in {"1", "true", "yes", "on"}
        timing = entry_timing(
            raw_candles_5m, direction.value, now_ms=quality_now_ms,
            min_volume_ratio=min_volume_ratio,
            min_speed_ratio=min_speed_ratio,
            require_volume_speed=bool(require_volume_speed),
        )
        trigger_5m, volume_speed_trigger = timing["trigger"], timing["volume_speed"]
        structure_trigger = timing["cross"]
        volume_ratio, speed_ratio = timing["volume_ratio"], timing["speed_ratio"]
        current_move = timing["current_move"]
        activity_candle = timing["candle"]

        divergence_5m = False
        if self._div_system:
            normalized = [
                {
                    "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                    "close": float(row[4]), "volume": float(row[5]), "time": row[0],
                }
                for row in candles_5m
            ]
            try:
                (has_long, long_info, _), (has_short, short_info, _) = self._div_system["scan_both_directions"](normalized, "5m")
                if direction is TrendV3Direction.LONG:
                    divergence_5m = bool(has_long and long_info)
                else:
                    divergence_5m = bool(has_short and short_info)
            except Exception:
                divergence_5m = False

        if direction is TrendV3Direction.LONG:
            moer = evaluate_moer_long_structure(candles_4h, candles_30m)
            moer_reentry = str(moer.get("state")) in {"B2", "B3"}
        else:
            moer = evaluate_moer_short_structure(candles_4h, candles_30m)
            moer_reentry = str(moer.get("state")) in {"S2", "S3"}

        structure_confirmation_required = bool(
            params.get("trend_v3_require_one_structure_confirmation", False)
        )
        moer_reentry_required = bool(
            params.get("trend_v3_require_moer_reentry_for_entry", False)
        )
        structure_confirmed = has_structural_confirmation(
            moer_reentry,
            structure_trigger,
            divergence_5m,
        )
        runner_add_enabled = bool(params.get("trend_runner_add_enabled", False))
        require_moer_for_add = bool(
            params.get("trend_v3_require_moer_reentry_for_add", True)
        )
        min_add_volume_ratio_1m = float(
            params.get("trend_runner_add_min_1m_volume_ratio", 1.4) or 1.4
        )
        min_add_speed_ratio_1m = float(
            params.get("trend_runner_add_min_1m_speed_ratio", 1.2) or 1.2
        )
        micro_continuation = bool(
            directional_move_1m
            and volume_ratio_1m >= min_add_volume_ratio_1m
            and speed_ratio_1m >= min_add_speed_ratio_1m
        )
        continuation_add_confirmed = bool(
            runner_add_enabled
            and trigger_5m
            and (
                moer_reentry
                if require_moer_for_add
                else (volume_speed_trigger and micro_continuation)
            )
        )

        policy = TrendV3(
            min_open_score=float(params.get("trend_v3_min_open_score", 5.0) or 5.0),
            min_add_score=float(params.get("trend_v3_min_add_score", 6.0) or 6.0),
            strong_adx=float(params.get("trend_v3_strong_adx", 23.0) or 23.0),
            min_volume_ratio=float(params.get("trend_v3_min_volume_ratio", 1.8) or 1.8),
            min_speed_ratio=float(params.get("trend_v3_min_speed_ratio", 1.5) or 1.5),
        )
        macro_event_overlay = resolve_tradfi_macro_event_bonus(
            params,
            symbol,
            direction.value,
        )
        decision = policy.evaluate(TrendContext(
            symbol=symbol,
            direction=direction,
            trend_4h=trend_4h,
            adx_4h=adx_4h,
            structure_30m=structure_30m,
            moer_reentry_30m=moer_reentry,
            trigger_5m=trigger_5m,
            divergence_5m=divergence_5m,
            volume_ratio_5m=volume_ratio,
            speed_ratio_5m=speed_ratio,
            already_open=has_current_pos,
            add_on_confirmed=continuation_add_confirmed,
            fundamental_direction=fundamental_direction,
            fundamental_valid_until=str(oil_fundamental.get("valid_until", "")),
            macro_event_score=float(macro_event_overlay.get("score", 0.0) or 0.0),
            macro_event_label=str(macro_event_overlay.get("detail", "")),
        ))
        ma34_extension = resolve_directional_ma_extension(
            current_price,
            ma34,
            direction.value,
        )
        try:
            max_ma34_extension = max(
                0.0,
                float(params.get("trend_v3_max_entry_ma34_extension_pct", 0) or 0),
            )
        except (TypeError, ValueError):
            max_ma34_extension = 0.0
        extension_allowed = not max_ma34_extension or ma34_extension <= max_ma34_extension
        factors = [
            {
                "name": "Oil fundamental report", "key": "oil_fundamental",
                "enabled": fundamental_enabled,
                "triggered": fundamental_enabled and fundamental_direction is direction,
                "score_added": 0.0,
                "detail": str(oil_fundamental.get("report", "")),
                "raw": {
                    "direction": fundamental_direction.value if fundamental_direction else "",
                    "confidence": oil_fundamental.get("confidence"),
                    "min_confidence": oil_fundamental.get("min_confidence", 0.70),
                    "as_of": str(oil_fundamental.get("as_of", "")),
                    "valid_until": str(oil_fundamental.get("valid_until", "")),
                },
            },
            event_factor,
            {
                "name": "Scheduled macro event overlay", "key": "macro_event_overlay",
                "enabled": bool(macro_event_overlay.get("enabled", False)),
                "triggered": bool(macro_event_overlay.get("triggered", False)),
                "score_added": float(macro_event_overlay.get("score", 0.0) or 0.0),
                "detail": str(macro_event_overlay.get("detail", "")),
                "raw": macro_event_overlay.get("raw") or {},
            },
            {
                "name": "V3 4H trend", "key": "trend_filter", "enabled": True,
                "triggered": trend_4h > 0, "score_added": 2.0 if trend_4h > 0 else 0.0,
                "detail": trend.get("detail", ""), "raw": {"regime": trend.get("regime"), **trend_raw},
            },
            {
                "name": "V3 30m structure", "key": "trend_v3_30m", "enabled": True,
                "triggered": structure_30m > 0, "score_added": 1.5 if structure_30m > 0 else 0.0,
                "detail": str(moer.get("detail", "")), "raw": moer,
            },
            {
                "name": "V3 5m trigger", "key": "trend_v3_5m", "enabled": True,
                "triggered": trigger_5m, "score_added": 2.0 if trigger_5m and volume_ratio >= policy.min_volume_ratio and speed_ratio >= policy.min_speed_ratio else 1.0 if trigger_5m else 0.0,
                "detail": (
                    f"volume_ratio={volume_ratio:.2f} speed_ratio={speed_ratio:.2f} "
                    f"cross={structure_trigger} volume_speed={volume_speed_trigger} "
                    f"volume_speed_required={bool(require_volume_speed)} source={timing['source']} quality=entry_quality_v1"
                ),
                "raw": {
                    "volume_ratio": volume_ratio,
                    "speed_ratio": speed_ratio,
                    "current_move": current_move,
                    "candle_ts": str(activity_candle[0]),
                    "open": float(activity_candle[1]),
                    "high": float(activity_candle[2]),
                    "low": float(activity_candle[3]),
                    "close": float(activity_candle[4]),
                    "activity_source": timing["source"],
                    "structure_closed_only": True,
                    "quality": timing["quality"],
                    "volume_speed_trigger": volume_speed_trigger,
                    "volume_speed_required": bool(require_volume_speed),
                },
            },
            {
                "name": "Trend runner continuation", "key": "trend_runner_add",
                "enabled": runner_add_enabled,
                "triggered": continuation_add_confirmed,
                "score_added": 0.0,
                "detail": (
                    f"1m_volume={volume_ratio_1m:.2f}x 1m_speed={speed_ratio_1m:.2f}x "
                    f"5m_volume_speed={volume_speed_trigger} "
                    f"30m_reentry={moer_reentry}"
                ),
                "raw": {
                    "micro_continuation": micro_continuation,
                    "continuation_add_confirmed": continuation_add_confirmed,
                    "require_moer_for_add": require_moer_for_add,
                    "volume_ratio_1m": volume_ratio_1m,
                    "speed_ratio_1m": speed_ratio_1m,
                    "quality_1m": quality_1m,
                    "minimum_volume_ratio_1m": min_add_volume_ratio_1m,
                    "minimum_speed_ratio_1m": min_add_speed_ratio_1m,
                },
            },
            {
                "name": "V3 structural confirmation", "key": "trend_v3_structure_confirmation",
                "enabled": structure_confirmation_required,
                "triggered": structure_confirmed,
                "score_added": 0.0,
                "detail": (
                    f"30m_reentry={moer_reentry} 5m_ma_cross={structure_trigger} "
                    f"5m_divergence={divergence_5m}"
                ),
                "raw": {
                    "30m_reentry": moer_reentry,
                    "5m_ma_cross": structure_trigger,
                    "5m_divergence": divergence_5m,
                },
            },
            {
                "name": "V3 anti-chase gate", "key": "trend_v3_anti_chase",
                "enabled": max_ma34_extension > 0,
                "triggered": extension_allowed,
                "score_added": 0.0,
                "detail": (
                    f"ma34_extension={ma34_extension:.4f} "
                    f"maximum={max_ma34_extension:.4f}"
                ),
                "raw": {
                    "ma34": ma34,
                    "ma34_extension": ma34_extension,
                    "maximum": max_ma34_extension,
                },
            },
            {
                "name": "V3 score", "key": "trend_v3_score", "enabled": True,
                "triggered": decision.action in {TrendV3Action.OPEN, TrendV3Action.ADD}, "score_added": 0.0,
                "detail": ",".join(decision.reasons), "raw": {"action": decision.action.value, "score": decision.score},
            },
        ]
        factors.extend(await self._v3_market_observation_factors(
            symbol, direction.value, params, adx_metrics, decision,
        ))
        if decision.action not in {TrendV3Action.OPEN, TrendV3Action.ADD}:
            return "HOLD", f"trend_v3 {decision.action.value} score={decision.score:.1f}: {','.join(decision.reasons)}", factors
        if (
            decision.action is TrendV3Action.OPEN
            and structure_confirmation_required
            and not structure_confirmed
        ):
            return (
                "HOLD",
                f"trend_v3 WATCH score={decision.score:.1f}: missing_structure_confirmation",
                factors,
            )
        if (
            decision.action is TrendV3Action.OPEN
            and moer_reentry_required
            and not moer_reentry
        ):
            return (
                "HOLD",
                f"trend_v3 WATCH score={decision.score:.1f}: missing_30m_moer_reentry",
                factors,
            )
        if decision.action is TrendV3Action.OPEN and not extension_allowed:
            return (
                "HOLD",
                f"trend_v3 WATCH score={decision.score:.1f}: entry_overextended_from_5m_ma34",
                factors,
            )

        if decision.action is TrendV3Action.ADD:
            try:
                max_add_extension = max(
                    0.0,
                    float(params.get(
                        "trend_runner_add_max_ma34_extension_pct",
                        max_ma34_extension,
                    ) or 0),
                )
            except (TypeError, ValueError):
                max_add_extension = max_ma34_extension
            if max_add_extension and ma34_extension > max_add_extension:
                return (
                    "HOLD",
                    f"trend_runner_add blocked: overextended_from_5m_ma34 "
                    f"({ma34_extension:.4f}>{max_add_extension:.4f})",
                    factors,
                )

        if score_only:
            return "HOLD", "rotation_score_only", factors
        position_plan = params.get("position_plan") or {}
        if decision.action is TrendV3Action.ADD:
            position_factor = float(params.get("trend_v3_add_on_position_factor", position_plan.get("add_on_pct", 0.50)) or 0.50)
        else:
            position_factor = float(params.get("trend_v3_entry_position_factor", position_plan.get("entry_pct", 0.50)) or 0.50)
        trade_context = await self._calc_white_dove_margin_plan(
            strategy,
            config,
            current_price,
            max(0.01, min(1.0, position_factor)),
            symbol,
            is_add_on=decision.action is TrendV3Action.ADD,
        )
        # The global transition-slot guard runs later in _execute_trade and reads
        # the selected candidate score from this context. Keep the Trend V3 score
        # attached to the order plan just like the legacy dynamic-score path does.
        trade_context["entry_score"] = float(decision.score)
        quantity = float(trade_context.get("quantity", 0.0) or 0.0)
        if decision.action is TrendV3Action.ADD:
            runner_add = await self._get_trend_runner_add_context(
                strategy, config, symbol, direction.value
            )
            if not runner_add.get("allowed"):
                return (
                    "HOLD",
                    f"trend_runner_add blocked: {runner_add.get('reason', 'unknown')}",
                    factors,
                )
            quantity = min(quantity, float(runner_add["quantity_cap"]))
            quantity = await self._round_contract_quantity_down(
                symbol, quantity, strategy.market_type
            )
            if quantity <= 0:
                return "HOLD", "trend_runner_add blocked: quantity_below_contract_minimum", factors
            trade_context["quantity"] = quantity
            trade_context["trend_runner_add"] = {
                **runner_add,
                "symbol": symbol,
                "direction": direction.value,
            }
        if quantity <= 0:
            return "HOLD", f"trend_v3 margin plan unavailable{self._format_trade_context(trade_context)}", factors
        self._pending_trade_quantity = quantity
        self._pending_trade_context = trade_context
        signal = "BUY" if direction is TrendV3Direction.LONG else "SELL"
        return signal, (
            f"trend_v3 {decision.action.value} score={decision.score:.1f}/"
            f"{policy.min_add_score if decision.action is TrendV3Action.ADD else policy.min_open_score:.1f}: "
            f"{','.join(decision.reasons)}{self._format_trade_context(trade_context)}"
        ), factors

    async def _white_dove_strategy(self, strategy, current_price, symbol, config):
        """???????????? + ???? + ????
        ??: (signal, reason, factors)
        """
        params = strategy.params or {}
        min_score = params.get("min_score", 3)
        base_qty = float(params.get("quantity", 0.001) or 0.001)
        entry_signals = set(params.get("entry_signals") or ["bottom_divergence", "jin1_ext", "jin1_std", "jin2_approx"])
        add_on_signals = set(params.get("add_on_signals") or ["jin3_approx", "shou1", "shou2"])
        confirmation_rules = params.get("confirmation_rules") or {}
        position_plan = params.get("position_plan") or {}
        entry_pct = float(
            position_plan.get(
                "entry_pct",
                position_plan.get(
                    "confirm_pct",
                    position_plan.get("probe_pct", params.get("main_entry_percent", 0.30)),
                ),
            )
            or 0.30
        )
        add_on_pct = float(
            position_plan.get("add_on_pct", params.get("add_on_percent", 0.25)) or 0.25
        )
        long_position_multiplier = max(
            0.01,
            min(5.0, float(params.get("long_position_multiplier", 1) or 1)),
        )
        score = 0
        reasons = []
        factors = []
        self._pending_trade_quantity = None
        strategy_side = (getattr(strategy, "side", "BUY") or "BUY").upper()
        allow_long = strategy_side in ("BUY", "BOTH")
        allow_short = strategy_side in ("SELL", "BOTH")

        long_trigger = None
        short_trigger = None
        long_confirmation_hits = 0
        short_confirmation_hits = 0
        moer_long_signal = {"enabled": False, "state": "disabled", "score": 0.0}
        moer_long_add_on = False
        moer_short_signal = {"enabled": False, "state": "disabled", "score": 0.0}
        moer_short_add_on = False

        # 0. ??????????
        open_symbols = await self._get_open_position_symbols(config)
        current_positions = len(open_symbols)
        max_open_symbols = int(params.get("max_open_symbols", 5))
        limit_direction = None
        if allow_long and not allow_short:
            limit_direction = "LONG"
        elif allow_short and not allow_long:
            limit_direction = "SHORT"
        if limit_direction:
            strategy_position_keys = await self._get_strategy_live_position_keys(config, strategy)
            strategy_direction_symbols = {
                pos_symbol
                for pos_symbol, direction in strategy_position_keys
                if direction == limit_direction
            }
            normalized_limit_symbol = self._normalize_strategy_symbol_for_market(symbol, strategy.market_type)
            limit_positions = len(strategy_direction_symbols)
            if normalized_limit_symbol not in strategy_direction_symbols and limit_positions >= max_open_symbols:
                return "HOLD", (
                    f"????: ???{limit_direction}???????????"
                    f"({limit_positions}/{max_open_symbols})"
                ), []
            current_positions = -1
        norm_strategy_symbol = symbol.replace("-", "")
        has_current_pos = any(
            norm_strategy_symbol in s.replace("-", "") or s.replace("-", "").startswith(norm_strategy_symbol)
            for s in open_symbols
        )
        if not has_current_pos and current_positions >= max_open_symbols:
            return "HOLD", f"????: ???????????({current_positions}/{max_open_symbols})", []

        # 0.5 ???????????????????????
        cooldown_minutes = int(params.get("open_cooldown_minutes", 60))
        if cooldown_minutes > 0:
            try:
                async with AsyncSessionLocal() as cd_db:
                    last_open = await cd_db.execute(
                        select(StrategyLog)
                        .where(
                            StrategyLog.strategy_id == strategy.id,
                            StrategyLog.exchange_config_id == config.id,
                            StrategyLog.symbol == symbol,
                            StrategyLog.signal.in_(("BUY", "SELL")),
                        )
                        .order_by(StrategyLog.created_at.desc())
                        .limit(1)
                    )
                    last_open_log = last_open.scalar_one_or_none()
                    if self._is_exit_strategy_log(last_open_log):
                        last_open_log = None
                    if last_open_log and last_open_log.created_at:
                        last_ts = last_open_log.created_at
                        if last_ts.tzinfo is None:
                            last_ts = last_ts.replace(tzinfo=timezone.utc)
                        elapsed_min = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
                        if elapsed_min < cooldown_minutes:
                            return "HOLD", f"????: ?????({elapsed_min:.0f}/{cooldown_minutes}??)", []
            except Exception:
                pass

        if bool(params.get("trend_v3_enabled", False)):
            return await self._white_dove_v3_strategy(
                strategy, current_price, symbol, config, has_current_pos
            )
        
        # 1. ???? (????)
        if self._macro_filter:
            can_open, macro_reason = self._macro_filter.check_can_open_position(
                current_positions=current_positions
            )
            if not can_open:
                return "HOLD", f"????: ???? ({macro_reason})", []
            reasons.append(f"????: {macro_reason}")
            factors.append({
                "name": "????", "key": "macro_filter", "enabled": True,
                "triggered": True, "score_added": 0, "detail": macro_reason, "raw": {},
            })
        else:
            reasons.append("????????")
            factors.append({
                "name": "????", "key": "macro_filter", "enabled": False,
                "triggered": False, "score_added": 0, "detail": "???", "raw": {},
            })
        
        # 2. ?????????????5m+30m?????
        div_triggered = False
        div_score = 0
        div_detail = ""
        div_raw = {}
        long_entry_timeframes = set()
        long_bottom_divergence_timeframes = set()
        long_add_timeframes = set()
        short_entry_timeframes = set()
        short_add_timeframes = set()
        if self._div_system:
            # ????????????????
            timeframes = params.get("divergence_timeframes") or [
                {"bar": params.get("divergence_timeframe", "5m"), "weight": 2}
            ]
            min_confidence = int(params.get("min_chanlun_confidence", 75))

            total_long_score = 0
            total_short_score = 0
            long_trigger_type = None
            short_trigger_type = None
            tf_details = []

            for tf_cfg in timeframes:
                bar = tf_cfg.get("bar", "5m")
                weight = int(tf_cfg.get("weight", 1))
                klines = await okx_manager.get_candles(symbol, bar, 100)
                if len(klines) < 30:
                    tf_details.append(f"{bar}K???")
                    continue
                candles = [{
                    'open': float(k[1]), 'high': float(k[2]),
                    'low': float(k[3]), 'close': float(k[4]),
                    'volume': float(k[5]), 'time': k[0]
                } for k in klines]

                (has_long, long_info, long_msg), (has_short, short_info, short_msg) = \
                    self._div_system['scan_both_directions'](candles, bar)

                if allow_long and has_long and long_info:
                    confidence = round(long_info.get('confidence') or 0)
                    stype = long_info.get('stype')
                    if confidence >= min_confidence and stype in (entry_signals | add_on_signals):
                        w = weight if stype in entry_signals else 1
                        total_long_score += w
                        long_trigger_type = 'entry' if stype in entry_signals else 'add_on'
                        if stype in entry_signals:
                            long_entry_timeframes.add(bar)
                            if stype == "bottom_divergence":
                                long_bottom_divergence_timeframes.add(bar)
                        else:
                            long_add_timeframes.add(bar)
                        tf_details.append(f"{bar}??+{w}(??{confidence}): {long_msg}")
                    else:
                        tf_details.append(f"{bar}????(??{confidence}): {long_msg}")

                if allow_short and has_short and short_info:
                    confidence = round(short_info.get('confidence') or 0)
                    stype = short_info.get('stype')
                    if confidence >= min_confidence and stype in (entry_signals | add_on_signals):
                        w = weight if stype in entry_signals else 1
                        total_short_score += w
                        short_trigger_type = 'entry' if stype in entry_signals else 'add_on'
                        if stype in entry_signals:
                            short_entry_timeframes.add(bar)
                        else:
                            short_add_timeframes.add(bar)
                        tf_details.append(f"{bar}??+{w}(??{confidence}): {short_msg}")
                    else:
                        tf_details.append(f"{bar}????(??{confidence}): {short_msg}")

                if not has_long and not has_short:
                    tf_details.append(f"{bar}?????")

            div_detail = " | ".join(tf_details) or "?????"

            if total_long_score > 0 and allow_long:
                score += total_long_score
                div_score = total_long_score
                div_triggered = True
                long_trigger = long_trigger_type
                reasons.append(f"????(+{total_long_score}): {div_detail}")
                div_raw = {'direction': 'bullish', 'score': total_long_score}

            if total_short_score > 0 and allow_short:
                score += total_short_score
                div_score = total_short_score
                div_triggered = True
                short_trigger = short_trigger_type
                reasons.append(f"????(+{total_short_score}): {div_detail}")
                div_raw = {'direction': 'bearish', 'score': total_short_score}

            if not div_triggered:
                reasons.append(div_detail)
        
        factors.append({
            "name": "????", "key": "divergence", "enabled": bool(self._div_system),
            "triggered": div_triggered, "score_added": div_score,
            "detail": div_detail or "?????", "raw": div_raw,
        })
        required_long_div_tfs = {str(tf) for tf in (params.get("require_long_divergence_timeframes") or []) if str(tf).strip()}
        required_short_div_tfs = {str(tf) for tf in (params.get("require_short_divergence_timeframes") or []) if str(tf).strip()}
        strict_long_bottom_div_tfs = {str(tf) for tf in (params.get("require_long_bottom_divergence_timeframes") or []) if str(tf).strip()}
        sequence_enabled = bool(params.get("require_divergence_sequence", False))
        sequence_first = str(params.get("divergence_sequence_first_timeframe", "1m") or "1m")
        sequence_second = str(params.get("divergence_sequence_second_timeframe", "5m") or "5m")
        sequence_window = max(60.0, float(params.get("divergence_sequence_window_seconds", 1800) or 1800))
        sequence_symbol = self._normalize_strategy_symbol_for_market(symbol, strategy.market_type)
        sequence_now = time.monotonic()

        def _sequence_confirmed(direction: str, hits: set[str]) -> tuple[bool, float | None]:
            key = (int(strategy.id), sequence_symbol, direction)
            armed_at = self._divergence_sequence_arms.get(key)
            age = sequence_now - armed_at if armed_at is not None else None
            if age is not None and age > sequence_window:
                self._divergence_sequence_arms.pop(key, None)
                armed_at, age = None, None
            confirmed = bool(sequence_second in hits and armed_at is not None and 0 < (age or 0) <= sequence_window)
            # Arm after testing: a same-scan 1m+5m hit cannot bypass the required order.
            if sequence_first in hits:
                self._divergence_sequence_arms[key] = sequence_now
            return confirmed, age

        long_sequence_ok, long_sequence_age = _sequence_confirmed("LONG", long_bottom_divergence_timeframes)
        short_sequence_ok, short_sequence_age = _sequence_confirmed("SHORT", short_entry_timeframes)
        staged_div_cfg = params.get("staged_divergence_entry") or {}
        staged_div_enabled = isinstance(staged_div_cfg, dict) and bool(staged_div_cfg.get("enabled", False))
        staged_first_tf = str(staged_div_cfg.get("first_timeframe", "1m") or "1m")
        staged_second_tf = str(staged_div_cfg.get("second_timeframe", "5m") or "5m")
        staged_long_first_hit = staged_first_tf in long_bottom_divergence_timeframes
        staged_short_first_hit = staged_first_tf in short_entry_timeframes
        staged_long_second_hit = staged_second_tf in long_bottom_divergence_timeframes
        staged_short_second_hit = staged_second_tf in short_entry_timeframes
        sequence_pair = {sequence_first, sequence_second}
        long_sequence_required = sequence_enabled and sequence_pair.issubset(strict_long_bottom_div_tfs)
        short_sequence_required = sequence_enabled and sequence_pair.issubset(required_short_div_tfs)
        long_divergence_confluence_ok = not required_long_div_tfs or required_long_div_tfs.issubset(long_entry_timeframes)
        short_divergence_confluence_ok = short_sequence_ok if short_sequence_required else (not required_short_div_tfs or required_short_div_tfs.issubset(short_entry_timeframes))
        strict_long_bottom_divergence_ok = long_sequence_ok if long_sequence_required else (not strict_long_bottom_div_tfs or strict_long_bottom_div_tfs.issubset(long_bottom_divergence_timeframes))
        if long_sequence_required or short_sequence_required:
            factors.append({
                "name": "ordered 1m-to-5m divergence", "key": "divergence_sequence", "enabled": True,
                "triggered": long_sequence_ok or short_sequence_ok, "score_added": 0,
                "detail": f"first={sequence_first} second={sequence_second} window={sequence_window:.0f}s long_ok={long_sequence_ok} long_age={long_sequence_age} short_ok={short_sequence_ok} short_age={short_sequence_age}",
                "raw": {"first": sequence_first, "second": sequence_second, "window_seconds": sequence_window, "long_ok": long_sequence_ok, "short_ok": short_sequence_ok},
            })
        if strict_long_bottom_div_tfs:
            missing = [] if strict_long_bottom_divergence_ok else sorted(strict_long_bottom_div_tfs - long_bottom_divergence_timeframes)
            factors.append({
                "name": "strict long bottom-divergence confluence", "key": "strict_long_bottom_divergence_timeframe_confluence",
                "enabled": True, "triggered": strict_long_bottom_divergence_ok, "score_added": 0,
                "detail": f"required={sorted(strict_long_bottom_div_tfs)}, bottom_divergence_hit={sorted(long_bottom_divergence_timeframes)}, missing={missing}, sequential={long_sequence_required}",
                "raw": {"required": sorted(strict_long_bottom_div_tfs), "bottom_divergence_hit": sorted(long_bottom_divergence_timeframes), "missing": missing, "sequential": long_sequence_required},
            })

        if required_long_div_tfs:
            missing = sorted(required_long_div_tfs - long_entry_timeframes)
            factors.append({
                "name": "????????",
                "key": "long_divergence_timeframe_confluence",
                "enabled": True,
                "triggered": not missing,
                "score_added": 0,
                "detail": (
                    f"required={sorted(required_long_div_tfs)}, "
                    f"hit={sorted(long_entry_timeframes)}, missing={missing}"
                ),
                "raw": {
                    "required": sorted(required_long_div_tfs),
                    "hit": sorted(long_entry_timeframes),
                    "missing": missing,
                    "add_on_hit": sorted(long_add_timeframes),
                },
            })
        if required_short_div_tfs:
            missing = sorted(required_short_div_tfs - short_entry_timeframes)
            factors.append({
                "name": "????????",
                "key": "short_divergence_timeframe_confluence",
                "enabled": True,
                "triggered": not missing,
                "score_added": 0,
                "detail": (
                    f"required={sorted(required_short_div_tfs)}, "
                    f"hit={sorted(short_entry_timeframes)}, missing={missing}"
                ),
                "raw": {
                    "required": sorted(required_short_div_tfs),
                    "hit": sorted(short_entry_timeframes),
                    "missing": missing,
                    "add_on_hit": sorted(short_add_timeframes),
                },
            })
        
        # 3. ??????
        news_triggered = False
        news_score = 0
        news_detail = "???"
        news_raw = {}
        if params.get("news_factor_enabled", False):
            coin = symbol.split("-")[0]
            recent_news = await self._fetch_recent_news_factors(coin)
            if recent_news:
                net_strength = sum(n.strength for n in recent_news)
                news_raw = {
                    'net_strength': net_strength,
                    'count': len(recent_news),
                    'top_title': recent_news[0].title[:40] if recent_news else '',
                }
                if allow_short and not allow_long:
                    if net_strength <= -2:
                        score += 1
                        news_score = 1
                        news_triggered = True
                        reasons.append(f"????(+1): ???{net_strength}")
                        news_detail = f"???{net_strength} ({len(recent_news)}?)"
                    elif net_strength >= 2:
                        score -= 1
                        news_score = -1
                        news_triggered = True
                        reasons.append(f"????(-1?????): ???{net_strength}")
                        news_detail = f"???{net_strength} ({len(recent_news)}?)"
                    else:
                        reasons.append(f"????: ???{net_strength}")
                        news_detail = f"?? ???{net_strength}"
                elif net_strength >= 2:
                    score += 1
                    news_score = 1
                    news_triggered = True
                    reasons.append(f"????(+1): ???{net_strength}")
                    news_detail = f"???{net_strength} ({len(recent_news)}?)"
                elif net_strength <= -2:
                    score -= 1
                    news_score = -1
                    news_triggered = True
                    reasons.append(f"????(-1): ???{net_strength}")
                    news_detail = f"???{net_strength} ({len(recent_news)}?)"
                else:
                    reasons.append(f"????: ???{net_strength}")
                    news_detail = f"?? ???{net_strength}"
            else:
                reasons.append("??: ?24h?????")
                news_detail = "?24h?????"
                news_raw = {'count': 0}
        factors.append({
            "name": "????", "key": "news_factor",
            "enabled": params.get("news_factor_enabled", False),
            "triggered": news_triggered, "score_added": news_score,
            "detail": news_detail, "raw": news_raw,
        })
        
        # 4. ?????
        # Completed liquidations are historical aftershock context only.  They do
        # not add entry score and cannot satisfy a confirmation requirement.
        liq_detail = "无近期清算数据"
        liq_raw = {}
        if self._liq_signal:
            coin = symbol.split("-")[0]
            liq_result, liq_reason = self._liq_signal.check_signal(coin, current_price)
            liq_detail = liq_result.get("reason", liq_reason) if liq_result else liq_reason
            if liq_result:
                liq_raw = {
                    "signal": liq_result.get("signal"),
                    "strength": liq_result.get("strength", 0),
                    "mode": "risk_filter_only",
                }
            reasons.append(f"清算风险上下文: {liq_detail}")
        factors.append({
            "name": "清算近期风险", "key": "liquidation_map",
            "enabled": bool(self._liq_signal), "triggered": False,
            "score_added": 0, "detail": liq_detail, "raw": liq_raw,
        })

        # 4.5 Trend regime: 4H sets the main direction, 30m sets execution.
        trend_position_multiplier = 1.0
        trend_filter_triggered_for_side = False
        trend_add_allowed_for_side = False
        trend_block_long = None
        trend_block_short = None
        trend_regime = "disabled"
        if params.get("trend_regime_enabled", params.get("trend_filter_enabled", True)):
            trend = await self._get_trend_regime_signal(symbol, params)
            trend_regime = str(trend.get("regime", "unknown"))
            trend_score = float(trend.get("score", 0.0) or 0.0)
            trend_detail = trend.get("detail", "")
            trend_entry_allowed = bool(trend.get("entry_allowed", False))
            trend_add_allowed = bool(trend.get("add_allowed", False))
            trend_position_multiplier = float(trend.get("position_multiplier", 1.0) or 0.0)

            side_score = 0.0
            side_state = "neutral"
            if allow_long and not allow_short:
                side_score = trend_score
                side_state = "aligned" if trend_regime in ("strong_long", "weak_long") else "conflict"
                if trend_regime == "strong_long":
                    trend_filter_triggered_for_side = True
                    trend_add_allowed_for_side = trend_add_allowed
                elif trend_regime == "weak_long" and trend_entry_allowed:
                    trend_filter_triggered_for_side = True
                else:
                    trend_block_long = f"???????({trend_regime})"
            elif allow_short and not allow_long:
                side_score = -trend_score
                side_state = "aligned" if trend_regime in ("strong_short", "weak_short") else "conflict"
                if trend_regime == "strong_short":
                    trend_filter_triggered_for_side = True
                    trend_add_allowed_for_side = trend_add_allowed
                elif trend_regime == "weak_short" and trend_entry_allowed:
                    trend_filter_triggered_for_side = True
                else:
                    trend_block_short = f"???????({trend_regime})"
            else:
                side_score = abs(trend_score)
                trend_filter_triggered_for_side = trend_regime in ("strong_long", "strong_short")
                trend_add_allowed_for_side = trend_filter_triggered_for_side

            score += side_score
            if side_score > 0:
                reasons.append(f"??????(+{side_score:g}): {trend_detail}")
            elif side_score < 0:
                reasons.append(f"??????({side_score:g}): {trend_detail}")
            else:
                reasons.append(f"??????: {trend_detail}")
            factors.append({
                "name": "????",
                "key": "trend_filter",
                "enabled": True,
                "triggered": trend_filter_triggered_for_side,
                "score_added": side_score,
                "detail": trend_detail,
                "raw": {
                    **(trend.get("raw") or {}),
                    "regime": trend_regime,
                    "side_state": side_state,
                    "position_multiplier": trend_position_multiplier,
                    "entry_allowed": trend_entry_allowed,
                    "add_allowed": trend_add_allowed,
                },
            })

        moer_long_cfg = params.get("moer_long_structure") or {}
        if allow_long and not allow_short and isinstance(moer_long_cfg, dict) and bool(moer_long_cfg.get("enabled", False)):
            higher_bar = str(moer_long_cfg.get("higher_timeframe", "4H") or "4H")
            entry_bar = str(moer_long_cfg.get("entry_timeframe", "30m") or "30m")
            try:
                higher_klines, entry_klines = await asyncio.gather(
                    okx_manager.get_candles(symbol, higher_bar, 200),
                    okx_manager.get_candles(symbol, entry_bar, 240),
                )
                moer_long_signal = evaluate_moer_long_structure(
                    higher_klines,
                    entry_klines,
                    centre_tolerance=float(moer_long_cfg.get("centre_tolerance", 0.0015) or 0.0015),
                    b3_lookback=int(moer_long_cfg.get("b3_lookback", 48) or 48),
                )
                moer_long_signal["enabled"] = True
            except Exception as exc:
                moer_long_signal = {
                    "enabled": True,
                    "available": False,
                    "state": "error",
                    "score": 0.0,
                    "detail": f"Moer structure evaluation failed: {exc}",
                }
            moer_state = str(moer_long_signal.get("state", "none"))
            moer_score = 0.0
            if moer_state == "B3":
                moer_score = float(moer_long_cfg.get("b3_score", 1.4) or 1.4)
            elif moer_state == "B2":
                moer_score = float(moer_long_cfg.get("b2_score", 0.8) or 0.8)
            moer_long_signal["score"] = moer_score
            score += moer_score
            reasons.append(f"Moer 4H/30m structure: {moer_long_signal.get('detail', '')}")
            factors.append({
                "name": "Moer 4H MA170 + MA34 centre + 30m B2/B3",
                "key": "moer_long_structure",
                "enabled": True,
                "triggered": moer_state in {"B2", "B3"},
                "score_added": moer_score,
                "detail": str(moer_long_signal.get("detail", "")),
                "raw": moer_long_signal,
            })

        moer_short_cfg = params.get("moer_short_structure") or {}
        if allow_short and not allow_long and isinstance(moer_short_cfg, dict) and bool(moer_short_cfg.get("enabled", False)):
            higher_bar = str(moer_short_cfg.get("higher_timeframe", "4H") or "4H")
            entry_bar = str(moer_short_cfg.get("entry_timeframe", "30m") or "30m")
            try:
                higher_klines, entry_klines = await asyncio.gather(
                    okx_manager.get_candles(symbol, higher_bar, 200),
                    okx_manager.get_candles(symbol, entry_bar, 240),
                )
                moer_short_signal = evaluate_moer_short_structure(
                    higher_klines,
                    entry_klines,
                    centre_tolerance=float(moer_short_cfg.get("centre_tolerance", 0.0015) or 0.0015),
                    s3_lookback=int(moer_short_cfg.get("s3_lookback", 48) or 48),
                )
                moer_short_signal["enabled"] = True
            except Exception as exc:
                moer_short_signal = {
                    "enabled": True,
                    "available": False,
                    "state": "error",
                    "score": 0.0,
                    "detail": f"Moer short structure evaluation failed: {exc}",
                }
            moer_state = str(moer_short_signal.get("state", "none"))
            moer_score = 0.0
            if moer_state == "S3":
                moer_score = float(moer_short_cfg.get("s3_score", 1.4) or 1.4)
            elif moer_state == "S2":
                moer_score = float(moer_short_cfg.get("s2_score", 0.8) or 0.8)
            moer_short_signal["score"] = moer_score
            score += moer_score
            reasons.append(f"Moer 4H/30m short structure: {moer_short_signal.get('detail', '')}")
            factors.append({
                "name": "Moer 4H MA170 + MA34 centre + 30m S2/S3",
                "key": "moer_short_structure",
                "enabled": True,
                "triggered": moer_state in {"S2", "S3"},
                "score_added": moer_score,
                "detail": str(moer_short_signal.get("detail", "")),
                "raw": moer_short_signal,
            })

        staged_long_structure_ok = str(moer_long_signal.get("state", "none")) in {"B2", "B3"}
        staged_short_structure_ok = str(moer_short_signal.get("state", "none")) in {"S2", "S3"}
        staged_long_probe = staged_div_enabled and not has_current_pos and staged_long_first_hit and staged_long_structure_ok
        staged_short_probe = staged_div_enabled and not has_current_pos and staged_short_first_hit and staged_short_structure_ok
        staged_long_add_on = staged_div_enabled and has_current_pos and staged_long_second_hit
        staged_short_add_on = staged_div_enabled and has_current_pos and staged_short_second_hit
        trend_startup_long = False
        trend_startup_short = False
        trend_startup_cfg = params.get("trend_startup_entry") or {}
        if staged_div_enabled:
            factors.append({
                "name": "staged 1m divergence -> 5m confirmation",
                "key": "staged_divergence_entry",
                "enabled": True,
                "triggered": staged_long_probe or staged_short_probe or staged_long_add_on or staged_short_add_on,
                "score_added": 0,
                "detail": (
                    f"first={staged_first_tf} second={staged_second_tf} "
                    f"long_first={staged_long_first_hit} long_second={staged_long_second_hit} "
                    f"short_first={staged_short_first_hit} short_second={staged_short_second_hit} "
                    f"long_structure={staged_long_structure_ok} short_structure={staged_short_structure_ok}"
                ),
                "raw": {
                    "long_probe": staged_long_probe,
                    "short_probe": staged_short_probe,
                    "long_add_on": staged_long_add_on,
                    "short_add_on": staged_short_add_on,
                },
            })

        # Market-wide BTC 4H gate: a strong BTC trend blocks new counter-trend alt entries.
        market_gate_state = "disabled"
        market_block_long = None
        market_block_short = None
        if bool(params.get("btc_market_gate_enabled", False)):
            market_gate = await self._get_btc_4h_market_gate(params)
            market_gate_state = str(market_gate.get("state", "unknown"))
            market_gate_detail = str(market_gate.get("detail", ""))
            if market_gate_state == "strong_short" and allow_long and not allow_short:
                market_block_long = f"BTC 4H strong short: {market_gate_detail}"
            elif market_gate_state == "strong_long" and allow_short and not allow_long:
                market_block_short = f"BTC 4H strong long: {market_gate_detail}"
            factors.append({
                "name": "BTC market gate",
                "key": "btc_market_gate",
                "enabled": True,
                "triggered": market_gate_state in {"strong_long", "strong_short"},
                "score_added": 0.0,
                "detail": market_gate_detail,
                "raw": {**(market_gate.get("raw") or {}), "state": market_gate_state},
            })
            reasons.append(f"BTC market gate: {market_gate_detail}")

        # ADX/ATR is a state gate, deliberately score-neutral.
        adx_atr_gate = {"enabled": False, "allowed": True, "state": "disabled", "reason": "未启用"}
        adx_atr_block_long = None
        adx_atr_block_short = None
        adx_atr_direction = "LONG" if allow_long and not allow_short else "SHORT" if allow_short and not allow_long else "BOTH"
        if adx_atr_direction in {"LONG", "SHORT"}:
            adx_atr_gate = await evaluate_adx_atr_regime(symbol, adx_atr_direction, params)
            if not adx_atr_gate.get("allowed", True):
                if adx_atr_direction == "LONG":
                    adx_atr_block_long = str(adx_atr_gate.get("reason", "ADX/ATR未通过"))
                else:
                    adx_atr_block_short = str(adx_atr_gate.get("reason", "ADX/ATR未通过"))
            reasons.append(f"ADX/ATR状态: {adx_atr_gate.get('reason', '')}")
            factors.append({
                "name": "ADX/ATR market regime", "key": "adx_atr_regime",
                "enabled": bool(adx_atr_gate.get("enabled", False)),
                "triggered": bool(adx_atr_gate.get("allowed", True)), "score_added": 0,
                "detail": adx_atr_gate.get("reason", ""), "raw": adx_atr_gate,
            })

        # Trend-start entry: aligned 4H/30m regime plus a 5m breakout-retest.
        # It is a separate small first-entry path; divergence staging remains intact.
        if isinstance(trend_startup_cfg, dict) and bool(trend_startup_cfg.get("enabled", False)):
            try:
                startup_klines = await okx_manager.get_candles(
                    symbol, str(trend_startup_cfg.get("timeframe", "5m") or "5m"), 40
                )
                if len(startup_klines) >= 20:
                    bars = [
                        {"high": float(k[2]), "low": float(k[3]), "close": float(k[4])}
                        for k in startup_klines
                    ]
                    lookback = int(trend_startup_cfg.get("breakout_lookback", 12) or 12)
                    breakout_pct = float(trend_startup_cfg.get("breakout_pct", 0.001) or 0.001)
                    retest_pct = float(trend_startup_cfg.get("retest_pct", 0.006) or 0.006)
                    reference = bars[-(lookback + 4):-4]
                    recent = bars[-4:-1]
                    current = bars[-1]
                    if reference and recent:
                        ref_high = max(item["high"] for item in reference)
                        ref_low = min(item["low"] for item in reference)
                        long_break = any(item["high"] >= ref_high * (1 + breakout_pct) for item in recent)
                        short_break = any(item["low"] <= ref_low * (1 - breakout_pct) for item in recent)
                        long_retest = current["low"] <= ref_high * (1 + retest_pct) and current["close"] >= ref_high
                        short_retest = current["high"] >= ref_low * (1 - retest_pct) and current["close"] <= ref_low
                        require_startup_adx = bool(trend_startup_cfg.get("require_adx", True))
                        adx_ok = bool(adx_atr_gate.get("allowed", True))
                        if require_startup_adx:
                            adx_ok = adx_ok and bool(adx_atr_gate.get("enabled", False))
                        else:
                            adx_ok = True
                        allow_weak_startup = bool(trend_startup_cfg.get("allow_weak_trend", False))
                        long_regime_ok = trend_regime == "strong_long" or (
                            allow_weak_startup and trend_regime == "weak_long"
                        )
                        short_regime_ok = trend_regime == "strong_short" or (
                            allow_weak_startup and trend_regime == "weak_short"
                        )
                        trend_startup_long = (
                            allow_long and not has_current_pos and long_regime_ok
                            and long_break and long_retest and adx_ok
                        )
                        trend_startup_short = (
                            allow_short and not has_current_pos and short_regime_ok
                            and short_break and short_retest and adx_ok
                        )
                        startup_score = float(trend_startup_cfg.get("score", 1.5) or 1.5)
                        startup_score_added = startup_score if trend_startup_long or trend_startup_short else 0.0
                        score += startup_score_added
                        reasons.append(
                            f"trend startup 5m breakout-retest: long={trend_startup_long} "
                            f"short={trend_startup_short} score=+{startup_score_added:g}"
                        )
                        factors.append({
                            "name": "trend startup 4H/30m/5m breakout-retest",
                            "key": "trend_startup_entry",
                            "enabled": True,
                            "triggered": trend_startup_long or trend_startup_short,
                            "score_added": startup_score_added,
                            "detail": f"ref_high={ref_high:.6g} ref_low={ref_low:.6g} adx_ok={adx_ok}",
                            "raw": {
                                "trend_regime": trend_regime,
                                "long_break": long_break,
                                "short_break": short_break,
                                "long_retest": long_retest,
                                "short_retest": short_retest,
                            },
                        })
            except (TypeError, ValueError, IndexError) as exc:
                reasons.append(f"trend startup unavailable: {exc}")

        # 5. ??????
        ma_triggered = False
        ma_score = 0
        ma_detail = "????"
        ma_raw = {}
        if params.get("ma_cross_enabled", True):
            ma_bar = params.get("ma_cross_timeframe", "1H")
            klines_ma = await okx_manager.get_candles(symbol, ma_bar, 55)
            if len(klines_ma) >= 50:
                closes = [float(k[4]) for k in klines_ma]
                ma20 = sum(closes[-20:]) / 20
                ma50 = sum(closes[-50:]) / 50
                ma_raw = {'ma20': round(ma20, 2), 'ma50': round(ma50, 2)}
                ma_bullish = ma20 > ma50
                if allow_long and not allow_short:
                    # ????????+1???-1
                    if ma_bullish and strategy.last_signal != "BUY":
                        score += 1; ma_score = 1; ma_triggered = True
                        reasons.append("MA20??MA50(+1)")
                        ma_detail = f"MA20({ma20:.2f}) > MA50({ma50:.2f})"
                        if confirmation_rules.get("need_ma_cross", False):
                            long_confirmation_hits += 1
                    elif not ma_bullish and strategy.last_signal != "SELL":
                        score -= 1; ma_score = -1; ma_triggered = True
                        reasons.append("MA20??MA50(-1)")
                        ma_detail = f"MA20({ma20:.2f}) < MA50({ma50:.2f})"
                elif allow_short and not allow_long:
                    # ????????+1???????????????
                    if not ma_bullish and strategy.last_signal != "SELL":
                        score += 1; ma_score = 1; ma_triggered = True
                        reasons.append("MA20??MA50(????+1)")
                        ma_detail = f"MA20({ma20:.2f}) < MA50({ma50:.2f})"
                        if confirmation_rules.get("need_ma_cross", False):
                            short_confirmation_hits += 1
                    elif ma_bullish:
                        ma_score = 0; ma_triggered = False
                        reasons.append("MA20??MA50(?????????)")
                        ma_detail = f"MA20({ma20:.2f}) > MA50({ma50:.2f})"
                else:
                    # BOTH??
                    if ma_bullish and strategy.last_signal != "BUY":
                        score += 1; ma_score = 1; ma_triggered = True
                        reasons.append("MA20??MA50(+1)")
                        ma_detail = f"MA20({ma20:.2f}) > MA50({ma50:.2f})"
                    elif not ma_bullish and strategy.last_signal != "SELL":
                        score -= 1; ma_score = -1; ma_triggered = True
                        reasons.append("MA20??MA50(-1)")
                        ma_detail = f"MA20({ma20:.2f}) < MA50({ma50:.2f})"
                if not ma_triggered:
                    ma_detail = f"MA20({ma20:.2f}) vs MA50({ma50:.2f})"
        factors.append({
            "name": "????", "key": "ma_cross",
            "enabled": params.get("ma_cross_enabled", True), "triggered": ma_triggered,
            "score_added": ma_score, "detail": ma_detail, "raw": ma_raw,
        })

        # 6. ?K???MA5??????????
        bk_triggered = False
        bk_score = 0
        bk_detail = "???"
        bk_raw = {}
        black_candle_tfs = params.get("black_candle_timeframes") or []
        if black_candle_tfs and self._div_system:
            total_bk_score = 0
            bk_details = []
            for tf_cfg in black_candle_tfs:
                bar = tf_cfg.get("bar", "5m")
                weight = int(tf_cfg.get("weight", 1))
                klines_bk = await okx_manager.get_candles(symbol, bar, 20)
                if len(klines_bk) < 7:
                    bk_details.append(f"{bar}K???")
                    continue
                rows_bk = [{
                    'open': float(k[1]), 'high': float(k[2]),
                    'low': float(k[3]), 'close': float(k[4]),
                    'volume': float(k[5]),
                } for k in klines_bk]
                result = detect_black_candle(rows_bk)
                if result.get("triggered"):
                    total_bk_score += weight
                    bk_details.append(f"{bar}?K+{weight}(??{result.get('confidence',0)}): {result.get('reason','')}")
                    bk_raw = {"ma5_curr": result.get("ma5_curr"), "ma5_prev": result.get("ma5_prev")}
                else:
                    bk_details.append(f"{bar}?K???: {result.get('reason','')}")

            if total_bk_score > 0:
                bk_triggered = True
                bk_detail = " | ".join(bk_details)
                if allow_short and not allow_long:
                    score += total_bk_score
                    bk_score = total_bk_score
                    reasons.append(f"?K????(+{total_bk_score}): {bk_detail}")
                    if not short_trigger:
                        short_trigger = 'entry'
                elif allow_long and not allow_short:
                    score -= total_bk_score
                    bk_score = -total_bk_score
                    reasons.append(f"?K????(-{total_bk_score}): {bk_detail}")
                else:
                    score += total_bk_score
                    bk_score = total_bk_score
                    reasons.append(f"?K????(+{total_bk_score}): {bk_detail}")
                    if not short_trigger:
                        short_trigger = 'entry'
            else:
                bk_detail = " | ".join(bk_details) or "?K???"
                reasons.append(bk_detail)

        factors.append({
            "name": "?K??", "key": "black_candle",
            "enabled": bool(black_candle_tfs),
            "triggered": bk_triggered, "score_added": bk_score,
            "detail": bk_detail, "raw": bk_raw,
        })

        # 7. ?????????????/?????????????
        # 6.5 Elliott wave structure confirmation. It confirms or blocks, but never
        # replaces the required divergence-confluence first-entry gate.
        elliott_block_long = None
        elliott_block_short = None
        elliott_enabled = bool(params.get("elliott_wave_enabled", False))
        elliott_mode = str(params.get("elliott_wave_mode", "block_conflict") or "block_conflict")
        elliott_detail = "disabled"
        elliott_raw = {}
        elliott_signal = 0
        elliott_score = 0.0
        if elliott_enabled:
            try:
                elliott = await self._get_elliott_wave_signal(symbol, params)
                elliott_signal = int(elliott.get("signal", 0) or 0)
                elliott_detail = elliott.get("detail") or "elliott neutral"
                elliott_raw = elliott.get("raw") or {}
                raw_elliott_score = abs(float(elliott.get("score", 0) or 0))
                elliott_weight = float(params.get("elliott_wave_score", 1.0) or 1.0)
                conflict_penalty = float(params.get("elliott_wave_conflict_penalty", elliott_weight) or elliott_weight)

                if allow_long and not allow_short:
                    if elliott_signal > 0:
                        elliott_score = max(raw_elliott_score, elliott_weight)
                        score += elliott_score
                        reasons.append(f"Elliott bullish confirmation(+{elliott_score:g}): {elliott_detail}")
                    elif elliott_signal < 0:
                        elliott_score = -conflict_penalty
                        score += elliott_score
                        reasons.append(f"Elliott bearish conflict({elliott_score:g}): {elliott_detail}")
                        if elliott_mode == "block_conflict":
                            elliott_block_long = f"Elliott bearish conflict ({elliott_detail})"
                    else:
                        reasons.append(f"Elliott neutral: {elliott_detail}")
                elif allow_short and not allow_long:
                    if elliott_signal < 0:
                        elliott_score = max(raw_elliott_score, elliott_weight)
                        score += elliott_score
                        reasons.append(f"Elliott bearish confirmation(+{elliott_score:g}): {elliott_detail}")
                    elif elliott_signal > 0:
                        elliott_score = -conflict_penalty
                        score += elliott_score
                        reasons.append(f"Elliott bullish conflict({elliott_score:g}): {elliott_detail}")
                        if elliott_mode == "block_conflict":
                            elliott_block_short = f"Elliott bullish conflict ({elliott_detail})"
                    else:
                        reasons.append(f"Elliott neutral: {elliott_detail}")
                else:
                    if elliott_signal != 0:
                        elliott_score = max(raw_elliott_score, elliott_weight)
                        score += elliott_score
                    reasons.append(f"Elliott signal {elliott_signal}: {elliott_detail}")

                if elliott_mode == "require_alignment":
                    if allow_long and elliott_signal <= 0:
                        elliott_block_long = f"Elliott alignment required ({elliott_detail})"
                    if allow_short and elliott_signal >= 0:
                        elliott_block_short = f"Elliott alignment required ({elliott_detail})"
            except Exception as exc:
                elliott_detail = f"failed: {exc}"
                reasons.append(f"Elliott failed, pass-through: {exc}")

        factors.append({
            "name": "Elliott Wave", "key": "elliott_wave",
            "enabled": elliott_enabled,
            "triggered": elliott_signal != 0,
            "score_added": elliott_score,
            "detail": elliott_detail,
            "raw": {**elliott_raw, "mode": elliott_mode, "signal": elliott_signal},
        })

        harmonic_block_long = None
        harmonic_block_short = None
        harmonic_enabled = bool(params.get("harmonic_filter_enabled", False))
        harmonic_mode = str(params.get("harmonic_filter_mode", "block_conflict") or "block_conflict")
        harmonic_detail = "???"
        harmonic_raw = {}
        harmonic_signal = 0
        harmonic_score = 0.0
        harmonic_triggered = False
        if harmonic_enabled:
            try:
                harmonic = await self._get_harmonic_filter_signal(symbol, params)
                harmonic_signal = int(harmonic.get("signal", 0) or 0)
                harmonic_detail = harmonic.get("detail") or "??????"
                harmonic_raw = harmonic.get("raw") or {}
                harmonic_triggered = harmonic_signal != 0

                if harmonic_signal > 0:
                    if allow_long and not allow_short:
                        harmonic_score = float(params.get("harmonic_same_direction_score", 0.8) or 0.8)
                        score += harmonic_score
                        reasons.append(f"Harmonic bullish confirmation(+{harmonic_score:g}): {harmonic_detail}")
                    elif allow_short and not allow_long:
                        harmonic_score = -float(params.get("harmonic_conflict_penalty", 1.2) or 1.2)
                        score += harmonic_score
                        reasons.append(f"Harmonic bullish conflict({harmonic_score:g}): {harmonic_detail}")
                    else:
                        reasons.append(f"Harmonic bullish filter: {harmonic_detail}")
                    if harmonic_mode == "block_conflict" and allow_short and not allow_long:
                        harmonic_block_short = f"Harmonic bullish conflict ({harmonic_detail})"
                elif harmonic_signal < 0:
                    if allow_short and not allow_long:
                        harmonic_score = float(params.get("harmonic_same_direction_score", 0.8) or 0.8)
                        score += harmonic_score
                        reasons.append(f"Harmonic bearish confirmation(+{harmonic_score:g}): {harmonic_detail}")
                    elif allow_long and not allow_short:
                        harmonic_score = -float(params.get("harmonic_conflict_penalty", 1.2) or 1.2)
                        score += harmonic_score
                        reasons.append(f"Harmonic bearish conflict({harmonic_score:g}): {harmonic_detail}")
                    else:
                        reasons.append(f"Harmonic bearish filter: {harmonic_detail}")
                    if harmonic_mode == "block_conflict" and allow_long and not allow_short:
                        harmonic_block_long = f"Harmonic bearish conflict ({harmonic_detail})"
                else:
                    reasons.append(f"Harmonic neutral: {harmonic_detail}")
                if harmonic_mode == "require_alignment":
                    if allow_long and harmonic_signal <= 0:
                        harmonic_block_long = f"???????? ({harmonic_detail})"
                    if allow_short and harmonic_signal >= 0:
                        harmonic_block_short = f"???????? ({harmonic_detail})"
            except Exception as exc:
                harmonic_detail = f"????: {exc}"
                reasons.append(f"??????(??): {exc}")

        factors.append({
            "name": "????", "key": "harmonic_filter",
            "enabled": harmonic_enabled, "triggered": harmonic_triggered,
            "score_added": harmonic_score, "detail": harmonic_detail,
            "raw": {**harmonic_raw, "mode": harmonic_mode, "signal": harmonic_signal, "score": harmonic_score},
        })

        harmonic_rebound_triggered = False
        harmonic_rebound_score = 0.0
        harmonic_rebound_detail = "???"
        harmonic_rebound_raw = {}
        harmonic_rebound_position_multiplier = 1.0
        if (
            allow_long
            and not allow_short
            and bool(params.get("allow_harmonic_rebound_long_entry", False))
        ):
            harmonic_rebound_detail = "??????"
            if harmonic_signal > 0:
                if trend_regime in ("strong_long", "weak_long"):
                    try:
                        confirm = await self._get_short_term_bullish_confirmation(symbol, params)
                    except Exception as exc:
                        confirm = {"triggered": False, "detail": f"5m????: {exc}", "raw": {"error": str(exc)}}
                    harmonic_rebound_raw = {
                        "trend_regime": trend_regime,
                        "harmonic_signal": harmonic_signal,
                        "confirm": confirm,
                    }
                    if confirm.get("triggered"):
                        harmonic_rebound_triggered = True
                        harmonic_rebound_score = float(params.get("harmonic_rebound_long_score", 3.0) or 3.0)
                        harmonic_rebound_position_multiplier = float(
                            params.get("harmonic_rebound_long_position_multiplier", 1.0) or 1.0
                        )
                        score += harmonic_rebound_score
                        reasons.append(
                            f"?????(+{harmonic_rebound_score:g}): {harmonic_detail} | {confirm.get('detail')}"
                        )
                        if not long_trigger:
                            long_trigger = "harmonic_rebound"
                    else:
                        harmonic_rebound_detail = f"?????5m???: {confirm.get('detail')}"
                        reasons.append(f"???????: {harmonic_rebound_detail}")
                else:
                    harmonic_rebound_detail = f"??????????????({trend_regime})"
                    harmonic_rebound_raw = {"trend_regime": trend_regime, "harmonic_signal": harmonic_signal}
                    reasons.append(f"???????: {harmonic_rebound_detail}")
            elif harmonic_signal < 0:
                harmonic_rebound_detail = "???????????"
                harmonic_rebound_raw = {"trend_regime": trend_regime, "harmonic_signal": harmonic_signal}
            else:
                harmonic_rebound_raw = {"trend_regime": trend_regime, "harmonic_signal": harmonic_signal}

        factors.append({
            "name": "?????",
            "key": "harmonic_rebound_long",
            "enabled": bool(params.get("allow_harmonic_rebound_long_entry", False)),
            "triggered": harmonic_rebound_triggered,
            "score_added": harmonic_rebound_score,
            "detail": harmonic_rebound_detail if not harmonic_rebound_triggered else "???? + 5m??",
            "raw": {
                **harmonic_rebound_raw,
                "position_multiplier": harmonic_rebound_position_multiplier,
            },
        })

        # OI/funding is observation-only until enough 5-minute snapshots exist.
        derivatives_observation = {"enabled": False, "status": "disabled", "would_block": False, "enforced": False, "reason": "未启用"}
        derivatives_block_long = None
        derivatives_block_short = None
        derivatives_direction = "SHORT" if allow_short and not allow_long else "LONG" if allow_long and not allow_short else "BOTH"
        if derivatives_direction in {"LONG", "SHORT"}:
            derivatives_observation = await self._derivatives_market_data.entry_observation(
                symbol, derivatives_direction, adx_atr_gate.get("atr_pct"), params.get("oi_funding_filter") or {}
            )
            if derivatives_observation.get("enforced", False):
                if derivatives_direction == "LONG":
                    derivatives_block_long = str(derivatives_observation.get("reason", "OI/资金费率未通过"))
                else:
                    derivatives_block_short = str(derivatives_observation.get("reason", "OI/资金费率未通过"))
            reasons.append(f"OI/资金费率: {derivatives_observation.get('reason', '')}")
            factors.append({
                "name": "OI/funding observation", "key": "oi_funding_context",
                "enabled": bool(derivatives_observation.get("enabled", False)),
                "triggered": not bool(derivatives_observation.get("would_block", False)), "score_added": 0,
                "detail": derivatives_observation.get("reason", ""), "raw": derivatives_observation,
            })

        target_direction = "SHORT" if allow_short and not allow_long else "LONG" if allow_long and not allow_short else "BOTH"
        dynamic_score = self._build_white_dove_dynamic_score(factors, target_direction, min_score, params)
        decision_score = float(dynamic_score.get("decision_score", score) or 0)
        dynamic_position_multiplier = float(dynamic_score.get("position_multiplier", 1.0) or 1.0)
        dynamic_entry_allowed = bool(dynamic_score.get("entry_allowed", True))
        score_based_entry_mode = bool(params.get("score_based_entry_mode", False))
        # Keep divergence/breakout entries on the normal threshold, but require a
        # stronger score when the score itself is the only entry trigger.
        score_based_entry_min_score = max(
            min_score,
            float(params.get("score_based_entry_min_score", min_score) or min_score),
        )
        score_based_min_trend_position_multiplier = max(
            0.0,
            min(5.0, float(params.get("score_based_min_trend_position_multiplier", 1.0) or 1.0)),
        )
        if dynamic_score.get("enabled"):
            reasons.append(f"????: {dynamic_score.get('detail')}")
            factors.append({
                "name": "????",
                "key": "dynamic_score",
                "enabled": True,
                "triggered": decision_score >= min_score,
                "score_added": 0,
                "detail": dynamic_score.get("detail", ""),
                "raw": dynamic_score,
            })
        
        total_reason = " | ".join(reasons)
        allow_trend_add_after_entry = bool(params.get("allow_trend_add_after_entry", False))
        trend_add_min_score = float(params.get("trend_add_min_score", min_score) or min_score)

        # ????????????????????????????????
        # ????????????????/??/??????????????
        allow_score_fallback_entry = score_based_entry_mode or not bool(params.get("require_primary_entry_signal", False))
        score_fallback_threshold = (
            score_based_entry_min_score if score_based_entry_mode else min_score
        )
        if staged_div_enabled:
            # In staged mode, a first entry needs 1m divergence plus Moer structure;
            # the 5m divergence is reserved for confirming an add-on.
            if allow_long:
                long_trigger = "entry" if staged_long_probe else None
                if has_current_pos and staged_long_add_on:
                    long_trigger = "add_on"
            if allow_short:
                short_trigger = "entry" if staged_short_probe else None
                if has_current_pos and staged_short_add_on:
                    short_trigger = "add_on"
        if dynamic_entry_allowed and decision_score >= min_score:
            if trend_startup_long and allow_long and not has_current_pos:
                long_trigger = "entry"
                reasons.append("trend startup entry: aligned 4H/30m plus 5m breakout-retest")
            if trend_startup_short and allow_short and not has_current_pos:
                short_trigger = "entry"
                reasons.append("trend startup entry: aligned 4H/30m plus 5m breakout-retest")
        if (
            allow_long
            and has_current_pos
            and not long_trigger
            and allow_trend_add_after_entry
            and bool(params.get("allow_add_existing_position", False))
            and trend_filter_triggered_for_side
            and trend_add_allowed_for_side
            and decision_score >= trend_add_min_score
            and dynamic_entry_allowed
        ):
            long_trigger = 'add_on'
        moer_add_on_enabled = bool(
            isinstance(moer_long_cfg, dict) and moer_long_cfg.get("allow_add_on", False)
        )
        if (
            allow_long
            and has_current_pos
            and not long_trigger
            and moer_add_on_enabled
            and bool(params.get("allow_add_existing_position", False))
            and str(moer_long_signal.get("state", "none")) in {"B2", "B3"}
            and trend_add_allowed_for_side
        ):
            long_trigger = 'add_on'
            moer_long_add_on = True
            reasons.append(f"Moer {moer_long_signal.get('state')} confirmed pullback add-on")
        moer_short_add_on_enabled = bool(
            isinstance(moer_short_cfg, dict) and moer_short_cfg.get("allow_add_on", False)
        )
        if (
            allow_short
            and has_current_pos
            and not short_trigger
            and moer_short_add_on_enabled
            and bool(params.get("allow_add_existing_position", False))
            and str(moer_short_signal.get("state", "none")) in {"S2", "S3"}
            and trend_add_allowed_for_side
        ):
            short_trigger = 'add_on'
            moer_short_add_on = True
            reasons.append(f"Moer {moer_short_signal.get('state')} confirmed pullback add-on")
        if (
            allow_short
            and has_current_pos
            and not short_trigger
            and allow_trend_add_after_entry
            and bool(params.get("allow_add_existing_position", False))
            and trend_filter_triggered_for_side
            and trend_add_allowed_for_side
            and decision_score >= trend_add_min_score
            and dynamic_entry_allowed
        ):
            short_trigger = 'add_on'
        if allow_score_fallback_entry and dynamic_entry_allowed:
            if not long_trigger and allow_long and decision_score >= score_fallback_threshold:
                long_trigger = 'add_on' if (
                    has_current_pos
                    and allow_trend_add_after_entry
                    and bool(params.get("allow_add_existing_position", False))
                ) else 'entry'
                if score_based_entry_mode and long_trigger == "entry":
                    reasons.append("?????: ?????????????????")
            if not short_trigger and allow_short and decision_score >= score_fallback_threshold:
                short_trigger = 'add_on' if (
                    has_current_pos
                    and allow_trend_add_after_entry
                    and bool(params.get("allow_add_existing_position", False))
                ) else 'entry'
                if score_based_entry_mode and short_trigger == "entry":
                    reasons.append("?????: ?????????????????")
        elif decision_score >= min_score and not dynamic_entry_allowed:
            reasons.append(dynamic_score.get("entry_block_reason") or "???????????????")
            total_reason = " | ".join(reasons)
        elif decision_score >= min_score and not (long_trigger or short_trigger):
            reasons.append("??????????????????")
            total_reason = " | ".join(reasons)

        if long_trigger and allow_long:
            if long_trigger == "entry" and not trend_startup_long and not strict_long_bottom_divergence_ok:
                missing = sorted(
                    strict_long_bottom_div_tfs - long_bottom_divergence_timeframes
                )
                return "HOLD", (
                    f"strict long entry requires bottom divergence on {missing} | "
                    f"{total_reason}"
                ), factors

            if long_trigger == "entry" and not trend_startup_long and not score_based_entry_mode and not long_divergence_confluence_ok:
                missing = sorted(required_long_div_tfs - long_entry_timeframes)
                return "HOLD", f"??????????????{missing} | {total_reason}", factors

            if market_block_long:
                return "HOLD", f"market direction gate blocked long: {market_block_long} | {total_reason}", factors

            if adx_atr_block_long and not trend_startup_long:
                return "HOLD", f"ADX/ATR趋势门槛拦截多头: {adx_atr_block_long} | {total_reason}", factors
            if derivatives_block_long:
                return "HOLD", f"OI/资金费率门槛拦截多头: {derivatives_block_long} | {total_reason}", factors

            if trend_block_long and not score_based_entry_mode:
                return "HOLD", f"????????: {trend_block_long} | {total_reason}", factors

            if trend_block_long and score_based_entry_mode:
                reasons.append(f"??????????: {trend_block_long}")

            regime_entry_block = self._score_based_regime_entry_block(
                "LONG",
                trend_regime,
                factors,
                score_based_entry_mode,
                params,
            )
            if long_trigger != "add_on" and regime_entry_block:
                return "HOLD", (
                    f"regime-aware score gate: {regime_entry_block} | "
                    f"{' | '.join(reasons)}"
                ), factors

            if long_trigger == "add_on" and not trend_add_allowed_for_side:
                return "HOLD", f"????????: ??{trend_regime}????? | {total_reason}", factors

            if elliott_block_long:
                return "HOLD", f"????Elliott??: {elliott_block_long} | {total_reason}", factors

            if harmonic_block_long:
                return "HOLD", f"????????: {harmonic_block_long} | {total_reason}", factors

            if confirmation_rules.get("need_liquidation", False) or confirmation_rules.get("need_ma_cross", False):
                expected = sum(1 for key in ("need_liquidation", "need_ma_cross") if confirmation_rules.get(key, False))
                if long_confirmation_hits < expected:
                    return "HOLD", f"????????({long_confirmation_hits}/{expected}) | {total_reason}", factors

            if long_trigger == "add_on" and strategy.last_signal != "BUY" and not moer_long_add_on and not staged_long_add_on:
                return "HOLD", f"?????????????????? | {total_reason}", factors

            if decision_score >= min_score and dynamic_entry_allowed:
                guard_reason = await self._same_symbol_position_guard(config, strategy, symbol, "LONG")
                if guard_reason:
                    return "HOLD", f"??????: {guard_reason} | {total_reason}", factors

                position_factor = entry_pct if long_trigger in ("entry", "harmonic_rebound") else add_on_pct
                if trend_startup_long:
                    startup_multiplier = (
                        trend_startup_cfg.get("weak_position_multiplier", 0.20)
                        if trend_regime == "weak_long"
                        else trend_startup_cfg.get("position_multiplier", 0.30)
                    )
                    position_factor *= float(startup_multiplier or 0.20)
                if staged_long_probe:
                    try:
                        position_factor *= float(staged_div_cfg.get("probe_position_multiplier", 0.5) or 0.5)
                    except (TypeError, ValueError):
                        position_factor *= 0.5
                position_factor *= long_position_multiplier
                effective_trend_position_multiplier = trend_position_multiplier
                if score_based_entry_mode and long_trigger == "entry":
                    effective_trend_position_multiplier = max(
                        trend_position_multiplier,
                        score_based_min_trend_position_multiplier,
                    )
                position_factor *= effective_trend_position_multiplier
                if long_trigger == "harmonic_rebound":
                    position_factor *= harmonic_rebound_position_multiplier
                    reasons.append(f"????????x{harmonic_rebound_position_multiplier:g}")
                position_factor *= dynamic_position_multiplier
                position_factor, regime_sizing_note = self._apply_score_based_regime_sizing(
                    position_factor,
                    entry_pct if long_trigger in ("entry", "harmonic_rebound") else add_on_pct,
                    "LONG",
                    trend_regime,
                    dynamic_score,
                    score_based_entry_mode,
                    params,
                )
                if regime_sizing_note:
                    reasons.append(regime_sizing_note)
                if long_position_multiplier != 1:
                    reasons.append(f"??????x{long_position_multiplier:g}")
                if effective_trend_position_multiplier != 1:
                    trend_bar = params.get("trend_filter_timeframe", "4H")
                    reasons.append(f"{trend_bar}??????x{trend_position_multiplier:g}")
                if dynamic_position_multiplier != 1:
                    reasons.append(f"??????x{dynamic_position_multiplier:g}")

                qty = 0.0
                trade_context = None
                if params.get("margin_sizing_enabled", True):
                    try:
                        trade_context = await self._calc_white_dove_margin_plan(
                            strategy=strategy,
                            config=config,
                            current_price=current_price,
                            position_factor=position_factor,
                            symbol=symbol,
                        )
                        qty = float(trade_context.get("quantity", 0) or 0)
                    except Exception as e:
                        reasons.append(f"????????????????: {e}")

                if qty <= 0:
                    if trade_context:
                        margin_ccy = trade_context.get("margin_ccy") or "USDT"
                        return "HOLD", f"margin plan below executable threshold ({margin_ccy}){self._format_trade_context(trade_context)}", factors
                    if params.get("margin_sizing_enabled", True):
                        return "HOLD", "margin plan unavailable; skip default-size fallback", factors
                    qty = base_qty * position_factor
                    trade_context = None

                if trade_context is None:
                    trade_context = {}
                trade_context["entry_score"] = float(decision_score)
                self._pending_trade_quantity = max(qty, 0)
                self._pending_trade_context = trade_context
                long_reason = " | ".join(reasons)
                return "BUY", f"???????{decision_score:g}/{min_score}(??{score:g}): {long_reason}{self._format_trade_context(trade_context)}", factors

        if short_trigger and allow_short:
            if short_trigger == "entry" and not trend_startup_short and not score_based_entry_mode and not short_divergence_confluence_ok:
                missing = sorted(required_short_div_tfs - short_entry_timeframes)
                return "HOLD", f"??????????????{missing} | {total_reason}", factors

            if market_block_short:
                return "HOLD", f"market direction gate blocked short: {market_block_short} | {total_reason}", factors

            if adx_atr_block_short and not trend_startup_short:
                return "HOLD", f"ADX/ATR趋势门槛拦截空头: {adx_atr_block_short} | {total_reason}", factors
            if derivatives_block_short:
                return "HOLD", f"OI/资金费率门槛拦截空头: {derivatives_block_short} | {total_reason}", factors

            if trend_block_short and not score_based_entry_mode:
                return "HOLD", f"????????: {trend_block_short} | {total_reason}", factors

            if trend_block_short and score_based_entry_mode:
                reasons.append(f"??????????: {trend_block_short}")

            regime_entry_block = self._score_based_regime_entry_block(
                "SHORT",
                trend_regime,
                factors,
                score_based_entry_mode,
                params,
            )
            if short_trigger != "add_on" and regime_entry_block:
                return "HOLD", (
                    f"regime-aware score gate: {regime_entry_block} | "
                    f"{' | '.join(reasons)}"
                ), factors

            if short_trigger == "add_on" and not trend_add_allowed_for_side:
                return "HOLD", f"????????: ??{trend_regime}????? | {total_reason}", factors

            if elliott_block_short:
                return "HOLD", f"????Elliott??: {elliott_block_short} | {total_reason}", factors

            if harmonic_block_short:
                return "HOLD", f"????????: {harmonic_block_short} | {total_reason}", factors

            if confirmation_rules.get("need_liquidation", False) or confirmation_rules.get("need_ma_cross", False):
                expected = sum(1 for key in ("need_liquidation", "need_ma_cross") if confirmation_rules.get(key, False))
                if short_confirmation_hits < expected:
                    return "HOLD", f"????????({short_confirmation_hits}/{expected}) | {total_reason}", factors

            if short_trigger == "add_on" and strategy.last_signal != "SELL" and not moer_short_add_on and not staged_short_add_on:
                return "HOLD", f"?????????????????? | {total_reason}", factors

            if decision_score >= min_score and dynamic_entry_allowed:
                guard_reason = await self._same_symbol_position_guard(config, strategy, symbol, "SHORT")
                if guard_reason:
                    return "HOLD", f"??????: {guard_reason} | {total_reason}", factors

                position_factor = entry_pct if short_trigger == "entry" else add_on_pct
                if trend_startup_short:
                    startup_multiplier = (
                        trend_startup_cfg.get("weak_position_multiplier", 0.20)
                        if trend_regime == "weak_short"
                        else trend_startup_cfg.get("position_multiplier", 0.30)
                    )
                    position_factor *= float(startup_multiplier or 0.20)
                if staged_short_probe:
                    try:
                        position_factor *= float(staged_div_cfg.get("probe_position_multiplier", 0.5) or 0.5)
                    except (TypeError, ValueError):
                        position_factor *= 0.5
                effective_trend_position_multiplier = trend_position_multiplier
                if score_based_entry_mode and short_trigger == "entry":
                    effective_trend_position_multiplier = max(
                        trend_position_multiplier,
                        score_based_min_trend_position_multiplier,
                    )
                position_factor *= effective_trend_position_multiplier
                position_factor *= dynamic_position_multiplier
                position_factor, regime_sizing_note = self._apply_score_based_regime_sizing(
                    position_factor,
                    entry_pct if short_trigger == "entry" else add_on_pct,
                    "SHORT",
                    trend_regime,
                    dynamic_score,
                    score_based_entry_mode,
                    params,
                )
                if regime_sizing_note:
                    reasons.append(regime_sizing_note)
                if effective_trend_position_multiplier != 1:
                    trend_bar = params.get("trend_filter_timeframe", "4H")
                    reasons.append(f"{trend_bar}????????x{trend_position_multiplier:g}")
                if dynamic_position_multiplier != 1:
                    reasons.append(f"??????x{dynamic_position_multiplier:g}")

                qty = 0.0
                trade_context = None
                if params.get("margin_sizing_enabled", True):
                    try:
                        trade_context = await self._calc_white_dove_margin_plan(
                            strategy=strategy,
                            config=config,
                            current_price=current_price,
                            position_factor=position_factor,
                            symbol=symbol,
                        )
                        qty = float(trade_context.get("quantity", 0) or 0)
                    except Exception as e:
                        reasons.append(f"????????????????: {e}")

                if qty <= 0:
                    if trade_context:
                        margin_ccy = trade_context.get("margin_ccy") or "USDT"
                        return "HOLD", f"margin plan below executable threshold ({margin_ccy}){self._format_trade_context(trade_context)}", factors
                    if params.get("margin_sizing_enabled", True):
                        return "HOLD", "margin plan unavailable; skip default-size fallback", factors
                    qty = base_qty * position_factor
                    trade_context = None

                if trade_context is None:
                    trade_context = {}
                trade_context["entry_score"] = float(decision_score)
                self._pending_trade_quantity = max(qty, 0)
                self._pending_trade_context = trade_context
                short_reason = " | ".join(reasons)
                return "SELL", f"???????{decision_score:g}/{min_score}(??{score:g}): {short_reason}{self._format_trade_context(trade_context)}", factors

        # ?????????????????????
        if allow_short and not allow_long:
            return "HOLD", f"???????{decision_score:g}/{min_score}(??{score:g}): {total_reason}", factors
        if allow_long and not allow_short:
            return "HOLD", f"???????{decision_score:g}/{min_score}(??{score:g}): {total_reason}", factors
        return "HOLD", f"???????{decision_score:g}/{min_score}(??{score:g}): {total_reason}", factors

    # ==================== ??????? ====================

    def _get_contract_value(self, symbol: str) -> float:
        """静态兜底合约面值(SWAP/FUTURES)，仅用于实时查询失败时"""
        return get_static_ct_val(symbol)

    async def _get_profit_lock_contract_value(self, symbol: str) -> float:
        """Require an exchange-confirmed linear USDT contract specification."""
        try:
            instruments = await okx_manager.get_instruments("SWAP")
            for item in instruments:
                if item.get("instId") != symbol:
                    continue
                value = float(item.get("ctVal") or 0)
                multiplier = float(item.get("ctMult") or 1)
                if (
                    math.isfinite(value) and value > 0
                    and multiplier == 1
                    and item.get("ctType") == "linear"
                    and item.get("settleCcy") == "USDT"
                    and item.get("ctValCcy") == symbol.split("-")[0]
                ):
                    return value
                break
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "profit_lock_spec_unavailable symbol=%s error=%s", symbol, type(exc).__name__
            )
            return 0.0
        logging.getLogger(__name__).warning("profit_lock_spec_invalid symbol=%s", symbol)
        return 0.0

    async def _get_contract_value_live(self, symbol: str, market_type: str = "SWAP") -> float:
        """???? OKX ???????????????????"""
        trade_symbol = self._normalize_strategy_symbol_for_market(symbol, market_type)
        inst_type = "FUTURES" if (market_type or "").upper() == "FUTURES" else "SWAP"
        try:
            instruments = await okx_manager.get_instruments(inst_type)
            instrument = next(
                (item for item in instruments if item.get("instId", "").upper() == trade_symbol),
                None,
            )
            if instrument:
                ct_val = float(instrument.get("ctVal") or 0)
                if ct_val > 0:
                    return ct_val
        except Exception as e:
            print(f"???????? [{trade_symbol}]: {e}")
        return self._get_contract_value(trade_symbol)

    async def _get_available_usdt(self, config: ExchangeConfig) -> float:
        """??????USDT"""
        return await self._get_available_balance(config, "USDT")

    async def _get_available_balance(self, config: ExchangeConfig, ccy: str) -> float:
        """?????????????"""
        target_ccy = (ccy or "USDT").upper()
        details = await okx_manager.get_balance(
            api_key=decrypt_text(config.api_key),
            api_secret=decrypt_text(config.api_secret),
            passphrase=decrypt_text(config.api_passphrase or ""), simulated=bool(config.is_testnet))
        for d in details or []:
            if (d.get("ccy") or "").upper() == target_ccy:
                value = d.get("availEq") or d.get("availBal") or d.get("cashBal") or d.get("eq")
                try:
                    return float(value or 0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    async def _get_currency_equity(self, config: ExchangeConfig, ccy: str) -> float:
        details = await okx_manager.get_balance(
            api_key=decrypt_text(config.api_key),
            api_secret=decrypt_text(config.api_secret),
            passphrase=decrypt_text(config.api_passphrase or ""),
            simulated=bool(config.is_testnet),
        )
        for item in details or []:
            if str(item.get("ccy", "")).upper() == ccy.upper():
                value = float(item.get("eq") or 0)
                if math.isfinite(value) and value > 0:
                    return value
        raise ValueError("account_equity_unavailable_for_sizing")

    async def _get_instrument_margin_ccy(self, symbol: str, market_type: str = "SWAP") -> str:
        trade_symbol = self._normalize_strategy_symbol_for_market(symbol, market_type)
        market_type = (market_type or "SWAP").upper()
        if market_type == "SPOT":
            return "USDT"
        if market_type == "SWAP":
            return "USDT"
        if "_UM" in trade_symbol:
            return "USDC"
        try:
            instruments = await okx_manager.get_instruments("FUTURES")
            instrument = next(
                (item for item in instruments if item.get("instId", "").upper() == trade_symbol),
                None,
            )
            settle_ccy = (instrument or {}).get("settleCcy") or ""
            if settle_ccy.upper() == "USD":
                return "USDC"
            return settle_ccy.upper() or "USDT"
        except Exception:
            return "USDT"

    async def _get_current_position_count(self, config: ExchangeConfig) -> int:
        positions = await monitor_service.get_positions(config)
        count = 0
        for pos in positions or []:
            try:
                if float(pos.get("pos", 0) or 0) != 0:
                    count += 1
            except (TypeError, ValueError):
                continue
        return count

    async def _get_open_position_symbols(self, config: ExchangeConfig) -> list:
        """?????????? symbol ????? instId ????"""
        positions = await monitor_service.get_positions(config)
        symbols = []
        seen = set()
        for pos in positions or []:
            try:
                if float(pos.get("pos", 0) or 0) != 0:
                    inst_id = pos.get("instId", "")
                    norm = (inst_id or "").replace("-", "").upper()
                    if norm and norm not in seen:
                        symbols.append(inst_id)
                        seen.add(norm)
            except (TypeError, ValueError):
                continue
        return symbols

    def _position_key_from_values(self, symbol: str, direction: str) -> tuple[str, str]:
        return (self._normalize_strategy_symbol(symbol), (direction or "").upper())

    def _position_key_from_okx_position(self, pos: dict) -> tuple[str, str] | None:
        try:
            pos_size = float(pos.get("pos", 0) or 0)
        except (TypeError, ValueError):
            return None
        if pos_size == 0:
            return None
        symbol = self._normalize_strategy_symbol(pos.get("instId", ""))
        if not symbol:
            return None
        pos_side = (pos.get("posSide") or "net").lower()
        direction = "SHORT" if pos_side == "short" or (pos_side == "net" and pos_size < 0) else "LONG"
        return (symbol, direction)

    async def _get_live_position_keys(self, config: ExchangeConfig) -> set[tuple[str, str]]:
        positions = await monitor_service.get_positions(config)
        keys = set()
        for pos in positions or []:
            key = self._position_key_from_okx_position(pos)
            if key:
                keys.add(key)
        return keys

    # ==================== 交易所侧兜底止损（Phase 2c） ====================

    def _native_stop_trigger_price(self, direction: str, entry_price: float, params: dict) -> float | None:
        """兜底止损触发价：hard_stop 启用 → stop_pct × NATIVE_STOP_MULTIPLIER
        （正常时软件轮询止损先触发，交易所单纯做保险）；未启用 → NATIVE_STOP_DEFAULT_PCT；
        params.native_stop_pct 显式覆盖优先；native_stop_enabled=False 整体关闭。"""
        params = params or {}
        if not params.get("native_stop_enabled", True):
            return None
        try:
            entry = float(entry_price or 0)
        except (TypeError, ValueError):
            return None
        if entry <= 0:
            return None
        override = params.get("native_stop_pct")
        try:
            if override:
                pct = float(override)
            else:
                hard_stop = (params.get("exit_factors") or {}).get("hard_stop") or {}
                if hard_stop.get("enabled", False):
                    raw_pct = float(hard_stop.get("stop_pct", 0.07))
                    # hard_stop 自 2026-08-05（docs/09 · P1）起可按保证金收益率口径配置，
                    # 而触发价永远是价格口径——必须按杠杆折算回价格，否则 upl_ratio 下的
                    # 0.30 会被当成 30% 价格止损（×1.5 = 45%），兜底等于不存在。
                    hs_metric = str(hard_stop.get("metric", "price_change")).lower()
                    if hs_metric in {"upl_ratio", "margin_return", "roe"}:
                        try:
                            lev = float(params.get("leverage", 1) or 1)
                        except (TypeError, ValueError):
                            lev = 1.0
                        if lev > 0:
                            raw_pct = raw_pct / lev
                    pct = raw_pct * self.NATIVE_STOP_MULTIPLIER
                else:
                    pct = self.NATIVE_STOP_DEFAULT_PCT
        except (TypeError, ValueError):
            pct = self.NATIVE_STOP_DEFAULT_PCT
        if pct <= 0 or pct >= 1:
            return None
        if (direction or "").upper() == "LONG":
            return entry * (1 - pct)
        return entry * (1 + pct)

    @staticmethod
    def _format_stop_trigger_px(price: float, tick_sz: str | None, direction: str) -> str:
        """触发价对齐 tickSz——LONG 向下、SHORT 向上取整，只会把兜底线推远，绝不更近。"""
        try:
            tick = Decimal(str(tick_sz)) if tick_sz else None
        except Exception:
            tick = None
        if not tick or tick <= 0:
            return f"{price:.8f}".rstrip("0").rstrip(".")
        rounding = ROUND_FLOOR if (direction or "").upper() == "LONG" else ROUND_CEILING
        quantized = (Decimal(str(price)) / tick).to_integral_value(rounding=rounding) * tick
        return format(quantized.normalize(), "f")

    async def _cancel_native_stops(self, config: ExchangeConfig, symbol: str, pos_side: str = None) -> int:
        """撤掉该合约上本系统挂的兜底止损单（按 algoClOrdId 的 bg2sl 前缀识别，可按 posSide 过滤）。

        自愈设计：不依赖本地状态记 algoId——重启/漏撤后，下一次开仓或减仓走到这里会清干净。
        失败只告警返回 0，清理失败不阻断主流程。"""
        try:
            api_key = decrypt_text(config.api_key)
            api_secret = decrypt_text(config.api_secret)
            passphrase = decrypt_text(config.api_passphrase or "")
            pending = await okx_manager.get_pending_algo_stops(api_key, api_secret, passphrase, inst_id=symbol, simulated=bool(config.is_testnet))
            items = []
            for algo in pending or []:
                if not str(algo.get("algoClOrdId") or "").startswith(NATIVE_STOP_ALGO_PREFIX):
                    continue
                if pos_side and (algo.get("posSide") or "").lower() != pos_side.lower():
                    continue
                algo_id = algo.get("algoId")
                if algo_id:
                    items.append({"algoId": algo_id, "instId": symbol})
            if items:
                await okx_manager.cancel_algo_orders(api_key, api_secret, passphrase, items, simulated=bool(config.is_testnet))
                print(f"[兜底止损] 清理旧挂单 {symbol} ×{len(items)}")
            return len(items)
        except Exception as e:
            print(f"[兜底止损清理失败] {symbol}: {e}")
            return 0

    async def _place_native_stop_backstop(
        self, config: ExchangeConfig, symbol: str, direction: str,
        entry_price: float, quantity: float, params: dict,
        market_type: str = "SWAP",
    ):
        """开仓成交后在交易所侧挂条件止损兜底单（reduceOnly，触发即市价平仓）。

        进程挂掉/断网/查询失败期间软件轮询止损失效，这张单是最后防线。
        任何失败只告警不上抛——开仓已经发生，兜底失败不能把成功的开仓标记为失败。"""
        market_type = (market_type or "SWAP").upper()
        if market_type == "SPOT":
            return None
        trigger = self._native_stop_trigger_price(direction, entry_price, params)
        if not trigger or not quantity or quantity <= 0:
            return None
        pos_side = "long" if (direction or "").upper() == "LONG" else "short"
        try:
            tick_sz = None
            try:
                inst_type = "FUTURES" if market_type == "FUTURES" else "SWAP"
                instruments = await okx_manager.get_instruments(inst_type)
                tick_sz = next(
                    (item.get("tickSz") for item in instruments if item.get("instId") == symbol), None
                )
            except Exception:
                pass
            trigger_px = self._format_stop_trigger_px(trigger, tick_sz, direction)

            credentials = (decrypt_text(config.api_key), decrypt_text(config.api_secret),
                           decrypt_text(config.api_passphrase or ""))
            old_stops = await okx_manager.get_pending_algo_stops(
                *credentials, inst_id=symbol, simulated=bool(config.is_testnet))
            result = await okx_manager.place_algo_stop_loss(
                decrypt_text(config.api_key),
                decrypt_text(config.api_secret),
                decrypt_text(config.api_passphrase or ""),
                inst_id=symbol,
                td_mode=(params or {}).get("margin_mode", "cross") or "cross",
                side="sell" if pos_side == "long" else "buy",
                sz=str(quantity),
                trigger_px=trigger_px,
                pos_side=pos_side, simulated=bool(config.is_testnet))
            from app.services.native_stop_validation import native_stop_covers
            new_id = str(result.get("algoId") or "")
            confirmed = await okx_manager.get_pending_algo_stops(
                *credentials, inst_id=symbol, simulated=bool(config.is_testnet))
            if not new_id or not any(str(row.get("algoId")) == new_id and native_stop_covers(
                    row, symbol, direction.upper(), quantity, float(trigger_px)) for row in confirmed or []):
                raise RuntimeError("replacement stop unverified; old protection retained")
            # Cancel only the pre-placement snapshot, never the new protection.
            obsolete = [{"algoId": row["algoId"], "instId": symbol} for row in old_stops or []
                        if row.get("algoId") and str(row["algoId"]) != new_id
                        and str(row.get("algoClOrdId") or "").startswith(NATIVE_STOP_ALGO_PREFIX)
                        and row.get("posSide") == pos_side]
            if obsolete:
                cancelled = await okx_manager.cancel_algo_orders(
                    *credentials, obsolete, simulated=bool(config.is_testnet))
                if len(cancelled) != len(obsolete) or any(str(row.get("sCode")) != "0" for row in cancelled):
                    print(f"[native-stop] {symbol}: old-stop cancellation incomplete; new stop retained")
            print(f"[兜底止损] {symbol} {direction} 触发价{trigger_px} 数量{quantity} algoId={result.get('algoId')}")
            self._native_stop_stats["placed"] += 1
            return result
        except Exception as e:
            print(f"[兜底止损挂单失败] {symbol} {direction}: {e}（不阻断开仓，软件止损轮询仍在）")
            # 兜底止损是"软件轮询失效时的最后防线"，挂不上必须有人知道（Phase 2.5f）：
            # 此前这里只 print，而这恰恰是最不该静默的一类失败。
            self._native_stop_stats["failed"] += 1
            await self._alert(
                "兜底止损挂单失败",
                f"{symbol} {direction}: {e}（开仓未受影响，但交易所侧已无最后防线）",
                key=f"native-stop-failed-{symbol}-{direction}",
            )
            return None

    async def _refresh_native_stop_after_reduce(
        self, config: ExchangeConfig, symbol: str, close_side: str, params: dict,
    ) -> float | None:
        """Keep old reduce-only protection until remaining size and replacement are confirmed."""
        pos_side = "long" if (close_side or "").upper() == "SELL" else "short"
        direction = "LONG" if pos_side == "long" else "SHORT"
        try:
            positions = await monitor_service.get_positions(config)
        except Exception as e:
            print(f"[兜底止损] 减仓后剩余仓位查询失败，暂不重挂 {symbol}: {e}")
            return None
        remaining = 0.0
        entry_px = 0.0
        for pos in positions or []:
            if (pos.get("instId") or "").upper() != (symbol or "").upper():
                continue
            p_side = (pos.get("posSide") or "net").lower()
            try:
                p_size = float(pos.get("pos", 0) or 0)
            except (TypeError, ValueError):
                continue
            if p_side == pos_side or (p_side == "net" and p_size != 0 and (p_size > 0) == (pos_side == "long")):
                remaining = abs(p_size)
                try:
                    entry_px = float(pos.get("avgPx", 0) or 0)
                except (TypeError, ValueError):
                    entry_px = 0.0
                break
        if remaining > 0 and entry_px > 0:
            await self._place_native_stop_backstop(
                config, symbol, direction, entry_px, remaining, params,
                market_type="FUTURES" if not (symbol or "").upper().endswith("-SWAP") else "SWAP",
            )
        elif remaining == 0:
            await self._cancel_native_stops(config, symbol, pos_side)
        return remaining

    async def _same_symbol_position_guard(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
        symbol: str,
        target_direction: str,
    ) -> str | None:
        """Block duplicate or opposite same-contract opens unless the strategy allows them."""
        params = strategy.params or {}
        normalized_symbol = self._normalize_strategy_symbol_for_market(symbol, strategy.market_type)
        live_keys = await self._get_live_position_keys(config)
        same_symbol_positions = {
            direction
            for pos_symbol, direction in live_keys
            if pos_symbol == normalized_symbol
        }
        if not same_symbol_positions:
            return None
        if not bool(params.get("allow_add_existing_position", False)):
            return f"{normalized_symbol} ???????????/??"
        if target_direction not in same_symbol_positions and not bool(params.get("allow_hedge_same_symbol", False)):
            return f"{normalized_symbol} ?????????????"
        return None

    async def _get_strategy_live_position_keys(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
    ) -> set[tuple[str, str]]:
        """Return live position keys attributed to this concrete strategy."""
        strategy_marker = self._format_strategy_marker(strategy)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(TradeRecord.symbol, TradeRecord.direction)
                .where(
                    TradeRecord.user_id == strategy.user_id,
                    TradeRecord.exchange_config_id == config.id,
                    TradeRecord.is_closed == False,
                    TradeRecord.strategy_tag == strategy_marker,
                )
                .order_by(TradeRecord.updated_at.desc(), TradeRecord.id.desc())
            )
            strategy_keys = {
                self._position_key_from_values(symbol, direction)
                for symbol, direction in result.all()
            }
        # Strategy ownership comes from local open TradeRecord rows. This must not depend on
        # OKX positions already being visible, otherwise a fast scan can overshoot limits.
        return strategy_keys

    async def _assert_global_open_symbol_limit(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
        trade_symbol: str,
    ) -> None:
        params = strategy.params or {}
        try:
            global_limit = int(params.get("global_max_open_symbols", 0) or 0)
        except (TypeError, ValueError):
            global_limit = 0
        if global_limit <= 0:
            return

        strategy_ids = self._get_strategy_id_set(
            params.get("global_open_symbol_strategy_ids"), strategy.id
        )
        if not strategy_ids:
            return

        normalized_trade_symbol = self._normalize_strategy_symbol(trade_symbol)
        open_symbols = await self._get_open_symbols_for_strategy_ids(config, strategy, strategy_ids)

        if normalized_trade_symbol in open_symbols:
            return
        if len(open_symbols) >= global_limit:
            if await self._rotate_transition_tail(
                config,
                strategy,
                normalized_trade_symbol,
                strategy_ids,
            ):
                return
            strategy_list = ",".join(str(strategy_id) for strategy_id in sorted(strategy_ids))
            raise ValueError(
                f"auto order risk blocked: strategies[{strategy_list}] reached global open symbol limit "
                f"({len(open_symbols)}/{global_limit})"
            )
        transition_enabled = bool(params.get("global_transition_slot_enabled", False))
        primary_limit = int(params.get("global_primary_open_symbols", global_limit) or global_limit)
        if transition_enabled and len(open_symbols) >= primary_limit:
            pending_context = getattr(self, "_pending_trade_context", None) or {}
            entry_score = pending_context.get("entry_score")
            minimum_score = float(params.get(
                "global_transition_min_score",
                float(params.get("trend_v3_min_open_score", 0) or 0) + 0.5,
            ) or 0)
            if not transition_slot_allows(
                len(open_symbols), primary_limit, entry_score, minimum_score
            ):
                raise ValueError(
                    "auto order risk blocked: transition slot requires "
                    f"score >= {minimum_score:g} (actual {entry_score})"
                )

    async def _rotate_transition_tail(
        self,
        config: ExchangeConfig,
        incoming_strategy: TradingStrategy,
        incoming_symbol: str,
        strategy_ids: set[int],
    ) -> bool:
        """Close one weak profitable tail so a stronger ranked candidate can use slot four."""
        params = incoming_strategy.params or {}
        if not bool(params.get("global_transition_rotation_enabled", False)):
            return False
        pending_context = getattr(self, "_pending_trade_context", None) or {}
        try:
            entry_score = float(pending_context.get("entry_score"))
            minimum_score = float(params.get(
                "global_transition_min_score",
                float(params.get("trend_v3_min_open_score", 0) or 0) + 0.5,
            ) or 0)
        except (TypeError, ValueError):
            return False
        if entry_score < minimum_score:
            return False

        now = datetime.now(timezone.utc)
        cooldown = max(30.0, float(params.get("global_transition_cooldown_minutes", 30)))
        async with AsyncSessionLocal() as cooldown_db:
            recent = await cooldown_db.execute(select(StrategyLog.id).where(
                StrategyLog.exchange_config_id == config.id,
                StrategyLog.reason.like("%transition slot rotation:%"),
                StrategyLog.created_at >= now - timedelta(minutes=cooldown),
            ).limit(1))
            if recent.first() is not None:
                return False

        try:
            live_positions = await monitor_service.get_positions(config)
        except Exception:
            return False
        live_by_symbol = {
            self._normalize_strategy_symbol(str(position.get("instId") or "")): position
            for position in live_positions or []
            if float(position.get("pos", 0) or 0) != 0
        }
        maximum_tail_ratio = max(0.01, min(0.30, float(
            params.get("global_transition_tail_max_ratio", 0.30) or 0.30
        )))

        async with AsyncSessionLocal() as rotation_db:
            records = await rotation_db.execute(
                select(
                    TradeRecord.symbol,
                    TradeRecord.direction,
                    TradeRecord.strategy_tag,
                ).where(
                    TradeRecord.user_id == incoming_strategy.user_id,
                    TradeRecord.exchange_config_id == config.id,
                    TradeRecord.is_closed == False,
                )
            )
            owner_rows = records.all()
            owner_strategies = {
                candidate_id: await rotation_db.get(TradingStrategy, candidate_id)
                for candidate_id in strategy_ids
            }

        incoming_ticker = await okx_manager.get_ticker(incoming_symbol)
        incoming_price = float(incoming_ticker.get("last") or 0)
        if not math.isfinite(incoming_price) or incoming_price <= 0:
            return False
        _, incoming_reason, incoming_factors = await self._white_dove_v3_strategy(
            incoming_strategy, incoming_price, incoming_symbol, config, False, score_only=True,
        )
        if incoming_reason != "rotation_score_only":
            return False
        entry_score = next(((f.get("raw") or {}).get("score") for f in incoming_factors if f.get("key") == "trend_v3_score"), None)
        if not rotation_score_gap(entry_score, entry_score, minimum_score, 0):
            return False

        candidates = []
        seen_symbols = set()
        for raw_symbol, direction, strategy_tag in owner_rows:
            symbol = self._normalize_strategy_symbol(raw_symbol)
            if symbol in seen_symbols or symbol == incoming_symbol:
                continue
            owner_id = self._extract_strategy_id_from_tag(strategy_tag)
            owner_strategy = owner_strategies.get(owner_id)
            position = live_by_symbol.get(symbol)
            if owner_strategy is None or position is None:
                continue
            seen_symbols.add(symbol)
            quantity = abs(float(position.get("pos", 0) or 0))
            unrealized_pnl = float(position.get("upl", 0) or 0)
            unrealized_pnl_ratio = float(position.get("uplRatio", 0) or 0)
            open_time_ms = str(position.get("cTime", "") or "")
            try:
                age_ms = now.timestamp() * 1000 - float(open_time_ms)
                if not math.isfinite(age_ms) or age_ms < 1800000:
                    continue
            except (TypeError, ValueError):
                continue
            owner_params = owner_strategy.params or {}
            core_ratio = max(0.01, min(1.0, float(
                owner_params.get("trend_runner_min_remaining_ratio", 0.50) or 0.50
            )))
            _, _, core_quantity = await self._resolve_trend_runner_core_quantity(
                owner_strategy,
                config,
                symbol,
                str(direction or "").upper(),
                open_time_ms,
                quantity,
            )
            remaining_ratio = quantity / max(core_quantity / core_ratio, 1e-12)
            profitable_tail = is_replaceable_transition_tail(
                quantity,
                core_quantity,
                core_ratio,
                unrealized_pnl,
                maximum_tail_ratio,
            )
            trend = await self._get_trend_regime_signal(symbol, owner_params)
            regime = str(trend.get("regime") or "unknown").lower()
            strong_regime = "strong_long" if str(direction).upper() == "LONG" else "strong_short"
            replacement_kind = classify_transition_replacement(
                unrealized_pnl,
                profitable_tail,
                regime,
                strong_regime,
            )
            if replacement_kind is None:
                continue
            if not rotation_weak_closed_bars(
                await okx_manager.get_candles(symbol, "5m", 100),
                str(direction).upper(), now.timestamp() * 1000,
            ):
                continue
            price = float(position.get("markPx") or 0)
            if not math.isfinite(price) or price <= 0:
                continue
            _, _, old_factors = await self._white_dove_v3_strategy(
                owner_strategy, price, symbol, config, False, score_only=True,
            )
            old_score = next(((f.get("raw") or {}).get("score") for f in old_factors if f.get("key") == "trend_v3_score"), None)
            if not rotation_score_gap(entry_score, old_score, minimum_score, float(params.get("global_transition_min_score_delta", 1.5))):
                continue
            candidates.append({
                "old_score": float(old_score),
                "symbol": symbol,
                "direction": str(direction or "").upper(),
                "quantity": quantity,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_ratio": unrealized_pnl_ratio,
                "remaining_ratio": remaining_ratio,
                "replacement_kind": replacement_kind,
                "strategy": owner_strategy,
                "position": position,
                "regime": regime,
            })

        if not candidates:
            return False
        tail = min(
            candidates,
            key=lambda item: (
                0 if item["replacement_kind"] == "losing" else 1,
                item["old_score"],
                item["remaining_ratio"],
                item["quantity"],
            ),
        )
        close_side = "SELL" if tail["direction"] == "LONG" else "BUY"
        pos_side = str(tail["position"].get("posSide") or "").lower()
        if pos_side not in {"long", "short"}:
            pos_side = None
        mark_price = float(
            tail["position"].get("markPx", 0)
            or tail["position"].get("last", 0)
            or tail["position"].get("avgPx", 0)
            or 0
        )
        reduced = await self._do_reduce(
            tail["strategy"],
            config,
            tail["symbol"],
            tail["quantity"],
            close_side,
            mark_price,
            (
                f"transition slot rotation: {tail['replacement_kind']} "
                f"residual {tail['remaining_ratio']*100:.1f}% "
                f"regime={tail['regime']} old_score={tail['old_score']:.1f} replaced by {incoming_symbol} score={entry_score:.1f}"
            ),
            tail["strategy"].params or {},
            pos_side,
            set(),
        )
        if reduced:
            # A successful submit is not proof of a fill. Never reuse the old entry plan.
            positions = await okx_manager.get_positions(
                decrypt_text(config.api_key), decrypt_text(config.api_secret),
                decrypt_text(config.api_passphrase or ""), simulated=bool(config.is_testnet),
            )
            remaining = any(self._normalize_strategy_symbol(p.get("instId", "")) == tail["symbol"] and abs(float(p.get("pos") or 0)) > 0 for p in positions)
            print(f"[rotation verification] {tail['symbol']} flat={not remaining}; rescan required")
        return False

    @staticmethod
    def _get_strategy_id_set(raw_strategy_ids, fallback_strategy_id: int) -> set[int]:
        values = raw_strategy_ids or [fallback_strategy_id]
        return {
            int(strategy_id)
            for strategy_id in values
            if str(strategy_id).strip().isdigit()
        }

    async def _get_open_symbols_for_strategy_ids(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
        strategy_ids: set[int],
    ) -> set[str]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(TradeRecord.symbol, TradeRecord.strategy_tag)
                .where(
                    TradeRecord.user_id == strategy.user_id,
                    TradeRecord.exchange_config_id == config.id,
                    TradeRecord.is_closed == False,
                )
                .order_by(TradeRecord.updated_at.desc(), TradeRecord.id.desc())
            )
            return {
                self._normalize_strategy_symbol(symbol)
                for symbol, strategy_tag in result.all()
                if self._extract_strategy_id_from_tag(strategy_tag) in strategy_ids
            }

    async def _get_global_entry_allocation_context(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
    ) -> dict | None:
        """Resolve the next account-wide staged allocation for a new position."""
        params = strategy.params or {}
        allocation_steps = params.get("global_entry_allocation_steps")
        if not allocation_steps:
            return None
        strategy_ids = self._get_strategy_id_set(
            params.get("global_entry_allocation_strategy_ids"), strategy.id
        )
        if not strategy_ids:
            return None
        open_symbols = await self._get_open_symbols_for_strategy_ids(config, strategy, strategy_ids)
        allocation_pct = resolve_global_entry_allocation(len(open_symbols), allocation_steps)
        return {
            "strategy_ids": sorted(strategy_ids),
            "open_symbols": sorted(open_symbols),
            "open_symbol_count": len(open_symbols),
            "allocation_steps": allocation_steps,
            "allocation_pct": allocation_pct,
        }

    def _extract_strategy_id_from_tag(self, strategy_tag: str | None) -> int | None:
        tag = (strategy_tag or "").strip()
        if not tag.startswith("#"):
            return None
        raw_id = []
        for char in tag[1:]:
            if not char.isdigit():
                break
            raw_id.append(char)
        return int("".join(raw_id)) if raw_id else None

    def _infer_market_type_from_symbol(self, symbol: str) -> str:
        normalized = (symbol or "").strip().upper()
        if normalized.endswith("-SWAP") or normalized.endswith("USDTSWAP"):
            return "SWAP"
        parts = normalized.split("-")
        if len(parts) >= 3 and parts[-1].isdigit():
            return "FUTURES"
        return "SPOT"

    def _strategy_family_from_values(self, strategy_type: str | None, params: dict | None = None) -> str:
        params = params or {}
        explicit_family = (params.get("strategy_family") or params.get("position_horizon") or "").strip().lower()
        if explicit_family:
            return explicit_family

        strategy_type = (strategy_type or "").strip().lower()
        if strategy_type == "micro_scalp":
            return "scalp"
        if strategy_type in {"white_dove", "multi_factor", "macro_filtered", "divergence", "liquidation_map", "ma_cross"}:
            return "trend"
        if strategy_type == "grid":
            return "grid"
        if strategy_type == "rsi":
            return "mean_reversion"
        return strategy_type or "unknown"

    def _strategy_family(self, strategy: TradingStrategy) -> str:
        return self._strategy_family_from_values(strategy.strategy_type, strategy.params or {})

    async def _assert_no_cross_strategy_symbol_conflict(
        self,
        config: ExchangeConfig,
        strategy: TradingStrategy,
        trade_symbol: str,
    ) -> None:
        """Coordinate same-symbol opens without blocking compatible strategy styles."""
        params = strategy.params or {}
        mode = str(params.get("cross_strategy_symbol_mode", "ledger_only") or "ledger_only").lower()
        if bool(params.get("allow_cross_strategy_same_symbol", False)) or mode != "exclusive":
            return

        normalized_symbol = self._normalize_strategy_symbol(trade_symbol)
        strategy_marker = self._format_strategy_marker(strategy)
        current_market_type = (strategy.market_type or self._infer_market_type_from_symbol(trade_symbol)).upper()

        async with AsyncSessionLocal() as guard_db:
            result = await guard_db.execute(
                select(TradeRecord.symbol, TradeRecord.direction, TradeRecord.strategy_tag)
                .where(
                    TradeRecord.user_id == strategy.user_id,
                    TradeRecord.exchange_config_id == config.id,
                    TradeRecord.is_closed == False,
                )
                .order_by(TradeRecord.updated_at.desc(), TradeRecord.id.desc())
            )
            open_records = result.all()
            strategy_ids = {
                strategy_id
                for _symbol, _direction, strategy_tag in open_records
                if (strategy_id := self._extract_strategy_id_from_tag(strategy_tag)) is not None
            }
            strategy_meta = {}
            if strategy_ids:
                meta_result = await guard_db.execute(
                    select(
                        TradingStrategy.id,
                        TradingStrategy.market_type,
                        TradingStrategy.strategy_type,
                        TradingStrategy.params,
                    ).where(TradingStrategy.id.in_(strategy_ids))
                )
                strategy_meta = {
                    row.id: {
                        "market_type": (row.market_type or "").upper(),
                        "family": self._strategy_family_from_values(row.strategy_type, row.params or {}),
                    }
                    for row in meta_result.all()
                }

        conflicts = []
        for symbol, direction, strategy_tag in open_records:
            record_symbol = self._normalize_strategy_symbol(symbol)
            record_tag = strategy_tag or "???"
            if record_symbol != normalized_symbol or record_tag == strategy_marker:
                continue

            record_strategy_id = self._extract_strategy_id_from_tag(record_tag)
            meta = strategy_meta.get(record_strategy_id, {})
            record_market_type = (meta.get("market_type") or self._infer_market_type_from_symbol(symbol)).upper()
            if record_market_type != current_market_type:
                continue

            record_family = meta.get("family") or "unknown"
            conflicts.append((record_tag, direction, record_market_type, record_family))

        if conflicts:
            holders = ", ".join(
                f"{tag}/{market_type}/{family}/{(direction or 'UNKNOWN').upper()}"
                for tag, direction, market_type, family in conflicts[:3]
            )
            raise ValueError(
                f"???????: {normalized_symbol} ???????????({holders})?"
                "??????????"
            )

    async def _fetch_recent_news_factors(self, coin: str, hours: int = 24, limit: int = 10):
        """??????????????session????????"""
        try:
            async with AsyncSessionLocal() as db:
                now = datetime.now(timezone.utc)
                cutoff = now - timedelta(hours=hours)
                from sqlalchemy import select, and_
                result = await db.execute(
                    select(NewsFactor).where(
                        and_(
                            NewsFactor.symbol == coin,
                            NewsFactor.created_at > cutoff,
                            NewsFactor.expires_at > now,
                        )
                    ).order_by(NewsFactor.created_at.desc()).limit(limit)
                )
                return result.scalars().all()
        except Exception as e:
            print(f"????????: {e}")
            return []

    @staticmethod
    def _format_trade_context(context: dict) -> str:
        if not context:
            return ""
        margin_ccy = context.get("margin_ccy") or "USDT"
        return (
            f" | ???{context.get('target_margin', 0):.2f}{margin_ccy}"
            f" | ??{context.get('nominal_usdt', 0):.2f}{margin_ccy}"
            f" | ??{context.get('quantity', 0)}"
        )

    def _build_white_dove_dynamic_score(
        self,
        factors: list,
        direction: str,
        min_score: float,
        params: dict,
    ) -> dict:
        """Classify white-dove factors so one strong lead factor is not flattened."""
        raw_base_score = sum(float(f.get("score_added", 0) or 0) for f in factors)
        base_score = raw_base_score
        enabled = bool(params.get("dynamic_scoring_enabled", True))
        if not enabled:
            return {
                "enabled": False,
                "base_score": raw_base_score,
                "raw_base_score": raw_base_score,
                "decision_score": raw_base_score,
                "position_multiplier": 1.0,
                "detail": "???????",
            }

        priority = {
            "divergence": 5,
            "harmonic_rebound_long": 5,
            "liquidation_map": 4,
            "black_candle": 3,
            "elliott_wave": 3,
            "harmonic_filter": 3,
            "trend_filter": 2,
            "ma_cross": 2,
            "news_factor": 1,
        }
        trade_type_by_key = {
            "harmonic_rebound_long": "?????",
            "divergence": "???",
            "liquidation_map": "?????",
            "black_candle": "?????",
            "elliott_wave": "Elliott???",
            "trend_filter": "?????",
            "ma_cross": "?????",
            "news_factor": "?????",
        }
        divergence_cycle = ",".join(
            str(tf.get("bar", params.get("divergence_timeframe", "5m")))
            for tf in (params.get("divergence_timeframes") or [{"bar": params.get("divergence_timeframe", "5m")}])
        )
        black_candle_cycle = ",".join(
            str(tf.get("bar", "5m"))
            for tf in (params.get("black_candle_timeframes") or [])
        ) or "???"
        cycle_profile_by_key = {
            "divergence": {
                "role": "????",
                "cycle": divergence_cycle,
                "phase": "??/??????",
                "yang": "?????",
                "yin": "????????????",
            },
            "liquidation_map": {
                "role": "?????",
                "cycle": "????/?????",
                "phase": "??/??",
                "yang": "???????",
                "yin": "??????????",
            },
            "black_candle": {
                "role": "?????",
                "cycle": black_candle_cycle,
                "phase": "????/??",
                "yang": "?????????",
                "yin": "??????????",
            },
            "elliott_wave": {
                "role": "Elliott????",
                "cycle": "30m/4H",
                "phase": "5?/ABC??",
                "yang": "???????????",
                "yin": "????????/????",
            },
            "harmonic_rebound_long": {
                "role": "???????",
                "cycle": "4H/1D + 5m",
                "phase": "D???/????",
                "yang": "??????????????????",
                "yin": "????????????????",
            },
            "trend_filter": {
                "role": "????",
                "cycle": str(params.get("trend_filter_timeframe", "4H")),
                "phase": "??????",
                "yang": "??????",
                "yin": "???????????",
            },
            "ma_cross": {
                "role": "????",
                "cycle": str(params.get("ma_cross_timeframe", "1H")),
                "phase": "????",
                "yang": "??????",
                "yin": "??????????",
            },
            "news_factor": {
                "role": "????",
                "cycle": "24h",
                "phase": "????",
                "yang": "??????",
                "yin": "??????????????",
            },
        }
        complement_map = {
            "liquidation_map": {"divergence", "black_candle"},
            "divergence": {"liquidation_map", "black_candle", "elliott_wave"},
            "black_candle": {"liquidation_map", "divergence", "elliott_wave"},
            "elliott_wave": {"divergence", "trend_filter", "ma_cross", "harmonic_filter"},
            "harmonic_filter": {"divergence", "trend_filter", "ma_cross", "elliott_wave"},
            "harmonic_rebound_long": {"trend_filter", "harmonic_filter", "elliott_wave", "ma_cross"},
            "news_factor": {"liquidation_map", "divergence", "black_candle", "elliott_wave", "harmonic_filter"},
            "trend_filter": {"liquidation_map", "divergence", "black_candle", "elliott_wave", "harmonic_filter"},
            "ma_cross": {"liquidation_map", "divergence", "black_candle", "elliott_wave", "harmonic_filter"},
        }
        core_keys = {"liquidation_map", "divergence", "black_candle", "elliott_wave", "harmonic_filter", "harmonic_rebound_long"}
        min_support_count = max(
            0,
            int(params.get("dynamic_min_support_factors", 1) or 0),
        )
        require_confluence = bool(params.get("dynamic_require_confluence_for_entry", False))

        positive = []
        negative = []
        neutral = []
        gates = []
        for factor in factors:
            key = factor.get("key") or ""
            score_added = float(factor.get("score_added", 0) or 0)
            item = {
                "key": key,
                "name": factor.get("name") or key,
                "raw_score": score_added,
                "score": score_added,
                "triggered": bool(factor.get("triggered")),
                "enabled": bool(factor.get("enabled")),
                "detail": factor.get("detail") or "",
            }
            cycle_profile = cycle_profile_by_key.get(key) or {}
            item.update(cycle_profile)
            if key in ("macro_filter",):
                gates.append(item)
            elif score_added > 0:
                positive.append(item)
            elif score_added < 0:
                negative.append(item)
            elif factor.get("enabled") and key in priority:
                neutral.append(item)

        raw_primary = None
        raw_core_positive = [item for item in positive if item["key"] in core_keys]
        if raw_core_positive:
            raw_primary = max(
                raw_core_positive,
                key=lambda item: (item["raw_score"], priority.get(item["key"], 0)),
            )
        elif positive:
            raw_primary = max(
                positive,
                key=lambda item: (item["raw_score"], priority.get(item["key"], 0)),
            )
        raw_positive_total = sum(item["raw_score"] for item in positive)
        raw_negative_total = sum(abs(item["raw_score"]) for item in negative)
        raw_support_keys = {item["key"] for item in positive if raw_primary and item["key"] != raw_primary["key"]}
        raw_environment_support_keys = raw_support_keys & {"trend_filter", "ma_cross"}
        raw_complementary_keys = complement_map.get(raw_primary["key"], set()) if raw_primary else set()
        raw_complementary_support_keys = raw_support_keys & raw_complementary_keys
        raw_complementary_support_count = len(raw_complementary_support_keys)
        if not raw_primary:
            cycle_weight_stage_key = "wait"
        elif require_confluence and raw_complementary_support_count < min_support_count:
            cycle_weight_stage_key = "defense"
        elif raw_negative_total >= raw_positive_total or (raw_negative_total > 0 and raw_complementary_support_count == 0):
            cycle_weight_stage_key = "defense"
        elif (
            raw_primary["key"] in core_keys
            and raw_complementary_support_count > 0
            and (raw_environment_support_keys or raw_complementary_support_count >= 2)
        ):
            cycle_weight_stage_key = "trend"
        elif raw_primary["key"] in core_keys and raw_complementary_support_count > 0:
            cycle_weight_stage_key = "rebound"
        else:
            cycle_weight_stage_key = "defense"

        default_cycle_weights = {
            "wait": {
                "liquidation_map": 1.00,
                "divergence": 1.00,
                "black_candle": 1.00,
                "elliott_wave": 1.00,
                "harmonic_filter": 1.00,
                "harmonic_rebound_long": 1.00,
                "trend_filter": 1.00,
                "ma_cross": 1.00,
                "news_factor": 1.00,
            },
            "defense": {
                "liquidation_map": 0.90,
                "divergence": 1.00,
                "black_candle": 0.80,
                "elliott_wave": 0.90,
                "harmonic_filter": 0.90,
                "harmonic_rebound_long": 1.00,
                "trend_filter": 1.25,
                "ma_cross": 1.10,
                "news_factor": 0.80,
            },
            "rebound": {
                "liquidation_map": 1.10,
                "divergence": 1.15,
                "black_candle": 1.25,
                "elliott_wave": 1.15,
                "harmonic_filter": 1.10,
                "harmonic_rebound_long": 1.20,
                "trend_filter": 0.85,
                "ma_cross": 0.90,
                "news_factor": 1.00,
            },
            "trend": {
                "liquidation_map": 1.00,
                "divergence": 1.10,
                "black_candle": 0.95,
                "elliott_wave": 1.25,
                "harmonic_filter": 1.15,
                "harmonic_rebound_long": 1.15,
                "trend_filter": 1.20,
                "ma_cross": 1.15,
                "news_factor": 0.80,
            },
        }
        user_cycle_weights = params.get("dynamic_cycle_weights") or {}
        stage_cycle_weights = {
            **default_cycle_weights.get(cycle_weight_stage_key, default_cycle_weights["wait"]),
            **(user_cycle_weights.get(cycle_weight_stage_key) or {}),
        }
        weighted_positive = []
        weighted_negative = []
        weighted_neutral = []
        cycle_weight_notes = []
        for item in positive + negative + neutral:
            raw_score = float(item.get("raw_score", 0) or 0)
            weight = max(0.0, min(2.0, float(stage_cycle_weights.get(item["key"], 1.0) or 1.0)))
            weighted_score = raw_score * weight
            item["cycle_weight"] = weight
            item["score"] = weighted_score
            if raw_score and abs(weight - 1.0) > 0.001:
                cycle_weight_notes.append(
                    f"{item.get('name') or item['key']} {raw_score:g}x{weight:g}={weighted_score:g}"
                )
            if weighted_score > 0:
                weighted_positive.append(item)
            elif weighted_score < 0:
                weighted_negative.append(item)
            else:
                weighted_neutral.append(item)
        positive = weighted_positive
        negative = weighted_negative
        neutral = weighted_neutral
        base_score = sum(item["score"] for item in positive) + sum(item["score"] for item in negative)

        primary = None
        core_positive = [item for item in positive if item["key"] in core_keys]
        if core_positive:
            primary = max(
                core_positive,
                key=lambda item: (item["score"], priority.get(item["key"], 0)),
            )
        elif positive:
            primary = max(
                positive,
                key=lambda item: (item["score"], priority.get(item["key"], 0)),
            )

        positive_total = sum(item["score"] for item in positive)
        negative_total = sum(abs(item["score"]) for item in negative)
        penalty_weight = max(
            0.0,
            min(1.0, float(params.get("dynamic_negative_penalty_weight", 0.25) or 0.25)),
        )
        decision_score = positive_total - negative_total * penalty_weight

        primary_override_score = float(params.get("dynamic_primary_override_score", min_score) or min_score)
        primary_override = bool(primary and primary["score"] >= primary_override_score)
        if primary_override:
            decision_score = max(decision_score, float(min_score))

        support_count = len([item for item in positive if primary and item["key"] != primary["key"]])
        support_keys = {item["key"] for item in positive if primary and item["key"] != primary["key"]}
        environment_support_keys = support_keys & {"trend_filter", "ma_cross"}
        complementary_keys = complement_map.get(primary["key"], set()) if primary else set()
        complementary_support_keys = support_keys & complementary_keys
        complementary_support_count = len(complementary_support_keys)
        neutral_keys = {item["key"] for item in neutral}
        negative_keys = {item["key"] for item in negative}
        position_multiplier = 1.0
        boundary_notes = []
        entry_allowed = True
        entry_block_reason = ""
        if require_confluence and primary and primary["key"] in {"trend_filter", "ma_cross", "news_factor"}:
            entry_allowed = False
            entry_block_reason = "??/?????????????/??/?K??"
        elif require_confluence and primary and complementary_support_count < min_support_count:
            entry_allowed = False
            entry_block_reason = (
                f"????: ????{min_support_count}??????"
                f"??{complementary_support_count}?"
            )
        elif require_confluence and not primary:
            entry_allowed = False
            entry_block_reason = "??????????"

        strong_liquidity_trial = (
            direction == "LONG"
            and bool(params.get("dynamic_allow_strong_liquidity_trend_entry", False))
            and primary is not None
            and primary["key"] == "liquidation_map"
            and primary["score"] >= float(params.get("dynamic_strong_liquidity_score", 4.0) or 4.0)
            and {"trend_filter", "ma_cross"}.issubset(support_keys)
            and complementary_support_count < min_support_count
        )
        if strong_liquidity_trial:
            entry_allowed = True
            entry_block_reason = ""
            trial_cap = max(
                0.05,
                min(1.0, float(params.get("dynamic_strong_liquidity_trial_position_cap", 0.35) or 0.35)),
            )
            position_multiplier *= trial_cap
            boundary_notes.append("strong liquidity + trend + MA trial")

        light_long_probe = (
            direction == "LONG"
            and bool(params.get("dynamic_allow_light_long_probe", False))
            and primary is not None
            and primary["key"] == "liquidation_map"
            and not entry_allowed
            and complementary_support_count < min_support_count
            and decision_score >= float(min_score) + float(params.get("dynamic_light_long_probe_min_score_extra", 0.75) or 0.75)
            and negative_total <= float(params.get("dynamic_light_long_probe_max_negative_total", 1.5) or 1.5)
            and (
                not bool(params.get("dynamic_light_long_probe_require_environment", True))
                or bool(environment_support_keys)
            )
        )
        if light_long_probe:
            entry_allowed = True
            entry_block_reason = ""
            probe_cap = max(
                0.05,
                min(0.5, float(params.get("dynamic_light_long_probe_position_cap", 0.20) or 0.20)),
            )
            position_multiplier *= probe_cap
            boundary_notes.append("??????: ??????????")

        if primary:
            if primary["key"] == "liquidation_map":
                has_structure_support = any(item["key"] in ("divergence", "black_candle") for item in positive)
                if not has_structure_support:
                    position_multiplier *= max(
                        0.1,
                        min(1.0, float(params.get("dynamic_liquidity_only_position_multiplier", 0.70) or 0.70)),
                    )
                    boundary_notes.append("??????????")
            if primary["key"] == "divergence":
                has_liquidity_support = any(item["key"] == "liquidation_map" for item in positive)
                if not has_liquidity_support:
                    position_multiplier *= max(
                        0.1,
                        min(1.0, float(params.get("dynamic_structure_without_liquidity_position_multiplier", 1.0) or 1.0)),
                    )
                    boundary_notes.append("??????????")
            if primary["key"] == "black_candle" and "divergence" in neutral_keys:
                position_multiplier *= max(
                    0.1,
                    min(1.0, float(params.get("dynamic_momentum_only_position_multiplier", 0.80) or 0.80)),
                )
                boundary_notes.append("?K????????")

        if primary and complementary_support_count > 0:
            confluence_multiplier = max(
                1.0,
                min(2.0, float(params.get("dynamic_confluence_position_multiplier", 1.15) or 1.15)),
            )
            high_confluence_multiplier = max(
                confluence_multiplier,
                min(2.5, float(params.get("dynamic_high_confluence_position_multiplier", 1.30) or 1.30)),
            )
            if complementary_support_count >= 2:
                position_multiplier *= high_confluence_multiplier
                boundary_notes.append(f"??????{','.join(sorted(complementary_support_keys))}")
            else:
                position_multiplier *= confluence_multiplier
                boundary_notes.append(f"????{','.join(sorted(complementary_support_keys))}")
        elif environment_support_keys:
            boundary_notes.append(f"????{','.join(sorted(environment_support_keys))}?????")

        if negative_total > 0:
            drag_cap = max(
                0.0,
                min(0.8, float(params.get("dynamic_drag_position_cap", 0.30) or 0.30)),
            )
            position_multiplier *= 1 - min(drag_cap, negative_total * 0.10)
            boundary_notes.append(f"????{','.join(sorted(negative_keys))}")

        if direction == "SHORT":
            direction_label = "??"
            rebound_stage_name = "????"
            trend_stage_name = "????"
            stage_path = "???? -> ???? -> ????"
        elif direction == "LONG":
            direction_label = "??"
            rebound_stage_name = "????"
            trend_stage_name = "????"
            stage_path = "???? -> ???? -> ????"
        else:
            direction_label = "??"
            rebound_stage_name = "????"
            trend_stage_name = "????"
            stage_path = "???? -> ???? -> ????"

        if not primary:
            stage_key = "wait"
            stage_name = "??????"
            stage_action = "???????"
            stage_position_mode = "????"
        elif not entry_allowed:
            stage_key = "defense"
            stage_name = "????"
            stage_action = "??????????"
            stage_position_mode = "??/????"
        elif negative_total >= positive_total or (negative_total > 0 and complementary_support_count == 0):
            stage_key = "defense"
            stage_name = "????"
            stage_action = "?????????????"
            stage_position_mode = "??????"
        elif (
            primary["key"] in core_keys
            and complementary_support_count > 0
            and (environment_support_keys or complementary_support_count >= 2)
        ):
            stage_key = "trend"
            stage_name = trend_stage_name
            stage_action = "?????????????"
            stage_position_mode = "??/?????"
        elif primary["key"] in core_keys and complementary_support_count > 0:
            stage_key = "rebound"
            stage_name = rebound_stage_name
            stage_action = "????????????"
            stage_position_mode = "??"
        else:
            stage_key = "defense"
            stage_name = "????"
            stage_action = "????????????"
            stage_position_mode = "??/????"

        if stage_key == "defense":
            stage_cap = max(
                0.1,
                min(1.0, float(params.get("dynamic_stage_defense_position_cap", 0.50) or 0.50)),
            )
            position_multiplier = min(position_multiplier, stage_cap)
            boundary_notes.append(f"??={stage_name}?????{stage_cap:g}")
        elif stage_key == "rebound":
            stage_cap = max(
                0.1,
                min(1.2, float(params.get("dynamic_stage_rebound_position_cap", 0.75) or 0.75)),
            )
            position_multiplier = min(position_multiplier, stage_cap)
            boundary_notes.append(f"??={stage_name}????")
        elif stage_key == "trend":
            stage_boost = max(
                1.0,
                min(1.5, float(params.get("dynamic_stage_trend_position_multiplier", 1.0) or 1.0)),
            )
            position_multiplier *= stage_boost
            boundary_notes.append(f"??={stage_name}")

        cycle_weight_stage_name = {
            "wait": "??????",
            "defense": "????",
            "rebound": rebound_stage_name,
            "trend": trend_stage_name,
        }.get(cycle_weight_stage_key, cycle_weight_stage_key)

        max_position_multiplier = max(
            1.0,
            min(2.5, float(params.get("dynamic_max_position_multiplier", 1.30) or 1.30)),
        )
        position_multiplier = max(0.1, min(max_position_multiplier, position_multiplier))
        trade_type = trade_type_by_key.get(primary["key"], "?????") if primary else "?????"
        primary_text = f"{primary['name']}+{primary['score']:g}" if primary else "?"
        boundary_text = "?".join(boundary_notes) if boundary_notes else "????????"
        support_text = (
            f"????{complementary_support_count}?"
            if complementary_support_count
            else "?????"
        )
        primary_cycle_text = (
            f"{primary.get('cycle', '-')}/{primary.get('phase', '-')}"
            if primary
            else "?"
        )
        complementary_cycle_text = "?".join(
            f"{item.get('name') or item.get('key')}[{item.get('cycle', '-')}/{item.get('phase', '-')}]"
            for item in positive
            if item["key"] in complementary_support_keys
        ) or "?"
        if entry_block_reason:
            boundary_text = f"{boundary_text}?{entry_block_reason}"
        detail = (
            f"{trade_type}: ??={primary_text}?{support_text}?"
            f"???={primary_cycle_text}?????={complementary_cycle_text}?"
            f"????={cycle_weight_stage_name}???={direction_label}???={stage_name}?"
            f"??={boundary_text}????{raw_base_score:g}????{base_score:g}????{decision_score:g}"
        )
        return {
            "enabled": True,
            "direction": direction,
            "base_score": base_score,
            "raw_base_score": raw_base_score,
            "decision_score": decision_score,
            "primary_factor": primary,
            "positive_factors": positive,
            "negative_factors": negative,
            "neutral_factors": neutral,
            "gate_factors": gates,
            "trade_type": trade_type,
            "primary_override": primary_override,
            "light_long_probe": light_long_probe,
            "entry_allowed": entry_allowed,
            "entry_block_reason": entry_block_reason,
            "support_count": support_count,
            "support_keys": sorted(support_keys),
            "complementary_support_count": complementary_support_count,
            "complementary_support_keys": sorted(complementary_support_keys),
            "environment_support_keys": sorted(environment_support_keys),
            "market_stage": {
                "key": stage_key,
                "name": stage_name,
                "action": stage_action,
                "position_mode": stage_position_mode,
                "direction": direction,
                "direction_label": direction_label,
                "path": stage_path,
            },
            "cycle_weight_stage": {
                "key": cycle_weight_stage_key,
                "name": cycle_weight_stage_name,
            },
            "cycle_weight_notes": cycle_weight_notes,
            "factor_cycles": {
                item["key"]: {
                    "name": item.get("name"),
                    "role": item.get("role"),
                    "cycle": item.get("cycle"),
                    "phase": item.get("phase"),
                    "yang": item.get("yang"),
                    "yin": item.get("yin"),
                    "raw_score": item.get("raw_score"),
                    "score": item.get("score"),
                    "cycle_weight": item.get("cycle_weight"),
                    "triggered": item.get("triggered"),
                }
                for item in (positive + negative + neutral)
                if item.get("key")
            },
            "position_multiplier": position_multiplier,
            "boundary_notes": boundary_notes,
            "detail": detail,
        }

    async def _calc_white_dove_margin_plan(
        self,
        strategy: TradingStrategy,
        config: ExchangeConfig,
        current_price: float,
        position_factor: float,
        symbol: str = None,
        is_add_on: bool = False,
    ) -> dict:
        """
        ????????????
        effective_pct = min(position_percent, max_symbol_margin_percent)
        batch_margin = available_usdt * effective_pct * position_factor
        """
        params = strategy.params or {}
        leverage = min(
            int(params.get("leverage", 20) or 20),
            int(params.get("max_auto_leverage", self.MAX_AUTO_LEVERAGE) or self.MAX_AUTO_LEVERAGE),
            self.MAX_AUTO_LEVERAGE,
        )
        instrument_max_leverage = None
        if strategy.market_type != "SPOT":
            instrument_max_leverage = await self._get_instrument_max_leverage(
                symbol or strategy.symbol,
                strategy.market_type,
            )
            leverage = min(leverage, instrument_max_leverage)
        position_percent = min(
            float(params.get("position_percent", 0.35) or 0.35),
            float(params.get("max_auto_position_percent", self._get_max_auto_position_percent(strategy)) or self._get_max_auto_position_percent(strategy)),
            self._get_max_auto_position_percent(strategy),
        )
        max_symbol_margin_percent = min(
            float(params.get("max_symbol_margin_percent", 0.30) or 0.30),
            float(params.get("max_auto_symbol_margin_percent", self._get_max_auto_symbol_margin_percent(strategy)) or self._get_max_auto_symbol_margin_percent(strategy)),
            self._get_max_auto_symbol_margin_percent(strategy),
        )
        effective_pct = max(0.0, min(1.0, min(position_percent, max_symbol_margin_percent)))
        factor = max(0.01, float(position_factor or 0.01))
        global_entry_allocation = None
        if not is_add_on:
            global_entry_allocation = await self._get_global_entry_allocation_context(config, strategy)
            if global_entry_allocation is not None:
                allocation_pct = global_entry_allocation.get("allocation_pct")
                if allocation_pct is None:
                    return {
                        "available_usdt": 0.0,
                        "effective_pct": effective_pct,
                        "position_factor": factor,
                        "target_margin": 0.0,
                        "nominal_usdt": 0.0,
                        "quantity": 0.0,
                        "rejected_reason": "global_entry_allocation_capacity_reached",
                        "global_entry_allocation": global_entry_allocation,
                    }
                # A staged allocation is the final entry size. It intentionally ignores
                # legacy per-strategy entry factors and uses the remaining balance.
                effective_pct = float(allocation_pct)
                factor = 1.0
        # Keep an entry or add-on within the configured share of currently available margin,
        # even when score/trend multipliers would otherwise enlarge the requested batch.
        per_order_margin_cap = params.get("per_order_margin_cap_percent")
        if per_order_margin_cap is not None and effective_pct > 0:
            try:
                per_order_margin_cap = max(0.01, min(1.0, float(per_order_margin_cap)))
                factor = min(factor, per_order_margin_cap / effective_pct)
            except (TypeError, ValueError):
                per_order_margin_cap = None
        default_buffer = 0.03 if (strategy.strategy_type or "") == "micro_scalp" else 0.0
        sizing_buffer = max(
            0.0,
            min(0.20, float(params.get("sizing_safety_buffer_pct", default_buffer) or 0.0)),
        )

        margin_ccy = await self._get_instrument_margin_ccy(symbol or strategy.symbol, strategy.market_type)
        available_usdt = await self._get_available_balance(config, margin_ccy)
        if available_usdt <= 0 or current_price <= 0:
            return {
                "available_usdt": max(available_usdt, 0.0),
                "margin_ccy": margin_ccy,
                "effective_pct": effective_pct,
                "position_factor": factor,
                "per_order_margin_cap_percent": per_order_margin_cap,
                "sizing_safety_buffer_pct": sizing_buffer,
                "target_margin": 0.0,
                "nominal_usdt": 0.0,
                "quantity": 0.0,
                "global_entry_allocation": global_entry_allocation,
                "contract_value": await self._get_contract_value_live(symbol or strategy.symbol, strategy.market_type),
            }

        total_target_margin = available_usdt * effective_pct
        batch_margin = total_target_margin * factor
        safe_batch_margin = batch_margin * (1 - sizing_buffer)

        # This cap is applied AFTER staged allocation, never overwritten by it.
        # It limits new entries only; existing positions and exit orders are untouched.
        equity_cap_pct = params.get("new_entry_equity_cap_percent")
        if not is_add_on and equity_cap_pct is not None:
            equity_cap_pct = float(equity_cap_pct)
            if not math.isfinite(equity_cap_pct) or not 0 < equity_cap_pct <= 1:
                raise ValueError("invalid_new_entry_equity_cap_percent")
            equity = await self._get_currency_equity(config, margin_ccy)
            safe_batch_margin = min(safe_batch_margin, equity * equity_cap_pct)

        if strategy.market_type == "SPOT":
            qty = safe_batch_margin / current_price
            return {
                "available_usdt": round(available_usdt, 4),
                "margin_ccy": margin_ccy,
                "effective_pct": effective_pct,
                "position_factor": factor,
                "sizing_safety_buffer_pct": sizing_buffer,
                "max_margin_limit": round(batch_margin, 4),
                "target_margin": round(safe_batch_margin, 4),
                "nominal_usdt": round(safe_batch_margin, 4),
                "quantity": max(round(qty, 6), 0.0001),
                "global_entry_allocation": global_entry_allocation,
                "contract_value": 1.0,
                "leverage": leverage,
            }

        trade_symbol = symbol or strategy.symbol
        ct_val = await self._get_contract_value_live(trade_symbol, strategy.market_type)
        nominal = safe_batch_margin * leverage
        raw_qty = nominal / (current_price * ct_val)
        final_qty = await self._round_contract_quantity_down(trade_symbol, raw_qty, strategy.market_type)
        estimated_margin = (final_qty * current_price * ct_val) / max(leverage, 1) if final_qty > 0 else 0.0
        # A rounded lot can be technically valid while economically meaningless.  When a
        # strategy opts into a minimum margin, reject dust orders instead of submitting them.
        min_order_margin = max(0.0, float(params.get("min_order_margin_usdt", 0.0) or 0.0))
        below_minimum_margin = final_qty <= 0 or estimated_margin + 1e-9 < min_order_margin
        return {
            "available_usdt": round(available_usdt, 4),
            "margin_ccy": margin_ccy,
            "effective_pct": effective_pct,
            "position_factor": factor,
            "per_order_margin_cap_percent": per_order_margin_cap,
            "sizing_safety_buffer_pct": sizing_buffer,
            "max_margin_limit": round(batch_margin, 4),
            "target_margin": round(estimated_margin, 4),
            "nominal_usdt": round(final_qty * current_price * ct_val, 4),
            "minimum_margin_usdt": round(min_order_margin, 4),
            "rejected_reason": "below_minimum_margin" if below_minimum_margin else "",
            "quantity": 0.0 if below_minimum_margin else final_qty,
            "contract_value": ct_val,
            "leverage": leverage,
            "instrument_max_leverage": instrument_max_leverage,
            "global_entry_allocation": global_entry_allocation,
        }

    async def _get_instrument_max_leverage(
        self,
        symbol: str,
        market_type: str = "SWAP",
    ) -> int:
        """Return the live exchange leverage ceiling so broad pools do not reject orders."""
        trade_symbol = self._normalize_strategy_symbol_for_market(symbol, market_type)
        inst_type = "FUTURES" if (market_type or "").upper() == "FUTURES" else "SWAP"
        try:
            instruments = await okx_manager.get_instruments(inst_type)
            instrument = next(
                (item for item in instruments if item.get("instId", "").upper() == trade_symbol),
                None,
            )
            max_leverage = float((instrument or {}).get("lever") or 0)
            if max_leverage > 0:
                return max(1, int(max_leverage))
        except Exception as exc:
            logger.warning("instrument leverage lookup failed for %s: %s", trade_symbol, exc)
        return self.MAX_AUTO_LEVERAGE

    async def _round_contract_quantity_down(self, symbol: str, quantity: float, market_type: str = "SWAP") -> float:
        """Round contract quantity down to OKX lot size so later normalization cannot exceed risk caps."""
        trade_symbol = self._normalize_strategy_symbol_for_market(symbol, market_type)
        inst_type = "FUTURES" if (market_type or "").upper() == "FUTURES" else "SWAP"
        try:
            instruments = await okx_manager.get_instruments(inst_type)
            instrument = next(
                (item for item in instruments if item.get("instId", "").upper() == trade_symbol),
                None,
            )
            if instrument:
                lot_size = Decimal(str(instrument.get("lotSz") or "0.01"))
                min_size = Decimal(str(instrument.get("minSz") or lot_size))
                requested = Decimal(str(quantity))
                if lot_size > 0:
                    rounded = (requested / lot_size).to_integral_value(rounding=ROUND_FLOOR) * lot_size
                    if rounded < min_size:
                        return 0.0
                    return float(rounded)
        except Exception as e:
            print(f"?????????? [{trade_symbol}]: {e}")

        coin = (trade_symbol or "").split("-")[0].upper()
        if coin in ("BTC", "ETH"):
            return max(round(quantity, 2), 0.01)
        return max(round(quantity, 1), 0.1)
    
    async def _evaluate_factors(self, current_price, symbol, factors_config):
        """????????????????????????????"""
        score = 0.0
        factor_results = []
        enabled_flags = []
        macro_blocked = False
        macro_reason = ""
        
        klines_1h = None
        klines_5m = None
        
        def ensure_klines_1h(min_bars=50):
            nonlocal klines_1h
            if klines_1h is None:
                klines_1h = okx_manager.get_candles(symbol, "1H", min_bars)
            return klines_1h
        
        # 1. ????
        macro_cfg = factors_config.get("macro_filter", {})
        if macro_cfg.get("enabled", False) and self._macro_filter:
            can_open, reason = self._macro_filter.check_can_open_position(
                current_positions=macro_cfg.get("current_positions", 0)
            )
            if not can_open:
                macro_blocked = True
                macro_reason = reason
            w = macro_cfg.get("weight", 0)
            if can_open and w > 0:
                score += w
            factor_results.append({
                "name": "????",
                "key": "macro_filter",
                "enabled": True,
                "triggered": can_open,
                "score_added": w if can_open else 0,
                "detail": reason,
                "raw": {"can_open": can_open, "risk_level": self._macro_filter.risk_level},
            })
        else:
            factor_results.append({
                "name": "????",
                "key": "macro_filter",
                "enabled": False,
                "triggered": None,
                "score_added": 0,
                "detail": "???",
                "raw": {},
            })
        
        if macro_blocked:
            return {
                "score": score,
                "factors": factor_results,
                "macro_blocked": True,
                "macro_reason": macro_reason,
            }
        
        # 2. ????
        div_cfg = factors_config.get("divergence", {})
        if div_cfg.get("enabled", False) and self._div_system:
            bar = div_cfg.get("timeframe", "5m")
            limit = 100 if bar in ("5m", "15m") else 50
            klines = await okx_manager.get_candles(symbol, bar, limit)
            if len(klines) >= 30:
                candles = [{"open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
                            "close": float(k[4]), "volume": float(k[5]), "time": k[0]} for k in klines]
                has_div, info, div_msg = self._div_system['get_divergence_summary'](candles, bar)
                direction = info.get('direction') if info else None
                triggered = bool(has_div and direction)
                w = div_cfg.get("weight", 2)
                score_added = 0
                if direction == 'bullish':
                    score += w
                    score_added = w
                elif direction == 'bearish':
                    score -= w
                    score_added = -w
                if triggered:
                    pass
                factor_results.append({
                    "name": "????",
                    "key": "divergence",
                    "enabled": True,
                    "triggered": triggered,
                    "score_added": score_added,
                    "detail": div_msg,
                    "raw": {
                        "confidence": info.get('confidence') if info else None,
                        "direction": direction,
                        "stype": info.get('stype') if info else None,
                    },
                })
                enabled_flags.append(triggered)
            else:
                factor_results.append({
                    "name": "????", "key": "divergence", "enabled": True,
                    "triggered": False, "score_added": 0, "detail": "????", "raw": {},
                })
                enabled_flags.append(False)
        
        # 3. ?????
        liq_cfg = factors_config.get("liquidation_map", {})
        if liq_cfg.get("enabled", False) and self._liq_signal:
            coin = symbol.split("-")[0]
            liq_result, liq_reason = self._liq_signal.check_signal(coin, current_price)
            triggered = liq_result and liq_result.get('signal') in ('LONG', 'LONG_BIAS')
            w = liq_cfg.get("weight", 2)
            added = 0
            if triggered:
                strength = liq_result.get('strength', 1)
                added = w * strength / 3
                score += added
            factor_results.append({
                "name": "?????",
                "key": "liquidation_map",
                "enabled": True,
                "triggered": triggered,
                "score_added": round(added, 2),
                "detail": liq_result.get('reason', liq_reason) if liq_result else liq_reason,
                "raw": {"strength": liq_result.get('strength') if liq_result else 0},
            })
            enabled_flags.append(triggered)
        
        # 4. ????
        ma_cfg = factors_config.get("ma_cross", {})
        if ma_cfg.get("enabled", False):
            klines_1h = await okx_manager.get_candles(symbol, "1H", 50)
            if len(klines_1h) >= 20:
                closes = [float(k[4]) for k in klines_1h]
                ma5 = sum(closes[-5:]) / 5
                ma10 = sum(closes[-10:]) / 10
                triggered = ma5 > ma10
                w = ma_cfg.get("weight", 1)
                if triggered:
                    score += w
                factor_results.append({
                    "name": "????",
                    "key": "ma_cross",
                    "enabled": True,
                    "triggered": triggered,
                    "score_added": w if triggered else 0,
                    "detail": f"MA5({ma5:.2f}) {'>' if triggered else '<='} MA10({ma10:.2f})",
                    "raw": {"ma5": ma5, "ma10": ma10},
                })
                enabled_flags.append(triggered)
            else:
                factor_results.append({
                    "name": "????", "key": "ma_cross", "enabled": True,
                    "triggered": False, "score_added": 0, "detail": "????", "raw": {},
                })
                enabled_flags.append(False)
        
        # 5. RSI
        rsi_cfg = factors_config.get("rsi", {})
        if rsi_cfg.get("enabled", False):
            if klines_1h is None:
                klines_1h = await okx_manager.get_candles(symbol, "1H", 15)
            if len(klines_1h) >= 14:
                closes = [float(k[4]) for k in klines_1h]
                rsi = self._calculate_rsi(closes)
                oversold = rsi_cfg.get("oversold", 30)
                triggered = rsi < oversold
                w = rsi_cfg.get("weight", 1)
                if triggered:
                    score += w
                factor_results.append({
                    "name": "RSI??",
                    "key": "rsi",
                    "enabled": True,
                    "triggered": triggered,
                    "score_added": w if triggered else 0,
                    "detail": f"RSI {rsi:.2f} {'<' if triggered else '>='} {oversold}",
                    "raw": {"rsi": rsi, "oversold": oversold},
                })
                enabled_flags.append(triggered)
            else:
                factor_results.append({
                    "name": "RSI??", "key": "rsi", "enabled": True,
                    "triggered": False, "score_added": 0, "detail": "????", "raw": {},
                })
                enabled_flags.append(False)
        
        # 6. ????
        reb_cfg = factors_config.get("rebound", {})
        if reb_cfg.get("enabled", False):
            if klines_1h is None:
                klines_1h = await okx_manager.get_candles(symbol, "1H", 24)
            if len(klines_1h) >= 24:
                lows = [float(k[3]) for k in klines_1h]
                low_24h = min(lows)
                rebound_pct = (current_price - low_24h) / low_24h
                threshold = reb_cfg.get("threshold", 0.03)
                triggered = rebound_pct >= threshold
                w = reb_cfg.get("weight", 1)
                if triggered:
                    score += w
                factor_results.append({
                    "name": "24h??",
                    "key": "rebound",
                    "enabled": True,
                    "triggered": triggered,
                    "score_added": w if triggered else 0,
                    "detail": f"?{low_24h:.2f}??{rebound_pct*100:.1f}% {'>=' if triggered else '<'} {threshold*100:.1f}%",
                    "raw": {"low_24h": low_24h, "rebound_pct": round(rebound_pct, 4)},
                })
                enabled_flags.append(triggered)
            else:
                factor_results.append({
                    "name": "24h??", "key": "rebound", "enabled": True,
                    "triggered": False, "score_added": 0, "detail": "????", "raw": {},
                })
                enabled_flags.append(False)
        
        # 7. ???
        boll_cfg = factors_config.get("bollinger", {})
        if boll_cfg.get("enabled", False):
            if klines_1h is None:
                klines_1h = await okx_manager.get_candles(symbol, "1H", 25)
            if len(klines_1h) >= 20:
                closes = [float(k[4]) for k in klines_1h]
                ma20 = sum(closes[-20:]) / 20
                std = (sum((x - ma20) ** 2 for x in closes[-20:]) / 20) ** 0.5
                lower = ma20 - 2 * std
                triggered = current_price <= lower
                w = boll_cfg.get("weight", 1)
                if triggered:
                    score += w
                factor_results.append({
                    "name": "?????",
                    "key": "bollinger",
                    "enabled": True,
                    "triggered": triggered,
                    "score_added": w if triggered else 0,
                    "detail": f"??{current_price:.2f} {'<=' if triggered else '>'} ??{lower:.2f}",
                    "raw": {"lower": lower, "upper": ma20 + 2 * std, "ma20": ma20},
                })
                enabled_flags.append(triggered)
            else:
                factor_results.append({
                    "name": "?????", "key": "bollinger", "enabled": True,
                    "triggered": False, "score_added": 0, "detail": "????", "raw": {},
                })
                enabled_flags.append(False)
        
        # 8. MACD??
        macd_cfg = factors_config.get("macd_cross", {})
        if macd_cfg.get("enabled", False):
            if klines_1h is None:
                klines_1h = await okx_manager.get_candles(symbol, "1H", 40)
            if len(klines_1h) >= 35:
                closes = [float(k[4]) for k in klines_1h]
                macd_line, signal_line, _ = calc_macd(closes)
                if len(macd_line) >= 2 and len(signal_line) >= 2:
                    prev_macd = macd_line[-2]
                    prev_signal = signal_line[-2]
                    curr_macd = macd_line[-1]
                    curr_signal = signal_line[-1]
                    triggered = prev_macd <= prev_signal and curr_macd > curr_signal
                    w = macd_cfg.get("weight", 1)
                    if triggered:
                        score += w
                    factor_results.append({
                        "name": "MACD??",
                        "key": "macd_cross",
                        "enabled": True,
                        "triggered": triggered,
                        "score_added": w if triggered else 0,
                        "detail": f"MACD({curr_macd:.4f}) {'??' if triggered else '???'} Signal({curr_signal:.4f})",
                        "raw": {"macd": curr_macd, "signal": curr_signal},
                    })
                    enabled_flags.append(triggered)
                else:
                    factor_results.append({
                        "name": "MACD??", "key": "macd_cross", "enabled": True,
                        "triggered": False, "score_added": 0, "detail": "??????", "raw": {},
                    })
                    enabled_flags.append(False)
            else:
                factor_results.append({
                    "name": "MACD??", "key": "macd_cross", "enabled": True,
                    "triggered": False, "score_added": 0, "detail": "????", "raw": {},
                })
                enabled_flags.append(False)
        
        # 9. ?????
        vol_cfg = factors_config.get("volume_spike", {})
        if vol_cfg.get("enabled", False):
            if klines_1h is None:
                klines_1h = await okx_manager.get_candles(symbol, "1H", 25)
            if len(klines_1h) >= 20:
                volumes = [float(k[5]) for k in klines_1h]
                avg_vol = sum(volumes[-20:]) / 20
                current_vol = volumes[-1]
                multiplier = vol_cfg.get("multiplier", 1.5)
                triggered = current_vol >= avg_vol * multiplier
                w = vol_cfg.get("weight", 1)
                if triggered:
                    score += w
                factor_results.append({
                    "name": "?????",
                    "key": "volume_spike",
                    "enabled": True,
                    "triggered": triggered,
                    "score_added": w if triggered else 0,
                    "detail": f"???{current_vol:.2f} {'>=' if triggered else '<'} ??{avg_vol:.2f}x{multiplier}",
                    "raw": {"current_vol": current_vol, "avg_vol": avg_vol, "multiplier": multiplier},
                })
                enabled_flags.append(triggered)
            else:
                factor_results.append({
                    "name": "?????", "key": "volume_spike", "enabled": True,
                    "triggered": False, "score_added": 0, "detail": "????", "raw": {},
                })
                enabled_flags.append(False)
        
        # 10. ????
        news_cfg = factors_config.get("news_factor", {})
        if news_cfg.get("enabled", False):
            coin = symbol.split("-")[0]
            recent_news = await self._fetch_recent_news_factors(coin)
            if recent_news:
                net_strength = sum(n.strength for n in recent_news)
                w = news_cfg.get("weight", 1.5)
                score_added = 0
                if net_strength > 0:
                    score_added = min(round(w * net_strength / 3, 2), round(w * 2, 2))
                    score += score_added
                elif net_strength < 0:
                    score_added = max(round(w * net_strength / 3, 2), round(-w * 2, 2))
                    score += score_added
                triggered = abs(net_strength) >= 2
                factor_results.append({
                    "name": "????",
                    "key": "news_factor",
                    "enabled": True,
                    "triggered": triggered,
                    "score_added": score_added,
                    "detail": f"?24h?????{net_strength} ({len(recent_news)}?)",
                    "raw": {"net_strength": net_strength, "count": len(recent_news), "items": [
                        {"title": n.title[:60], "strength": n.strength, "dim": n.dimension} for n in recent_news[:3]
                    ]},
                })
                enabled_flags.append(triggered)
            else:
                factor_results.append({
                    "name": "????", "key": "news_factor", "enabled": True,
                    "triggered": False, "score_added": 0, "detail": "?24h???????", "raw": {},
                })
                enabled_flags.append(False)
        
        return {
            "score": round(score, 2),
            "factors": factor_results,
            "macro_blocked": False,
            "macro_reason": "",
            "enabled_flags": enabled_flags,
        }
    
    async def _multi_factor_strategy(self, strategy, current_price, symbol):
        """??????????????????????"""
        params = strategy.params or {}
        min_score = params.get("min_score", 5)
        logic_mode = params.get("logic_mode", "weighted_sum")
        factors_config = params.get("factors", {})
        
        eval_result = await self._evaluate_factors(current_price, symbol, factors_config)
        
        if eval_result["macro_blocked"]:
            return "HOLD", f"???: ???? ({eval_result['macro_reason']})"
        
        score = eval_result["score"]
        enabled_flags = eval_result["enabled_flags"]
        details = " | ".join([f["detail"] for f in eval_result["factors"] if f["enabled"]])
        
        if logic_mode == "all_required":
            if enabled_flags and all(enabled_flags) and strategy.last_signal != "BUY":
                return "BUY", f"???(????) ??{score}: {details}"
            return "HOLD", f"???(????) ??{score}: {details}"
        
        elif logic_mode == "any_one":
            if any(enabled_flags) and strategy.last_signal != "BUY":
                return "BUY", f"???(????) ??{score}: {details}"
            return "HOLD", f"???(????) ??{score}: {details}"
        
        else:  # weighted_sum
            if score >= min_score and strategy.last_signal != "BUY":
                return "BUY", f"???(??{score}/{min_score}): {details}"
            return "HOLD", f"???(??{score}/{min_score}): {details}"
    
    async def scan_factors(self, strategy, current_price, symbol):
        """??????????????????"""
        params = strategy.params or {}
        factors_config = params.get("factors", {})
        eval_result = await self._evaluate_factors(current_price, symbol, factors_config)
        
        # ??????? weight ??
        for f in eval_result["factors"]:
            cfg = factors_config.get(f["key"], {})
            f["weight"] = cfg.get("weight", 0)
        
        return {
            "symbol": symbol,
            "current_price": current_price,
            "strategy_type": strategy.strategy_type,
            "score": eval_result["score"],
            "min_score": params.get("min_score", 5),
            "logic_mode": params.get("logic_mode", "weighted_sum"),
            "macro_blocked": eval_result["macro_blocked"],
            "macro_reason": eval_result["macro_reason"],
            "factors": eval_result["factors"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    
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
    
    async def _execute_trade(self, db, strategy, config, signal, price, symbol=None) -> bool:
        params = strategy.params or {}
        strategy_name = strategy.name
        strategy_marker = self._format_strategy_marker(strategy)
        strategy_user_id = strategy.user_id
        strategy_symbol = strategy.symbol
        strategy_market_type = strategy.market_type
        leverage = min(
            int(params.get("leverage", 1) or 1),
            int(params.get("max_auto_leverage", self.MAX_AUTO_LEVERAGE) or self.MAX_AUTO_LEVERAGE),
            self.MAX_AUTO_LEVERAGE,
        )
        pending_context = getattr(self, "_pending_trade_context", None)
        if isinstance(pending_context, dict):
            try:
                planned_leverage = int(pending_context.get("leverage") or leverage)
                leverage = min(leverage, max(1, planned_leverage))
            except (TypeError, ValueError):
                pass
        quantity = getattr(self, "_pending_trade_quantity", None)
        if quantity is None or quantity <= 0:
            quantity = params.get("quantity", 0.001)
        trade_symbol = symbol or strategy_symbol
        trade_direction = "LONG" if signal == "BUY" else "SHORT"
        
        try:
            async with self._order_guard_lock:
                normalized_trade_symbol = self._normalize_strategy_symbol(trade_symbol)
                if strategy.strategy_type == "micro_scalp":
                    if normalized_trade_symbol in self._get_excluded_strategy_symbols(strategy):
                        raise ValueError(f"????????: ?????? {normalized_trade_symbol}")

                if strategy.strategy_type in {"micro_scalp", "white_dove"} or "max_open_symbols" in params:
                    max_open_symbols = int(params.get("max_open_symbols", 1) or 1)
                    strategy_position_keys = await self._get_strategy_live_position_keys(config, strategy)
                    same_symbol_positions = {
                        direction
                        for pos_symbol, direction in strategy_position_keys
                        if pos_symbol == normalized_trade_symbol
                    }
                    allow_add_existing = params.get("allow_add_existing_position", False)
                    if isinstance(allow_add_existing, str):
                        allow_add_existing = allow_add_existing.strip().lower() in {"1", "true", "yes", "on"}
                    allow_hedge_same_symbol = params.get("allow_hedge_same_symbol", False)
                    if isinstance(allow_hedge_same_symbol, str):
                        allow_hedge_same_symbol = allow_hedge_same_symbol.strip().lower() in {"1", "true", "yes", "on"}
                    if same_symbol_positions:
                        if not bool(allow_add_existing):
                            raise ValueError(f"????????: {normalized_trade_symbol} ???????????/??")
                        if trade_direction not in same_symbol_positions and not bool(allow_hedge_same_symbol):
                            raise ValueError(f"????????: {normalized_trade_symbol} ?????????????")
                        try:
                            max_entries_per_symbol = int(params.get("max_entries_per_symbol", 0) or 0)
                        except (TypeError, ValueError):
                            max_entries_per_symbol = 0
                        if max_entries_per_symbol > 0:
                            async with AsyncSessionLocal() as position_db:
                                open_entries = await position_db.execute(
                                    select(TradeRecord.id).where(
                                        TradeRecord.user_id == strategy_user_id,
                                        TradeRecord.exchange_config_id == config.id,
                                        TradeRecord.symbol == normalized_trade_symbol,
                                        TradeRecord.direction == trade_direction,
                                        TradeRecord.strategy_tag == strategy_marker,
                                        TradeRecord.is_closed == False,
                                    )
                                )
                                entry_count = len(open_entries.scalars().all())
                            if entry_count >= max_entries_per_symbol:
                                raise ValueError(
                                    f"??????: {normalized_trade_symbol} "
                                    f"?????????({entry_count}/{max_entries_per_symbol})"
                                )
                    else:
                        strategy_direction_symbols = {
                            pos_symbol
                            for pos_symbol, direction in strategy_position_keys
                            if direction == trade_direction
                        }
                        if len(strategy_direction_symbols) >= max_open_symbols:
                            raise ValueError(
                                f"????????: {strategy_marker} {trade_direction} ???????????"
                                f"({len(strategy_direction_symbols)}/{max_open_symbols})"
                            )

                await self._assert_no_cross_strategy_symbol_conflict(config, strategy, trade_symbol)
                await self._assert_global_open_symbol_limit(config, strategy, trade_symbol)

                ct_val = 1.0  # SPOT: position_size already denominated in coin units, no contract multiplier
                if strategy_market_type != "SPOT":
                    ct_val = await self._get_contract_value_live(trade_symbol, strategy_market_type)
                    margin_ccy = await self._get_instrument_margin_ccy(trade_symbol, strategy_market_type)
                    available_usdt = await self._get_available_balance(config, margin_ccy)
                    if available_usdt <= 0:
                        raise ValueError(f"????????: ?????{margin_ccy}??????")
                    estimated_margin = (float(quantity) * float(price) * ct_val) / max(leverage, 1)
                    max_margin = available_usdt * self._get_max_auto_symbol_margin_percent(strategy)
                    if available_usdt > 0 and estimated_margin > max_margin * 1.001:
                        raise ValueError(
                            f"????????: ?????{estimated_margin:.2f}{margin_ccy} "
                            f"> ????{max_margin:.2f}{margin_ccy}"
                        )

                allow_min_size_bump = params.get("allow_min_size_bump", False)
                if isinstance(allow_min_size_bump, str):
                    allow_min_size_bump = allow_min_size_bump.strip().lower() in {"1", "true", "yes", "on"}
                request = OpenPositionRequest(
                    symbol=trade_symbol,
                    direction=trade_direction,
                    quantity=quantity,
                    market_type=strategy_market_type,
                    margin_mode=params.get("margin_mode", "cross") or "cross",
                    leverage=leverage,
                    remark=f"??????: {strategy_marker} ({strategy.strategy_type})",
                    allow_min_size_bump=bool(allow_min_size_bump),
                )
                db_order = await self._execution_gateway.open_position(
                    db,
                    strategy_user_id,
                    config,
                    request,
                )
                executed_price = getattr(db_order, "executed_price", None) or price
                executed_qty = getattr(db_order, "executed_qty", None) or getattr(db_order, "quantity", quantity)
                external_order_id = getattr(db_order, "binance_order_id", None)
                db.add(TradeRecord(
                    user_id=strategy_user_id,
                    exchange_config_id=config.id,
                    symbol=trade_symbol,
                    direction=trade_direction,
                    entry_price=executed_price,
                    position_size=executed_qty,
                    ct_val=ct_val,
                    leverage=leverage,
                    strategy_tag=strategy_marker,
                    notes=f"??????: {strategy_marker} | ??: {strategy.strategy_type}",
                    is_closed=False,
                    external_id=external_order_id,
                ))
                # 台账必须紧贴下单落库，先于飞书/兜底止损这些外部调用（Phase 2.5c / E12）。
                # 此前 TradeRecord 只 db.add、由调用方在数秒后才 commit，中间要经历飞书
                # HTTP(8s) 与兜底止损的多次 OKX 调用——每个 await 都是取消点，而
                # wait_for(180s) 超时与关停注入的 CancelledError 是 BaseException，
                # **不走下面的 except Exception、也不 rollback**，session 一关
                # TradeRecord 就没了：交易所有仓、本地无账（若取消发生在挂兜底止损之前，
                # 该仓位还永不被离场检查管理）。提前 commit 后，place_order 返回与落库
                # 之间不再有任何 await，也就没有取消点。
                # 附带修好另一条：此前落库前的任何异常都会被下面的 rollback 连 TradeRecord
                # 一起丢掉，而订单早已被 place_order 内部 commit——同样是孤儿仓。
                try:
                    await db.commit()
                except Exception as commit_error:
                    # 走到这里订单已在交易所成交，落库失败即孤儿仓，必须有人知道
                    await self._alert(
                        "下单已成交但台账落库失败",
                        f"{trade_symbol} {trade_direction} qty={executed_qty} "
                        f"order_id={external_order_id}: {commit_error}",
                        key=f"ledger-commit-failed-{trade_symbol}",
                    )
                    raise
                runner_add = (
                    pending_context.get("trend_runner_add")
                    if isinstance(pending_context, dict)
                    else None
                )
                if isinstance(runner_add, dict):
                    await self._record_trend_runner_add(
                        runner_add,
                        strategy,
                        config,
                        float(executed_qty),
                        float(executed_price),
                    )
                try:
                    await feishu_openclaw_notifier.notify_open_trade(
                        strategy_name=strategy_name,
                        strategy_marker=strategy_marker,
                        symbol=trade_symbol,
                        direction=trade_direction,
                        signal=signal,
                        quantity=executed_qty,
                        price=executed_price,
                        leverage=leverage,
                        order_id=external_order_id,
                    )
                except Exception as notify_error:
                    print(f"Feishu open trade notify unexpected error: {notify_error}")
                # 交易所侧兜底止损必须覆盖交易所真实总仓位。
                # 同币加仓时仅以本次 executed_qty 重挂会留下旧仓裸露。
                await self._execution_gateway.refresh_native_stop(
                    config,
                    NativeStopRequest(trade_symbol, trade_direction, params),
                )
                strategy.total_trades = int(strategy.total_trades or 0) + 1
                self._last_trade_quantity = getattr(db_order, "quantity", quantity)
                self._last_trade_ok_at = time.time()
                return True
        except Exception as e:
            await db.rollback()
            try:
                await db.refresh(config)
            except Exception:
                pass
            error_msg = str(e)
            self._last_trade_error = error_msg
            print(f"??????: {strategy_name} {trade_symbol} {signal} {quantity} - {error_msg}")
            db.add(Message(
                title=f"??????: {strategy_name}",
                content=(
                    f"??: {trade_symbol}\n"
                    f"??: {signal}\n"
                    f"??: {quantity}\n"
                    f"??: {price}\n"
                    f"??: {error_msg}"
                ),
                msg_type="system",
                status=MessageStatus.UNREAD.value,
                receiver_id=strategy_user_id,
            ))
            await db.commit()
            self._last_trade_quantity = None
            return False
        finally:
            self._pending_trade_quantity = None
            self._pending_trade_context = None


strategy_engine = StrategyEngine()
