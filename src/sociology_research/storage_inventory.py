"""Read-only inventory of SRA-owned paths for the desktop Storage settings page.

Never deletes, moves, or creates any files. Symlinks are not traversed. The
research data directory comes from the active application service (not guessed
from the current working directory), including developer SRA_DATA_DIR overrides.
"""
from __future__ import annotations

import os
from pathlib import Path

from .user_storage import user_config_dir, user_root

_MAX_ENTRIES = 100_000


def _directory_usage(path: Path) -> dict[str, object]:
    total = 0
    files = 0
    limited = False
    if path.is_symlink() or not path.is_dir():
        return {"bytes": 0, "files": 0, "limited": False}
    try:
        for directory, dirs, names in os.walk(path, followlinks=False):
            here = Path(directory)
            dirs[:] = [name for name in dirs if not (here / name).is_symlink()]
            for name in names:
                item = here / name
                if item.is_symlink():
                    continue
                try:
                    if item.is_file():
                        total += item.stat().st_size
                        files += 1
                except OSError:
                    continue
                if files >= _MAX_ENTRIES:
                    limited = True
                    break
            if limited:
                break
    except OSError:
        limited = True
    return {"bytes": total, "files": files, "limited": limited}


def storage_inventory(data_dir: Path) -> dict[str, object]:
    root = user_root().resolve()
    data = Path(data_dir).expanduser().resolve()
    config = user_config_dir().resolve()
    managed_cache = root / "cache"
    # Users can override caches explicitly; report these paths, but never claim
    # an external location is SRA-owned or automatically removed on uninstall.
    configured_caches = []
    for var in ("HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "DOCLING_ARTIFACTS_PATH"):
        raw = os.environ.get(var, "").strip()
        if raw:
            configured_caches.append({"variable": var, "path": str(Path(raw).expanduser().resolve())})
    data_managed = data == root or root in data.parents
    return {
        "user_root": str(root),
        "config_dir": str(config),
        "data_dir": str(data),
        "cache_dir": str(managed_cache),
        "data_managed": data_managed,
        "usage": {
            "data": _directory_usage(data),
            "config": _directory_usage(config),
            "cache": _directory_usage(managed_cache),
        },
        "configured_caches": configured_caches,
        "notes": (
            "Only the SRA-managed user root is eligible for optional removal at uninstall. "
            "Custom/legacy data locations, original PDFs, and caches shared with other software are never deleted automatically."
        ),
    }
