"""Model manager routes on the existing authenticated localhost API."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ...llm_client import OpenAICompatibleClient
from ...model_center_service import download_and_verify
from ...model_profiles import (activate_profile, list_profiles, import_existing_connections, profile_connection,
                               remove_profile, save_profile)
from ...model_runtime import state_report, set_managed_models_enabled
from ...network_settings import read_network_settings, save_network_settings
from ...services.application import ApplicationServices
from ..dependencies import get_services

router = APIRouter(prefix="/settings/model-center", tags=["model-center"])

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

@router.get("")
def get_model_center():
    return {"network": read_network_settings(), "profiles": list_profiles(), "models": state_report()}

@router.put("/network")
def update_network(body: NetworkUpdate):
    return save_network_settings(body.model_dump(exclude_unset=True))

@router.get("/profiles")
def get_profiles():
    return list_profiles()

@router.post("/profiles")
def upsert_profile(body: ProfileUpdate):
    return save_profile(body.model_dump(exclude_unset=True))

@router.post("/profiles/import-existing")
def import_existing():
    return import_existing_connections()

@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: str):
    return remove_profile(profile_id)

@router.post("/activate")
def activate(body: ProfileActivation):
    return activate_profile(body.slot, body.profile_id)

@router.get("/profiles/{profile_id}/models")
def list_provider_models(profile_id: str):
    key, base_url, _ = profile_connection(profile_id)
    client = OpenAICompatibleClient(api_key=key, base_url=base_url, timeout_seconds=30)
    return {"models": client.list_model_ids()}

@router.post("/test/{profile_id}")
def test_connection(profile_id: str):
    """A single explicit, potentially billed JSON chat request; no auto retry."""
    key, base_url, model = profile_connection(profile_id)
    client = OpenAICompatibleClient(api_key=key, base_url=base_url, timeout_seconds=30)
    output = client.complete_json(
        model=model,
        system_prompt='Return one JSON object with property "ok" set to true.',
        user_prompt='Connection diagnostic only. Respond with {"ok":true}.',
        max_tokens=96,
    )
    import json
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        raise ValueError("Model responded, but returned non-JSON content") from None
    if not isinstance(parsed, dict):
        raise ValueError("Model responded, but did not return a JSON object")
    return {"reachable": True, "model": model, "response_format": "json_object"}

@router.get("/models")
def model_status():
    return state_report()

@router.put("/models/mode")
def set_model_mode(body: ManagedModeUpdate):
    return set_managed_models_enabled(body.enabled)

@router.post("/models/download", status_code=202)
def start_download(body: ModelOperation, services: ApplicationServices = Depends(get_services)):
    job = services.jobs.create("docling-model-download")
    services.jobs.start(job, lambda progress: download_and_verify(formula=body.formula, download=True, progress=progress))
    return {"job_id": job.id}

@router.post("/models/verify", status_code=202)
def start_verification(body: ModelOperation, services: ApplicationServices = Depends(get_services)):
    job = services.jobs.create("docling-model-verify")
    services.jobs.start(job, lambda progress: download_and_verify(formula=body.formula, download=False, progress=progress))
    return {"job_id": job.id}
