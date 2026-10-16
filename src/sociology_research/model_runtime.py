"""Verified, persistent Docling model artifacts for offline PDF import.

A completed download is not trusted until a separate process runs actual
Docling conversion with HF_HUB_OFFLINE=1. No status is inferred from file counts.
"""
from __future__ import annotations

import importlib.metadata
import json
from datetime import datetime, timezone
from pathlib import Path

from .user_storage import _atomic_bytes, user_config_dir, user_root


def models_directory() -> Path:
    return user_root() / "models" / "docling"


def state_path() -> Path:
    return user_config_dir() / "docling-model-state.json"


def docling_version() -> str:
    try:
        return importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def read_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {}
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Invalid Docling model state")
    return result


def _model_manifest(directory: Path) -> dict[str, int]:
    """Record file sizes to detect deleted/truncated weights after verification.

    The authoritative proof is still the separate offline inference worker;
    this lightweight manifest protects it from becoming stale on disk.
    """
    if not directory.is_dir():
        return {}
    formats = {".onnx", ".safetensors", ".bin", ".pth", ".json", ".yaml", ".yml"}
    result: dict[str, int] = {}
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in formats:
            continue
        relative = path.relative_to(directory)
        if any(piece.startswith(".") for piece in relative.parts):
            continue
        try:
            result[relative.as_posix()] = path.stat().st_size
        except OSError:
            return {}
    return result


def _manifest_matches(saved: object) -> bool:
    if not isinstance(saved, dict) or not saved:
        return False
    current = _model_manifest(models_directory())
    return saved == current and any(Path(name).suffix.lower() in {".onnx", ".safetensors", ".pth", ".bin"}
                                for name in current)


def state_report(*, formula: bool = False) -> dict:
    state = read_state()
    managed = bool(state.get("managed_enabled", False))
    verified = (
        managed and state.get("verified") is True
        and state.get("docling_version") == docling_version()
        and (not formula or state.get("formula") is True)
        and _manifest_matches(state.get("file_sizes"))
    )
    return {
        "managed_enabled": managed,
        "verified": bool(verified),
        "model_directory": str(models_directory()),
        "docling_version": docling_version(),
        "downloaded_version": str(state.get("docling_version", "")),
        "formula_ready": bool(state.get("formula", False)) if verified else False,
        "verified_at": str(state.get("verified_at", "")) if verified else "",
    }


def verified_artifacts_path(*, formula: bool = False) -> Path | None:
    report = state_report(formula=formula)
    if not report["managed_enabled"]:
        return None  # Preserve legacy development/installed user caches.
    if not report["verified"]:
        raise ValueError(
            "SRA-managed Docling models need offline verification (or have changed version). "
            "Open Settings > Model Center and run Download & Verify before importing PDFs."
        )
    return models_directory()


def commit_verified_download(*, formula: bool) -> dict:
    # Called ONLY after download subprocess AND an independent offline
    # Docling conversion subprocess both exit successfully.
    manifest = _model_manifest(models_directory())
    if not manifest or not any(name.endswith((".onnx", ".safetensors", ".pth", ".bin")) for name in manifest):
        raise RuntimeError("Offline conversion succeeded, but model weight files are missing")
    state = {
        "file_sizes": manifest,
        "managed_enabled": True,
        "verified": True,
        "docling_version": docling_version(),
        "formula": bool(formula),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_bytes(state_path(), (json.dumps(state, indent=2) + "\n").encode("utf-8"))
    return state_report(formula=formula)


def set_managed_models_enabled(enabled: bool) -> dict:
    """Reversible mode switch; never delete existing model files or papers."""
    state = read_state()
    if enabled:
        if (state.get("verified") is not True
                or state.get("docling_version") != docling_version()
                or not _manifest_matches(state.get("file_sizes"))):
            raise ValueError("Download and offline-verify these Docling models before enabling managed mode")
    state["managed_enabled"] = bool(enabled)
    _atomic_bytes(state_path(), (json.dumps(state, indent=2) + "\n").encode("utf-8"))
    return state_report()
