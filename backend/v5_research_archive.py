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


def _timestamp(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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
            quote_volume_24h REAL,
            quote_volume_trailing REAL,
            PRIMARY KEY (scan_at, pool, symbol)
        );
        CREATE INDEX IF NOT EXISTS idx_v5_observation_time
            ON signal_observations (scan_ts, pool, symbol);

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
            outcome_24h REAL,
            weighted_outcome_24h REAL,
            outcome_observed_at TEXT,
            PRIMARY KEY (rebalance_day, pool, symbol, direction)
        );
        CREATE INDEX IF NOT EXISTS idx_v5_pending_outcomes
            ON shadow_legs (outcome_24h, rebalance_ts, pool, symbol);
        """
    )
    columns = {row[1] for row in connection.execute(
        "PRAGMA table_info(signal_observations)"
    )}
    if "quote_volume_trailing" not in columns:
        connection.execute(
            "ALTER TABLE signal_observations ADD COLUMN quote_volume_trailing REAL"
        )
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
            item = paired.setdefault(symbol, {"scores": {}})
            item["scores"][direction] = score
            item["price"] = _finite(row.get("price"))
            market = row.get("market") or {}
            item["return_24h"] = _finite(market.get("return_24h"))
            item["quote_volume_24h"] = _finite(market.get("quote_volume_24h"))
            item["quote_volume_trailing"] = _finite(
                market.get("quote_volume_trailing", market.get("quote_volume_24h"))
            )
        for symbol, row in paired.items():
            if set(row["scores"]) == {"LONG", "SHORT"}:
                yield pool, symbol, row


def archive_cross_sectional_scan(state_dir, payload):
    """Archive one completed scan and settle first available 24h outcomes."""
    completed = str(payload["completed"])
    scan_ts = _timestamp(completed)
    observations = list(_observation_rows(payload.get("observations_by_pool")))
    prices = {(pool, symbol): row["price"] for pool, symbol, row in observations
              if row.get("price") and row["price"] > 0}
    inserted_observations = inserted_legs = settled = 0
    with _open(state_dir) as connection:
        for pool, symbol, row in observations:
            connection.execute(
                """
                INSERT OR REPLACE INTO signal_observations(
                    scan_at, scan_ts, pool, symbol, long_score, short_score,
                    price, return_24h, quote_volume_24h, quote_volume_trailing
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (completed, scan_ts, pool, symbol, row["scores"]["LONG"],
                 row["scores"]["SHORT"], row.get("price"), row.get("return_24h"),
                 row.get("quote_volume_24h"), row.get("quote_volume_trailing")),
            )
            inserted_observations += 1

        plans = payload.get("cross_sectional_shadow_by_pool") or {}
        for pool, plan in plans.items():
            if plan.get("status") != "SHADOW_REBALANCE" or not plan.get("legs"):
                continue
            rebalance_day = datetime.fromtimestamp(scan_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            connection.execute(
                """
                INSERT OR IGNORE INTO shadow_rebalances(
                    rebalance_day, rebalance_at, rebalance_ts, pool, status,
                    gross_weight, net_weight, turnover, estimated_cost_fraction,
                    universe_month, universe_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rebalance_day, completed, scan_ts, pool, plan["status"],
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
                        risk_factor, notional_weight, entry_price, alpha, score_spread
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (rebalance_day, scan_ts, pool, leg["symbol"], leg["direction"],
                     leg["risk_factor"], float(leg["notional_weight"]),
                     float(leg["price"]), float(leg["alpha"]),
                     float(leg["score_spread"])),
                )
                inserted_legs += int(cursor.rowcount > 0)

        pending = connection.execute(
            """
            SELECT rebalance_day, rebalance_ts, pool, symbol, direction,
                   notional_weight, entry_price
            FROM shadow_legs
            WHERE outcome_24h IS NULL AND rebalance_ts <= ?
            """,
            (scan_ts - 86400,),
        ).fetchall()
        for row in pending:
            current_price = prices.get((row["pool"], row["symbol"]))
            if not current_price or current_price <= 0:
                continue
            sign = 1 if row["direction"] == "LONG" else -1
            outcome = sign * (current_price / row["entry_price"] - 1)
            connection.execute(
                """
                UPDATE shadow_legs
                SET outcome_24h = ?, weighted_outcome_24h = ?, outcome_observed_at = ?
                WHERE rebalance_day = ? AND pool = ? AND symbol = ? AND direction = ?
                """,
                (outcome, outcome * row["notional_weight"], completed,
                 row["rebalance_day"], row["pool"], row["symbol"], row["direction"]),
            )
            settled += 1

        cutoff = scan_ts - RETENTION_DAYS * 86400
        connection.execute("DELETE FROM signal_observations WHERE scan_ts < ?", (cutoff,))
        connection.execute("DELETE FROM shadow_legs WHERE rebalance_ts < ?", (cutoff,))
        connection.execute("DELETE FROM shadow_rebalances WHERE rebalance_ts < ?", (cutoff,))
    return {
        "signal_observations": inserted_observations,
        "new_shadow_legs": inserted_legs,
        "settled_24h_outcomes": settled,
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
        rebalances = connection.execute(
            "SELECT COUNT(*) FROM shadow_rebalances"
        ).fetchone()[0]
    return {
        "rebalances": int(rebalances or 0),
        "legs": int(row["legs"] or 0),
        "settled_24h_legs": int(row["settled"] or 0),
        "average_24h_directional_return": row["average_outcome"],
        "settled_24h_win_share": row["win_share"],
        "weighted_24h_return_sum": row["weighted_outcome"],
    }
