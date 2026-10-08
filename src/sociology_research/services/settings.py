"""Application service for secret-safe Lite/Pro model connection settings."""

from __future__ import annotations

from typing import Any

from ..config import Settings, save_local_environment


class SettingsService:
    def get_model_settings(self) -> dict[str, Any]:
        settings = Settings.from_environment()
        lite_key = settings.effective_lite_api_key or ""
        pro_key = settings.effective_pro_api_key or ""
        lite_url = settings.effective_lite_api_base_url or ""
        pro_url = settings.effective_pro_api_base_url or ""

        def masked(key: str) -> str:
            return (key[:4] + "…" + key[-4:]) if len(key) > 10 else ("configured" if key else "")

        return {
            "lite_api_key_masked": masked(lite_key),
            "has_lite_api_key": bool(lite_key),
            "lite_api_base_url": lite_url,
            "lite_model": settings.lite_model or "",
            "pro_api_key_masked": masked(pro_key),
            "has_pro_api_key": bool(pro_key),
            "pro_api_base_url": pro_url,
            "pro_model": settings.pro_model or "",
            "same_connection": bool(
                lite_key
                and pro_key
                and lite_key == pro_key
                and lite_url.rstrip("/") == pro_url.rstrip("/")
            ),
        }

    def save_model_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = Settings.from_environment()

        # Old single-connection payloads remain valid during the migration.
        shared_raw_key = payload.get("api_key")
        shared_raw_url = payload.get("api_base_url")
        raw_lite_key = payload.get("lite_api_key", shared_raw_key)
        raw_pro_key = payload.get("pro_api_key", shared_raw_key)

        def resolved_key(raw: Any, existing: str | None) -> str:
            if raw in (None, "", "••••••••"):
                return existing or ""
            return str(raw).strip()

        lite_key = resolved_key(raw_lite_key, current.effective_lite_api_key)
        pro_key = resolved_key(raw_pro_key, current.effective_pro_api_key)
        lite_url = str(
            payload.get(
                "lite_api_base_url",
                shared_raw_url if shared_raw_url is not None else current.effective_lite_api_base_url or "",
            )
            or ""
        ).strip()
        pro_url = str(
            payload.get(
                "pro_api_base_url",
                shared_raw_url if shared_raw_url is not None else current.effective_pro_api_base_url or "",
            )
            or ""
        ).strip()
        lite_model = str(payload.get("lite_model", current.lite_model or "") or "").strip()
        pro_model = str(payload.get("pro_model", current.pro_model or "") or "").strip()

        if bool(payload.get("pro_use_lite_connection", False)):
            pro_key = lite_key
            pro_url = lite_url

        for stage, key, base_url, model in (
            ("Lite", lite_key, lite_url, lite_model),
            ("Pro", pro_key, pro_url, pro_model),
        ):
            if not key:
                raise ValueError(f"{stage} API key cannot be blank")
            if not base_url.startswith(("http://", "https://")):
                raise ValueError(f"{stage} base URL must start with http:// or https://")
            if not model:
                raise ValueError(f"{stage} model name cannot be blank")

        save_local_environment(
            {
                "SRA_LITE_API_KEY": lite_key,
                "SRA_LITE_API_BASE_URL": lite_url,
                "SRA_LITE_MODEL": lite_model,
                "SRA_PRO_API_KEY": pro_key,
                "SRA_PRO_API_BASE_URL": pro_url,
                "SRA_PRO_MODEL": pro_model,
                # Clear legacy shared values written by older UI builds.
                "SRA_API_KEY": None,
                "SRA_API_BASE_URL": None,
            }
        )
        return self.get_model_settings()
