"""FastAPI route contracts with only local in-memory doubles."""
from __future__ import annotations
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1] / "src/sociology_research"


class FakeKeychain:
    def __init__(self): self.k = {}
    def set_password(self, service, uid, key): self.k[service, uid] = key
    def get_password(self, service, uid): return self.k.get((service, uid))
    def delete_password(self, service, uid): self.k.pop((service, uid))


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SRA_MODEL_CENTER_DIR", str(tmp_path))
    keys = FakeKeychain()
    monkeypatch.setitem(sys.modules, "keyring", keys)
    # Stand-in namespace packages reproduce the real project hierarchy.
    for name, path in [
        ("sociology_research", ROOT),
        ("sociology_research.api", ROOT / "api"),
        ("sociology_research.api.routes", ROOT / "api/routes"),
    ]:
        obj = types.ModuleType(name)
        obj.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, name, obj)
    config = types.ModuleType("sociology_research.config")
    writes = []
    config.save_local_environment = lambda v: writes.append(v)
    monkeypatch.setitem(sys.modules, "sociology_research.config", config)
    import sociology_research.model_center as core
    import sociology_research.api.routes.model_center as route
    route = importlib.reload(route)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    with TestClient(app) as tester:
        yield tester, writes, keys


def test_root_state_no_models(client):
    api, writes, keys = client
    result = api.get("/api/v1/settings/model-center")
    assert result.status_code == 200
    assert result.json()["models"]["state"] == "not_downloaded"
    assert result.json()["network"]["mode"] == "system"


def test_invalid_network_settings_blocked(client):
    api, _, _ = client
    res = api.put("/api/v1/settings/model-center/network",
                  json={"mode":"manual", "proxy_url":"https://bad-no-port"})
    assert res.status_code == 422
    assert api.get("/api/v1/settings/model-center").json()["network"]["mode"] == "system"


def test_active_provider_secure_and_not_returned(client):
    api, writes, keychain = client
    obj = {"name":"Pro Kimi", "model":"kimi-k3", "role":"pro",
           "base_url":"https://ark.cn-beijing.volces.com/api/plan/v3", "api_key":"TEST-SUPER-SECRET"}
    response = api.post("/api/v1/settings/model-center/profiles", json=obj)
    assert response.status_code == 200, response.text
    assert "TEST-SUPER-SECRET" not in response.text
    uid = response.json()["profile"]["id"]
    listing = api.get("/api/v1/settings/model-center/profiles")
    assert "TEST-SUPER-SECRET" not in listing.text
    applied = api.post(f"/api/v1/settings/model-center/profiles/{uid}/activate")
    assert applied.status_code == 200
    assert writes[0]["SRA_PRO_API_KEY"] == "TEST-SUPER-SECRET"
    assert api.delete(f"/api/v1/settings/model-center/profiles/{uid}").status_code == 200
    assert api.get("/api/v1/settings/model-center/profiles").json() == {"profiles": [], "active": {}}


def test_missing_profile_fails(client):
    api, _, _ = client
    assert api.post("/api/v1/settings/model-center/profiles/no-such-profile/activate").status_code == 404


def test_import_preflight_avoids_batch_pdf_failure(client):
    api, _, _ = client
    import sociology_research.api.routes.model_center as route
    import fastapi
    # Backward-compatible old installations remain allowed.
    route.require_import_readiness()
    api.put("/api/v1/settings/model-center/network", json={"mode": "direct", "use_managed_models": True})
    with pytest.raises(fastapi.HTTPException) as captured:
        route.require_import_readiness()
    assert captured.value.status_code == 503
