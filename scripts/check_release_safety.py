"""Fail CI before release if secrets, PDFs, or user databases enter a build.

Examples:
    python scripts/check_release_safety.py source .
    python scripts/check_release_safety.py artifact artifacts/win-x64
    python scripts/check_release_safety.py zip artifacts/SRA-Desktop-win-x64.zip

Paths are printed for failures, NEVER the contents of a potential secret.
"""
from __future__ import annotations

import argparse
from itertools import chain
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
    rb"(?i)[\x22\x27]SRA_(?:LITE_|PRO_)?API_KEY[\x22\x27][ \t]*:[ \t]*[\x22\x27]([A-Za-z0-9_./+\-=]{20,})"
)
# A provider token must start at a lexical boundary. Scanning for `sk-` anywhere
# in raw bytes flags unrelated base64 hashes and compiled .NET assemblies.
# Also detect credential assignments embedded within a configuration line,
# not only at the beginning of a .env file. In particular, a wheel RECORD with
# a forged non-digest second field must *not* evade the scan.
INLINE_SECRET_ASSIGNMENT = re.compile(
    rb"(?i)(?<![A-Za-z0-9_])SRA_(?:LITE_|PRO_)?API_KEY[ \t]*=[ \t]*[\x22\x27]?([A-Za-z0-9_./+\-=]{20,})"
)
GENERIC_TOKEN = re.compile(
    rb"(?i)(?<![A-Za-z0-9_\-])(?:sk-[A-Za-z0-9_\-]{24,}|AKLT[A-Za-z0-9]{20,})(?![A-Za-z0-9_\-])"
)
# Binary runtime assets can include coincidental token-looking byte sequences.
# We still inspect their bytes for explicit SRA_*API_KEY assignments; the loose
# standalone provider-token heuristic applies only to readable text.
BINARY_SUFFIXES = frozenset({
    ".dll", ".exe", ".so", ".dylib", ".pyd", ".a", ".o", ".obj",
    ".pyc", ".pyo", ".class", ".wasm", ".bin", ".dat", ".pak",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff",
    ".ico", ".icns", ".otf", ".ttf", ".woff", ".woff2", ".mp3",
    ".wav", ".mp4", ".mov", ".zip", ".gz", ".xz", ".whl",
})
# Wheel RECORD files contain URL-safe base64 hashes of installed dependencies.
# Those digests are *not* credentials, but their random bytes sometimes include
# `sk-` followed by many base64 characters. Strip ONLY the digest column;
# filenames remain scanned for accidental credential exposure.
RECORD_DIGEST = re.compile(rb"^(?:sha256|sha384|sha512)=[A-Za-z0-9_-]{20,}$")
MAX_RECORD_LINE_BYTES = 2 * 1024 * 1024
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


def _wheel_record(name: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return bool(parts and parts[-1] == "RECORD" and any(part.endswith(".dist-info") for part in parts[:-1]))


def _record_line_without_hash(line: bytes) -> bytes:
    fields = line.rsplit(b",", 2)
    if len(fields) == 3 and RECORD_DIGEST.fullmatch(fields[1]):
        return fields[0] + b",<redacted-digest>," + fields[2]
    return line


def _wheel_record_without_hashes(reader: Iterable[bytes]) -> Iterable[bytes]:
    """Remove RECORD's cryptographic digest column, not the package filenames.

    RECORD format: relative/path,sha256=urlsafe-base64,size. The split-once-
    per-chunk approach keeps parsing time linear for large bundled environments.
    """
    pending = b""
    for chunk in reader:
        lines = (pending + chunk).split(b"\n")
        pending = lines.pop()
        for line in lines:
            if len(line) > MAX_RECORD_LINE_BYTES:
                raise ValueError("Unexpectedly long Python wheel RECORD line")
            yield _record_line_without_hash(line) + b"\n"
        if len(pending) > MAX_RECORD_LINE_BYTES:
            raise ValueError("Unexpectedly long Python wheel RECORD line")
    if pending:
        yield _record_line_without_hash(pending)


def _looks_binary(name: str, prefix: bytes) -> bool:
    if PurePosixPath(name.replace("\\", "/")).suffix.lower() in BINARY_SUFFIXES:
        return True
    if prefix.startswith((b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")):
        return True
    return b"\x00" in prefix[:8192]


def _binary_contains_token(reader: Iterable[bytes], name: str = "") -> bool:
    """Conservative secret scan for source and release bundles.

    Block explicit SRA credential assignments even in binary files. Scan plain
    provider-style tokens only when the file is text; otherwise third-party
    compiled runtimes and wheel RECORD hashes create noisy false positives.
    """
    chunks = iter(reader)
    prefix = next(chunks, b"")
    if not prefix:
        return False
    check_generic = not _looks_binary(name, prefix)
    full_reader: Iterable[bytes] = chain((prefix,), chunks)
    if _wheel_record(name):
        full_reader = _wheel_record_without_hashes(full_reader)
        check_generic = True
    tail = b""
    for chunk in full_reader:
        subject = tail + chunk
        if (ENV_SECRET_ASSIGNMENT.search(subject) or JSON_SECRET_ASSIGNMENT.search(subject)
                or INLINE_SECRET_ASSIGNMENT.search(subject)):
            return True
        if check_generic and GENERIC_TOKEN.search(subject):
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
        if path.is_file() and _binary_contains_token(_file_chunks(path), name):
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
        if _binary_contains_token(_file_chunks(path), name):
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
            if _binary_contains_token(_zip_chunks(z, member), name):
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
