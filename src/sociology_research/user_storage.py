"""Per-user SRA storage, Windows DPAPI secrets, and safe legacy data migration.

The desktop installer MUST NOT bundle this directory. This module never exports
plaintext keys into build artifacts or logs.
"""

from __future__ import annotations

import base64
from contextlib import closing
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

_APP = "SRA"
_PRIVATE_KEYS = frozenset({"SRA_API_KEY", "SRA_LITE_API_KEY", "SRA_PRO_API_KEY"})


def user_root() -> Path:
    """Private, account-scoped state root; SRA_HOME is an explicit override."""
    custom = os.environ.get("SRA_HOME", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base).expanduser().resolve() / _APP
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / _APP


def user_config_dir() -> Path:
    return user_root() / "config"


def user_data_dir() -> Path:
    return user_root() / "data"


def resolve_data_dir(*, legacy_project_data: Path | None = None) -> Path:
    """Respect an explicit path. Preserve existing project libraries until migrated.

    A preexisting legacy DB wins only when no user-store DB exists. After copying
    data via migrate_user_data(), the per-user location takes precedence.
    """
    custom = os.environ.get("SRA_DATA_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    target = user_data_dir()
    if (target / "research.sqlite3").is_file():
        return target
    if legacy_project_data is not None:
        legacy = Path(legacy_project_data).expanduser().resolve()
        if (legacy / "research.sqlite3").is_file():
            return legacy
    return target


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".sra-", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        if sys.platform != "win32":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _json_dict(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"Invalid local SRA settings file: {path}")
    return value


def read_user_preferences() -> dict[str, str]:
    return _json_dict(user_config_dir() / "model-settings.json")


def write_user_preferences(values: dict[str, str]) -> None:
    if _PRIVATE_KEYS.intersection(values):
        raise ValueError("API keys must never be written to plaintext preferences")
    path = user_config_dir() / "model-settings.json"
    _atomic_bytes(path, (json.dumps(values, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def _dpapi_protect(value: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Encrypted API Key storage currently requires Windows DPAPI; on this platform use process environment variables or an ignored local .env for development.")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(value)
    source = DATA_BLOB(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    destination = DATA_BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptProtectData(ctypes.byref(source), "SRA local model credentials", None, None, None, 0x1, ctypes.byref(destination)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(destination.pbData, ctypes.c_void_p))


def _dpapi_unprotect(value: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("This secret file requires Windows DPAPI and the Windows user account that created it")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(value)
    source = DATA_BLOB(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    destination = DATA_BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(destination)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(destination.pbData, ctypes.c_void_p))


def _mac_keyring():
    """Native macOS Keychain via the keyring package; never a plaintext fallback."""
    try:
        from keyring.backends.macOS import Keyring
    except ImportError as exc:
        raise RuntimeError(
            "macOS secure model storage requires 'keyring'; install the SRA macOS desktop backend"
        ) from exc
    # Use the native system Keychain explicitly, never a third-party/file-based
    # keyring plugin that could silently weaken encrypted storage guarantees.
    return Keyring()


_MAC_SERVICE = "org.daseinzc509.sra.model-credentials"


def read_user_secrets() -> dict[str, str]:
    if sys.platform == "darwin":
        backend = _mac_keyring()
        return {
            name: secret
            for name in sorted(_PRIVATE_KEYS)
            if (secret := backend.get_password(_MAC_SERVICE, name))
        }
    if sys.platform == "win32":
        path = user_config_dir() / "model-keys.dpapi"
        if not path.is_file():
            return {}
        # Never silently fall back to plaintext or replace corrupt/foreign ciphertext.
        ciphertext = base64.b64decode(path.read_bytes(), validate=True)
        values = json.loads(_dpapi_unprotect(ciphertext).decode("utf-8"))
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise ValueError("Invalid encrypted SRA credentials")
        return {k: v for k, v in values.items() if k in _PRIVATE_KEYS}
    return {}


def write_user_secrets(values: dict[str, str]) -> None:
    if set(values).difference(_PRIVATE_KEYS):
        raise ValueError("Only API keys may be saved in the protected credential store")
    nonblank = {name: key.strip() for name, key in values.items() if key and key.strip()}
    if sys.platform == "darwin":
        keyring = _mac_keyring()
        prior = read_user_secrets()
        changed: list[str] = []
        try:
            for name in sorted(_PRIVATE_KEYS):
                changed.append(name)
                if name in nonblank:
                    keyring.set_password(_MAC_SERVICE, name, nonblank[name])
                else:
                    if name in prior:
                        keyring.delete_password(_MAC_SERVICE, name)
        except Exception:
            # Restore prior state if an individual Keychain write was refused.
            for name in reversed(changed):
                try:
                    if name in prior:
                        keyring.set_password(_MAC_SERVICE, name, prior[name])
                    else:
                        keyring.delete_password(_MAC_SERVICE, name)
                except Exception:
                    pass
            raise
        return
    if sys.platform != "win32":
        if nonblank:
            raise RuntimeError("Secure model credential storage is not implemented for this platform")
        return
    path = user_config_dir() / "model-keys.dpapi"
    if not nonblank:
        path.unlink(missing_ok=True)
        return
    raw = json.dumps(nonblank, ensure_ascii=False).encode("utf-8")
    _atomic_bytes(path, base64.b64encode(_dpapi_protect(raw)))


def migrate_user_data(source: Path, destination: Path | None = None, *, apply: bool = False) -> dict[str, Any]:
    """Copy legacy data safely, transactionally rewrite stored_path, never delete source.

    Running API/desktop must be stopped to avoid concurrent writes. DB backup uses
    SQLite's backup API, so WAL-mode databases are included consistently.
    """
    source = Path(source).expanduser().resolve()
    destination = Path(destination or user_data_dir()).expanduser().resolve()
    database = source / "research.sqlite3"
    if not database.is_file():
        raise ValueError(f"Legacy database not found: {database}")
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must be different, non-nested directories")
    if destination.exists():
        raise FileExistsError(f"Destination already exists; refusing to overwrite: {destination}")

    # Validate all referenced PDFs before creating ANY destination files.
    # sqlite3.Connection context managers commit/rollback, but do NOT close.
    # Explicit closure is required before a directory rename on Windows.
    with closing(sqlite3.connect(database)) as connection:
        records = connection.execute("SELECT id, stored_path FROM papers").fetchall()
    paths: list[tuple[str, Path, Path]] = []
    for paper_id, stored in records:
        old = Path(str(stored)).expanduser().resolve()
        if not old.is_relative_to(source) or not old.is_file():
            raise ValueError(f"PDF reference for paper {paper_id} is missing or outside source directory; migration aborted")
        relative = old.relative_to(source)
        paths.append((str(paper_id), old, destination / relative))

    report = {"source": str(source), "destination": str(destination), "paper_count": len(paths), "applied": False}
    if not apply:
        return report

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sra-data-migrate-", dir=destination.parent))
    try:
        # Copy attachments but not SQLite files; the DB is backed up consistently below.
        for item in source.rglob("*"):
            if item.is_symlink():
                raise ValueError(f"Symlink in legacy data directory, migration aborted: {item}")
            if not item.is_file():
                continue
            relative = item.relative_to(source)
            if item.name == "research.sqlite3" or item.name.startswith("research.sqlite3-"):
                continue
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        # Both connections must be closed (not just committed) before the
        # staged directory is renamed. Windows locks open SQLite files.
        with closing(sqlite3.connect(database)) as original:
            with closing(sqlite3.connect(staging / "research.sqlite3")) as copy:
                original.backup(copy)
                with copy:  # atomic commit/rollback for the path rewrite
                    for paper_id, _, new_path in paths:
                        copy.execute("UPDATE papers SET stored_path=? WHERE id=?", (str(new_path), paper_id))
                    remaining = copy.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
                    if remaining != len(paths):
                        raise RuntimeError("Database record count changed during migration")
        # Double-check every expected PDF exists in the staged copy.
        for _, old, new_path in paths:
            staged = staging / new_path.relative_to(destination)
            if not staged.is_file() or staged.stat().st_size != old.stat().st_size:
                raise RuntimeError(f"Copied PDF missing or size differs: {old.name}")
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    report["applied"] = True
    return report
