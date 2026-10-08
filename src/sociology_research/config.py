"""Local, secret-safe runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
        return cls(
            api_key=_optional_environment_value("SRA_API_KEY"),
            api_base_url=_optional_environment_value("SRA_API_BASE_URL"),
            lite_model=_optional_environment_value("SRA_LITE_MODEL"),
            pro_model=_optional_environment_value("SRA_PRO_MODEL"),
            lite_api_key=_optional_environment_value("SRA_LITE_API_KEY"),
            lite_api_base_url=_optional_environment_value("SRA_LITE_API_BASE_URL"),
            pro_api_key=_optional_environment_value("SRA_PRO_API_KEY"),
            pro_api_base_url=_optional_environment_value("SRA_PRO_API_BASE_URL"),
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


def save_local_environment(values: dict[str, str | None]) -> None:
    """Persist model settings to the ignored project .env and current process.

    Only keys supplied in ``values`` are changed. Unrelated .env entries and comments
    are preserved so model-setting edits do not wipe other local configuration.
    """

    path = local_env_path()
    updates = {name: str(value or "").strip() for name, value in values.items()}
    for name, value in updates.items():
        if not name or not name.replace("_", "").isalnum():
            raise ValueError(f"Invalid environment variable name: {name!r}")
        if "\n" in value or "\r" in value:
            raise ValueError(f"Environment value for {name} must be one line")

    original_lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    written: set[str] = set()
    output: list[str] = []

    for line in original_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        raw_name, _ = line.split("=", maxsplit=1)
        name = raw_name.strip()
        if name in updates:
            if name not in written:
                output.append(f"{name}={updates[name]}")
                written.add(name)
            continue
        output.append(line)

    for name, value in updates.items():
        if name not in written:
            output.append(f"{name}={value}")

    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    for name, value in updates.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


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


def _load_local_environment(path: Path) -> None:
    """Load simple KEY=value lines without overriding the process environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", maxsplit=1)
        name, value = name.strip(), value.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


_load_local_environment(_PROJECT_ROOT / ".env")
