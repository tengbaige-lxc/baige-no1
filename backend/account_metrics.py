from collections import defaultdict


def summarize_events(events):
    """Summarize realized PnL events without inventing missing values."""
    ordered = sorted(events, key=lambda item: (item[0], item[2]))
    pnls = [float(item[1]) for item in ordered]
    wins = sum(value > 0 for value in pnls)
    losses = sum(value < 0 for value in pnls)
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    daily = defaultdict(lambda: {"pnl": 0.0, "trade_count": 0})
    for timestamp, pnl, _ in ordered:
        value = float(pnl)
        cumulative += value
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        day = timestamp.date().isoformat()
        daily[day]["pnl"] += value
        daily[day]["trade_count"] += 1
    total = len(pnls)
    return {
        "total_trades": total,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / total * 100, 2) if total else 0.0,
        "realized_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / total, 2) if total else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else 0.0,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "max_drawdown": round(max_drawdown, 2),
        "daily_pnl": [
            {"date": day, "pnl": round(values["pnl"], 2),
             "trade_count": values["trade_count"]}
            for day, values in sorted(daily.items())
        ],
    }


def aggregate_accounts(accounts, period_days):
    daily = defaultdict(lambda: {"pnl": 0.0, "trade_count": 0})
    for account in accounts:
        for row in account.get("daily_pnl") or []:
            daily[row["date"]]["pnl"] += float(row.get("pnl") or 0)
            daily[row["date"]]["trade_count"] += int(row.get("trade_count") or 0)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    daily_rows = []
    for day, values in sorted(daily.items()):
        cumulative += values["pnl"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        daily_rows.append({"date": day, "pnl": round(values["pnl"], 2),
                           "trade_count": values["trade_count"]})
    trades = sum(int(row.get("total_trades") or 0) for row in accounts)
    wins = sum(int(row.get("win_count") or 0) for row in accounts)
    realized = sum(float(row.get("realized_pnl") or 0) for row in accounts)
    gross_profit = sum(float(row.get("gross_profit") or 0) for row in accounts)
    gross_loss = sum(float(row.get("gross_loss") or 0) for row in accounts)
    unrealized = sum(float(row.get("unrealized_pnl") or 0) for row in accounts)
    return {
        "id": "all", "name": "全部账户", "source_label": "全部",
        "period_days": period_days, "account_count": len(accounts),
        "equity": round(sum(float(row.get("equity") or 0) for row in accounts), 2),
        "available": round(sum(float(row.get("available") or 0) for row in accounts), 2),
        "position_count": sum(int(row.get("position_count") or 0) for row in accounts),
        "position_notional_usd": round(sum(float(row.get("position_notional_usd") or 0) for row in accounts), 2),
        "position_margin_usd": round(sum(float(row.get("position_margin_usd") or 0) for row in accounts), 2),
        "unrealized_pnl": round(unrealized, 2),
        "realized_pnl": round(realized, 2),
        "combined_pnl": round(realized + unrealized, 2),
        "total_trades": trades, "win_count": wins,
        "loss_count": sum(int(row.get("loss_count") or 0) for row in accounts),
        "win_rate": round(wins / trades * 100, 2) if trades else 0.0,
        "avg_pnl": round(realized / trades, 2) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else 0.0,
        "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2),
        "max_drawdown": round(max_drawdown, 2), "daily_pnl": daily_rows,
    }
