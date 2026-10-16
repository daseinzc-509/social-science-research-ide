"""Current Model Center API contracts tested against real handlers, offline.

Supersedes the early prototype contract that expected obsolete `mode`, `state`,
`role`, `/profiles/{id}/activate` and `require_import_readiness` endpoints.
The production app handles ValueError as HTTP 400; mirror that here while
avoiding imports of the full repository and desktop background services.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from sociology_research import model_profiles
from sociology_research import config, model_runtime


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SRA_HOME", str(tmp_path))
    monkeypatch.setattr(config, "local_env_path", lambda: tmp_path / "missing.env")
    profile_keys = {}
    live_secrets = {}
    monkeypatch.setattr(model_profiles, "read_profile_keys", lambda: dict(profile_keys))

    def save_keys(value):
        profile_keys.clear()
        profile_keys.update(value)

    def save_live(value):
        live_secrets.clear()
        live_secrets.update(value)

    monkeypatch.setattr(model_profiles, "write_profile_keys", save_keys)
    monkeypatch.setattr(config, "read_user_secrets", lambda: dict(live_secrets))
    monkeypatch.setattr(config, "write_user_secrets", save_live)

    root = Path(__file__).resolve().parents[1] / "src" / "sociology_research"

    def parent_module(name, folder):
        module = ModuleType(name)
        module.__package__ = name
        module.__path__ = [str(folder)]
        return module

    api_module = parent_module("sociology_research.api", root / "api")
    route_pkg = parent_module("sociology_research.api.routes", root / "api" / "routes")
    services_pkg = parent_module("sociology_research.services", root / "services")
    application = ModuleType("sociology_research.services.application")
    application.ApplicationServices = type("ApplicationServices", (), {})
    dependencies = ModuleType("sociology_research.api.dependencies")
    dependencies.get_services = lambda: SimpleNamespace(jobs=SimpleNamespace(
        create=lambda kind: SimpleNamespace(id="fake-download-job"),
        start=lambda job, callback: None,
    ))
    root_pkg = importlib.import_module("sociology_research")
    name = "sociology_research.api.routes.model_center"
    with monkeypatch.context() as scoped:
        scoped.setitem(sys.modules, "sociology_research.api", api_module)
        scoped.setitem(sys.modules, "sociology_research.api.routes", route_pkg)
        scoped.setitem(sys.modules, "sociology_research.services", services_pkg)
        scoped.setitem(sys.modules, "sociology_research.services.application", application)
        scoped.setitem(sys.modules, "sociology_research.api.dependencies", dependencies)
        scoped.delitem(sys.modules, name, raising=False)
        scoped.setattr(root_pkg, "api", api_module, raising=False)
        scoped.setattr(root_pkg, "services", services_pkg, raising=False)
        route = importlib.import_module(name)
        scoped.setitem(sys.modules, name, route)
        app = FastAPI()
        app.include_router(route.router, prefix="/api/v1")

        @app.exception_handler(ValueError)
        async def invalid(request: Request, error: ValueError):  # noqa: ARG001
            return JSONResponse(status_code=400, content={"detail": str(error)})

        with TestClient(app) as http:
            yield http, profile_keys, live_secrets


PREFIX = "/api/v1/settings/model-center"


def test_empty_state_preserves_legacy_cache(client):
    http, _, _ = client
    response = http.get(PREFIX)
    assert response.status_code == 200
    body = response.json()
    assert body["models"]["managed_enabled"] is False
    assert body["models"]["verified"] is False
    assert body["network"]["download_mode"] == "system"
    assert body["network"]["ai_mode"] == "system"
    assert model_runtime.verified_artifacts_path() is None


def test_invalid_proxy_is_rejected_without_saving(client):
    http, _, _ = client
    bad = http.put(PREFIX + "/network", json={
        "download_mode": "custom", "download_proxy": "https://bad-no-port",
    })
    assert bad.status_code == 400
    assert http.get(PREFIX).json()["network"]["download_mode"] == "system"
    valid = http.put(PREFIX + "/network", json={
        "download_mode": "custom", "download_proxy": "http://127.0.0.1:7890",
        "ai_mode": "direct",
    })
    assert valid.status_code == 200
    assert http.get(PREFIX).json()["network"]["ai_mode"] == "direct"
    assert http.get(PREFIX).json()["network"]["download_proxy"] == "http://127.0.0.1:7890"


def test_profile_activation_secrets_and_safe_delete(client):
    http, profile_keys, live_secrets = client
    secret = "NEVER-RETURN-ME-123"
    created = http.post(PREFIX + "/profiles", json={
        "name": "Pro Kimi", "model": "kimi-k3",
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "max_tokens": 4096, "api_key": secret,
    })
    assert created.status_code == 200, created.text
    profile = created.json()
    profile_id = profile["id"]
    assert profile_keys[profile_id] == secret
    assert secret not in created.text
    assert secret not in http.get(PREFIX + "/profiles").text
    assert secret not in http.get(PREFIX).text
    activated = http.post(PREFIX + "/activate", json={"slot": "pro", "profile_id": profile_id})
    assert activated.status_code == 200, activated.text
    assert live_secrets["SRA_PRO_API_KEY"] == secret
    assert http.delete(PREFIX + "/profiles/" + profile_id).status_code == 400

    replacement = http.post(PREFIX + "/profiles", json={
        "name": "Other Provider", "model": "other-model",
        "base_url": "https://models.example.org/v1", "api_key": "other-secret",
    })
    assert replacement.status_code == 200
    other_id = replacement.json()["id"]
    assert http.post(PREFIX + "/activate", json={"slot": "pro", "profile_id": other_id}).status_code == 200
    deleted = http.delete(PREFIX + "/profiles/" + profile_id)
    assert deleted.status_code == 200
    assert profile_id not in profile_keys


def test_missing_profile_and_invalid_role_rejected(client):
    http, _, _ = client
    assert http.post(PREFIX + "/activate", json={
        "slot": "pro", "profile_id": "missing",
    }).status_code == 400
    assert http.post(PREFIX + "/activate", json={
        "slot": "book", "profile_id": "missing",
    }).status_code == 400
    assert http.post(PREFIX + "/profiles", json={
        "name": "Invalid", "model": "m", "base_url": "https://api.example/v1",
        "role": "pro", "api_key": "key",
    }).status_code == 422  # Roles are selected on activation, not profile creation.


def test_managed_mode_requires_verification_and_download_uses_jobs(client):
    http, _, _ = client
    assert http.put(PREFIX + "/models/mode", json={"enabled": True}).status_code == 400
    assert http.get(PREFIX + "/models").json()["verified"] is False
    result = http.post(PREFIX + "/models/download", json={"formula": False})
    assert result.status_code == 202
    assert result.json() == {"job_id": "fake-download-job"}
    assert http.put(PREFIX + "/models/mode", json={"enabled": False}).status_code == 200
