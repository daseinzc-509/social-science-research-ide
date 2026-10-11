"""Fail the native Release build if a PyMuPDF/fitz implementation survives freezing.

This checks only file paths, not source strings, and never modifies the bundle.
It is deliberately strict: if Docling begins requiring PyMuPDF transitively,
review that dependency instead of distributing it silently.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def find_legacy_pdf_runtime(root: Path) -> list[str]:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"Frozen backend directory not found: {root}")
    matches = []
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        path_parts = [part.casefold() for part in entry.relative_to(root).parts]
        if any(
            part.startswith("pymupdf") or
            part in {"fitz", "fitz.py", "fitz.pyd", "fitz.dll"} or
            part.startswith("fitz.")
            for part in path_parts
        ):
            matches.append(entry.relative_to(root).as_posix())
    return sorted(matches)


def main() -> int:
    cli = argparse.ArgumentParser()
    cli.add_argument("bundle", type=Path)
    args = cli.parse_args()
    found = find_legacy_pdf_runtime(args.bundle)
    if found:
        print("Legacy PyMuPDF/fitz files remain in frozen backend:")
        for path in found[:30]:
            print("  " + path)
        return 2
    print("Docling-only packaging gate: no PyMuPDF/fitz files detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
