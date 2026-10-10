"""Release pruning must never remove runtime libraries or touch user data."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.prune_release_assets import prune, write_reports

ROOT = Path(__file__).resolve().parents[1]


def release_trees(tmp_path: Path) -> tuple[Path, Path]:
    backend = tmp_path / "dist" / "backend" / "sra-backend"
    desktop = tmp_path / "dist" / "desktop"
    backend.mkdir(parents=True)
    desktop.mkdir(parents=True)
    (backend / "sra-backend.exe").write_bytes(b"backend-executable")
    (desktop / "SRA.Desktop.exe").write_bytes(b"desktop-executable")
    return backend, desktop


def put(root: Path, filename: str, size: int = 64) -> Path:
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_preview_only_and_apply_exact_allowlist(tmp_path):
    backend, desktop = release_trees(tmp_path)
    pdb = put(desktop, "libSkiaSharp.pdb", 8)
    lib = put(backend, "_internal/pymupdf/mupdf-devel/lib/mupdfcpp64.lib", 9)
    header = put(backend, "_internal/pymupdf/mupdf-devel/include/mupdf.h", 2)
    keep = [
        put(backend, "_internal/torch/lib/torch_cpu.dll", 10),
        put(backend, "_internal/cv2/cv2.pyd", 10),
        put(backend, "_internal/pymupdf/mupdfcpp64.dll", 10),
        put(backend, "_internal/pymupdf/mupdf-devel/lib/needed.dll", 10),
        put(backend, "_internal/unrelated/mupdf-devel/dev.lib", 10),
        put(backend, "_internal/pymupdf/mupdf-devel/doc/release.pdf", 10),
        put(desktop, "System.Private.CoreLib.dll", 10),
    ]
    preview = prune(backend, desktop)
    assert not preview["applied"]
    assert preview["candidate_files"] == 3
    assert preview["candidate_bytes"] == 19
    assert all(path.exists() for path in [pdb, lib, header, *keep])
    report = prune(backend, desktop, apply=True)
    assert report["applied"] and report["removed_files"] == 3
    assert not any(path.exists() for path in (pdb, lib, header))
    assert all(path.exists() for path in keep)
    assert prune(backend, desktop, apply=True)["removed_files"] == 0


def test_dev_files_only_pruned_inside_pymupdf(tmp_path):
    backend, desktop = release_trees(tmp_path)
    for file in ("_internal/pymupdf/mupdf-devel/x.a",
                 "_internal/pymupdf/mupdf-devel/x.HPP",
                 "_internal/pymupdf/mupdf-devel/x.hxx"):
        put(backend, file)
    put(backend, "_internal/pymupdf/lib/a.lib")
    put(backend, "_internal/pymupdf/mupdf-devel/runtime.pyd")
    put(desktop, "symbols.PDB")
    result = prune(backend, desktop, apply=True)
    assert result["candidate_files"] == 4
    assert (backend / "_internal/pymupdf/lib/a.lib").exists()
    assert (backend / "_internal/pymupdf/mupdf-devel/runtime.pyd").exists()


def test_bad_roots_are_rejected_without_deleting(tmp_path):
    backend, desktop = release_trees(tmp_path)
    protected = put(tmp_path, "data/actual-research.sqlite3")
    assert protected.is_file()
    with pytest.raises(ValueError, match="not a frozen SRA release output"):
        prune(tmp_path, desktop, apply=True)
    nested = backend / "_internal"
    put(nested, "SRA.Desktop.exe")
    with pytest.raises(ValueError, match="independent"):
        prune(backend, nested, apply=True)
    assert protected.exists()


def test_report_is_readable_and_contains_exact_bytes(tmp_path):
    backend, desktop = release_trees(tmp_path)
    put(desktop, "libHarfBuzzSharp.pdb", 2048)
    result = prune(backend, desktop, apply=True)
    md, js = write_reports(result, target="win-x64", output_dir=tmp_path / "reports")
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["candidate_bytes"] == 2048
    assert "libHarfBuzzSharp.pdb" in md.read_text(encoding="utf-8")
    assert "dry-run" not in md.read_text(encoding="utf-8")


def test_cli_runs_in_preview_by_default(tmp_path):
    backend, desktop = release_trees(tmp_path)
    put(desktop, "libSkiaSharp.pdb", 100)
    report_dir = tmp_path / "audit"
    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts/prune_release_assets.py"),
         "--target", "win-x64", "--backend", str(backend),
         "--desktop", str(desktop), "--output-dir", str(report_dir)],
        capture_output=True, text=True, check=True,
    )
    assert "would remove" in process.stdout
    assert (desktop / "libSkiaSharp.pdb").exists()
    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts/prune_release_assets.py"),
         "--target", "win-x64", "--backend", str(backend),
         "--desktop", str(desktop), "--output-dir", str(report_dir), "--apply"],
        capture_output=True, text=True, check=True,
    )
    assert "removed 1" in process.stdout
    assert not (desktop / "libSkiaSharp.pdb").exists()


def test_cannot_write_report_inside_release_payload(tmp_path):
    backend, desktop = release_trees(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/prune_release_assets.py"),
         "--target", "win-x64", "--backend", str(backend),
         "--desktop", str(desktop), "--output-dir", str(desktop / "reports"), "--apply"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests may require Windows developer mode")
def test_symlinked_candidate_is_not_deleted_or_followed(tmp_path):
    backend, desktop = release_trees(tmp_path)
    outside = put(tmp_path, "outside.pdb", 20)
    (desktop / "unexpected.pdb").symlink_to(outside)
    result = prune(backend, desktop, apply=True)
    assert result["candidate_files"] == 0
    assert outside.exists()
    assert (desktop / "unexpected.pdb").is_symlink()


def test_workflows_prune_before_assembly_and_then_smoke():
    release = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert release.index("--output-dir dist/build-size-audit --apply") < release.index("Assemble complete Windows payload")
    assert release.index("--output-dir dist/build-size-audit --apply") < release.index("package_app.py")
    assert release.index("--output-dir dist/build-size-audit --apply") < release.index("scripts/smoke_backend.py")
    assert "scripts/check_release_safety.py artifact" in release
    assert "tests/test_release_pruning.py" in ci
