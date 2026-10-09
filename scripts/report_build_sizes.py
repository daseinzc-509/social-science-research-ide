"""Report sizes of actual SRA release build outputs, without reading file contents.

The report operates on frozen application directories and finished assets. Python
package categories are heuristic path groupings, not exact dependency attribution.
No package installations, model downloads, private-data scans or deletion.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MIB = 1024 * 1024
CATEGORY_ORDER = (
    "PyTorch family",
    "Docling family",
    "Transformers / Hugging Face",
    "OCR / computer vision",
    "NumPy / scientific stack",
    "Python runtime",
    "Other backend files",
)


@dataclass(frozen=True)
class FileSize:
    group: str
    relative_path: str
    size_bytes: int
    category: str


def _prefix_matches(segment: str, package: str) -> bool:
    """Match package name or package metadata, but not substring lookalikes."""
    return segment == package or segment.startswith(
        (package + ".", package + "-", package + "_")
    )


def classify_backend(relative: Path) -> str:
    parts = [part.lower().replace("-", "_") for part in relative.parts]
    stem = relative.name.lower()
    families = (
        ("PyTorch family", ("torch", "torchvision", "torchaudio", "functorch", "torchgen")),
        ("Docling family", ("docling", "docling_core", "docling_parse", "docling_ibm_models")),
        (
            "Transformers / Hugging Face",
            ("transformers", "huggingface_hub", "tokenizers", "accelerate", "safetensors", "sentencepiece"),
        ),
        (
            "OCR / computer vision",
            ("rapidocr", "rapidocr_onnxruntime", "onnxruntime", "cv2", "opencv_python", "easyocr", "pytesseract"),
        ),
        (
            "NumPy / scientific stack",
            ("numpy", "scipy", "scikit_learn", "sklearn", "pandas", "pandas_libs"),
        ),
    )
    for category, names in families:
        if any(_prefix_matches(part, name) for part in parts for name in names):
            return category
    if (stem.startswith(("python3", "libpython")) and stem.endswith((".dll", ".so", ".dylib"))) or \
       "lib_dynload" in parts:
        return "Python runtime"
    return "Other backend files"


def scan_directory(root: Path, group: str) -> list[FileSize]:
    if not root.is_dir():
        raise FileNotFoundError(f"Missing release directory ({group}): {root}")
    output: list[FileSize] = []
    for p in root.rglob("*"):
        # Symlinks inside macOS .app bundles must not be followed or double counted.
        if p.is_symlink() or not p.is_file():
            continue
        relative = p.relative_to(root)
        category = classify_backend(relative) if group == "backend" else "Desktop / .NET"
        output.append(FileSize(group, relative.as_posix(), p.stat().st_size, category))
    return output


def _mib(count: int) -> str:
    return f"{count / MIB:,.2f}"


def _escape_md(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;")


def _asset_files(patterns: Iterable[str]) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in patterns:
        for matched in glob.glob(pattern):
            path = Path(matched)
            if path.is_file() and not path.is_symlink():
                found[str(path.resolve())] = path
    if not found:
        raise FileNotFoundError("No packaged assets matched --asset-glob; refusing to report a build without output")
    return sorted(found.values(), key=lambda p: p.name.lower())


def make_report(*, target: str, profile: str, backend: Path, desktop: Path,
                payload: Path, asset_globs: list[str], output_dir: Path) -> dict:
    inputs = (backend, desktop, payload)
    if any(output_dir.resolve() == p.resolve() or output_dir.resolve().is_relative_to(p.resolve()) for p in inputs):
        raise ValueError("Output directory must not be inside a measured build directory")

    backend_files = scan_directory(backend, "backend")
    desktop_files = scan_directory(desktop, "desktop")
    payload_files = scan_directory(payload, "payload")
    asset_paths = _asset_files(asset_globs)

    backend_bytes = sum(f.size_bytes for f in backend_files)
    desktop_bytes = sum(f.size_bytes for f in desktop_files)
    payload_bytes = sum(f.size_bytes for f in payload_files)
    if backend_bytes == 0 or desktop_bytes == 0 or payload_bytes == 0:
        raise ValueError("One of the release directories was empty; refusing to publish a misleading audit")
    category_sizes = Counter({category: 0 for category in CATEGORY_ORDER})
    for file in backend_files:
        category_sizes[file.category] += file.size_bytes
    assert sum(category_sizes.values()) == backend_bytes
    largest = sorted(backend_files + desktop_files, key=lambda f: (-f.size_bytes, f.group, f.relative_path))[:30]
    asset_sizes = {p.name: p.stat().st_size for p in asset_paths}

    result = {
        "schema_version": 1,
        "target": target,
        "backend_profile": profile,
        "units": "bytes (MiB = bytes / 1048576)",
        "frozen_backend_bytes": backend_bytes,
        "desktop_publish_bytes": desktop_bytes,
        "assembled_payload_bytes": payload_bytes,
        "backend_categories_bytes": {category: category_sizes[category] for category in CATEGORY_ORDER},
        "assets_bytes": asset_sizes,
        "backend_file_count": len(backend_files),
        "desktop_file_count": len(desktop_files),
        "payload_file_count": len(payload_files),
        "top_30_files": [
            {"group": f.group, "path": f.relative_path, "category": f.category, "size_bytes": f.size_bytes}
            for f in largest
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"build-size-{target}"
    (output_dir / f"{prefix}.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output_dir / f"{prefix}-files.csv").open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(("group", "category", "size_bytes", "relative_path"))
        for file in sorted(backend_files + desktop_files, key=lambda f: (-f.size_bytes, f.group, f.relative_path)):
            writer.writerow((file.group, file.category, file.size_bytes, file.relative_path))

    lines = [
        f"# SRA Build Size Audit · {target}", "",
        f"- Backend profile: `{profile}`",
        "- All sizes are measured from build output files; no private library or model API is contacted.",
        "- MiB means 1,048,576 bytes. Numbers below are **not** download-speed or RAM-use estimates.", "",
        "## Uncompressed build outputs", "",
        "| Component | MiB | File count |", "|---|---:|---:|",
        f"| Frozen Python backend | {_mib(backend_bytes)} | {len(backend_files):,} |",
        f"| Avalonia + self-contained .NET publish | {_mib(desktop_bytes)} | {len(desktop_files):,} |",
        f"| Final assembled app payload (includes both) | {_mib(payload_bytes)} | {len(payload_files):,} |",
        "",
        "**Do not sum these three rows:** the assembled payload already contains copies of the backend and desktop files.", "",
        "## Compressed installer / distribution assets", "",
        "| File | MiB on disk |", "|---|---:|",
    ]
    for name, size in sorted(asset_sizes.items()):
        lines.append(f"| `{_escape_md(name)}` | {_mib(size)} |")
    lines.extend((
        "",
        "These are the generated Setup EXE / DMG / portable ZIP sizes **before** GitHub's outer artifact ZIP.", "",
        "## Frozen backend: package-family estimates", "",
        "| Family (path-based) | MiB | Share of backend |",
        "|---|---:|---:|",
    ))
    for category in CATEGORY_ORDER:
        size = category_sizes[category]
        lines.append(f"| {_escape_md(category)} | {_mib(size)} | {size / backend_bytes * 100:.1f}% |")
    lines.extend((
        "",
        "**Important:** package-family sizes are inferred from folder names and dist-info paths. ",
        "Native/shared libraries and transitively imported files can land in `Other backend files`; these figures are NOT exact dependency-attribution or safe-to-delete instructions.",
        "Each backend file is counted in exactly one category.", "",
        "## 30 largest frozen backend / desktop files", "",
        "| # | Part | File (relative to that part) | MiB |", "|---:|---|---|---:|",
    ))
    for i, file in enumerate(largest, 1):
        lines.append(f"| {i} | {file.group} | `{_escape_md(file.relative_path)}` | {_mib(file.size_bytes)} |")
    lines.extend((
        "", "## How to interpret and optimize", "",
        "1. Compare installer size against assembled payload size to distinguish compression from dependency weight.",
        "2. Inspect the CSV for unusually large runtime binaries or duplicated vendored components.",
        "3. Review PyInstaller hooks / `--collect-all` only after checking that PDF, OCR and table regression tests still work.",
        "4. Do not remove PyTorch / Docling or enable .NET trimming solely from this report; test a real PDF end to end.",
        "", "Data files: same-name `.json` for summary and `-files.csv` for all frozen backend/desktop files."
    ))
    (output_dir / f"{prefix}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=("win-x64", "osx-arm64"))
    parser.add_argument("--profile", choices=("full", "core"), default="full")
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--desktop", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--asset-glob", action="append", required=True, dest="assets")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = make_report(target=args.target, profile=args.profile, backend=args.backend,
                         desktop=args.desktop, payload=args.payload, asset_globs=args.assets,
                         output_dir=args.output_dir)
    print(f"SIZE AUDIT: {args.target} / {args.profile}")
    print(f"  Python backend: { _mib(report['frozen_backend_bytes']) } MiB")
    print(f"  Avalonia/.NET:  { _mib(report['desktop_publish_bytes']) } MiB")
    print(f"  Assembled app:  { _mib(report['assembled_payload_bytes']) } MiB")
    for name, size in report["assets_bytes"].items():
        print(f"  Asset {name}: {_mib(size)} MiB")
    print(f"Reports written to: {args.output_dir}")


if __name__ == "__main__":
    main()
