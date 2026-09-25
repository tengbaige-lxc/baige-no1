"""Conservative, backup-gated retention for non-ledger operational data."""

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
from pathlib import Path
import shutil
import sqlite3


@dataclass(frozen=True, slots=True)
class RetentionRule:
    name: str
    table: str
    timestamp_column: str
    days: int
    predicate: str = "1=1"


DEFAULT_RETENTION_RULES = (
    RetentionRule("hold_strategy_logs", "strategy_logs", "created_at", 7, "signal = 'HOLD'"),
    RetentionRule("derivatives_snapshots", "derivatives_market_snapshots", "observed_at", 35),
    RetentionRule("operation_logs", "operation_logs", "created_at", 90),
)


def create_verified_backup(database_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"{database_path.name}.bak-{stamp}-retention.gz"
    with database_path.open("rb") as source, gzip.open(destination, "wb") as target:
        shutil.copyfileobj(source, target)
    with gzip.open(destination, "rb") as archive:
        while archive.read(1024 * 1024):
            pass
    return destination


def _existing_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row[1]) for row in rows}


def run_retention(
    database_path: str | Path,
    *,
    apply: bool = False,
    backup_dir: str | Path | None = None,
    rules: tuple[RetentionRule, ...] = DEFAULT_RETENTION_RULES,
) -> dict:
    """Preview by default; applying requires a verified gzip backup."""
    path = Path(database_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if apply and backup_dir is None:
        raise ValueError("backup_dir is required when apply=True")

    backup_path = None
    if apply:
        backup_path = create_verified_backup(path, Path(backup_dir).resolve())

    connection = sqlite3.connect(path)
    results = []
    try:
        for rule in rules:
            columns = _existing_columns(connection, rule.table)
            if not columns:
                results.append({"rule": rule.name, "status": "table_missing", "rows": 0})
                continue
            if rule.timestamp_column not in columns:
                results.append({"rule": rule.name, "status": "column_missing", "rows": 0})
                continue
            where = (
                f"({rule.predicate}) AND \"{rule.timestamp_column}\" "
                f"< datetime('now', '-{int(rule.days)} days')"
            )
            count = int(connection.execute(
                f'SELECT COUNT(*) FROM "{rule.table}" WHERE {where}'
            ).fetchone()[0])
            if apply and count:
                connection.execute(f'DELETE FROM "{rule.table}" WHERE {where}')
            results.append({
                "rule": rule.name,
                "status": "deleted" if apply else "preview",
                "rows": count,
            })
        if apply:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "mode": "apply" if apply else "preview",
        "database": str(path),
        "backup": str(backup_path) if backup_path else None,
        "rules": results,
        "vacuum_performed": False,
    }
