import sqlite3

from app.db.data_retention import run_retention


def create_database(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE strategy_logs (signal TEXT, created_at TEXT);
        CREATE TABLE derivatives_market_snapshots (observed_at TEXT);
        CREATE TABLE operation_logs (created_at TEXT);
        CREATE TABLE trade_records (created_at TEXT);
        INSERT INTO strategy_logs VALUES ('HOLD', '2020-01-01 00:00:00');
        INSERT INTO strategy_logs VALUES ('BUY', '2020-01-01 00:00:00');
        INSERT INTO derivatives_market_snapshots VALUES ('2020-01-01 00:00:00');
        INSERT INTO operation_logs VALUES ('2020-01-01 00:00:00');
        INSERT INTO trade_records VALUES ('2020-01-01 00:00:00');
    """)
    connection.commit()
    connection.close()


def count(path, table):
    connection = sqlite3.connect(path)
    value = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    connection.close()
    return value


def test_retention_is_preview_only_by_default(tmp_path):
    database = tmp_path / "test.db"
    create_database(database)
    result = run_retention(database)
    assert result["mode"] == "preview"
    assert sum(row["rows"] for row in result["rules"]) == 3
    assert count(database, "strategy_logs") == 2


def test_apply_requires_and_verifies_backup_without_touching_ledger(tmp_path):
    database = tmp_path / "test.db"
    create_database(database)
    result = run_retention(database, apply=True, backup_dir=tmp_path / "backups")
    assert result["backup"].endswith(".gz")
    assert count(database, "strategy_logs") == 1
    assert count(database, "derivatives_market_snapshots") == 0
    assert count(database, "operation_logs") == 0
    assert count(database, "trade_records") == 1
