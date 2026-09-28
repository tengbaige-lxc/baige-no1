"""Durable, non-sensitive evidence for the V5 cross-sectional shadow book."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import sqlite3


ARCHIVE_FILE = "v5_cross_sectional_research.sqlite"
RETENTION_DAYS = 400
REBALANCE_STATUSES = {"SHADOW_REBALANCE", "LIVE_REBALANCE"}


def _timestamp(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ensure_columns(connection, table, definitions):
    columns = {row[1] for row in connection.execute(
        f"PRAGMA table_info({table})"
    )}
    for name, definition in definitions.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
            )


def _connect(state_dir):
    path = Path(state_dir) / ARCHIVE_FILE
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS signal_observations (
            scan_at TEXT NOT NULL,
            scan_ts REAL NOT NULL,
            pool TEXT NOT NULL,
            symbol TEXT NOT NULL,
            long_score REAL,
            short_score REAL,
            price REAL,
            return_24h REAL,
            volatility_30m REAL,
            quote_volume_24h REAL,
            quote_volume_trailing REAL,
            long_trend_4h_aligned INTEGER,
            short_trend_4h_aligned INTEGER,
            long_structure_30m_aligned INTEGER,
            short_structure_30m_aligned INTEGER,
            long_adx_4h_strong INTEGER,
            short_adx_4h_strong INTEGER,
            long_adx_4h REAL,
            short_adx_4h REAL,
            PRIMARY KEY (scan_at, pool, symbol)
        );
        CREATE INDEX IF NOT EXISTS idx_v5_observation_time
            ON signal_observations (scan_ts, pool, symbol);
        CREATE INDEX IF NOT EXISTS idx_v5_observation_symbol_time
            ON signal_observations (pool, symbol, scan_ts);

        CREATE TABLE IF NOT EXISTS shadow_rebalances (
            rebalance_day TEXT NOT NULL,
            rebalance_at TEXT NOT NULL,
            rebalance_ts REAL NOT NULL,
            pool TEXT NOT NULL,
            status TEXT NOT NULL,
            gross_weight REAL NOT NULL,
            net_weight REAL NOT NULL,
            turnover REAL NOT NULL,
            estimated_cost_fraction REAL NOT NULL,
            universe_month TEXT,
            universe_json TEXT NOT NULL,
            PRIMARY KEY (rebalance_day, pool)
        );

        CREATE TABLE IF NOT EXISTS shadow_legs (
            rebalance_day TEXT NOT NULL,
            rebalance_ts REAL NOT NULL,
            pool TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            risk_factor TEXT NOT NULL,
            notional_weight REAL NOT NULL,
            entry_price REAL NOT NULL,
            alpha REAL NOT NULL,
            score_spread REAL NOT NULL,
            directional_score REAL,
            opposite_score REAL,
            directional_score_edge REAL,
            normalized_momentum REAL,
            expected_edge_fraction REAL,
            volatility_30m REAL,
            liquidity_trailing REAL,
            trend_4h_aligned INTEGER,
            structure_30m_aligned INTEGER,
            adx_4h_strong INTEGER,
            adx_4h REAL,
            outcome_6h REAL,
            outcome_24h REAL,
            outcome_72h REAL,
            weighted_outcome_6h REAL,
            weighted_outcome_24h REAL,
            weighted_outcome_72h REAL,
            max_favorable_72h REAL,
            max_adverse_72h REAL,
            outcome_6h_observed_at TEXT,
            outcome_observed_at TEXT,
            outcome_72h_observed_at TEXT,
            PRIMARY KEY (rebalance_day, pool, symbol, direction)
        );
        CREATE INDEX IF NOT EXISTS idx_v5_pending_outcomes
            ON shadow_legs (outcome_24h, rebalance_ts, pool, symbol);
        """
    )
    _ensure_columns(connection, "signal_observations", {
        "quote_volume_trailing": "REAL",
        "volatility_30m": "REAL",
        "long_trend_4h_aligned": "INTEGER",
        "short_trend_4h_aligned": "INTEGER",
        "long_structure_30m_aligned": "INTEGER",
        "short_structure_30m_aligned": "INTEGER",
        "long_adx_4h_strong": "INTEGER",
        "short_adx_4h_strong": "INTEGER",
        "long_adx_4h": "REAL",
        "short_adx_4h": "REAL",
    })
    _ensure_columns(connection, "shadow_legs", {
        "directional_score": "REAL",
        "opposite_score": "REAL",
        "directional_score_edge": "REAL",
        "normalized_momentum": "REAL",
        "expected_edge_fraction": "REAL",
        "volatility_30m": "REAL",
        "liquidity_trailing": "REAL",
        "trend_4h_aligned": "INTEGER",
        "structure_30m_aligned": "INTEGER",
        "adx_4h_strong": "INTEGER",
        "adx_4h": "REAL",
        "outcome_6h": "REAL",
        "outcome_72h": "REAL",
        "weighted_outcome_6h": "REAL",
        "weighted_outcome_72h": "REAL",
        "max_favorable_72h": "REAL",
        "max_adverse_72h": "REAL",
        "outcome_6h_observed_at": "TEXT",
        "outcome_72h_observed_at": "TEXT",
    })
    return connection


@contextmanager
def _open(state_dir):
    connection = _connect(state_dir)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _observation_rows(observations_by_pool):
    for pool, observations in (observations_by_pool or {}).items():
        paired = {}
        for row in observations or []:
            symbol = str(row.get("symbol") or "").upper()
            direction = str(row.get("direction") or "").upper()
            score = _finite(row.get("score"))
            if not symbol or direction not in {"LONG", "SHORT"} or score is None:
                continue
            item = paired.setdefault(symbol, {"scores": {}, "signals": {}})
            item["scores"][direction] = score
            item["signals"][direction] = {
                "trend_4h_aligned": int(bool(row.get("trend_4h_aligned"))),
                "structure_30m_aligned": int(bool(row.get("structure_30m_aligned"))),
                "adx_4h_strong": int(bool(row.get("adx_4h_strong"))),
                "adx_4h": _finite(row.get("adx_4h")),
            }
            item["price"] = _finite(row.get("price"))
            market = row.get("market") or {}
            item["return_24h"] = _finite(market.get("return_24h"))
            item["volatility_30m"] = _finite(market.get("volatility"))
            item["quote_volume_24h"] = _finite(market.get("quote_volume_24h"))
            item["quote_volume_trailing"] = _finite(
                market.get("quote_volume_trailing", market.get("quote_volume_24h"))
            )
        for symbol, row in paired.items():
            if set(row["scores"]) == {"LONG", "SHORT"}:
                yield pool, symbol, row


def archive_cross_sectional_scan(state_dir, payload):
    """Archive rebalance evidence and settle path-aware forward outcomes."""
    completed = str(payload["completed"])
    scan_ts = _timestamp(completed)
    observations = list(_observation_rows(payload.get("observations_by_pool")))
    prices = {(pool, symbol): row["price"] for pool, symbol, row in observations
              if row.get("price") and row["price"] > 0}
    plans = payload.get("cross_sectional_shadow_by_pool") or {}
    rebalance_plans = {
        pool: plan for pool, plan in plans.items()
        if plan.get("status") in REBALANCE_STATUSES and plan.get("legs")
    }
    inserted_observations = inserted_legs = 0
    settled = {"6h": 0, "24h": 0, "72h": 0}
    with _open(state_dir) as connection:
        # Five-minute observations are highly autocorrelated and previously grew
        # the archive by tens of thousands of rows per day.  Persist the complete
        # cross-section only when a real target slot is formed; every scan still
        # contributes prices to forward-outcome settlement below.
        if rebalance_plans:
            for pool, symbol, row in observations:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO signal_observations(
                        scan_at, scan_ts, pool, symbol, long_score, short_score,
                        price, return_24h, volatility_30m, quote_volume_24h,
                        quote_volume_trailing, long_trend_4h_aligned,
                        short_trend_4h_aligned, long_structure_30m_aligned,
                        short_structure_30m_aligned, long_adx_4h_strong,
                        short_adx_4h_strong, long_adx_4h, short_adx_4h
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (completed, scan_ts, pool, symbol, row["scores"]["LONG"],
                     row["scores"]["SHORT"], row.get("price"), row.get("return_24h"),
                     row.get("volatility_30m"), row.get("quote_volume_24h"),
                     row.get("quote_volume_trailing"),
                     row["signals"]["LONG"]["trend_4h_aligned"],
                     row["signals"]["SHORT"]["trend_4h_aligned"],
                     row["signals"]["LONG"]["structure_30m_aligned"],
                     row["signals"]["SHORT"]["structure_30m_aligned"],
                     row["signals"]["LONG"]["adx_4h_strong"],
                     row["signals"]["SHORT"]["adx_4h_strong"],
                     row["signals"]["LONG"]["adx_4h"],
                     row["signals"]["SHORT"]["adx_4h"]),
                )
                inserted_observations += 1

        for pool, plan in rebalance_plans.items():
            rebalance_key = str(plan.get("target_slot") or datetime.fromtimestamp(
                scan_ts, tz=timezone.utc
            ).strftime("%Y-%m-%d"))
            connection.execute(
                """
                INSERT OR IGNORE INTO shadow_rebalances(
                    rebalance_day, rebalance_at, rebalance_ts, pool, status,
                    gross_weight, net_weight, turnover, estimated_cost_fraction,
                    universe_month, universe_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rebalance_key, completed, scan_ts, pool, plan["status"],
                 float(plan.get("gross_weight") or 0), float(plan.get("net_weight") or 0),
                 float(plan.get("estimated_one_way_turnover") or 0),
                 float(plan.get("estimated_cost_fraction") or 0),
                 plan.get("liquidity_universe_month"),
                 json.dumps(plan.get("liquidity_universe") or [])),
            )
            for leg in plan["legs"]:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO shadow_legs(
                        rebalance_day, rebalance_ts, pool, symbol, direction,
                        risk_factor, notional_weight, entry_price, alpha, score_spread,
                        directional_score, opposite_score, directional_score_edge,
                        normalized_momentum, expected_edge_fraction, volatility_30m,
                        liquidity_trailing, trend_4h_aligned, structure_30m_aligned,
                        adx_4h_strong, adx_4h
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (rebalance_key, scan_ts, pool, leg["symbol"], leg["direction"],
                     leg["risk_factor"], float(leg["notional_weight"]),
                     float(leg["price"]), float(leg["alpha"]),
                     float(leg["score_spread"]), _finite(leg.get("directional_score")),
                     _finite(leg.get("opposite_score")),
                     _finite(leg.get("directional_score_edge")),
                     _finite(leg.get("normalized_momentum")),
                     _finite(leg.get("expected_edge_fraction")),
                     _finite(leg.get("volatility_30m")),
                     _finite(leg.get("liquidity_trailing")),
                     int(bool(leg.get("trend_4h_aligned"))),
                     int(bool(leg.get("structure_30m_aligned"))),
                     int(bool(leg.get("adx_4h_strong"))),
                     _finite(leg.get("adx_4h"))),
                )
                inserted_legs += int(cursor.rowcount > 0)

        pending = connection.execute(
            """
            SELECT rebalance_day, rebalance_ts, pool, symbol, direction,
                   notional_weight, entry_price, outcome_6h, outcome_24h,
                   outcome_72h, max_favorable_72h, max_adverse_72h
            FROM shadow_legs
            WHERE rebalance_ts <= ? AND rebalance_ts >= ?
              AND (outcome_72h IS NULL OR max_favorable_72h IS NULL
                   OR max_adverse_72h IS NULL)
            """,
            (scan_ts, scan_ts - 80 * 3600),
        ).fetchall()
        for row in pending:
            current_price = prices.get((row["pool"], row["symbol"]))
            if not current_price or current_price <= 0:
                continue
            sign = 1 if row["direction"] == "LONG" else -1
            outcome = sign * (current_price / row["entry_price"] - 1)
            age = scan_ts - row["rebalance_ts"]
            outcome_6h = row["outcome_6h"]
            outcome_24h = row["outcome_24h"]
            outcome_72h = row["outcome_72h"]
            observed_6h = observed_24h = observed_72h = None
            if outcome_6h is None and age >= 6 * 3600:
                outcome_6h, observed_6h = outcome, completed
                settled["6h"] += 1
            if outcome_24h is None and age >= 24 * 3600:
                outcome_24h, observed_24h = outcome, completed
                settled["24h"] += 1
            if outcome_72h is None and age >= 72 * 3600:
                outcome_72h, observed_72h = outcome, completed
                settled["72h"] += 1
            favorable = max(
                outcome,
                row["max_favorable_72h"] if row["max_favorable_72h"] is not None else outcome,
            )
            adverse = min(
                outcome,
                row["max_adverse_72h"] if row["max_adverse_72h"] is not None else outcome,
            )
            connection.execute(
                """
                UPDATE shadow_legs
                SET outcome_6h = ?, outcome_24h = ?, outcome_72h = ?,
                    weighted_outcome_6h = ?, weighted_outcome_24h = ?,
                    weighted_outcome_72h = ?, max_favorable_72h = ?,
                    max_adverse_72h = ?,
                    outcome_6h_observed_at = COALESCE(outcome_6h_observed_at, ?),
                    outcome_observed_at = COALESCE(outcome_observed_at, ?),
                    outcome_72h_observed_at = COALESCE(outcome_72h_observed_at, ?)
                WHERE rebalance_day = ? AND pool = ? AND symbol = ? AND direction = ?
                """,
                (outcome_6h, outcome_24h, outcome_72h,
                 outcome_6h * row["notional_weight"] if outcome_6h is not None else None,
                 outcome_24h * row["notional_weight"] if outcome_24h is not None else None,
                 outcome_72h * row["notional_weight"] if outcome_72h is not None else None,
                 favorable, adverse, observed_6h, observed_24h, observed_72h,
                 row["rebalance_day"], row["pool"], row["symbol"], row["direction"]),
            )

        cutoff = scan_ts - RETENTION_DAYS * 86400
        connection.execute("DELETE FROM signal_observations WHERE scan_ts < ?", (cutoff,))
        connection.execute("DELETE FROM shadow_legs WHERE rebalance_ts < ?", (cutoff,))
        connection.execute("DELETE FROM shadow_rebalances WHERE rebalance_ts < ?", (cutoff,))
    return {
        "signal_observations": inserted_observations,
        "new_shadow_legs": inserted_legs,
        "settled_6h_outcomes": settled["6h"],
        "settled_24h_outcomes": settled["24h"],
        "settled_72h_outcomes": settled["72h"],
    }


def research_summary(state_dir):
    with _open(state_dir) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS legs,
                   SUM(CASE WHEN outcome_24h IS NOT NULL THEN 1 ELSE 0 END) AS settled,
                   AVG(outcome_24h) AS average_outcome,
                   AVG(CASE WHEN outcome_24h > 0 THEN 1.0 ELSE 0.0 END) AS win_share,
                   SUM(weighted_outcome_24h) AS weighted_outcome
            FROM shadow_legs
            """
        ).fetchone()
        span = connection.execute(
            "SELECT MIN(rebalance_ts), MAX(rebalance_ts) FROM shadow_legs"
        ).fetchone()
        rebalances = connection.execute(
            "SELECT COUNT(*) FROM shadow_rebalances"
        ).fetchone()[0]
        by_pool_rows = connection.execute(
            """
            SELECT pool, COUNT(*) AS legs,
                   SUM(outcome_6h IS NOT NULL) AS settled_6h,
                   SUM(outcome_24h IS NOT NULL) AS settled_24h,
                   SUM(outcome_72h IS NOT NULL) AS settled_72h,
                   AVG(outcome_24h) AS average_24h,
                   AVG(CASE WHEN outcome_24h > 0 THEN 1.0 ELSE 0.0 END) AS win_24h,
                   AVG(max_favorable_72h) AS average_mfe,
                   AVG(max_adverse_72h) AS average_mae
            FROM shadow_legs GROUP BY pool ORDER BY pool
            """
        ).fetchall()
        direction_rows = connection.execute(
            """
            SELECT pool, direction, SUM(outcome_24h IS NOT NULL) AS settled
            FROM shadow_legs GROUP BY pool, direction
            """
        ).fetchall()
        pools = [item[0] for item in connection.execute(
            "SELECT DISTINCT pool FROM shadow_rebalances ORDER BY pool"
        ).fetchall()]
    span_days = (
        max(0.0, (float(span[1]) - float(span[0])) / 86400)
        if span and span[0] is not None and span[1] is not None else 0.0
    )
    settled_by_bucket = {
        f"{item['pool']}:{item['direction']}": int(item["settled"] or 0)
        for item in direction_rows
    }
    for pool in pools:
        for direction in ("LONG", "SHORT"):
            settled_by_bucket.setdefault(f"{pool}:{direction}", 0)
    minimum_bucket = min(settled_by_bucket.values(), default=0)
    settled_24h = int(row["settled"] or 0)
    preliminary_ready = span_days >= 30 and settled_24h >= 120 and minimum_bucket >= 20
    promotion_ready = span_days >= 60 and settled_24h >= 240 and minimum_bucket >= 40
    return {
        "rebalances": int(rebalances or 0),
        "legs": int(row["legs"] or 0),
        "settled_24h_legs": int(row["settled"] or 0),
        "average_24h_directional_return": row["average_outcome"],
        "settled_24h_win_share": row["win_share"],
        "weighted_24h_return_sum": row["weighted_outcome"],
        "calendar_span_days": round(span_days, 2),
        "settled_by_pool_direction": settled_by_bucket,
        "by_pool": {
            item["pool"]: {
                "legs": int(item["legs"] or 0),
                "settled_6h": int(item["settled_6h"] or 0),
                "settled_24h": int(item["settled_24h"] or 0),
                "settled_72h": int(item["settled_72h"] or 0),
                "average_24h_directional_return": item["average_24h"],
                "settled_24h_win_share": item["win_24h"],
                "average_mfe_72h": item["average_mfe"],
                "average_mae_72h": item["average_mae"],
            }
            for item in by_pool_rows
        },
        "readiness": {
            "stage": (
                "promotion_candidate" if promotion_ready
                else "preliminary_analysis" if preliminary_ready
                else "collecting"
            ),
            "preliminary_ready": preliminary_ready,
            "promotion_ready": promotion_ready,
            "preliminary_requirements": {
                "calendar_days": 30,
                "settled_24h_legs": 120,
                "per_pool_direction": 20,
            },
            "promotion_requirements": {
                "calendar_days": 60,
                "settled_24h_legs": 240,
                "per_pool_direction": 40,
            },
        },
    }
