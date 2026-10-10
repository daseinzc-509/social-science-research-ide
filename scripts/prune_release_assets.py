"""Conservatively remove known non-runtime files from SRA release output.

Only the already-built PyInstaller backend and dotnet publish directory are
eligible; this never touches a source checkout, user home, imported PDFs,
model cache, .env or research databases.

Dry-run is the default. --apply is needed to remove files; report sizes are
calculated before unlinking. Keep the frozen backend smoke test *after* this
step in the release workflow, to catch unexpected runtime regressions.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024
DEVELOPMENT_SUFFIXES = frozenset({".lib", ".a", ".h", ".hpp", ".hxx"})
VALID_TARGETS = ("win-x64", "osx-arm64")


@dataclass(frozen=True)
class PrunableFile:
    component: str
    path: Path
    relative: str
    size: int
    reason: str


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)  # Python >=3.12 on Windows
    return bool(is_junction and is_junction())


def _require_release_tree(root: Path, *, component: str) -> Path:
    if _is_link_or_junction(root):
        raise ValueError(f"{component} root cannot be a symlink or junction: {root}")
    root = root.absolute()
    if not root.is_dir():
        raise FileNotFoundError(f"{component} output directory not found: {root}")
    markers = {
        "backend": ("sra-backend", "sra-backend.exe"),
        "desktop": ("SRA.Desktop", "SRA.Desktop.exe"),
    }[component]
    if not any((root / marker).is_file() for marker in markers):
        raise ValueError(f"{component} folder is not a frozen SRA release output: {root}")
    return root


def _is_pymupdf_dev_artifact(relative: Path) -> bool:
    """Match only pymupdf/mupdf-devel/{headers,static or link libraries}."""
    parts = tuple(part.casefold() for part in relative.parts)
    if relative.suffix.casefold() not in DEVELOPMENT_SUFFIXES:
        return False
    return any(parts[i:i + 2] == ("pymupdf", "mupdf-devel")
               for i in range(len(parts) - 2))


def _is_opencv_video_codec(relative: Path) -> bool:
    """Exactly OpenCV's Windows FFmpeg video I/O plugin, never cv2.pyd.

    Only used after the actual frozen executable passes the PDF/image probe.
    """
    parts = tuple(part.casefold() for part in relative.parts)
    return (
        len(parts) == 3
        and parts[:2] == ("_internal", "cv2")
        and re.fullmatch(r"opencv_videoio_ffmpeg[0-9]+_64\.dll", parts[-1]) is not None
    )


def _eligible_files(
    root: Path, component: str, *, remove_opencv_video: bool = False
) -> list[PrunableFile]:
    candidates: list[PrunableFile] = []
    root_resolved = root.resolve(strict=True)
    for path in root.rglob("*"):
        if _is_link_or_junction(path) or not path.is_file():
            continue
        # Defensive guard against symlink/junction parents and escaped paths.
        if not path.resolve(strict=True).is_relative_to(root_resolved):
            raise ValueError(f"Candidate file escapes {component} build directory: {path}")
        relative = path.relative_to(root)
        if any(_is_link_or_junction(parent) for parent in path.parents if parent != root and parent.is_relative_to(root)):
            raise ValueError(f"Link/junction parent inside release tree: {relative}")
        if component == "desktop" and path.suffix.casefold() == ".pdb":
            reason = "Release debug symbol (PDB), not a runtime dependency"
        elif component == "backend" and _is_pymupdf_dev_artifact(relative):
            reason = "PyMuPDF development header/static or import library"
        elif component == "backend" and remove_opencv_video and _is_opencv_video_codec(relative):
            reason = "Optional OpenCV video/FFmpeg codec (PDF image decode is runtime-tested)"
        else:
            continue
        candidates.append(PrunableFile(component, path, relative.as_posix(), path.stat().st_size, reason))
    return sorted(candidates, key=lambda item: (item.component, item.relative.casefold()))


def prune(
    backend: Path, desktop: Path, *, apply: bool = False,
    remove_opencv_video: bool = False,
) -> dict:
    """Return an audit report. No file is removed unless apply=True."""
    backend = _require_release_tree(backend, component="backend")
    desktop = _require_release_tree(desktop, component="desktop")
    backend_canonical, desktop_canonical = backend.resolve(), desktop.resolve()
    if (backend_canonical == desktop_canonical or
        backend_canonical.is_relative_to(desktop_canonical) or
        desktop_canonical.is_relative_to(backend_canonical)):
        raise ValueError("Backend and desktop roots must be independent release outputs")
    files = _eligible_files(backend, "backend", remove_opencv_video=remove_opencv_video) + _eligible_files(desktop, "desktop")
    total = sum(file.size for file in files)
    if apply:
        for file in files:
            if _is_link_or_junction(file.path) or not file.path.is_file():
                raise RuntimeError(f"Candidate changed during prune: {file.component}/{file.relative}")
            file.path.unlink()
        if (_eligible_files(backend, "backend", remove_opencv_video=remove_opencv_video)
                or _eligible_files(desktop, "desktop")):
            raise RuntimeError("Eligible files remained after pruning")
    return {
        "schema_version": 2,
        "applied": apply,
        "remove_opencv_video": remove_opencv_video,
        "removed_files": len(files) if apply else 0,
        "candidate_files": len(files),
        "candidate_bytes": total,
        "candidate_mib": round(total / MIB, 3),
        "files": [
            {
                "component": f.component,
                "relative_path": f.relative,
                "size_bytes": f.size,
                "reason": f.reason,
            }
            for f in files
        ],
    }


def _report_markdown(report: dict, target: str) -> str:
    rows = [
        f"# SRA Release Safe Pruning · {target}", "",
        f"- Mode: **{'applied' if report['applied'] else 'dry-run only'}**",
        f"- Candidate files: **{report['candidate_files']}**",
        f"- Uncompressed candidate size: **{report['candidate_mib']:.2f} MiB**",
        "- Desktop `.pdb` and PyMuPDF development libraries/headers are eligible.",
        "- Optional Windows video codec removal: **" + ("enabled" if report.get("remove_opencv_video") else "disabled") + "**.",
        "- Torch, Docling, OCR, OpenCV image decoder, `.pyd`, `.so` and `.dylib` runtime binaries are preserved.",
        "- **Compressed installer size is measured separately** by Build Size Audit.", "",
        "| Component | Removed candidate | MiB (uncompressed) |", "|---|---|---:|",
    ]
    # The GitHub Actions job summary has a size cap. Full file list remains
    # available in the adjacent JSON; only largest 25 are shown in Markdown.
    visible = sorted(report["files"], key=lambda i: -i["size_bytes"])[:25]
    for item in visible:
        safe_name = item["relative_path"].replace("|", "\\|").replace("`", "'").replace("<", "&lt;")
        rows.append(f"| {item['component']} | `{safe_name}` | {item['size_bytes'] / MIB:.2f} |")
    if not report["files"]:
        rows.append("| — | No eligible files found | 0.00 |")
    if len(report["files"]) > len(visible):
        rows.extend(("", f"Additional {len(report['files']) - len(visible)} candidates are listed in JSON."))
    return "\n".join(rows) + "\n"


def write_reports(report: dict, *, target: str, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"prune-report-{target}.md"
    json_path = output_dir / f"prune-report-{target}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(_report_markdown(report, target), encoding="utf-8")
    return md_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--desktop", type=Path, required=True)
    parser.add_argument("--target", choices=VALID_TARGETS, required=True)
    parser.add_argument("--apply", action="store_true", help="Actually delete allowlisted files (default: dry-run)")
    parser.add_argument("--remove-opencv-video-codecs", action="store_true",
                        help="Windows only; remove the narrow cv2 FFmpeg video plugin allowlist")
    parser.add_argument("--output-dir", type=Path, default=Path("dist/build-size-audit"))
    args = parser.parse_args()
    if args.remove_opencv_video_codecs and args.target != "win-x64":
        parser.error("--remove-opencv-video-codecs is only supported for win-x64")
    # Keep generated reports outside the build outputs. In particular, do not
    # accidentally embed the report and a workstation's path in the installer.
    for root in (args.backend.resolve(), args.desktop.resolve()):
        output = args.output_dir.resolve()
        if output == root or output.is_relative_to(root):
            parser.error("--output-dir must be outside both release directories")
    report = prune(args.backend, args.desktop, apply=args.apply,
                   remove_opencv_video=args.remove_opencv_video_codecs)
    md, _ = write_reports(report, target=args.target, output_dir=args.output_dir)
    action = "removed" if args.apply else "would remove"
    print(f"SAFE PRUNING: {action} {report['candidate_files']} file(s) / {report['candidate_mib']:.2f} MiB (uncompressed)")
    print(f"Report: {md}")


if __name__ == "__main__":
    main()
