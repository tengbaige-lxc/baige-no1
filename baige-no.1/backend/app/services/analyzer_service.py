"""
交易绩效分析服务
基于 real_trader_v2.py 和 trading_analyzer.py 的核心逻辑
"""
from typing import List, Dict, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc, case
from app.models.trade_record import TradeRecord


class AnalyzerService:
    """交易分析器"""
    
    async def get_performance_overview(
        self, db: AsyncSession, user_id: int, days: int = 30
    ) -> dict:
        """获取绩效概览"""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        
        # 基础统计
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
            and_(TradeRecord.user_id == user_id, TradeRecord.created_at >= since)
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
                and_(TradeRecord.user_id == user_id, TradeRecord.pnl_usdt > 0, TradeRecord.created_at >= since)
            )
        )
        gross_loss = await db.execute(
            select(func.sum(TradeRecord.pnl_usdt)).where(
                and_(TradeRecord.user_id == user_id, TradeRecord.pnl_usdt < 0, TradeRecord.created_at >= since)
            )
        )
        gp = gross_profit.scalar() or 0
        gl = abs(gross_loss.scalar() or 0)
        profit_factor = gp / gl if gl > 0 else 0
        
        # 最大回撤 (简化计算：累计盈亏的最大回落)
        trades_query = select(TradeRecord).where(
            and_(TradeRecord.user_id == user_id, TradeRecord.pnl_usdt.isnot(None), TradeRecord.created_at >= since)
        ).order_by(TradeRecord.created_at)
        
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
            and_(TradeRecord.user_id == user_id, TradeRecord.created_at >= since)
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
        
        # 每日盈亏
        daily_query = select(
            func.strftime('%Y-%m-%d', TradeRecord.created_at).label("date"),
            func.sum(TradeRecord.pnl_usdt).label("pnl"),
            func.count(TradeRecord.id).label("count"),
        ).where(
            and_(TradeRecord.user_id == user_id, TradeRecord.pnl_usdt.isnot(None), TradeRecord.created_at >= since)
        ).group_by(func.strftime('%Y-%m-%d', TradeRecord.created_at)).order_by("date")
        
        daily_result = await db.execute(daily_query)
        daily_pnl = [
            {"date": row.date, "pnl": round(row.pnl or 0, 2), "trade_count": row.count or 0}
            for row in daily_result.all()
        ]
        
        return {
            "overall": overall,
            "symbol_stats": symbol_stats,
            "daily_pnl": daily_pnl,
            "period_days": days,
        }
    
    async def get_trade_records(
        self, db: AsyncSession, user_id: int,
        symbol: str = None, is_closed: bool = None,
        page: int = 1, page_size: int = 20
    ) -> tuple:
        """获取交易记录列表"""
        query = select(TradeRecord).where(TradeRecord.user_id == user_id).order_by(desc(TradeRecord.created_at))
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


analyzer_service = AnalyzerService()
