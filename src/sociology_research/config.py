"""Local, secret-safe runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    api_base_url: str | None
    lite_model: str | None
    pro_model: str | None

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            api_key=_optional_environment_value("SRA_API_KEY"),
            api_base_url=_optional_environment_value("SRA_API_BASE_URL"),
            lite_model=_optional_environment_value("SRA_LITE_MODEL"),
            pro_model=_optional_environment_value("SRA_PRO_MODEL"),
        )

    @classmethod
    def from_values(cls, values: dict[str, str | None]) -> "Settings":
        return cls(
            api_key=(values.get("SRA_API_KEY") or "").strip() or None,
            api_base_url=(values.get("SRA_API_BASE_URL") or "").strip() or None,
            lite_model=(values.get("SRA_LITE_MODEL") or "").strip() or None,
            pro_model=(values.get("SRA_PRO_MODEL") or "").strip() or None,
        )

    def require_model_configuration(self) -> tuple[str, str, str, str]:
        missing = [
            name
            for name, value in (
                ("SRA_API_KEY", self.api_key),
                ("SRA_API_BASE_URL", self.api_base_url),
                ("SRA_LITE_MODEL", self.lite_model),
                ("SRA_PRO_MODEL", self.pro_model),
            )
            if not value
        ]
        if missing:
            raise ValueError("Missing model configuration: " + ", ".join(missing))
        return self.api_key, self.api_base_url, self.lite_model, self.pro_model


def local_env_path() -> Path:
    return _PROJECT_ROOT / ".env"


def save_local_environment(values: dict[str, str | None]) -> None:
    """Persist model settings to the ignored project .env and current process."""
    allowed = ("SRA_API_KEY", "SRA_API_BASE_URL", "SRA_LITE_MODEL", "SRA_PRO_MODEL")
    lines = [f"{name}={str(values.get(name) or '').strip()}" for name in allowed]
    local_env_path().write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name in allowed:
        value = str(values.get(name) or "").strip()
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
