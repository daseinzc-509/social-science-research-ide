"""Fail the native Release build if a PyMuPDF/fitz implementation survives freezing.

This checks only file paths, not source strings, and never modifies the bundle.
It is deliberately strict: if Docling begins requiring PyMuPDF transitively,
review that dependency instead of distributing it silently.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

NON_RUNTIME_BLOAT_DIRS = frozenset({
    "faker",
    "polyfactory",
    "pytest",
    "_pytest",
    "tkinter",
    "tcl8",
    "_tcl_data",
    "_tk_data",
    "pythonwin",
})


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


def audit_frozen_bundle_hygiene(root: Path) -> dict[str, object]:
    """检查冻结后端目录是否存在遗留 PyMuPDF、重复多版本 .dist-info 或测试/GUI 冗余目录。"""
    root = Path(root)
    legacy = find_legacy_pdf_runtime(root)
    internal = root / "_internal"
    dist_by_pkg: dict[str, list[str]] = {}
    bloat_dirs: list[str] = []

    if internal.is_dir():
        for entry in sorted(internal.iterdir(), key=lambda p: p.name.casefold()):
            if not entry.is_dir():
                continue
            lower = entry.name.casefold()
            if lower in NON_RUNTIME_BLOAT_DIRS or re.fullmatch(
                r"(faker|polyfactory|pytest)-.+\.dist-info", lower
            ):
                bloat_dirs.append(f"_internal/{entry.name}")
            if lower.endswith(".dist-info") and "-" in entry.name[: -len(".dist-info")]:
                stem = entry.name[: -len(".dist-info")]
                pkg_raw, _ver = stem.rsplit("-", 1)
                pkg = re.sub(r"[-_.]+", "-", pkg_raw).strip("-").casefold()
                if pkg:
                    dist_by_pkg.setdefault(pkg, []).append(entry.name)

    duplicate_dist_infos = {
        pkg: sorted(names)
        for pkg, names in sorted(dist_by_pkg.items())
        if len(names) > 1
    }
    return {
        "legacy_pdf_files": legacy,
        "duplicate_dist_infos": duplicate_dist_infos,
        "dev_gui_bloat_dirs": sorted(bloat_dirs),
        "clean": not legacy and not duplicate_dist_infos and not bloat_dirs,
    }


def main() -> int:
    cli = argparse.ArgumentParser()
    cli.add_argument("bundle", type=Path)
    cli.add_argument("--strict-hygiene", action="store_true",
                     help="Also fail if duplicate .dist-info or test/GUI bloat directories exist")
    args = cli.parse_args()
    audit = audit_frozen_bundle_hygiene(args.bundle)
    found = audit["legacy_pdf_files"]
    if found:
        print("Legacy PyMuPDF/fitz files remain in frozen backend:")
        for path in found[:30]:
            print("  " + path)
        return 2
    if audit["duplicate_dist_infos"] or audit["dev_gui_bloat_dirs"]:
        print(
            f"Bundle hygiene notice: duplicate_dist_infos={audit['duplicate_dist_infos']}, "
            f"dev_gui_bloat_dirs={audit['dev_gui_bloat_dirs']}"
        )
        if args.strict_hygiene:
            return 3
    print("Docling-only packaging gate: no PyMuPDF/fitz files detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
