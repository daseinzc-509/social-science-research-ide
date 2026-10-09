"""No external API calls or genuine user files; regression tests for privacy gates."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

from sociology_research import config, user_storage

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_safety.py"
spec = importlib.util.spec_from_file_location("sra_release_safety", SCRIPT)
assert spec and spec.loader
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


def _fake_crypto(monkeypatch):
    # CI runners aren't necessarily Windows. DPAPI is not mocked in production.
    # These cases exercise Windows DPAPI storage on Linux CI. Since macOS
    # Keychain now has a separate backend, explicitly simulate Windows.
    monkeypatch.setattr(user_storage.sys, "platform", "win32")
    monkeypatch.setattr(user_storage, "_dpapi_protect", lambda b: b"ENCRYPTED:" + base64.b64encode(b))
    monkeypatch.setattr(user_storage, "_dpapi_unprotect", lambda b: base64.b64decode(b.removeprefix(b"ENCRYPTED:")))


def _clear_model_env(monkeypatch):
    for name in config._MODEL_FIELDS:
        monkeypatch.delenv(name, raising=False)


def test_user_paths_and_legacy_library_compatibility(tmp_path, monkeypatch):
    home = tmp_path / "private-root"
    monkeypatch.setenv("SRA_HOME", str(home))
    monkeypatch.delenv("SRA_DATA_DIR", raising=False)
    legacy = tmp_path / "project" / "data"
    legacy.mkdir(parents=True)
    (legacy / "research.sqlite3").write_bytes(b"legacy")
    assert user_storage.resolve_data_dir(legacy_project_data=legacy) == legacy
    assert user_storage.resolve_data_dir(legacy_project_data=None) == home / "data"
    (home / "data").mkdir(parents=True)
    (home / "data" / "research.sqlite3").write_bytes(b"migrated")
    assert user_storage.resolve_data_dir(legacy_project_data=legacy) == home / "data"
    alternate = tmp_path / "override"
    monkeypatch.setenv("SRA_DATA_DIR", str(alternate))
    assert user_storage.resolve_data_dir(legacy_project_data=legacy) == alternate


def test_settings_encrypted_per_user_and_legacy_scrub(tmp_path, monkeypatch):
    _fake_crypto(monkeypatch)
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("SRA_HOME", str(tmp_path / "UserData"))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(config, "_PROJECT_ROOT", project)
    example_key = "EXAMPLE_" + "X" * 34
    legacy = project / ".env"
    legacy.write_text(f"# local developer config\nSRA_API_KEY={example_key}\nSRA_DATA_DIR=./data\n", encoding="utf-8")
    assert config.Settings.from_environment().api_key == example_key
    config.save_local_environment({
        "SRA_LITE_API_KEY": example_key,
        "SRA_PRO_API_KEY": example_key,
        "SRA_LITE_MODEL": "test-lite",
        "SRA_PRO_MODEL": "test-pro",
        "SRA_API_KEY": None,
    })
    preferences = user_storage.user_config_dir() / "model-settings.json"
    encrypted = user_storage.user_config_dir() / "model-keys.dpapi"
    assert preferences.is_file() and encrypted.is_file()
    assert example_key.encode() not in encrypted.read_bytes()
    assert example_key not in preferences.read_text(encoding="utf-8")
    assert example_key not in legacy.read_text(encoding="utf-8")
    assert "SRA_DATA_DIR=./data" in legacy.read_text(encoding="utf-8")
    assert config.Settings.from_environment().effective_pro_api_key == example_key
    # A new run can recover from DPAPI without any process model env.
    _clear_model_env(monkeypatch)
    assert config.Settings.from_environment().lite_model == "test-lite"
    assert config.Settings.from_environment().effective_lite_api_key == example_key


def test_legacy_migration_preview_does_not_write(tmp_path, monkeypatch):
    _fake_crypto(monkeypatch)
    _clear_model_env(monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(config, "_PROJECT_ROOT", project)
    monkeypatch.setenv("SRA_HOME", str(tmp_path / "UserData"))
    (project / ".env").write_text("SRA_API_KEY=" + "T" * 35 + "\n", encoding="utf-8")
    preview = config.migrate_legacy_model_config()
    assert preview["applied"] is False
    assert not user_storage.user_config_dir().exists()
    result = config.migrate_legacy_model_config(apply=True)
    assert result["applied"] is True
    assert "SRA_API_KEY=" not in (project / ".env").read_text(encoding="utf-8")
    assert config.Settings.from_environment().api_key == "T" * 35


def test_unencrypted_key_persistence_refused_off_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_HOME", str(tmp_path))
    monkeypatch.setattr(user_storage.sys, "platform", "linux")
    with pytest.raises(RuntimeError):
        user_storage.write_user_secrets({"SRA_LITE_API_KEY": "test-only-key"})
    assert not (tmp_path / "config" / "model-keys.dpapi").exists()


def _sample_library(path: Path) -> Path:
    path.mkdir(parents=True)
    pdf = path / "papers" / "sample.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.7 example fake document")
    database = path / "research.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE papers (id TEXT PRIMARY KEY, stored_path TEXT NOT NULL)")
        conn.execute("INSERT INTO papers VALUES (?, ?)", ("paper-test", str(pdf.resolve())))
    return pdf


def test_data_migration_copies_and_rewrites_paths_without_deleting_source(tmp_path):
    src = tmp_path / "old-data"
    pdf = _sample_library(src)
    dst = tmp_path / "new-data"
    preview = user_storage.migrate_user_data(src, dst)
    assert preview["paper_count"] == 1 and preview["applied"] is False
    assert not dst.exists()
    result = user_storage.migrate_user_data(src, dst, apply=True)
    assert result["applied"] is True
    assert pdf.is_file()
    assert (dst / "papers" / "sample.pdf").is_file()
    with sqlite3.connect(dst / "research.sqlite3") as conn:
        rewritten, = conn.execute("SELECT stored_path FROM papers WHERE id='paper-test'").fetchone()
    assert Path(rewritten) == dst / "papers" / "sample.pdf"
    with pytest.raises(FileExistsError):
        user_storage.migrate_user_data(src, dst, apply=True)


def test_data_migration_fails_cleanly_when_pdf_missing(tmp_path):
    src = tmp_path / "old"
    pdf = _sample_library(src)
    pdf.unlink()
    dst = tmp_path / "new"
    with pytest.raises(ValueError):
        user_storage.migrate_user_data(src, dst, apply=True)
    assert not dst.exists()


def test_release_gate_blocks_private_documents_and_secret_names(tmp_path):
    dist = tmp_path / "publish"
    dist.mkdir()
    (dist / "SRA.Desktop.exe").write_bytes(b"normal executable placeholder")
    assert safety.check_artifact(dist) == []
    (dist / "private.pdf").write_bytes(b"%PDF-1.7")
    assert any("private.pdf" in issue for issue in safety.check_artifact(dist))
    (dist / "private.pdf").unlink()
    (dist / ".env").write_text("SRA_API_KEY=" + "T" * 35, encoding="utf-8")
    assert any(".env" in issue for issue in safety.check_artifact(dist))
    (dist / ".env").unlink()
    (dist / "settings.txt").write_text("SRA_LITE_API_KEY=" + "T" * 35, encoding="utf-8")
    assert any("suspected embedded" in issue for issue in safety.check_artifact(dist))
    (dist / "settings.txt").unlink()
    with zipfile.ZipFile(tmp_path / "release.zip", "w") as z:
        z.writestr("SRA.Desktop.exe", b"safe")
        z.writestr("data/research.sqlite3", b"sqlite")
    assert safety.check_zip(tmp_path / "release.zip")


def test_source_gate_checks_tracked_files(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("TESTONLY=1")
    monkeypatch.setattr(safety, "_source_files", lambda root: [root / ".env"])
    assert any("environment file" in x for x in safety.check_source(tmp_path))
    (tmp_path / ".env").unlink()
    source = tmp_path / "module.py"
    source.write_text('MODEL = "sk-" + "A" * 32')
    monkeypatch.setattr(safety, "_source_files", lambda root: [source])
    assert safety.check_source(tmp_path) == []


def test_data_migration_closes_sqlite_handles_before_rename(tmp_path, monkeypatch):
    """Avoid Windows WinError 5 / WinError 32 when renaming the staged directory."""
    import types
    from pathlib import Path

    source = tmp_path / "old"
    _sample_library(source)
    target = tmp_path / "new"
    original_connect = sqlite3.connect
    trackers = []

    class TrackedConnection:
        def __init__(self, path):
            self.inner = original_connect(path)
            self.closed = False
            trackers.append(self)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def backup(self, target, *args, **kwargs):
            return self.inner.backup(target.inner, *args, **kwargs)

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

        def close(self):
            self.closed = True
            self.inner.close()

    monkeypatch.setattr(user_storage, "sqlite3", types.SimpleNamespace(connect=TrackedConnection))
    original_rename = Path.rename

    def rename_only_when_closed(path, dest):
        assert len(trackers) >= 3
        assert all(item.closed for item in trackers), "A SQLite connection is still open at rename"
        return original_rename(path, dest)

    monkeypatch.setattr(Path, "rename", rename_only_when_closed)
    user_storage.migrate_user_data(source, target, apply=True)
    assert (target / "research.sqlite3").exists()
