import argparse
import json
from pathlib import Path

from app.db.data_retention import run_retention


def parse_args():
    parser = argparse.ArgumentParser(description="Preview or apply Baige V5 retention rules")
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_retention(
        args.database,
        apply=args.apply,
        backup_dir=args.backup_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
