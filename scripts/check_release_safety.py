"""Fail CI before release if secrets, PDFs, or user databases enter a build.

Examples:
    python scripts/check_release_safety.py source .
    python scripts/check_release_safety.py artifact artifacts/win-x64
    python scripts/check_release_safety.py zip artifacts/SRA-Desktop-win-x64.zip

Paths are printed for failures, NEVER the contents of a potential secret.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable

# Only consider LITERAL assignment values, never source expressions such as
# `"SRA_LITE_API_KEY": current.lite_api_key` (which are not embedded secrets).
ENV_SECRET_ASSIGNMENT = re.compile(
    rb"(?im)^[ \t]*(?:export[ \t]+)?SRA_(?:LITE_|PRO_)?API_KEY[ \t]*=[ \t]*[\x22\x27]?([A-Za-z0-9_./+\-=]{20,})"
)
JSON_SECRET_ASSIGNMENT = re.compile(
    rb"(?im)^[ \t]*[\x22\x27]SRA_(?:LITE_|PRO_)?API_KEY[\x22\x27][ \t]*:[ \t]*[\x22\x27]([A-Za-z0-9_./+\-=]{20,})"
)
GENERIC_TOKEN = re.compile(rb'(?i)(?:sk-[A-Za-z0-9_\-]{24,}|AKLT[A-Za-z0-9]{20,})')
MAX_FILE_BYTES = 1024 * 1024 * 1024  # 1 GiB; memory usage remains bounded


def disallowed_path(name: str) -> str | None:
    path = PurePosixPath(name.replace("\\", "/"))
    parts = [part.lower() for part in path.parts if part not in {".", ""}]
    if not parts:
        return None
    filename = parts[-1]
    if filename == ".env.example":
        return None
    if filename == ".env" or filename.startswith(".env."):
        return "environment file"
    if filename.endswith((".pdf", ".sqlite", ".sqlite3", ".db")) or ".sqlite3-" in filename or ".db-" in filename:
        return "private PDF or database"
    # certifi's public CA trust bundle is needed for HTTPS in a frozen Python
    # runtime. It is not a user credential; don't blanket-allow *.pem.
    public_ca_bundle = len(parts) >= 2 and parts[-2:] == ["certifi", "cacert.pem"]
    if (filename.endswith((".dpapi", ".pem", ".p12", ".pfx", ".key")) and not public_ca_bundle) or filename in {"secrets.json", "credentials.json"}:
        return "credential storage"
    if any(part in {".conda-env", ".venv", ".conda-pkgs"} for part in parts):
        return "private runtime directory"
    # PyInstaller can legitimately embed third-party package resources such as
    # backend/sra-backend/_internal/docling/.../data. Only that specific subtree
    # is exempt; project-/app-level data directories remain forbidden.
    if "data" in parts:
        index = parts.index("data")
        frozen_package_data = "_internal" in parts[:index] and "backend" in parts[:index]
        if not frozen_package_data:
            return "private runtime directory"
    return None


def _binary_contains_token(reader: Iterable[bytes]) -> bool:
    tail = b""
    for chunk in reader:
        subject = tail + chunk
        if GENERIC_TOKEN.search(subject) or ENV_SECRET_ASSIGNMENT.search(subject) or JSON_SECRET_ASSIGNMENT.search(subject):
            return True
        tail = subject[-200:]
    return False


def _file_chunks(path: Path) -> Iterable[bytes]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Unexpectedly large file: {path}")
    with path.open("rb") as f:
        while part := f.read(1024 * 1024):
            yield part


def _zip_chunks(z: zipfile.ZipFile, member: zipfile.ZipInfo) -> Iterable[bytes]:
    if member.file_size > MAX_FILE_BYTES:
        raise ValueError(f"Unexpectedly large archived file: {member.filename}")
    with z.open(member) as f:
        while part := f.read(1024 * 1024):
            yield part


def _source_files(root: Path) -> list[Path]:
    # On CI, ALWAYS inspect exactly what Git tracks, not the entire developer disk.
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("git ls-files unavailable; refusing to pass source security gate")
    return [root / bytes_name.decode("utf-8", errors="replace") for bytes_name in result.stdout.split(b"\x00") if bytes_name]


def check_source(root: Path) -> list[str]:
    failures: list[str] = []
    for path in _source_files(root):
        name = str(path.relative_to(root)).replace("\\", "/")
        reason = disallowed_path(name)
        if reason:
            failures.append(f"{name}: {reason}")
            continue
        if path.is_symlink():
            failures.append(f"{name}: symlink in tracked source")
            continue
        if path.is_file() and _binary_contains_token(_file_chunks(path)):
            failures.append(f"{name}: suspected embedded API credential")
    return failures


def check_artifact(root: Path) -> list[str]:
    if not root.is_dir():
        raise FileNotFoundError(f"Publish directory missing: {root}")
    failures: list[str] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            # macOS .app bundles / Python frameworks contain internal symlinks.
            # Reject escapes and dangling links rather than rejecting every link.
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                failures.append(f"{path.relative_to(root)}: unsafe symlink")
            continue
        if not path.is_file():
            continue
        name = str(path.relative_to(root)).replace("\\", "/")
        reason = disallowed_path(name)
        if reason:
            failures.append(f"{name}: {reason}")
            continue
        if _binary_contains_token(_file_chunks(path)):
            failures.append(f"{name}: suspected embedded API credential")
    return failures


def check_zip(file: Path) -> list[str]:
    failures: list[str] = []
    if not file.is_file():
        raise FileNotFoundError(file)
    with zipfile.ZipFile(file) as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            name = member.filename
            reason = disallowed_path(name)
            if reason:
                failures.append(f"{name}: {reason}")
                continue
            if _binary_contains_token(_zip_chunks(z, member)):
                failures.append(f"{name}: suspected embedded API credential")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="SRA release privacy gate")
    parser.add_argument("mode", choices=("source", "artifact", "zip"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        path = args.path.resolve()
        failures = {"source": check_source, "artifact": check_artifact, "zip": check_zip}[args.mode](path)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"RELEASE SAFETY GATE: ERROR: {exc}", file=sys.stderr)
        return 2
    if failures:
        print(f"RELEASE SAFETY GATE: FAILED ({len(failures)} issue(s))", file=sys.stderr)
        for finding in failures:
            print(f"  BLOCKED: {finding}", file=sys.stderr)
        return 1
    print(f"RELEASE SAFETY GATE: PASSED ({args.mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
