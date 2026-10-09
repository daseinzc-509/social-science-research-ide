"""Streaming SHA-256: never read a multi-GB Docling installer fully into RAM."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def write(directory: Path, target: str) -> Path:
    directory = directory.resolve()
    files = sorted(
        file for file in directory.iterdir()
        if file.is_file() and file.suffix.lower() in (".zip", ".exe", ".dmg")
    )
    if not files:
        raise ValueError(f"No package archives exist in {directory}")
    result = directory / f"SHA256SUMS-{target}.txt"
    result.write_text(
        "".join(f"{digest(file)}  {file.name}\n" for file in files),
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("target")
    opts = parser.parse_args()
    print(write(opts.directory, opts.target))
