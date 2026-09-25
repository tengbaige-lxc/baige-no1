"""Report identical files shared by two deployments; never deletes files."""

import argparse
import hashlib
import json
from pathlib import Path


EXCLUDED_PARTS = {
    ".git", "__pycache__", ".pytest_cache", "backups", "staging", "state", "data",
}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root: Path) -> dict[str, tuple[int, str]]:
    result = {}
    for path in root.rglob("*"):
        if not path.is_file() or EXCLUDED_PARTS.intersection(path.relative_to(root).parts):
            continue
        relative = path.relative_to(root).as_posix()
        result[relative] = (path.stat().st_size, file_hash(path))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()
    left = inventory(args.left.resolve())
    right = inventory(args.right.resolve())
    duplicates = [
        path for path in sorted(left.keys() & right.keys())
        if left[path] == right[path]
    ]
    print(json.dumps({
        "left": str(args.left.resolve()),
        "right": str(args.right.resolve()),
        "identical_relative_files": len(duplicates),
        "reclaimable_bytes_after_sharing": sum(left[path][0] for path in duplicates),
        "files": duplicates,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
