"""
交易绩效分析服务
基于 real_trader_v2.py 和 trading_analyzer.py 的核心逻辑
"""
import os
import asyncio
import json
import logging
import time
from typing import List, Dict, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc, case
from app.models.trade_record import TradeRecord
from app.models.exchange_config import ExchangeConfig
from app.models.trading_strategy import TradingStrategy
from app.services.okx_client import okx_manager, decrypt_text
from app.services.monitor_service import monitor_service
from app.services.contract_specs import get_static_ct_val
from app.services.close_receipt import match_complete_close


# 对账参数（Phase 2.5c/#10 + 2.5e/#11，2026-07-17 经用户确认）
RECONCILE_SIZE_EPS = 1e-6           # 张数比较容差（浮点噪声级）
RECONCILE_MISSING_GRACE_SECONDS = 120  # "交易所已无持仓"需两次观察间隔≥此值才关账
# 孤儿仓兜底止损的保底百分比（价格口径，docs/09 · P3）：找不到可继承的策略参数时用它。
# 不能沿用 _native_stop_trigger_price 的 10% 默认——20x 下 10% 价格 = 200% 保证金，
# 实测 SOL 孤儿仓正是被这个"保护"了 5.4 天直到人工平仓。
ORPHAN_BACKSTOP_FALLBACK_PCT = 0.03


def _reconcile_native_stop_enabled() -> bool:
    """对账补录的孤儿仓是否自动挂兜底止损。环境变量 RECONCILE_NATIVE_STOP=0 可关。"""
    return os.environ.get("RECONCILE_NATIVE_STOP", "1").strip().lower() not in {"0", "false", "no", "off"}


class AnalyzerService:
    """交易分析器"""

    def __init__(self) -> None:
        # (symbol, direction) → 首次观察到"台账有、交易所无"的时间戳。
        # 关账必须连续两次观察（间隔 ≥ GRACE）才执行：_do_reduce 不持下单锁（E8），
        # 交易所已成交、台账核销还没写完的瞬间，单次观察会把 lot 错标"未对账关闭"，
        # 随后 FIFO 核销找不到 open lot，已实现 PnL 直接丢失。双次观察 + 引擎重启
        # 清零（内存态）意味着启动首轮永不关闭任何记录——刻意保守。
        self._missing_since: dict = {}

    async def _complete_close_receipt(self, db, config, record):
        if (not str(record.external_id or '').isdigit()
                or not str(record.strategy_tag or '').startswith('#')):
            return None
        credentials = dict(api_key=decrypt_text(config.api_key),
                           api_secret=decrypt_text(config.api_secret),
                           passphrase=decrypt_text(config.api_passphrase or ''),
                           simulated=bool(config.is_testnet))
        order = await okx_manager.get_order_detail(
            **credentials, inst_id=record.symbol, ord_id=record.external_id)
        history = await okx_manager.get_positions_history(**credentials, inst_id=record.symbol)
        receipt = match_complete_close(record, order, history)
        if not receipt:
            return None
        start = datetime.fromtimestamp(receipt['opened_ms'] / 1000, timezone.utc) - timedelta(seconds=1)
        end = datetime.fromtimestamp(receipt['closed_ms'] / 1000, timezone.utc) + timedelta(seconds=1)
        others = (await db.execute(select(TradeRecord.id).where(
            TradeRecord.exchange_config_id == config.id,
            TradeRecord.user_id == record.user_id,
            TradeRecord.symbol == record.symbol,
            TradeRecord.direction == record.direction,
            TradeRecord.id != record.id,
            TradeRecord.created_at <= end,
            TradeRecord.updated_at >= start,
        ).limit(1))).first()
        return None if others else receipt

    @staticmethod
    def _engine_singleton():
        """晚导入引擎单例（告警通道 / 下单锁 / 兜底止损），避免模块级循环导入。"""
        from app.services.strategy_engine import strategy_engine
        return strategy_engine

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if value in (None, ""):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            if value in (None, ""):
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default
    
    async def get_performance_overview(
        self, db: AsyncSession, user_id: int, days: int = 30
    ) -> dict:
        """获取绩效概览"""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        close_time = case(
            (TradeRecord.strategy_tag == "OKX同步", TradeRecord.created_at),
            else_=func.coalesce(TradeRecord.updated_at, TradeRecord.created_at),
        )
        realized_filters = [
            TradeRecord.user_id == user_id,
            TradeRecord.is_closed == True,
            TradeRecord.pnl_usdt.isnot(None),
            close_time >= since,
        ]
        
        # 基础统计：只统计已平仓的已实现盈亏。持仓中的 pnl_usdt 是浮盈亏，不能混入绩效。
        stats_query = select(
            func.count(TradeRecord.id).label("total"),
            func.sum(case((TradeRecord.pnl_usdt > 0, 1), else_=0)).label("wins"),
            func.sum(case((TradeRecord.pnl_usdt < 0, 1), else_=0)).label("losses"),
            func.sum(TradeRecord.pnl_usdt).label("total_pnl"),
            func.avg(TradeRecord.pnl_usdt).label("avg_pnl"),
            func.avg(case((TradeRecord.pnl_usdt > 0, TradeRecord.pnl_usdt))).label("avg_win"),
            func.avg(case((TradeRecord.pnl_usdt < 0, TradeRecord.pnl_usdt))).label("avg_loss"),
            func.max(TradeRecord.pnl_usdt).label("max_win"),
            func.min(TradeRecord.pnl_usdt).label("max_loss"),
        ).where(
            and_(*realized_filters)
        )
        
        result = await db.execute(stats_query)
        row = result.one()
        
        total = row.total or 0
        wins = row.wins or 0
        losses = row.losses or 0
        win_rate = (wins / total * 100) if total > 0 else 0
        total_pnl = row.total_pnl or 0
        avg_pnl = row.avg_pnl or 0
        avg_win = row.avg_win or 0
        avg_loss = row.avg_loss or 0
        max_win = row.max_win
        max_loss = row.max_loss
        
        # 盈亏比
        gross_profit = await db.execute(
            select(func.sum(TradeRecord.pnl_usdt)).where(
                and_(*realized_filters, TradeRecord.pnl_usdt > 0)
            )
        )
        gross_loss = await db.execute(
            select(func.sum(TradeRecord.pnl_usdt)).where(
                and_(*realized_filters, TradeRecord.pnl_usdt < 0)
            )
        )
        gp = gross_profit.scalar() or 0
        gl = abs(gross_loss.scalar() or 0)
        profit_factor = gp / gl if gl > 0 else 0
        
        # 最大回撤 (简化计算：累计盈亏的最大回落)
        trades_query = select(TradeRecord).where(
            and_(*realized_filters)
        ).order_by(close_time, TradeRecord.id)
        
        trades_result = await db.execute(trades_query)
        trades = trades_result.scalars().all()
        
        max_drawdown = 0
        peak = 0
        cum_pnl = 0
        for t in trades:
            cum_pnl += (t.pnl_usdt or 0)
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_drawdown:
                max_drawdown = dd
        
        overall = {
            "total_trades": total,
            "win_count": wins,
            "loss_count": losses,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "avg_win": round(avg_win, 2) if avg_win else None,
            "avg_loss": round(avg_loss, 2) if avg_loss else None,
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_drawdown, 2),
            "max_win": round(max_win, 2) if max_win else None,
            "max_loss": round(max_loss, 2) if max_loss else None,
        }
        
        # 币种统计
        symbol_query = select(
            TradeRecord.symbol,
            func.count(TradeRecord.id).label("total"),
            func.sum(case((TradeRecord.pnl_usdt > 0, 1), else_=0)).label("wins"),
            func.sum(case((TradeRecord.pnl_usdt < 0, 1), else_=0)).label("losses"),
            func.sum(TradeRecord.pnl_usdt).label("total_pnl"),
            func.avg(TradeRecord.pnl_usdt).label("avg_pnl"),
        ).where(
            and_(*realized_filters)
        ).group_by(TradeRecord.symbol)
        
        sym_result = await db.execute(symbol_query)
        symbol_stats = []
        for row in sym_result.all():
            sym_total = row.total or 0
            sym_wins = row.wins or 0
            symbol_stats.append({
                "symbol": row.symbol,
                "total_trades": sym_total,
                "win_count": sym_wins,
                "loss_count": row.losses or 0,
                "win_rate": round((sym_wins / sym_total * 100), 2) if sym_total > 0 else 0,
                "total_pnl": round(row.total_pnl or 0, 2),
                "avg_pnl": round(row.avg_pnl or 0, 2),
            })

        # 策略统计
        strategy_label = func.coalesce(TradeRecord.strategy_tag, "未标记")
        strategy_query = select(
            strategy_label.label("strategy_tag"),
            func.count(TradeRecord.id).label("total"),
            func.sum(case((TradeRecord.pnl_usdt > 0, 1), else_=0)).label("wins"),
            func.sum(case((TradeRecord.pnl_usdt < 0, 1), else_=0)).label("losses"),
            func.sum(TradeRecord.pnl_usdt).label("total_pnl"),
            func.avg(TradeRecord.pnl_usdt).label("avg_pnl"),
        ).where(
            and_(*realized_filters)
        ).group_by(strategy_label).order_by(func.sum(TradeRecord.pnl_usdt).asc())

        strategy_result = await db.execute(strategy_query)
        strategy_stats = []
        for row in strategy_result.all():
            strategy_total = row.total or 0
            strategy_wins = row.wins or 0
            strategy_stats.append({
                "strategy_tag": row.strategy_tag or "未标记",
                "total_trades": strategy_total,
                "win_count": strategy_wins,
                "loss_count": row.losses or 0,
                "win_rate": round((strategy_wins / strategy_total * 100), 2) if strategy_total > 0 else 0,
                "total_pnl": round(row.total_pnl or 0, 2),
                "avg_pnl": round(row.avg_pnl or 0, 2),
            })
        
        # 每日盈亏
        close_date = func.strftime('%Y-%m-%d', close_time)
        daily_query = select(
            close_date.label("date"),
            func.sum(TradeRecord.pnl_usdt).label("pnl"),
            func.count(TradeRecord.id).label("count"),
        ).where(
            and_(*realized_filters)
        ).group_by(close_date).order_by("date")
        
        daily_result = await db.execute(daily_query)
        daily_pnl = [
            {"date": row.date, "pnl": round(row.pnl or 0, 2), "trade_count": row.count or 0}
            for row in daily_result.all()
        ]
        
        return {
            "overall": overall,
            "symbol_stats": symbol_stats,
            "strategy_stats": strategy_stats,
            "daily_pnl": daily_pnl,
            "period_days": days,
        }
    
    async def get_trade_records(
        self, db: AsyncSession, user_id: int,
        symbol: str = None, is_closed: bool = None,
        page: int = 1, page_size: int = 20
    ) -> tuple:
        """获取交易记录列表"""
        record_time = case(
            (TradeRecord.strategy_tag == "OKX同步", TradeRecord.created_at),
            else_=func.coalesce(TradeRecord.updated_at, TradeRecord.created_at),
        )
        query = (
            select(TradeRecord)
            .where(TradeRecord.user_id == user_id)
            .order_by(desc(record_time), desc(TradeRecord.id))
        )
        count_query = select(func.count()).select_from(TradeRecord).where(TradeRecord.user_id == user_id)
        
        if symbol:
            query = query.where(TradeRecord.symbol == symbol.upper())
            count_query = count_query.where(TradeRecord.symbol == symbol.upper())
        if is_closed is not None:
            query = query.where(TradeRecord.is_closed == is_closed)
            count_query = count_query.where(TradeRecord.is_closed == is_closed)
        
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
        records = result.scalars().all()
        
        return records, total

    async def _resolve_live_ct_val(self, symbol: str) -> float:
        """取合约面值：优先 OKX 实时 ctVal，失败回退静态表（Phase 2.5e / L5）。

        与 `strategy_engine._get_contract_value_live` 同口径。此前本文件建 TradeRecord
        时完全不写 ct_val，只能靠 `resolve_ct_val` 回退静态表（表外币种默认 1.0）——
        自动开仓路径已把实时 ctVal 落库，交易所同步这条入口却没享受同等待遇；
        若依赖同步记录做平仓核销，PnL 仍可能踩回 ctVal 缺失的老坑。
        `get_instruments` 内部有缓存，逐条调用不会打爆限流。
        """
        try:
            instruments = await okx_manager.get_instruments("SWAP")
            target = (symbol or "").upper()
            for item in instruments:
                if (item.get("instId") or "").upper() == target:
                    ct_val = float(item.get("ctVal") or 0)
                    if ct_val > 0:
                        return ct_val
        except Exception as e:
            print(f"[analyzer] 取实时 ctVal 失败 [{symbol}]，回退静态表: {e}")
        return get_static_ct_val(symbol)

    async def sync_from_okx(self, db: AsyncSession, user_id: int, config: ExchangeConfig) -> dict:
        """从 OKX 同步历史持仓（已平仓记录）到 trade_records"""
        api_key = decrypt_text(config.api_key)
        api_secret = decrypt_text(config.api_secret)
        passphrase = decrypt_text(config.api_passphrase or "")

        positions = await okx_manager.get_positions_history(api_key, api_secret, passphrase, limit=100, simulated=bool(config.is_testnet))
        if not isinstance(positions, list):
            raise ValueError("OKX 返回的历史持仓数据格式异常")

        # 获取已有 external_id 去重
        result = await db.execute(
            select(TradeRecord.external_id).where(
                and_(
                    TradeRecord.user_id == user_id,
                    TradeRecord.exchange_config_id == config.id,
                    TradeRecord.external_id.isnot(None),
                )
            )
        )
        existing_ids = {row[0] for row in result.all()}

        imported = 0
        skipped = 0
        invalid = 0
        for pos in positions:
            if not isinstance(pos, dict):
                invalid += 1
                continue

            pos_id = pos.get("posId")
            if not pos_id or pos_id in existing_ids:
                skipped += 1
                continue

            inst_id = (pos.get("instId") or "").upper()
            if not inst_id:
                invalid += 1
                continue

            # 解析方向
            pos_side = (pos.get("posSide") or "").lower()
            direction = "LONG" if pos_side == "long" else "SHORT"

            # 解析价格/数量/PnL
            entry_price = self._to_float(pos.get("openAvgPx"))
            exit_price = self._to_float(pos.get("closeAvgPx"))
            position_size = self._to_float(pos.get("closeTotalPos"))
            if position_size <= 0:
                position_size = self._to_float(pos.get("openMaxPos"))
            pnl = self._to_float(pos.get("realizedPnl"))
            if pnl == 0:
                pnl = self._to_float(pos.get("pnl"))
            leverage = self._to_int(pos.get("lever"), default=1)

            if entry_price <= 0 or position_size <= 0:
                invalid += 1
                continue

            # 解析时间（OKX 返回毫秒时间戳字符串；cTime 为记录时间，uTime 通常更接近平仓更新时间）
            def parse_okx_time(value):
                if not value:
                    return None
                try:
                    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
                except (ValueError, TypeError):
                    return None

            created_at = parse_okx_time(pos.get("cTime"))
            closed_at = parse_okx_time(pos.get("uTime"))

            record = TradeRecord(
                user_id=user_id,
                exchange_config_id=config.id,
                symbol=inst_id,
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price if exit_price > 0 else None,
                position_size=position_size,
                pnl_usdt=pnl,
                ct_val=await self._resolve_live_ct_val(inst_id),
                leverage=leverage,
                is_closed=True,
                external_id=pos_id,
                strategy_tag="OKX同步",
                notes=f"OKX posId:{pos_id}",
                created_at=created_at or closed_at or datetime.now(timezone.utc),
                updated_at=closed_at or created_at or datetime.now(timezone.utc),
            )
            db.add(record)
            existing_ids.add(pos_id)
            imported += 1

        live_sync = await self.sync_live_positions(db, user_id, config, commit=False)
        await db.commit()
        return {
            "imported": imported,
            "skipped": skipped,
            "invalid": invalid,
            "total_from_exchange": len(positions),
            "live_sync": live_sync,
        }

    async def sync_live_positions(
        self,
        db: AsyncSession,
        user_id: int,
        config: ExchangeConfig,
        commit: bool = True,
    ) -> dict:
        """对账：以 FIFO 台账为 lot 结构的权威，交易所持仓只作"总量校验和"。

        （Phase 2.5c/#10 + 2.5e/#11，2026-07-17 经用户确认的语义）

        旧版本让交易所当权威：每 60s 把最新 lot 的 size/entry_price 覆盖为交易所
        全量、其余 lot 静默标已平且不算 PnL——OKX 只给净持仓+avgPx，从净持仓无法
        重建 lot 结构，这个方向的"校准"必然不可逆地毁掉 FIFO 分批账本（L6），
        Phase 1a 修 ctVal 的收益随之被抵消。`max_entries_per_symbol>1` 是既有功能，
        多 lot 是有意设计。

        新语义：
        - **永不改写/关闭引擎管理的已有 lot**（唯一例外是 None 回填 ct_val）；
          也不再把交易所整仓 upl 写到某个 lot 的 pnl_usdt 上——未实现盈亏归
          monitor（实时），已实现盈亏只在平仓时由 FIFO 核销写入。
        - 交易所 > 台账 → 差额按未归属 lot 补录 + 告警 + 对整仓重挂兜底止损
          （补录的 lot 不被任何策略离场管理，兜底是它唯一的保护，可 env 关闭）。
        - 交易所 < 台账 → 只告警不动账（该关哪个 lot 是账务决策，选错会改变
          FIFO PnL 归属）。
        - 交易所已无该持仓 → 连续两次观察（间隔 ≥120s）才标"未对账关闭"，
          PnL 留空不编造；单次观察会与 _do_reduce 的核销竞态（见 __init__ 注释）。
        - 全程持引擎下单锁，与在途开仓串行——避免"下单已受理、台账 commit 在即"
          的瞬间被当成孤儿仓补录。

        get_positions 失败直接上抛（fail-closed，先于任何 DB 触碰），由调用方中止。
        """
        manual_position_symbols = {
            symbol.strip().upper()
            for symbol in os.getenv("MANUAL_POSITION_SYMBOLS", "").split(",")
            if symbol.strip()
        }
        raw_positions = await monitor_service.get_positions(config)
        live_positions = []
        for pos in raw_positions or []:
            pos_size = self._to_float(pos.get("pos"))
            if pos_size == 0:
                continue
            symbol = (pos.get("instId") or "").upper()
            if not symbol:
                continue
            if symbol in manual_position_symbols:
                continue
            pos_side = (pos.get("posSide") or "net").lower()
            direction = "SHORT" if pos_side == "short" or (pos_side == "net" and pos_size < 0) else "LONG"
            live_positions.append({
                "symbol": symbol,
                "direction": direction,
                "entry_price": self._to_float(pos.get("avgPx")),
                "position_size": abs(pos_size),
                "pnl_usdt": self._to_float(pos.get("upl")),
                "leverage": self._to_int(pos.get("lever"), default=1),
                "market_type": (pos.get("instType") or "SWAP").upper(),
                "external_id": f"okx-live:{symbol}:{direction.lower()}",
            })

        engine = self._engine_singleton()
        created = 0
        booked_diff = 0
        updated = 0
        closed_stale = 0
        pending_close = 0
        reconciled_close = 0
        receipt_attempts = 0
        now = datetime.now(timezone.utc)
        monotonic_now = time.time()

        backstop_params_cache: dict = {}

        async def _resolve_backstop_params(symbol: str) -> dict:
            """孤儿仓兜底止损要用的参数（docs/09 · P3）。

            此前这里传 {}，于是走 _native_stop_trigger_price 的 10% 价格默认值——
            20x 下等于 −200% 保证金，即"爆仓之后才触发"。改为继承管理该 symbol 的
            活跃策略的 native_stop_pct，找不到再退到 ORPHAN_BACKSTOP_FALLBACK_PCT。
            只传价格口径的 native_stop_pct，不传 exit_factors——后者可能是保证金口径。
            """
            cached = backstop_params_cache.get(symbol)
            if cached is not None:
                return cached
            resolved: dict = {}
            fallback: dict = {}
            try:
                strategies = (await db.execute(
                    select(TradingStrategy).where(
                        TradingStrategy.user_id == user_id,
                        TradingStrategy.is_active == True,
                    )
                )).scalars().all()
                for strategy in strategies:
                    sparams = strategy.params or {}
                    try:
                        pct = float(sparams.get("native_stop_pct") or 0)
                    except (TypeError, ValueError):
                        continue
                    if pct <= 0:
                        continue
                    candidate = {"native_stop_pct": pct}
                    watched = {
                        str(s).upper()
                        for s in ([strategy.symbol] + list(sparams.get("symbols") or []))
                        if s
                    }
                    if symbol.upper() in watched:
                        resolved = candidate
                        break
                    if not fallback:
                        fallback = candidate
            except Exception:
                resolved = {}
            resolved = resolved or fallback or {"native_stop_pct": ORPHAN_BACKSTOP_FALLBACK_PCT}
            backstop_params_cache[symbol] = resolved
            return resolved

        async def _book_lot(live: dict, quantity: float, why: str) -> None:
            db.add(TradeRecord(
                user_id=user_id,
                exchange_config_id=config.id,
                symbol=live["symbol"],
                direction=live["direction"],
                entry_price=live["entry_price"],
                position_size=quantity,
                pnl_usdt=None,
                ct_val=await self._resolve_live_ct_val(live["symbol"]),
                leverage=live["leverage"],
                strategy_tag="OKX实时同步",
                notes=f"对账补录: {why}（未归属 lot，不被任何策略离场管理，需人工确认归属）",
                is_closed=False,
                external_id=live["external_id"],
                created_at=now,
                updated_at=now,
            ))
            await engine._alert(
                "对账发现孤儿仓，已补录",
                f"{live['symbol']} {live['direction']} {why}",
                key=f"reconcile-orphan-{config.id}-{live['symbol']}-{live['direction']}",
            )
            if _reconcile_native_stop_enabled():
                # 对整仓（交易所总量）重挂兜底：_place_native_stop_backstop 会先清理
                # 同 (symbol, posSide) 的旧 bg2sl 单再挂新单，故必须按全量而非差额挂，
                # 否则会把引擎原有的兜底换成只覆盖差额的单。失败只告警（其内部已计数）。
                await engine._place_native_stop_backstop(
                    config, live["symbol"], live["direction"],
                    live["entry_price"], live["position_size"],
                    await _resolve_backstop_params(live["symbol"]),
                    market_type=live.get("market_type", "SWAP"),
                )

        async with engine._order_guard_lock:
            result = await db.execute(
                select(TradeRecord).where(
                    and_(
                        TradeRecord.user_id == user_id,
                        TradeRecord.exchange_config_id == config.id,
                        TradeRecord.is_closed == False,
                    )
                ).order_by(desc(TradeRecord.created_at), desc(TradeRecord.id))
            )
            open_records = list(result.scalars().all())

            open_by_key: dict[tuple[str, str], list[TradeRecord]] = {}
            for record in open_records:
                key = ((record.symbol or "").upper(), (record.direction or "").upper())
                open_by_key.setdefault(key, []).append(record)

            live_keys = {(p["symbol"], p["direction"]) for p in live_positions}

            for live in live_positions:
                key = (live["symbol"], live["direction"])
                self._missing_since.pop((config.id, *key), None)
                records = open_by_key.get(key, [])

                for record in records:
                    if record.ct_val is None:
                        record.ct_val = await self._resolve_live_ct_val(live["symbol"])
                        updated += 1

                ledger_total = sum(float(r.position_size or 0) for r in records)
                live_total = float(live["position_size"])
                diff = live_total - ledger_total

                if not records:
                    await _book_lot(live, live_total, f"交易所持有 {live_total} 张，台账无任何未平记录")
                    created += 1
                elif diff > RECONCILE_SIZE_EPS:
                    await _book_lot(
                        live, diff,
                        f"交易所实际 {live_total} 张 > 台账合计 {ledger_total} 张，差额 {diff:g} 张入账",
                    )
                    booked_diff += 1
                elif diff < -RECONCILE_SIZE_EPS:
                    await engine._alert(
                        "对账发现台账多于交易所",
                        f"{live['symbol']} {live['direction']} 台账合计 {ledger_total} 张 > "
                        f"交易所 {live_total} 张。不自动关账（该关哪个 lot 会改变 FIFO PnL 归属），请人工核对",
                        key=f"reconcile-excess-{config.id}-{live['symbol']}-{live['direction']}",
                    )

            for key, records in open_by_key.items():
                if key[0] in manual_position_symbols:
                    continue
                if key in live_keys:
                    continue
                account_position_key = (config.id, *key)
                first_seen = self._missing_since.get(account_position_key)
                if first_seen is None:
                    self._missing_since[account_position_key] = monotonic_now
                    pending_close += len(records)
                    continue
                if monotonic_now - first_seen < RECONCILE_MISSING_GRACE_SECONDS:
                    pending_close += len(records)
                    continue
                self._missing_since.pop(account_position_key, None)
                for record in records:
                    receipt = None
                    if len(records) == 1 and receipt_attempts < 2:
                        receipt_attempts += 1
                        try:
                            receipt = await asyncio.wait_for(
                                self._complete_close_receipt(db, config, record), timeout=6)
                        except Exception as exc:
                            logging.getLogger(__name__).warning(
                                'close_receipt_unavailable record=%s error=%s',
                                record.id, type(exc).__name__)
                    if receipt:
                        record.is_closed = True
                        record.exit_price = receipt['exit_price']
                        # pnl_usdt keeps its established GROSS contract. Fees/net
                        # stay explicit in the receipt, never silently mixed in.
                        record.pnl_usdt = receipt['gross_pnl']
                        record.updated_at = datetime.fromtimestamp(receipt['closed_ms'] / 1000, timezone.utc)
                        record.notes = (record.notes or '') + ' | okx_complete_close_receipt=' + json.dumps(receipt, sort_keys=True)
                        reconciled_close += 1
                        closed_stale += 1
                        continue
                    record.is_closed = True
                    record.notes = (record.notes or "") + " | 未对账关闭: OKX 已无该持仓（平仓来源未知，PnL 未核算）"
                    record.updated_at = now
                    closed_stale += 1
                await engine._alert(
                    "对账关闭无对应持仓的台账记录",
                    f"{key[0]} {key[1]} 共 {len(records)} 条未平记录，OKX 连续两次观察均无该持仓。"
                    f"已关账；本轮完整回执匹配 {reconciled_close} 条，其余 PnL 保持待核算，平仓操作者不推测",
                    key=f"reconcile-missing-{config.id}-{key[0]}-{key[1]}",
                )

        if commit:
            await db.commit()

        return {
            "created": created,
            "booked_diff": booked_diff,
            "updated": updated,
            "closed_stale": closed_stale,
            "reconciled_close": reconciled_close,
            "closed_duplicates": 0,  # 语义已移除：不再压扁多 lot（保留键为兼容 journal 快照）
            "pending_close": pending_close,
            "live_position_count": len(live_positions),
        }


analyzer_service = AnalyzerService()
