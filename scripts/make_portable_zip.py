"""Zip an installed app layout without including user data, with ZIP64 support."""
from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path


def make_zip(source: Path, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite release archive: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
        for file in sorted(source.rglob("*")):
            rel = file.relative_to(source).as_posix()
            if file.is_symlink():
                raise ValueError(f"Symlinks in Windows portable ZIP not supported: {rel}")
            if file.is_file():
                archive.write(file, rel)
    print(f"Wrote {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    args = ap.parse_args()
    make_zip(args.source, args.output)
