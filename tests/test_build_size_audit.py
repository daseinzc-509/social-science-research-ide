"""Regression tests for the platform-neutral release size audit.

Only synthetic build outputs are used. No PyInstaller, .NET SDK or model downloads.
"""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("sra_report_build_sizes", ROOT / "scripts" / "report_build_sizes.py")
assert SPEC and SPEC.loader
import sys
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def _file(path: Path, bytes_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as writer:
        writer.truncate(bytes_count)


def _fixture(tmp_path: Path):
    backend, desktop, payload = [tmp_path / name for name in ("backend", "desktop", "payload")]
    _file(backend / "_internal" / "torch" / "lib" / "torch_cpu.dll", 200)
    _file(backend / "_internal" / "docling" / "parse.pyc", 300)
    _file(backend / "_internal" / "transformers" / "models" / "a.pyc", 400)
    _file(backend / "_internal" / "numpy" / "core" / "numpy.lib", 500)
    _file(backend / "python312.dll", 60)
    _file(backend / "_internal" / "others" / "runtime.dll", 40)
    _file(desktop / "SRA.Desktop.exe", 140)
    _file(desktop / "System.Private.CoreLib.dll", 180)
    _file(payload / "backend" / "sra-backend" / "library.dll", 1500)
    _file(payload / "SRA.Desktop.exe", 500)
    assets = tmp_path / "dist"
    _file(assets / "SRA-Desktop-Setup-win-x64-v0.2.0-alpha.1.exe", 600)
    _file(assets / "SRA-win-x64-v0.2.0-alpha.1.zip", 700)
    _file(assets / "private.pdf", 900)
    return backend, desktop, payload, assets


def _report(tmp_path: Path):
    backend, desktop, payload, assets = _fixture(tmp_path)
    out = tmp_path / "reports"
    result = module.make_report(
        target="win-x64", profile="full", backend=backend, desktop=desktop, payload=payload,
        asset_globs=[str(assets / "SRA-Desktop-Setup-*.exe"), str(assets / "SRA-win-*.zip")], output_dir=out
    )
    return result, out


def test_backend_family_sizes_are_exclusive_and_sum_to_total(tmp_path):
    result, _ = _report(tmp_path)
    categories = result["backend_categories_bytes"]
    assert result["frozen_backend_bytes"] == 1500
    assert categories["PyTorch family"] == 200
    assert categories["Docling family"] == 300
    assert categories["Transformers / Hugging Face"] == 400
    assert categories["NumPy / scientific stack"] == 500
    assert categories["Python runtime"] == 60
    assert categories["Other backend files"] == 40
    assert sum(categories.values()) == result["frozen_backend_bytes"]
    assert result["desktop_publish_bytes"] == 320
    assert result["assembled_payload_bytes"] == 2000


def test_report_writes_markdown_json_and_all_file_csv(tmp_path):
    result, out = _report(tmp_path)
    expected = [out / f"build-size-win-x64.{ext}" for ext in ("md", "json")]
    expected.append(out / "build-size-win-x64-files.csv")
    assert all(file.exists() for file in expected)
    saved = json.loads(expected[1].read_text(encoding="utf-8"))
    assert saved == result
    md = expected[0].read_text(encoding="utf-8")
    assert "**Do not sum these three rows:**" in md
    assert "package-family sizes are inferred" in md
    assert "Setup" in md
    with expected[2].open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 8
    assert rows[0]["size_bytes"] == "500"
    assert all("tmp" not in row["relative_path"] for row in rows)
    assert all("private.pdf" not in str(row) for row in rows)


def test_asset_glob_must_match_installer_or_archive(tmp_path):
    backend, desktop, payload, assets = _fixture(tmp_path)
    with pytest.raises(FileNotFoundError, match="No packaged assets matched"):
        module.make_report(
            target="win-x64", profile="core", backend=backend, desktop=desktop,
            payload=payload, asset_globs=[str(assets / "*.dmg")], output_dir=tmp_path / "report"
        )


def test_missing_uncompressed_output_fails_loudly(tmp_path):
    backend, desktop, payload, assets = _fixture(tmp_path)
    with pytest.raises(FileNotFoundError, match="Missing release directory"):
        module.make_report(
            target="win-x64", profile="core", backend=backend, desktop=desktop,
            payload=tmp_path / "missing-payload", asset_globs=[str(assets / "*.exe")],
            output_dir=tmp_path / "report"
        )


def test_no_symlink_double_count_or_recursive_follow(tmp_path):
    backend, desktop, payload, assets = _fixture(tmp_path)
    try:
        (backend / "_internal" / "duplicate").symlink_to(backend / "_internal" / "torch", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable for this user or OS")
    result = module.make_report(
        target="win-x64", profile="full", backend=backend, desktop=desktop,
        payload=payload, asset_globs=[str(assets / "*.exe")], output_dir=tmp_path / "report"
    )
    assert result["frozen_backend_bytes"] == 1500


def test_no_output_inside_measured_directory(tmp_path):
    backend, desktop, payload, assets = _fixture(tmp_path)
    with pytest.raises(ValueError, match="Output directory must not be inside"):
        module.make_report(
            target="win-x64", profile="full", backend=backend, desktop=desktop,
            payload=payload, asset_globs=[str(assets / "*.exe")], output_dir=backend / "report"
        )


def test_workflow_uploads_small_report_without_releasing_it():
    workflow = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python scripts/report_build_sizes.py" in workflow
    assert "SRA-build-size-audit-${{ matrix.target }}" in workflow
    assert "cat \"dist/build-size-audit" in workflow
    assert "pattern: SRA-bundle-*" in workflow  # reports are NOT added to public installer assets
    assert "tests/test_build_size_audit.py" in ci
    assert "osx-x64" not in workflow


def test_mac_dmg_payload_and_relative_names(tmp_path):
    backend, desktop, _, assets = _fixture(tmp_path)
    mac = tmp_path / "SRA.app"
    _file(mac / "Contents" / "MacOS" / "SRA.Desktop", 170)
    _file(mac / "Contents" / "Resources" / "backend" / "sra-backend", 200)
    _file(assets / "SRA-osx-arm64-v0.2.0-alpha.1.dmg", 240)
    result = module.make_report(
        target="osx-arm64", profile="full", backend=backend, desktop=desktop,
        payload=mac, asset_globs=[str(assets / "*.dmg")], output_dir=tmp_path / "mac-reports"
    )
    assert result["assembled_payload_bytes"] == 370
    assert list(result["assets_bytes"].values()) == [240]
    assert result["top_30_files"][0]["path"] == "_internal/numpy/core/numpy.lib"
