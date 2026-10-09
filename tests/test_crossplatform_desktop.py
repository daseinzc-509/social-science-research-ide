"""No remote credentials, external models, binary packagers, or user PDFs."""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sociology_research.api.desktop_entry import DesktopTokenMiddleware, token_is_valid
from sociology_research import user_storage


def load_relative(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_session_auth_covers_pdfs_settings_and_health():
    token = "f" * 64
    app = FastAPI()
    @app.get("/api/v1/health")
    def health(): return {"status": "ok"}
    @app.get("/api/v1/papers/123/pdf")
    def pdf(): return {"private": "research text"}
    @app.put("/api/v1/settings/models")
    def save(): return {"saved": True}
    app.add_middleware(DesktopTokenMiddleware, token=token)
    client = TestClient(app)
    for method, path in (
        ("get", "/api/v1/health"),
        ("get", "/api/v1/papers/123/pdf"),
        ("put", "/api/v1/settings/models"),
    ):
        assert getattr(client, method)(path).status_code == 401
        assert getattr(client, method)(path, headers={"X-SRA-Desktop-Token": "bad"}).status_code == 401
        assert getattr(client, method)(path, headers={"X-SRA-Desktop-Token": token}).status_code == 200
    assert not token_is_valid("secret")
    assert token_is_valid(token)


def test_mac_keychain_write_read_delete(monkeypatch, tmp_path):
    # Only a simulated Keychain; no real macOS Keychain or credentials involved.
    registry: dict[tuple[str, str], str] = {}
    class FakeKeyring:
        def get_password(self, service, name): return registry.get((service, name))
        def set_password(self, service, name, secret): registry[(service, name)] = secret
        def delete_password(self, service, name): registry.pop((service, name), None)
    monkeypatch.setattr(user_storage.sys, "platform", "darwin")
    monkeypatch.setattr(user_storage, "_mac_keyring", lambda: FakeKeyring())
    monkeypatch.setenv("SRA_HOME", str(tmp_path))
    user_storage.write_user_secrets({"SRA_LITE_API_KEY": "EXAMPLE_ONLY"})
    assert user_storage.read_user_secrets() == {"SRA_LITE_API_KEY": "EXAMPLE_ONLY"}
    assert list(tmp_path.rglob("*")) == []  # No plaintext secret file, even in test.
    user_storage.write_user_secrets({})
    assert user_storage.read_user_secrets() == {}


def test_macos_app_layout_keeps_backend_under_resources(tmp_path):
    module = load_relative("sra_mac_app", "installer/macos/package_app.py")
    desktop = tmp_path / "desktop"
    backend = tmp_path / "backend"
    desktop.mkdir()
    backend.mkdir()
    (desktop / "SRA.Desktop").write_bytes(b"desktop-mach-o-placeholder")
    (desktop / "SRA.Desktop.dll").write_bytes(b"fake managed payload")
    (backend / "sra-backend").write_bytes(b"backend-mach-o-placeholder")
    (backend / "_internal").mkdir()
    (backend / "_internal" / "test.txt").write_text("test")
    output = tmp_path / "SRA.app"
    result = module.package_app(desktop, backend, output, "0.2.0")
    assert (result / "Contents/MacOS/SRA.Desktop").is_file()
    assert (result / "Contents/Resources/backend/sra-backend/sra-backend").is_file()
    import plistlib
    with (result / "Contents/Info.plist").open("rb") as file:
        info = plistlib.load(file)
    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleShortVersionString"] == "0.2.0"


def test_windows_payload_contains_backend_not_private_data(tmp_path):
    module = load_relative("sra_win_app", "scripts/assemble_windows.py")
    desktop, backend = tmp_path / "desktop", tmp_path / "backend"
    desktop.mkdir(); backend.mkdir()
    (desktop / "SRA.Desktop.exe").write_bytes(b"binary-placeholder")
    (backend / "sra-backend.exe").write_bytes(b"binary-placeholder")
    target = module.assemble(desktop, backend, tmp_path / "installed")
    assert (target / "SRA.Desktop.exe").exists()
    assert (target / "backend/sra-backend/sra-backend.exe").exists()
    assert not (target / "data").exists()


def test_safety_gate_blocks_private_files_but_allows_embedded_library_assets(tmp_path):
    module = load_relative("sra_safety", "scripts/check_release_safety.py")
    bundle = tmp_path / "SRA.app"
    pkg = bundle / "Contents/Resources/backend/sra-backend/_internal/docling/data"
    pkg.mkdir(parents=True)
    (pkg / "model.json").write_text("{}")
    trusted_ca = bundle / "Contents/Resources/backend/sra-backend/_internal/certifi"
    trusted_ca.mkdir(parents=True)
    (trusted_ca / "cacert.pem").write_text("dummy public CA")
    assert module.check_artifact(bundle) == []
    private = bundle / "Contents/Resources/data"
    private.mkdir()
    (private / "my-private-note.txt").write_text("something")
    assert any("private runtime" in item for item in module.check_artifact(bundle))
    (private / "my-private-note.txt").unlink()
    private.rmdir()
    (bundle / "original.pdf").write_bytes(b"%PDF-private")
    assert module.check_artifact(bundle)


def test_release_versions_and_git_target_matrix():
    module = load_relative("sra_version", "scripts/release_version.py")
    assert module.parse("v0.2.0-alpha.1")["assembly"] == "0.2.0.0"
    assert module.parse("0.2.0")["tag"] == "v0.2.0"
    for wrong in ("dev", "1.0", "v1.2.3/bad"):
        with pytest.raises(ValueError): module.parse(wrong)
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    release = (ROOT / ".github/workflows/release-desktop.yml").read_text()
    for name in ("win-x64", "osx-arm64"):
        assert name in ci and name in release
    assert "osx-x64" not in ci and "osx-x64" not in release
    assert "macos-26-intel" not in ci and "macos-26-intel" not in release
    assert "scripts/smoke_backend.py" in release


def test_frozen_backend_build_profile_requires_docling(monkeypatch):
    module = load_relative("sra_build_cmd", "scripts/build_backend.py")
    monkeypatch.setattr(module, "available", lambda x: False)
    base = module.command_args("core")
    assert "--onedir" in base
    with pytest.raises(RuntimeError, match="docling"):
        module.command_args("full")


def test_release_asset_guard_is_bounded(tmp_path):
    module = load_relative("sra_sizes", "scripts/check_asset_sizes.py")
    sample = tmp_path / "SRA-alpha.dmg"
    sample.write_bytes(b"preview package bytes")
    assert module.validate(tmp_path, limit=1000) == []
    assert module.validate(tmp_path, limit=3)


def test_macos_arm_only_and_no_intel_fallback():
    workflow = (ROOT / ".github/workflows/release-desktop.yml").read_text(encoding="utf-8")
    assert "uname -m" in workflow and "arm64" in workflow
    assert "osx-arm64" in workflow
    assert "osx-x64" not in workflow
    launcher = (ROOT / "desktop/SRA.Desktop/Services/LocalBackendHost.cs").read_text(encoding="utf-8")
    assert 'info.Environment["SRA_PDF_PARSER"] = "pymupdf"' not in launcher
    packager = (ROOT / "installer/macos/package_app.py").read_text(encoding="utf-8")
    assert '"LSMinimumSystemVersion": "14.0"' in packager
