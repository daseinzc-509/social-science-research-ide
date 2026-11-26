"""Model manager routes on the existing authenticated localhost API.

薄 HTTP 适配层：业务逻辑统一托管于 ModelCenterService（并挂载于 ApplicationServices）。
响应模型通过返回类型注解自动注册到 OpenAPI Schema。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ...model_profiles import effective_connections  # 保留契约符号供统一设置测试校验
from ...services.application import ApplicationServices
from ...services.model_center import ModelCenterService
from ..dependencies import get_services

router = APIRouter(prefix="/settings/model-center", tags=["model-center"])

_DEFAULT_SERVICE = ModelCenterService()


def _model_center_service(services: ApplicationServices | None = None) -> ModelCenterService:
    if services is not None:
        existing = getattr(services, "model_center", None)
        if existing is not None:
            return existing
        return ModelCenterService(getattr(services, "jobs", None))
    return _DEFAULT_SERVICE


class NetworkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    download_mode: str = "system"
    download_proxy: str = ""
    ai_mode: str = "system"
    ai_proxy: str = ""


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    name: str
    base_url: str
    model: str
    max_tokens: int = Field(default=8192, ge=1024, le=16000)
    api_key: str | None = None


class ProfileActivation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: str
    profile_id: str


class ManagedModeUpdate(BaseModel):
    enabled: bool


class ModelOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formula: bool = False


class EngineInstallRequest(BaseModel):
    """按需安装 CPU-only 独立托管 Docling 解析引擎参数。"""
    model_config = ConfigDict(extra="forbid")
    mirror_preset: str = "default"
    custom_pypi_index: str = ""


class EnhancementUpdate(BaseModel):
    """Only feature switches; never overwrite the Vision provider's credentials."""
    model_config = ConfigDict(extra="forbid")
    formula_enabled: bool
    vision_enabled: bool


class EnhancementPreferencesResponse(BaseModel):
    formula_enabled: bool
    vision_enabled: bool


class ModelCenterOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    network: dict[str, Any]
    profiles: dict[str, Any]
    models: dict[str, Any]
    engine: dict[str, Any] = Field(default_factory=dict)
    effective: dict[str, Any]
    enhancements: EnhancementPreferencesResponse


class ProviderModelsResponse(BaseModel):
    models: list[str] = Field(default_factory=list)


class ConnectionTestResponse(BaseModel):
    reachable: bool
    model: str
    response_format: str


class ModelOperationAccepted(BaseModel):
    job_id: str


def enhancement_preferences() -> dict[str, bool]:
    return _DEFAULT_SERVICE.enhancement_preferences()


@router.get("")
def get_model_center() -> ModelCenterOverviewResponse:
    # effective_connections 由 ModelCenterService.overview() 统一聚合
    return _DEFAULT_SERVICE.overview()  # type: ignore[return-value]


@router.get("/enhancements")
def get_enhancements() -> EnhancementPreferencesResponse:
    return _DEFAULT_SERVICE.enhancement_preferences()  # type: ignore[return-value]


@router.put("/enhancements")
def update_enhancements(body: EnhancementUpdate):
    return _DEFAULT_SERVICE.update_enhancements(
        formula_enabled=body.formula_enabled,
        vision_enabled=body.vision_enabled,
    )


@router.put("/network")
def update_network(body: NetworkUpdate) -> dict[str, Any]:
    return _DEFAULT_SERVICE.update_network(body.model_dump(exclude_unset=True))


@router.get("/profiles")
def get_profiles() -> dict[str, Any]:
    return _DEFAULT_SERVICE.get_profiles()


@router.post("/profiles")
def upsert_profile(body: ProfileUpdate) -> dict[str, Any]:
    return _DEFAULT_SERVICE.upsert_profile(body.model_dump(exclude_unset=True))


@router.post("/profiles/import-existing")
def import_existing() -> dict[str, Any]:
    return _DEFAULT_SERVICE.import_existing()


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str) -> dict[str, Any]:
    return _DEFAULT_SERVICE.delete_profile(profile_id)


@router.post("/activate")
def activate(body: ProfileActivation) -> dict[str, Any]:
    return _DEFAULT_SERVICE.activate(body.slot, body.profile_id)


@router.get("/profiles/{profile_id}/models")
def list_provider_models(profile_id: str) -> ProviderModelsResponse:
    return _DEFAULT_SERVICE.list_provider_models(profile_id)  # type: ignore[return-value]


@router.post("/test/{profile_id}")
def test_connection(profile_id: str) -> ConnectionTestResponse:
    """A single explicit, potentially billed JSON chat request; no auto retry."""
    return _DEFAULT_SERVICE.test_connection(profile_id)  # type: ignore[return-value]


@router.get("/models")
def model_status() -> dict[str, Any]:
    return _DEFAULT_SERVICE.model_status()


@router.put("/models/mode")
def set_model_mode(body: ManagedModeUpdate) -> dict[str, Any]:
    return _DEFAULT_SERVICE.set_model_mode(body.enabled)


@router.post("/models/download", status_code=202)
def start_download(
    body: ModelOperation, services: ApplicationServices = Depends(get_services)
) -> ModelOperationAccepted:
    return _model_center_service(services).start_model_operation(
        formula=body.formula, download=True, jobs=services.jobs
    )  # type: ignore[return-value]


@router.post("/models/verify", status_code=202)
def start_verification(
    body: ModelOperation, services: ApplicationServices = Depends(get_services)
) -> ModelOperationAccepted:
    return _model_center_service(services).start_model_operation(
        formula=body.formula, download=False, jobs=services.jobs
    )  # type: ignore[return-value]


@router.get("/engine")
def get_docling_engine() -> dict[str, Any]:
    return _DEFAULT_SERVICE.engine_status()


@router.post("/engine/install", status_code=202)
def start_engine_install(
    body: EngineInstallRequest, services: ApplicationServices = Depends(get_services)
) -> ModelOperationAccepted:
    return _model_center_service(services).start_engine_install(
        mirror_preset=body.mirror_preset,
        custom_pypi_index=body.custom_pypi_index,
        jobs=services.jobs,
    )  # type: ignore[return-value]


@router.post("/engine/verify", status_code=202)
def start_engine_verify(
    services: ApplicationServices = Depends(get_services),
) -> ModelOperationAccepted:
    return _model_center_service(services).start_engine_verify(
        jobs=services.jobs
    )  # type: ignore[return-value]


@router.delete("/engine")
def delete_docling_engine() -> dict[str, Any]:
    return _DEFAULT_SERVICE.uninstall_engine()
