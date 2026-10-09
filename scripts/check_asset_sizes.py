"""Stop before GitHub Release's per-asset size limit (2 GiB)."""
from __future__ import annotations

import argparse
from pathlib import Path

MAX_ASSET = 2_000_000_000  # keep headroom below GitHub's nominal 2 GiB limit


def validate(directory: Path, limit: int = MAX_ASSET) -> list[str]:
    return [
        f"{path.name}: {path.stat().st_size:,} bytes; exceeds configured {limit:,}-byte preview limit"
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".exe", ".zip", ".dmg"}
        and path.stat().st_size > limit
    ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    errors = validate(arguments.directory)
    if errors:
        for issue in errors:
            print("ASSET SIZE BLOCKED:", issue)
        raise SystemExit(1)
    print("Release asset size check PASSED")
