"""Offline regression tests for the Mac release privacy-gate false positives.

Uses synthetic library contents only; no real credentials, PDFs or user data.
Run: python -m pytest -q tests/test_release_safety_false_positives.py
"""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_safety.py"
spec = importlib.util.spec_from_file_location("sra_release_gate_fixture", SCRIPT)
assert spec is not None and spec.loader is not None
safety = importlib.util.module_from_spec(spec)
spec.loader.exec_module(safety)


# The b64url digest contains a *simulated* sk- prefix in the middle of a hash.
FAKE_DIGEST = b"abcdefgsk-" + b"x" * 33
assert len(FAKE_DIGEST) == 43
FAKE_WHEEL_RECORD = b"docling/example.py,sha256=" + FAKE_DIGEST + b",1024\n"


def _bundle(root: Path) -> tuple[Path, Path]:
    app = root / "SRA.app"
    library = app / "Contents" / "MacOS" / "System.Private.CoreLib.dll"
    record = app / "Contents" / "Resources" / "backend" / "sra-backend" / "_internal" / "docling_parse-2.134.0.dist-info" / "RECORD"
    library.parent.mkdir(parents=True, exist_ok=True)
    record.parent.mkdir(parents=True, exist_ok=True)
    # Synthetic binary contains a false-looking standalone `sk-...` substring.
    library.write_bytes(b"\x00\x01compiled-strings/x" + b"sk-" + b"z" * 32 + b"\x00\x02")
    record.write_bytes(FAKE_WHEEL_RECORD)
    return library, record


def test_bundled_runtime_and_wheel_record_are_not_credentials(tmp_path: Path):
    app = tmp_path / "SRA.app"
    _bundle(tmp_path)
    assert safety.check_artifact(app) == []


def test_records_still_scan_filenames_not_sha256_hashes(tmp_path: Path):
    app = tmp_path / "SRA.app"
    _, record = _bundle(tmp_path)
    secret_shaped_filename = "sk-" + "Z" * 32
    record.write_text(f"{secret_shaped_filename}.py,sha256={FAKE_DIGEST.decode()},15\n", encoding="utf-8")
    assert any("suspected embedded" in issue for issue in safety.check_artifact(app))


def test_explicit_key_assignment_still_blocks_in_binary_library(tmp_path: Path):
    app = tmp_path / "SRA.app"
    library, _ = _bundle(tmp_path)
    library.write_bytes(b"MZ\x00binary\nSRA_PRO_API_KEY=" + b"K" * 40 + b"\n")
    assert any("System.Private.CoreLib.dll" in issue for issue in safety.check_artifact(app))


def test_text_key_and_provider_tokens_still_block(tmp_path: Path):
    app = tmp_path / "SRA.app"
    _bundle(tmp_path)
    settings = app / "Contents" / "Resources" / "settings.json"
    settings.write_text('{"SRA_LITE_API_KEY": "' + 'A' * 40 + '"}', encoding="utf-8")
    assert any("settings.json" in issue for issue in safety.check_artifact(app))
    settings.write_text('provider_token = "sk-' + 'T' * 32 + '"', encoding="utf-8")
    assert any("settings.json" in issue for issue in safety.check_artifact(app))


def test_private_pdf_env_and_database_still_block(tmp_path: Path):
    app = tmp_path / "SRA.app"
    _bundle(tmp_path)
    for name in (".env", "private.pdf", "research.sqlite3", "model-keys.dpapi"):
        suspicious = app / "Contents" / "Resources" / name
        suspicious.write_bytes(b"test-only")
        assert any(name in issue for issue in safety.check_artifact(app))
        suspicious.unlink()


def test_zip_scans_contents_and_never_mistakes_wheel_hash(tmp_path: Path):
    app = tmp_path / "SRA.app"
    _bundle(tmp_path)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as dest:
        for f in app.rglob("*"):
            if f.is_file():
                dest.write(f, f.relative_to(tmp_path).as_posix())
    assert safety.check_zip(archive) == []
    with zipfile.ZipFile(archive, "a") as dest:
        dest.writestr("SRA.app/Contents/Resources/.env", b"SRA_API_KEY=" + b"X" * 30)
    assert any(".env" in issue for issue in safety.check_zip(archive))


def test_plain_text_without_separator_does_not_match_random_hash():
    # Full provider-style tokens are bounded: inside an alphanumeric digest,
    # `sk-...` is not a standalone token.
    assert not safety._binary_contains_token([b"sha256=abcdefghijkl" + b"sk-" + b"A" * 32], "hashes.txt")
    assert safety._binary_contains_token([b"key = sk-" + b"A" * 32 + b"\n"], "settings.txt")


def test_missing_record_digest_must_not_hide_other_assignments():
    content = [b"foo.py,SRA_API_KEY=" + b"T" * 40 + b",19\n"]
    assert safety._binary_contains_token(content, "pkg.dist-info/RECORD")
    # Assignments in a true config file remain blocked.
    assert safety._binary_contains_token([b"SRA_API_KEY=" + b"T" * 40 + b"\n"], "cfg.txt")


def _windows_bundle(root: Path) -> Path:
    """Exactly the Windows paths reported by the release job."""
    app = root / "SRA-Windows"
    runtime = app / "System.Private.CoreLib.dll"
    torch_record = app / "backend" / "sra-backend" / "_internal" / "torch-2.14.1.dist-info" / "RECORD"
    transformers_record = app / "backend" / "sra-backend" / "_internal" / "transformers-5.19.0.dist-info" / "RECORD"
    for file in (runtime, torch_record, transformers_record):
        file.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"MZ\x00dotnet runtime text/" + b"sk-" + b"Q" * 32 + b"\x00")
    torch_record.write_bytes(b"torch/_tensor.py,sha256=" + FAKE_DIGEST + b",42\n")
    transformers_record.write_bytes(b"transformers/utils.py,sha256=" + FAKE_DIGEST + b",43\n")
    return app


def test_windows_bundle_runtime_and_wheel_record_false_positives(tmp_path: Path):
    app = _windows_bundle(tmp_path)
    assert safety.check_artifact(app) == []


def test_windows_bundle_zip_matches_directory_safety_gate(tmp_path: Path):
    app = _windows_bundle(tmp_path)
    zip_path = tmp_path / "SRA-win-x64-v0.2.0-alpha.1.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file in app.rglob("*"):
            if file.is_file():
                archive.write(file, file.relative_to(app).as_posix())
    assert safety.check_zip(zip_path) == []
    # The gate stays effective against actual explicit credentials.
    credential_file = app / "settings.json"
    credential_file.write_text('{"SRA_PRO_API_KEY": "' + 'K' * 36 + '"}', encoding="utf-8")
    assert any("settings.json" in finding for finding in safety.check_artifact(app))
    with zipfile.ZipFile(zip_path, "a") as archive:
        archive.write(credential_file, "settings.json")
    assert any("settings.json" in finding for finding in safety.check_zip(zip_path))
