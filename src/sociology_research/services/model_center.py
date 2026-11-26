"""模型中心应用服务：统一编排多服务商配置、网络代理、增强开关与受管 Docling 模型。

模块保持轻量导入（不依赖 SQLite Repository 或 Docling 运行时），
既注册于 ApplicationServices 组合根，也可在离线单测中独立加载。
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Settings, save_local_environment
from ..docling_engine import (
    engine_status as docling_engine_status,
    install_managed_engine,
    uninstall_managed_engine,
    validate_custom_pypi_index,
    validate_mirror_preset,
    verify_managed_engine,
)
from ..llm_client import OpenAICompatibleClient
from ..model_center_service import download_and_verify
from ..model_profiles import (
    activate_profile,
    effective_connections,
    import_existing_connections,
    list_profiles,
    profile_connection,
    remove_profile,
    save_profile,
)
from ..model_runtime import set_managed_models_enabled, state_report
from ..network_settings import read_network_settings, save_network_settings


class ModelCenterService:
    """模型配置中心的应用服务门面。"""

    def __init__(self, jobs: Any | None = None) -> None:
        self._jobs = jobs

    @staticmethod
    def enhancement_preferences() -> dict[str, bool]:
        settings = Settings.from_environment()
        return {
            "formula_enabled": settings.formula_enrichment_enabled,
            "vision_enabled": settings.vision_analysis_enabled,
        }

    def overview(self) -> dict[str, Any]:
        return {
            "network": read_network_settings(),
            "profiles": list_profiles(),
            "models": state_report(),
            "engine": docling_engine_status(),
            "effective": effective_connections(),
            "enhancements": self.enhancement_preferences(),
        }

    def update_enhancements(self, *, formula_enabled: bool, vision_enabled: bool) -> dict[str, bool]:
        # 只更新两个功能开关，不覆写用户已配置的视觉模型 URL/Model/API Key
        save_local_environment({
            "SRA_FORMULA_ENRICHMENT": "1" if formula_enabled else "0",
            "SRA_VISION_ENABLED": "1" if vision_enabled else "0",
        })
        return self.enhancement_preferences()

    @staticmethod
    def update_network(payload: dict[str, Any]) -> dict[str, Any]:
        return save_network_settings(payload)

    @staticmethod
    def get_profiles() -> dict[str, Any]:
        return list_profiles()

    @staticmethod
    def upsert_profile(payload: dict[str, Any]) -> dict[str, Any]:
        return save_profile(payload)

    @staticmethod
    def import_existing() -> dict[str, Any]:
        return import_existing_connections()

    @staticmethod
    def delete_profile(profile_id: str) -> dict[str, Any]:
        return remove_profile(profile_id)

    @staticmethod
    def activate(slot: str, profile_id: str) -> dict[str, Any]:
        return activate_profile(slot, profile_id)

    @staticmethod
    def list_provider_models(profile_id: str) -> dict[str, list[str]]:
        key, base_url, _ = profile_connection(profile_id)
        client = OpenAICompatibleClient(api_key=key, base_url=base_url, timeout_seconds=30)
        return {"models": client.list_model_ids()}

    @staticmethod
    def test_connection(profile_id: str) -> dict[str, Any]:
        """显式发起单次 JSON 连通性诊断请求，绝不自动重试。"""
        key, base_url, model = profile_connection(profile_id)
        client = OpenAICompatibleClient(api_key=key, base_url=base_url, timeout_seconds=30)
        output = client.complete_json(
            model=model,
            system_prompt='Return one JSON object with property "ok" set to true.',
            user_prompt='Connection diagnostic only. Respond with {"ok":true}.',
            max_tokens=96,
        )
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            raise ValueError("Model responded, but returned non-JSON content") from None
        if not isinstance(parsed, dict):
            raise ValueError("Model responded, but did not return a JSON object")
        return {"reachable": True, "model": model, "response_format": "json_object"}

    @staticmethod
    def model_status() -> dict[str, Any]:
        return state_report()

    @staticmethod
    def set_model_mode(enabled: bool) -> dict[str, Any]:
        return set_managed_models_enabled(enabled)

    def start_model_operation(
        self,
        *,
        formula: bool,
        download: bool,
        jobs: Any | None = None,
    ) -> dict[str, str]:
        job_service = jobs if jobs is not None else self._jobs
        if job_service is None:
            raise RuntimeError("JobService is required for model download/verification operations")
        kind = "docling-model-download" if download else "docling-model-verify"
        job = job_service.create(kind)
        job_service.start(
            job,
            lambda progress: download_and_verify(formula=formula, download=download, progress=progress),
        )
        return {"job_id": job.id}

    @staticmethod
    def engine_status() -> dict[str, Any]:
        return docling_engine_status()

    def start_engine_install(
        self,
        *,
        mirror_preset: str = "default",
        custom_pypi_index: str = "",
        jobs: Any | None = None,
    ) -> dict[str, str]:
        # 在创建后台任务前同步校验镜像与自定义源 URL，非法参数立即返回 HTTP 400
        preset = validate_mirror_preset(mirror_preset)
        custom_index = validate_custom_pypi_index(custom_pypi_index)
        job_service = jobs if jobs is not None else self._jobs
        if job_service is None:
            raise RuntimeError("JobService is required for Docling engine installation")
        job = job_service.create("docling-engine-install")
        job_service.start(
            job,
            lambda progress: install_managed_engine(
                mirror_preset=preset,
                custom_pypi_index=custom_index,
                progress=progress,
            ),
        )
        return {"job_id": job.id}

    def start_engine_verify(self, *, jobs: Any | None = None) -> dict[str, str]:
        job_service = jobs if jobs is not None else self._jobs
        if job_service is None:
            raise RuntimeError("JobService is required for Docling engine verification")
        job = job_service.create("docling-engine-verify")
        job_service.start(
            job,
            lambda progress: verify_managed_engine(progress=progress),
        )
        return {"job_id": job.id}

    @staticmethod
    def uninstall_engine() -> dict[str, Any]:
        return uninstall_managed_engine()
