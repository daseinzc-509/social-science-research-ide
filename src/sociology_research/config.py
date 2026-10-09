"""Local, secret-safe runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .user_storage import (read_user_preferences, read_user_secrets,
                           write_user_preferences, write_user_secrets)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Model connection settings with independent Lite and Pro endpoints.

    ``SRA_API_KEY`` / ``SRA_API_BASE_URL`` remain supported as legacy shared
    fallbacks. Stage-specific values always take precedence.
    """

    # Legacy shared connection. Kept for backwards compatibility with existing .env files.
    api_key: str | None = None
    api_base_url: str | None = None

    # Models remain stage-specific as before.
    lite_model: str | None = None
    pro_model: str | None = None

    # New independent stage connections.
    lite_api_key: str | None = None
    lite_api_base_url: str | None = None
    pro_api_key: str | None = None
    pro_api_base_url: str | None = None

    @property
    def effective_lite_api_key(self) -> str | None:
        return self.lite_api_key or self.api_key

    @property
    def effective_lite_api_base_url(self) -> str | None:
        return self.lite_api_base_url or self.api_base_url

    @property
    def effective_pro_api_key(self) -> str | None:
        return self.pro_api_key or self.api_key

    @property
    def effective_pro_api_base_url(self) -> str | None:
        return self.pro_api_base_url or self.api_base_url

    @classmethod
    def from_environment(cls) -> "Settings":
        # Process environment wins; then per-user DPAPI/config; then the legacy .env.
        # Never publish secrets in the program directory or include them in artifacts.
        preferences = read_user_preferences()
        secrets = read_user_secrets()
        legacy = _read_env_file(local_env_path())

        def value(name: str) -> str | None:
            return (
                _optional_environment_value(name)
                or (secrets.get(name) if name in _SECRET_FIELDS else preferences.get(name))
                or legacy.get(name)
                or None
            )

        return cls(
            api_key=value("SRA_API_KEY"),
            api_base_url=value("SRA_API_BASE_URL"),
            lite_model=value("SRA_LITE_MODEL"),
            pro_model=value("SRA_PRO_MODEL"),
            lite_api_key=value("SRA_LITE_API_KEY"),
            lite_api_base_url=value("SRA_LITE_API_BASE_URL"),
            pro_api_key=value("SRA_PRO_API_KEY"),
            pro_api_base_url=value("SRA_PRO_API_BASE_URL"),
        )

    @classmethod
    def from_values(cls, values: dict[str, str | None]) -> "Settings":
        clean = lambda name: (values.get(name) or "").strip() or None
        return cls(
            api_key=clean("SRA_API_KEY"),
            api_base_url=clean("SRA_API_BASE_URL"),
            lite_model=clean("SRA_LITE_MODEL"),
            pro_model=clean("SRA_PRO_MODEL"),
            lite_api_key=clean("SRA_LITE_API_KEY"),
            lite_api_base_url=clean("SRA_LITE_API_BASE_URL"),
            pro_api_key=clean("SRA_PRO_API_KEY"),
            pro_api_base_url=clean("SRA_PRO_API_BASE_URL"),
        )

    def require_lite_configuration(self) -> tuple[str, str, str]:
        return _require_stage_configuration(
            "Lite",
            self.effective_lite_api_key,
            self.effective_lite_api_base_url,
            self.lite_model,
            key_name="SRA_LITE_API_KEY (or legacy SRA_API_KEY)",
            url_name="SRA_LITE_API_BASE_URL (or legacy SRA_API_BASE_URL)",
            model_name="SRA_LITE_MODEL",
        )

    def require_pro_configuration(self) -> tuple[str, str, str]:
        return _require_stage_configuration(
            "Pro",
            self.effective_pro_api_key,
            self.effective_pro_api_base_url,
            self.pro_model,
            key_name="SRA_PRO_API_KEY (or legacy SRA_API_KEY)",
            url_name="SRA_PRO_API_BASE_URL (or legacy SRA_API_BASE_URL)",
            model_name="SRA_PRO_MODEL",
        )

    def require_model_configuration(self) -> tuple[str, str, str, str]:
        """Backwards-compatible shared-connection accessor.

        Older callers expected one API key/Base URL for both stages. Keep that
        contract when both resolved stage connections are identical; otherwise
        fail explicitly instead of silently sending Pro to Lite's provider.
        """

        lite_key, lite_url, lite_model = self.require_lite_configuration()
        pro_key, pro_url, pro_model = self.require_pro_configuration()
        if lite_key != pro_key or _normalize_base_url(lite_url) != _normalize_base_url(pro_url):
            raise ValueError(
                "Lite and Pro use different API connections; use require_lite_configuration() "
                "and require_pro_configuration() instead of require_model_configuration()."
            )
        return lite_key, lite_url, lite_model, pro_model


def local_env_path() -> Path:
    return _PROJECT_ROOT / ".env"


_SECRET_FIELDS = frozenset({"SRA_API_KEY", "SRA_LITE_API_KEY", "SRA_PRO_API_KEY"})
_MODEL_FIELDS = frozenset({
    "SRA_API_KEY", "SRA_API_BASE_URL", "SRA_LITE_MODEL", "SRA_PRO_MODEL",
    "SRA_LITE_API_KEY", "SRA_LITE_API_BASE_URL", "SRA_PRO_API_KEY", "SRA_PRO_API_BASE_URL",
})


def _read_env_file(path: Path) -> dict[str, str]:
    """Read the old developer .env, without copying its credentials into os.environ."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if name not in _MODEL_FIELDS:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if value:
            values[name] = value
    return values


def _scrub_project_model_settings() -> None:
    """After durable secure save, remove legacy key/value lines from repo .env.

    Keep unrelated .env entries (e.g. SRA_DATA_DIR). We deliberately do not
    create a plaintext backup in the project tree.
    """
    path = local_env_path()
    if not path.is_file():
        return
    original = path.read_text(encoding="utf-8").splitlines(keepends=True)
    retained: list[str] = []
    for line in original:
        if "=" in line and line.split("=", 1)[0].strip() in _MODEL_FIELDS:
            continue
        retained.append(line)
    if retained != original:
        # Preserve existing file permissions; don't create another secret-bearing file.
        path.write_text("".join(retained), encoding="utf-8")


def save_local_environment(values: dict[str, str | None]) -> None:
    """Save model settings per-user, with keys encrypted by Windows DPAPI.

    Legacy name retained so both the browser UI and FastAPI settings service work
    unchanged. A failed DPAPI save leaves the original project .env untouched.
    """
    if set(values).difference(_MODEL_FIELDS):
        raise ValueError("Unsupported SRA model setting in save request")
    updates: dict[str, str] = {}
    for name, raw in values.items():
        value = str(raw or "").strip()
        if "\n" in value or "\r" in value:
            raise ValueError(f"Environment value for {name} must be one line")
        updates[name] = value

    current = Settings.from_environment()
    merged = {
        "SRA_API_KEY": current.api_key or "",
        "SRA_API_BASE_URL": current.api_base_url or "",
        "SRA_LITE_MODEL": current.lite_model or "",
        "SRA_PRO_MODEL": current.pro_model or "",
        "SRA_LITE_API_KEY": current.lite_api_key or "",
        "SRA_LITE_API_BASE_URL": current.lite_api_base_url or "",
        "SRA_PRO_API_KEY": current.pro_api_key or "",
        "SRA_PRO_API_BASE_URL": current.pro_api_base_url or "",
    }
    merged.update(updates)

    # Encrypt BEFORE writing anything else; failure must not degrade to plaintext.
    write_user_secrets({name: merged[name] for name in _SECRET_FIELDS})
    write_user_preferences({name: merged[name] for name in _MODEL_FIELDS - _SECRET_FIELDS if merged[name]})
    _scrub_project_model_settings()
    for name, value in updates.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


def migrate_legacy_model_config(*, apply: bool = False) -> dict[str, object]:
    """Explicitly move project .env model values to secure per-user storage."""
    legacy = _read_env_file(local_env_path())
    report: dict[str, object] = {
        "source": str(local_env_path()),
        "target": "per-user SRA configuration and Windows DPAPI secret store",
        "model_fields_found": sorted(legacy),
        "applied": False,
    }
    if apply and legacy:
        # Save the full, resolved configuration; scrub only after encrypted save.
        save_local_environment(legacy)
        report["applied"] = True
    return report


@dataclass(frozen=True)
class AnalysisConfig:
    """Local application behavior; keep non-secret tuning out of environment variables."""

    request_timeout_seconds: int = 420
    lite_thinking: Literal["enabled", "disabled"] = "disabled"
    pro_thinking: Literal["enabled", "disabled"] = "enabled"
    pro_reasoning_effort: Literal["minimal", "low", "medium", "high"] = "low"
    lite_max_completion_tokens: int = 4000
    pro_max_completion_tokens: int = 16000
    pro_max_source_excerpts: int = 120
    pro_source_char_budget: int = 42000


def _require_stage_configuration(
    stage: str,
    api_key: str | None,
    api_base_url: str | None,
    model: str | None,
    *,
    key_name: str,
    url_name: str,
    model_name: str,
) -> tuple[str, str, str]:
    missing = [
        name
        for name, value in ((key_name, api_key), (url_name, api_base_url), (model_name, model))
        if not value
    ]
    if missing:
        raise ValueError(f"Missing {stage} model configuration: " + ", ".join(missing))
    return api_key, api_base_url, model


def _normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _optional_environment_value(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _load_nonsecret_legacy_options() -> None:
    """Keep SRA_DATA_DIR from old .env working; never export model keys."""
    path = local_env_path()
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line or raw_line.lstrip().startswith("#"):
            continue
        name, value = raw_line.split("=", 1)
        if name.strip() == "SRA_DATA_DIR":
            candidate = value.strip().strip('"').strip("'")
            if candidate:
                os.environ.setdefault("SRA_DATA_DIR", candidate)


_load_nonsecret_legacy_options()
