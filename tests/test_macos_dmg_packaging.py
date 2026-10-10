"""Offline DMG retry/fallback policy tests, runnable on Windows and Linux CI.

These tests mock macOS tools. Only a real Apple Silicon runner can validate
hdiutil's native disk-image operations and mount/unmount behavior.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import create_macos_dmg as dmg


def _source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "dmg-root"
    (source / "SRA.app" / "Contents" / "MacOS").mkdir(parents=True)
    (source / "SRA.app" / "Contents" / "MacOS" / "SRA.Desktop").write_bytes(b"desktop")
    (source / "Remove_SRA_User_Data.command").write_text("#!/bin/sh\n", encoding="utf-8")
    try:
        (source / "Applications").symlink_to("/Applications", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks need elevated privileges or developer mode here")
    return source, tmp_path / "SRA-osx-arm64-v0.2.0-alpha.2.dmg"


def _busy(argv: list[str]) -> dmg.DmgCommandError:
    return dmg.DmgCommandError(argv, subprocess.CompletedProcess(argv, 1, "", "hdiutil: create failed - Resource busy"))


def test_standard_creation_verifies_then_publishes(tmp_path, monkeypatch):
    source, final = _source(tmp_path)
    commands = []

    def fake_run(argv):
        commands.append(argv)
        if argv[:2] == ["hdiutil", "create"]:
            Path(argv[-1]).write_bytes(b"validated-DMG")
        return ""

    monkeypatch.setattr(dmg, "run", fake_run)
    assert dmg.create_dmg(source, final, wait=0) == final
    assert final.read_bytes() == b"validated-DMG"
    assert commands[0][:2] == ["hdiutil", "create"]
    assert "-srcfolder" in commands[0] and "-format" in commands[0]
    assert commands[-1][:2] == ["hdiutil", "verify"]
    assert "ditto" not in [cmd[0] for cmd in commands]


def test_transient_resource_busy_retries_with_unique_image(tmp_path, monkeypatch):
    source, final = _source(tmp_path)
    commands = []
    count = 0

    def fake_run(argv):
        nonlocal count
        commands.append(argv)
        if argv[:2] == ["hdiutil", "create"]:
            count += 1
            if count <= 2:
                Path(argv[-1]).write_bytes(b"partial")
                raise _busy(argv)
            Path(argv[-1]).write_bytes(b"complete")
        return ""

    monkeypatch.setattr(dmg, "run", fake_run)
    dmg.create_dmg(source, final, retries=4, wait=0)
    assert count == 3
    assert final.read_bytes() == b"complete"
    assert len({cmd[-1] for cmd in commands if cmd[:2] == ["hdiutil", "create"]}) == 3
    assert "ditto" not in [cmd[0] for cmd in commands]


def test_persistent_resource_busy_uses_private_rw_image(tmp_path, monkeypatch):
    source, final = _source(tmp_path)
    commands = []
    failed_detach = False

    def fake_run(argv):
        nonlocal failed_detach
        commands.append(argv)
        if argv[:2] == ["hdiutil", "create"]:
            if "-srcfolder" in argv:
                raise _busy(argv)
            Path(argv[-1]).write_bytes(b"rw-dmg")
        elif argv[:2] == ["hdiutil", "detach"] and not failed_detach:
            failed_detach = True
            raise _busy(argv)
        elif argv[:2] == ["hdiutil", "convert"]:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"fallback-compressed")
        return ""

    monkeypatch.setattr(dmg, "run", fake_run)
    dmg.create_dmg(source, final, retries=3, wait=0)
    assert final.read_bytes() == b"fallback-compressed"
    assert len([x for x in commands if x[:2] == ["hdiutil", "create"] and "-srcfolder" in x]) == 3
    assert any("-format" in x and "UDRW" in x for x in commands)
    assert [x[0] for x in commands].count("ditto") == 1
    assert [x[0] for x in commands].count("sync") == 1
    assert [x[:2] for x in commands].count(["hdiutil", "detach"]) == 2
    assert any(x[:2] == ["hdiutil", "convert"] for x in commands)
    assert not any(x[:3] == ["hdiutil", "detach", "-force"] for x in commands)


def test_does_not_retry_unrelated_hdiutil_failures_or_publish_partial_dmg(tmp_path, monkeypatch):
    source, final = _source(tmp_path)
    commands = []

    def fake_run(argv):
        commands.append(argv)
        raise dmg.DmgCommandError(argv, subprocess.CompletedProcess(argv, 1, "", "hdiutil: no space left on device"))

    monkeypatch.setattr(dmg, "run", fake_run)
    with pytest.raises(dmg.DmgCommandError, match="no space left"):
        dmg.create_dmg(source, final, retries=4, wait=0)
    assert len(commands) == 1
    assert not final.exists()


def test_wont_overwrite_or_write_into_user_source(tmp_path, monkeypatch):
    source, final = _source(tmp_path)
    final.write_bytes(b"existing-installer")
    with pytest.raises(FileExistsError):
        dmg.create_dmg(source, final, wait=0)
    with pytest.raises(ValueError, match="inside the source"):
        dmg.create_dmg(source, source / "bad.dmg", wait=0)
    assert final.read_bytes() == b"existing-installer"


def test_private_image_capacity_does_not_follow_applications_symlink(tmp_path):
    source, final = _source(tmp_path)
    assert 256 <= dmg._capacity_megabytes(source) < 1024


def test_workflow_replaces_single_hdiutil_with_wrapper():
    root = Path(__file__).resolve().parents[1]
    release = (root / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "python scripts/create_macos_dmg.py" in release
    assert "hdiutil create -volname" not in release  # old fragile inline pipeline must be gone
    assert release.index("scripts/check_release_safety.py artifact dist/SRA.app") < release.index("python scripts/create_macos_dmg.py")
    assert release.index("python scripts/create_macos_dmg.py") < release.index("python scripts/report_build_sizes.py")
    assert "tests/test_macos_dmg_packaging.py" in ci
