"""Historical portfolio replay for the V5 cross-sectional strategy.

This is a research-only command.  It reads an existing public-candle SQLite
cache and never imports account configuration, touches the live database, or
places orders.  The replay mirrors the current V5 factor gate, pool schedules,
liquidity selection, exposure caps, 20x sizing and fixed price stop.  It is not
a fill-level reconstruction because historical OI/funding snapshots, order
book slippage and every live exit overlay are unavailable.
"""
from __future__ import annotations

import argparse
import bisect
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
from statistics import mean

from app.services.market_regime import calculate_adx_atr
from app.services.moer_structure import (
    evaluate_moer_long_structure,
    evaluate_moer_short_structure,
)
from app.services.signal_quality import entry_timing
from app.services.trend_v3 import Action, Direction, TrendContext, TrendV3
from v5_cross_sectional import build_cross_sectional_shadow
from v5_execution import (
    cross_sectional_selected_by_pool,
    dynamic_exposure_plan,
    select_legs,
)
from v5_portfolio import market_features, risk_factor_for_symbol


FIVE_MINUTES_MS = 5 * 60 * 1000
THIRTY_MINUTES_MS = 30 * 60 * 1000
FOUR_HOURS_MS = 4 * 60 * 60 * 1000


def finite(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def resample_5m(rows, period_ms):
    """Resample exact 5m runs and estimate quote volume for liquidity ranks."""
    expected = period_ms // FIVE_MINUTES_MS
    groups = defaultdict(list)
    for row in rows:
        groups[int(row[0]) // period_ms * period_ms].append(row)
    output = []
    for start, group in sorted(groups.items()):
        group.sort(key=lambda item: int(item[0]))
        timestamps = [start + FIVE_MINUTES_MS * index for index in range(expected)]
        if [int(item[0]) for item in group] != timestamps:
            continue
        volume = sum(float(item[5]) for item in group)
        quote_volume = sum(float(item[5]) * float(item[4]) for item in group)
        output.append([
            start,
            float(group[0][1]),
            max(float(item[2]) for item in group),
            min(float(item[3]) for item in group),
            float(group[-1][4]),
            volume,
            0.0,
            quote_volume,
            "1",
        ])
    return output


def closed_window(rows, ends, now_ms, limit):
    index = bisect.bisect_right(ends, now_ms)
    return rows[max(0, index - limit):index]


def trend_snapshot(rows, *, fast, slow, slope_bars):
    if len(rows) < slow + slope_bars:
        return {"ok": False}
    closes = [float(row[4]) for row in rows]
    price = closes[-1]
    fast_now = mean(closes[-fast:])
    slow_now = mean(closes[-slow:])
    fast_previous = mean(closes[-fast - slope_bars:-slope_bars])
    slope = fast_now / fast_previous - 1 if fast_previous else 0.0
    return {
        "ok": True,
        "price": price,
        "ma_fast": fast_now,
        "ma_slow": slow_now,
        "slope": slope,
        "long_state": price > fast_now > slow_now,
        "short_state": price < fast_now < slow_now,
    }


class CandleCache:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def symbols(self):
        return {str(row[0]) for row in self.db.execute(
            "SELECT DISTINCT symbol FROM candles_5m"
        )}

    def bounds(self, symbols):
        marks = ",".join("?" for _ in symbols)
        return self.db.execute(
            f"SELECT min(ts), max(ts) FROM candles_5m WHERE symbol IN ({marks})",
            list(symbols),
        ).fetchone()

    def rows(self, symbol, start_ms, end_ms):
        return [
            [ts, opn, high, low, close, volume, 0.0, close * volume, "1"]
            for ts, opn, high, low, close, volume in self.db.execute(
                "SELECT ts,open,high,low,close,volume FROM candles_5m "
                "WHERE symbol=? AND ts>=? AND ts<=? ORDER BY ts",
                (symbol, start_ms, end_ms),
            )
        ]

    def close(self):
        self.db.close()


def slot_times(start_ms, end_ms, schedule):
    start = datetime.fromtimestamp(start_ms / 1000, timezone.utc)
    current = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    if current.timestamp() * 1000 < start_ms:
        current += timedelta(days=1)
    output = []
    while int(current.timestamp() * 1000) <= end_ms:
        for hour in sorted(set(schedule)):
            stamp = int((current + timedelta(hours=int(hour))).timestamp() * 1000)
            if start_ms <= stamp <= end_ms:
                output.append(stamp)
        current += timedelta(days=1)
    return sorted(output)


def score_pair(symbol, now_ms, rows_5m, rows_30m, rows_4h, params):
    fast = int(params.get("trend_regime_ma_fast", 34))
    slow = int(params.get("trend_regime_ma_slow", 170))
    slope_4h = int(params.get("trend_regime_higher_slope_bars", 8))
    slope_30m = int(params.get("trend_regime_entry_slope_bars", 12))
    slope_threshold = float(params.get("trend_regime_slope_threshold", 0.0005))
    if len(rows_5m) < 100 or len(rows_30m) < 240 or len(rows_4h) < slow + slope_4h:
        return []
    higher = trend_snapshot(rows_4h, fast=fast, slow=slow, slope_bars=slope_4h)
    entry = trend_snapshot(rows_30m, fast=fast, slow=slow, slope_bars=slope_30m)
    if not higher.get("ok") or not entry.get("ok"):
        return []
    adx = calculate_adx_atr(
        rows_4h,
        int((params.get("adx_atr_filter") or {}).get("adx_period", 14)),
    )
    if not adx.get("available"):
        return []
    adx_4h = finite(adx.get("adx"))
    min_volume = float(params.get("trend_v3_min_volume_ratio", 1.8))
    min_speed = float(params.get("trend_v3_min_speed_ratio", 1.5))
    policy = TrendV3(
        min_open_score=float(params.get("trend_v3_min_open_score", 6.5)),
        min_add_score=float(params.get("trend_v3_min_add_score", 6.5)),
        strong_adx=float(params.get("trend_v3_strong_adx", 35.0)),
        min_volume_ratio=min_volume,
        min_speed_ratio=min_speed,
    )
    market = market_features(rows_30m, now_ms)
    if not market:
        return []
    observations = []
    for direction_name in ("LONG", "SHORT"):
        direction = Direction(direction_name)
        if direction is Direction.LONG:
            trend_4h = 1 if higher["long_state"] and higher["slope"] >= slope_threshold else -1 if higher["short_state"] else 0
            structure_30m = 1 if entry["long_state"] and entry["slope"] >= slope_threshold else -1 if entry["short_state"] else 0
            moer = evaluate_moer_long_structure(rows_4h[-200:], rows_30m[-240:])
            moer_reentry = str(moer.get("state")) in {"B2", "B3"}
        else:
            trend_4h = 1 if higher["short_state"] and higher["slope"] <= -slope_threshold else -1 if higher["long_state"] else 0
            structure_30m = 1 if entry["short_state"] and entry["slope"] <= -slope_threshold else -1 if entry["long_state"] else 0
            moer = evaluate_moer_short_structure(rows_4h[-200:], rows_30m[-240:])
            moer_reentry = str(moer.get("state")) in {"S2", "S3"}
        timing = entry_timing(
            rows_5m[-100:],
            direction_name,
            now_ms=now_ms,
            min_volume_ratio=min_volume,
            min_speed_ratio=min_speed,
            require_volume_speed=bool(
                params.get("trend_v3_require_volume_speed_for_entry", False)
            ),
        )
        decision = policy.evaluate(TrendContext(
            symbol=symbol,
            direction=direction,
            trend_4h=trend_4h,
            adx_4h=adx_4h,
            structure_30m=structure_30m,
            moer_reentry_30m=moer_reentry,
            trigger_5m=bool(timing["trigger"]),
            divergence_5m=False,
            volume_ratio_5m=float(timing["volume_ratio"]),
            speed_ratio_5m=float(timing["speed_ratio"]),
        ))
        observations.append({
            "symbol": symbol,
            "direction": direction_name,
            "score": float(decision.score),
            "eligible": decision.action in {Action.OPEN, Action.ADD},
            "trend_4h_aligned": trend_4h > 0,
            "structure_30m_aligned": structure_30m > 0,
            "adx_4h_strong": adx_4h >= policy.strong_adx,
            "adx_4h": adx_4h,
            "price": float(rows_5m[-1][4]),
            "market": market,
        })
    return observations


def build_snapshots(cache, symbols_by_pool, slots, fetch_start_ms, params):
    observations = defaultdict(lambda: defaultdict(list))
    total = sum(len(values) for values in symbols_by_pool.values())
    completed = 0
    for pool, symbols in symbols_by_pool.items():
        pool_slots = set(slots[pool])
        for symbol in symbols:
            completed += 1
            rows = cache.rows(symbol, fetch_start_ms, max(pool_slots))
            rows_30m = resample_5m(rows, THIRTY_MINUTES_MS)
            rows_4h = resample_5m(rows, FOUR_HOURS_MS)
            ends_5m = [int(row[0]) + FIVE_MINUTES_MS for row in rows]
            ends_30m = [int(row[0]) + THIRTY_MINUTES_MS for row in rows_30m]
            ends_4h = [int(row[0]) + FOUR_HOURS_MS for row in rows_4h]
            for stamp in sorted(pool_slots):
                window_5m = closed_window(rows, ends_5m, stamp, 100)
                window_30m = closed_window(rows_30m, ends_30m, stamp, 240)
                window_4h = closed_window(rows_4h, ends_4h, stamp, 210)
                for row in score_pair(
                    symbol, stamp, window_5m, window_30m, window_4h, params
                ):
                    row["pool"] = pool
                    observations[stamp][pool].append(row)
            if completed % 10 == 0:
                print(json.dumps({
                    "phase": "features", "completed": completed, "total": total
                }), flush=True)
    return observations


def target_book(plans, config):
    selected = cross_sectional_selected_by_pool(
        plans,
        float(config["minimum_score"]),
        require_factor_gate=True,
    )
    legs = select_legs(
        selected,
        set(),
        {"LONG": 0, "SHORT": 0},
        max_per_side=int(config["max_positions_per_side"]),
        max_total=int(config["max_positions_total"]),
    )
    sides = {leg["risk_direction"] for leg in legs}
    if len(sides) == 1:
        legs = legs[:int(config["single_side_max_positions"])]
    counts = {
        side: sum(leg["risk_direction"] == side for leg in legs)
        for side in ("LONG", "SHORT")
    }
    exposure = dynamic_exposure_plan(legs, counts, config)
    factor_caps = config.get("risk_factor_margin_limits") or {}
    used_factor = defaultdict(float)
    side_groups = {
        side: [leg for leg in legs if leg["risk_direction"] == side]
        for side in ("LONG", "SHORT")
    }
    sized = []
    max_leg = float(config["max_loss_per_leg_equity_fraction"]) / (
        float(config["requested_leverage"]) * float(config["native_stop_price_pct"])
    )
    for side in ("LONG", "SHORT"):
        values = side_groups[side]
        if not values:
            continue
        side_budget = float(exposure["margin_limit"]) * float(
            exposure["side_share"].get(side, 0)
        )
        per_leg = min(max_leg, side_budget / len(values))
        for leg in values:
            factor = leg.get("risk_factor") or risk_factor_for_symbol(
                leg["symbol"], leg.get("pool")
            )
            factor_cap = float(factor_caps.get(factor, factor_caps.get("DEFAULT", .15)))
            margin = min(per_leg, max(0.0, factor_cap - used_factor[factor]))
            if margin <= 0:
                continue
            used_factor[factor] += margin
            sized.append({
                **leg,
                "risk_factor": factor,
                "margin_fraction": margin,
                "notional_fraction": margin * float(config["requested_leverage"]),
            })
    return sized, exposure


def event_books(observations, slots, config):
    cross_config = dict(config["cross_sectional_shadow"])
    states = {"crypto": {}, "tradfi": {}}
    plans = {}
    books = []
    schedule = {
        pool: set(hours)
        for pool, hours in cross_config["rebalance_utc_hours"].items()
    }
    all_slots = sorted(set(slots["crypto"]) | set(slots["tradfi"]))
    for stamp in all_slots:
        hour = datetime.fromtimestamp(stamp / 1000, timezone.utc).hour
        for pool in ("crypto", "tradfi"):
            if hour not in schedule[pool]:
                continue
            plan = build_cross_sectional_shadow(
                observations[stamp][pool],
                pool=pool,
                now_ms=stamp,
                state=states[pool],
                config=cross_config,
            )
            states[pool] = plan["state"]
            plans[pool] = plan
        legs, exposure = target_book(plans, config)
        books.append({"ts": stamp, "legs": legs, "exposure": exposure})
    return books


def direction_return(direction, entry, price):
    if direction == "LONG":
        return price / entry - 1
    return entry / price - 1


def simulate(cache, books, config, end_ms):
    equity = 100.0
    peak = equity
    peak_ts = int(books[0]["ts"]) if books else end_ms
    max_drawdown = 0.0
    max_drawdown_peak_ts = peak_ts
    max_drawdown_trough_ts = peak_ts
    turnover_total = 0.0
    estimated_cost_paid = 0.0
    curve = []
    trades = []
    prior_weights = {}
    cost_rate = float(config["cross_sectional_shadow"].get("cost_bps_per_side", 3)) / 10000
    stop_pct = float(config["hard_stop_price_pct"])
    for offset, book in enumerate(books):
        start = int(book["ts"])
        finish = int(books[offset + 1]["ts"]) if offset + 1 < len(books) else end_ms
        new_weights = {
            (leg["symbol"], leg["direction"]): float(leg["notional_fraction"])
            for leg in book["legs"]
        }
        turnover = sum(abs(new_weights.get(key, 0) - prior_weights.get(key, 0))
                       for key in set(new_weights) | set(prior_weights))
        cost = equity * turnover * cost_rate
        estimated_cost_paid += cost
        turnover_total += turnover
        equity = max(0.0, equity - cost)
        if equity > peak:
            peak = equity
            peak_ts = start
        drawdown = (peak - equity) / peak if peak else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_drawdown_peak_ts = peak_ts
            max_drawdown_trough_ts = start
        curve.append({"ts": start, "equity": round(equity, 6)})
        interval_base = equity
        positions = []
        bars_by_ts = defaultdict(dict)
        for leg in book["legs"]:
            rows = cache.rows(leg["symbol"], start - FIVE_MINUTES_MS, finish)
            entry_rows = [row for row in rows if int(row[0]) + FIVE_MINUTES_MS <= start]
            if not entry_rows:
                continue
            entry = float(entry_rows[-1][4])
            position = {
                **leg,
                "entry": entry,
                "active": True,
                "last": entry,
                "realized": 0.0,
                "opened_at": start,
            }
            positions.append(position)
            for row in rows:
                close_at = int(row[0]) + FIVE_MINUTES_MS
                if start < close_at <= finish:
                    bars_by_ts[close_at][leg["symbol"]] = row
        for stamp in sorted(bars_by_ts):
            floating = 0.0
            for position in positions:
                if not position["active"]:
                    floating += position["realized"]
                    continue
                row = bars_by_ts[stamp].get(position["symbol"])
                if row is not None:
                    position["last"] = float(row[4])
                    stopped = (
                        position["direction"] == "LONG"
                        and float(row[3]) <= position["entry"] * (1 - stop_pct)
                    ) or (
                        position["direction"] == "SHORT"
                        and float(row[2]) >= position["entry"] * (1 + stop_pct)
                    )
                    if stopped:
                        position["active"] = False
                        position["realized"] = (
                            -stop_pct * float(position["notional_fraction"])
                        )
                        trades.append({
                            "symbol": position["symbol"],
                            "pool": position["pool"],
                            "direction": position["direction"],
                            "opened_at": position["opened_at"],
                            "closed_at": stamp,
                            "return_pct": -stop_pct * 100,
                            "exit": "hard_stop",
                        })
                if position["active"]:
                    floating += float(position["notional_fraction"]) * direction_return(
                        position["direction"], position["entry"], position["last"]
                    )
                else:
                    floating += position["realized"]
            equity = interval_base * (1 + floating)
            if equity > peak:
                peak = equity
                peak_ts = stamp
            drawdown = (peak - equity) / peak if peak else 0.0
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_peak_ts = peak_ts
                max_drawdown_trough_ts = stamp
            curve.append({"ts": stamp, "equity": round(equity, 6)})
        for position in positions:
            if position["active"]:
                result = direction_return(
                    position["direction"], position["entry"], position["last"]
                )
                trades.append({
                    "symbol": position["symbol"],
                    "pool": position["pool"],
                    "direction": position["direction"],
                    "opened_at": position["opened_at"],
                    "closed_at": finish,
                    "return_pct": result * 100,
                    "exit": "rebalance",
                })
        prior_weights = {
            (position["symbol"], position["direction"]): float(position["notional_fraction"])
            for position in positions if position["active"]
        }
    return {
        "ending_equity": equity,
        "return_pct": (equity / 100 - 1) * 100,
        "max_drawdown_pct": max_drawdown * 100,
        "max_drawdown_peak_ts": max_drawdown_peak_ts,
        "max_drawdown_trough_ts": max_drawdown_trough_ts,
        "turnover_total_notional": turnover_total,
        "estimated_cost_paid": estimated_cost_paid,
        "curve": curve,
        "trades": trades,
    }


def summarize_trades(trades):
    groups = defaultdict(list)
    for trade in trades:
        groups[f"{trade['pool']}:{trade['direction']}"].append(trade)
    output = {}
    for key, values in sorted(groups.items()):
        returns = [float(item["return_pct"]) for item in values]
        output[key] = {
            "count": len(values),
            "win_rate_pct": round(100 * sum(value > 0 for value in returns) / len(values), 2),
            "average_price_return_pct": round(mean(returns), 4),
            "hard_stop_count": sum(item["exit"] == "hard_stop" for item in values),
        }
    return output


def benchmark_buy_and_hold(cache, symbol, start_ms, end_ms):
    rows = cache.rows(symbol, start_ms, end_ms)
    if len(rows) < 2:
        return None
    start_price = float(rows[0][4])
    end_price = float(rows[-1][4])
    peak = start_price
    peak_ts = int(rows[0][0])
    maximum = (0.0, peak_ts, peak_ts)
    for row in rows:
        timestamp = int(row[0])
        price = float(row[4])
        if price > peak:
            peak = price
            peak_ts = timestamp
        drawdown = (peak - price) / peak
        if drawdown > maximum[0]:
            maximum = (drawdown, peak_ts, timestamp)
    return {
        "symbol": symbol,
        "return_pct": round((end_price / start_price - 1) * 100, 4),
        "max_drawdown_pct": round(maximum[0] * 100, 4),
        "max_drawdown_peak": datetime.fromtimestamp(
            maximum[1] / 1000, timezone.utc
        ).isoformat(),
        "max_drawdown_trough": datetime.fromtimestamp(
            maximum[2] / 1000, timezone.utc
        ).isoformat(),
    }


def run(args):
    root = Path(args.root).resolve()
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    universes = json.loads((root / config["universe_file"]).read_text(encoding="utf-8"))
    cache = CandleCache(args.cache)
    try:
        cached = cache.symbols()
        symbols_by_pool = {
            pool: [symbol for symbol in universes[pool] if symbol in cached]
            for pool in ("crypto", "tradfi")
        }
        symbols = sorted(set(symbols_by_pool["crypto"]) | set(symbols_by_pool["tradfi"]))
        if not symbols:
            raise RuntimeError("no V5 universe symbols exist in the candle cache")
        first_ms, last_ms = cache.bounds(symbols)
        end_ms = int(last_ms // FIVE_MINUTES_MS * FIVE_MINUTES_MS)
        start_ms = end_ms - int(args.days * 86400 * 1000)
        fetch_start_ms = max(int(first_ms), start_ms - int(args.warmup_days * 86400 * 1000))
        schedule = config["cross_sectional_shadow"]["rebalance_utc_hours"]
        slots = {
            pool: slot_times(start_ms, end_ms, schedule[pool])
            for pool in ("crypto", "tradfi")
        }
        observations = build_snapshots(
            cache, symbols_by_pool, slots, fetch_start_ms, config["params"]
        )
        books = event_books(observations, slots, config)
        result = simulate(cache, books, config, end_ms)
        margin_utilization = [
            sum(float(leg["margin_fraction"]) for leg in book["legs"])
            for book in books
        ]
        leg_counts = [len(book["legs"]) for book in books]
        side_modes = defaultdict(int)
        for book in books:
            directions = {leg["direction"] for leg in book["legs"]}
            side_modes["both" if len(directions) == 2 else next(iter(directions), "cash")] += 1
        benchmark = benchmark_buy_and_hold(cache, "BTC-USDT-SWAP", start_ms, end_ms)
        generated = datetime.now(timezone.utc).isoformat()
        report = {
            "kind": "v5_historical_portfolio_replay_not_fill_level_backtest",
            "generated_at": generated,
            "window": {
                "days": args.days,
                "start": datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat(),
                "warmup_days_available": round((start_ms - fetch_start_ms) / 86400000, 2),
            },
            "universe": {
                "cached_symbols": len(cached),
                "v5_cached_symbols": len(symbols),
                "crypto": len(symbols_by_pool["crypto"]),
                "tradfi": len(symbols_by_pool["tradfi"]),
            },
            "assumptions": {
                "starting_equity": 100.0,
                "leverage": config["requested_leverage"],
                "hard_stop_price_pct": config["hard_stop_price_pct"] * 100,
                "cost_bps_per_side": config["cross_sectional_shadow"]["cost_bps_per_side"],
                "sizing": "current V5 account/side/factor caps and per-leg loss budget",
            },
            "summary": {
                "ending_equity": round(result["ending_equity"], 4),
                "return_pct": round(result["return_pct"], 4),
                "max_drawdown_pct": round(result["max_drawdown_pct"], 4),
                "max_drawdown_peak": datetime.fromtimestamp(
                    result["max_drawdown_peak_ts"] / 1000, timezone.utc
                ).isoformat(),
                "max_drawdown_trough": datetime.fromtimestamp(
                    result["max_drawdown_trough_ts"] / 1000, timezone.utc
                ).isoformat(),
                "rebalance_events": len(books),
                "leg_outcomes": len(result["trades"]),
                "average_margin_utilization_pct": round(
                    mean(margin_utilization) * 100, 4
                ) if margin_utilization else 0.0,
                "maximum_margin_utilization_pct": round(
                    max(margin_utilization) * 100, 4
                ) if margin_utilization else 0.0,
                "average_legs": round(mean(leg_counts), 4) if leg_counts else 0.0,
                "maximum_legs": max(leg_counts) if leg_counts else 0,
                "book_side_modes": dict(side_modes),
                "turnover_total_notional": round(
                    result["turnover_total_notional"], 4
                ),
                "estimated_cost_paid_on_100_start": round(
                    result["estimated_cost_paid"], 4
                ),
                "by_pool_direction": summarize_trades(result["trades"]),
            },
            "benchmark": benchmark,
            "books": books,
            "trades": result["trades"],
            "equity_curve": result["curve"],
            "limitations": [
                "Uses the current universe, so delisted/removed instruments can create survivorship bias.",
                "Historical OI, funding, macro/fundamental overlays and divergence snapshots are neutral/unavailable.",
                "Uses 5m OHLC fixed-stop execution and scheduled rebalances; it does not reconstruct order-book slippage, funding, native-stop latency, trailing partial exits or every live rotation guard.",
                "Estimated quote volume is 5m base volume times close because the old cache stores base volume only.",
                "Results are research evidence, not expected or guaranteed live returns.",
            ],
        }
        output = Path(args.output) if args.output else (
            root / "state" / "v5_backtests" /
            f"portfolio-replay-{args.days}d-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"report": str(output), **report["summary"]}, ensure_ascii=False))
        return report
    finally:
        cache.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--cache", required=True)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--warmup-days", type=int, default=32)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not 7 <= args.days <= 75:
        raise SystemExit("days must be between 7 and 75")
    if not 28 <= args.warmup_days <= 40:
        raise SystemExit("warmup-days must be between 28 and 40")
    run(args)


if __name__ == "__main__":
    main()
