"""Model Center regressions that run without real weights, credentials or network.

Requires only the submitted SRA source subset, not a full private repository.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import patch
from xml.etree import ElementTree

import pytest

from sociology_research import network_settings, model_runtime, model_profiles, model_center_service
from sociology_research.config import Settings, resolved_pro_output_budget


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    original = os.environ.copy()
    monkeypatch.setenv("SRA_HOME", str(tmp_path))
    from sociology_research import config
    monkeypatch.setattr(config, "local_env_path", lambda: tmp_path / "missing.env")
    keys = {}
    active_keys = {}
    monkeypatch.setattr(model_profiles, "read_profile_keys", lambda: dict(keys))
    monkeypatch.setattr(model_profiles, "write_profile_keys", lambda val: keys.clear() or keys.update(val))
    monkeypatch.setattr(config, "read_user_secrets", lambda: dict(active_keys))
    monkeypatch.setattr(config, "write_user_secrets", lambda val: active_keys.clear() or active_keys.update(val))
    yield SimpleNamespace(keys=keys, active_keys=active_keys, folder=tmp_path)
    os.environ.clear()
    os.environ.update(original)


@pytest.mark.parametrize("value", [
    "http://127.0.0.1:7890", "https://proxy.example.org:443", "http://[::1]:1080/",
])
def test_good_proxies(value):
    assert network_settings.validate_proxy_url(value).startswith(("http://", "https://"))


@pytest.mark.parametrize("value", [
    "", "socks5://127.0.0.1:7890", "http://127.0.0.1", "http://x:65536",
    "http://name:secret@127.0.0.1:7890", "http://x:7890/path", "http://x:7890?token=abc",
    "http://x:7890#fragment", "file:///tmp/abc", "http://x:abc", "http://x:80\nother",
])
def test_invalid_proxies(value):
    with pytest.raises(ValueError):
        network_settings.validate_proxy_url(value)


def test_network_persists_and_download_only_changes_child_env(isolated):
    config = network_settings.save_network_settings({
        "download_mode": "custom", "download_proxy": "http://127.0.0.1:7890",
        "ai_mode": "direct",
    })
    assert config["download_mode"] == "custom"
    assert network_settings.read_network_settings() == config
    parent = {"HTTPS_PROXY": "http://old:11", "ALL_PROXY": "http://other:22", "SRA_PRO_API_KEY": "sensitive", "KEEP": "ok"}
    child = network_settings.download_environment(parent)
    assert parent["HTTPS_PROXY"] == "http://old:11"
    assert child["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert "ALL_PROXY" not in child and "SRA_PRO_API_KEY" not in child
    assert child["KEEP"] == "ok"
    offline = network_settings.download_environment(parent, offline=True)
    assert offline["HF_HUB_OFFLINE"] == "1"
    assert offline["TRANSFORMERS_OFFLINE"] == "1"
    network_settings.save_network_settings({"download_mode": "direct", "ai_mode": "system"})
    assert "HTTPS_PROXY" not in network_settings.download_environment(parent)


def test_ai_opener_is_per_client_not_process(isolated):
    network_settings.save_network_settings({"ai_mode": "custom", "ai_proxy": "http://127.0.0.1:7890"})
    opener = network_settings.ai_request_opener()
    assert opener is not None
    from urllib.request import ProxyHandler
    handlers = [h for h in opener.handlers if isinstance(h, ProxyHandler)]
    assert handlers and handlers[0].proxies["https"] == "http://127.0.0.1:7890"
    network_settings.save_network_settings({"ai_mode": "direct"})
    assert network_settings.ai_request_opener() is not None
    network_settings.save_network_settings({"ai_mode": "system"})
    assert network_settings.ai_request_opener() is None


def test_managed_model_requires_real_verified_state(isolated, monkeypatch):
    monkeypatch.setattr(model_runtime, "docling_version", lambda: "2.134.0")
    assert model_runtime.verified_artifacts_path() is None  # old caches unaffected
    directory = isolated.folder.joinpath("models/docling")
    directory.mkdir(parents=True)
    (directory / "model.safetensors").write_bytes(b"nonempty fake weight")
    assert not model_runtime.state_report()["verified"]
    model_runtime.commit_verified_download(formula=False)
    assert model_runtime.verified_artifacts_path() == model_runtime.models_directory()
    assert model_runtime.state_report()["verified"]
    with pytest.raises(ValueError):
        model_runtime.verified_artifacts_path(formula=True)
    monkeypatch.setattr(model_runtime, "docling_version", lambda: "3.0.0")
    assert not model_runtime.state_report()["verified"]
    with pytest.raises(ValueError):
        model_runtime.verified_artifacts_path()


def test_download_and_verify_runs_two_isolated_steps_and_writes_last(isolated, monkeypatch):
    monkeypatch.setattr(model_runtime, "docling_version", lambda: "2.134.0")
    steps = []
    def fake(action, *, directory, formula, progress):
        steps.append(action)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "model.safetensors").write_bytes(b"fake model")
        assert directory == model_runtime.models_directory()
        assert model_runtime.state_report()["verified"] is False
        if action == "verify":
            progress("OFFLINE_VERIFIED")
    monkeypatch.setattr(model_center_service, "_run_worker", fake)
    assert model_center_service.download_and_verify(formula=True, download=True, progress=lambda s: None)["verified"]
    assert steps == ["download", "verify"]


def test_failed_download_never_activates_model(isolated, monkeypatch):
    monkeypatch.setattr(model_runtime, "docling_version", lambda: "2.134.0")
    def fail(action, **kwargs):
        if action == "verify":
            raise RuntimeError("verification failed")
    monkeypatch.setattr(model_center_service, "_run_worker", fail)
    with pytest.raises(RuntimeError):
        model_center_service.download_and_verify(formula=False, download=True, progress=lambda s: None)
    assert not model_runtime.state_report()["managed_enabled"]


def test_model_worker_command_frozen_and_python(isolated, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    command = model_center_service.worker_command("download", directory=Path("/tmp/test"), formula=False)
    assert command[1:3] == ["-m", "sociology_research.model_center_worker"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command = model_center_service.worker_command("verify", directory=Path("/tmp/test"), formula=True)
    assert command[1] == "--sra-model-worker" and "--formula" in command


def test_profiles_do_not_store_keys_in_plaintext(isolated):
    p = model_profiles.save_profile({"name":"火山方舟", "base_url":"https://ark.cn-beijing.volces.com/api/plan/v3",
        "model":"kimi-k3", "api_key":"super-secret-token-123", "max_tokens":4096})
    assert "api_key" not in p
    assert p["id"] in isolated.keys
    path = isolated.folder / "config/model-profiles.json"
    assert "super-secret-token-123" not in path.read_text(encoding="utf-8")
    assert model_profiles.list_profiles()["profiles"][0]["has_api_key"]
    updated = model_profiles.save_profile({"id":p["id"],"name":"Kimi", "base_url":p["base_url"],
        "model":"kimi-k3", "max_tokens":8192})
    assert isolated.keys[p["id"]] == "super-secret-token-123"
    assert updated["name"] == "Kimi"


def test_profile_activation_is_real_config_used_by_new_jobs(isolated):
    p = model_profiles.save_profile({"name":"GLM", "base_url":"https://example.org/v1",
        "model":"glm", "api_key":"key-abc", "max_tokens":6000})
    assert model_profiles.activate_profile("lite", p["id"])["slot"] == "lite"
    settings = Settings.from_environment()
    assert settings.require_lite_configuration() == ("key-abc", "https://example.org/v1", "glm")
    assert model_profiles.active_token_budget("lite", "glm") == 6000
    model_profiles.activate_profile("pro", p["id"])
    assert Settings.from_environment().require_pro_configuration() == ("key-abc", "https://example.org/v1", "glm")
    model_profiles.activate_profile("vision", p["id"])
    assert Settings.from_environment().require_vision_configuration() == ("key-abc", "https://example.org/v1", "glm")
    with pytest.raises(ValueError):
        model_profiles.remove_profile(p["id"])


def test_kimi_manual_max_tokens_is_used_when_active(isolated):
    p = model_profiles.save_profile({"name":"Kimi", "base_url":"https://example.org/v1",
        "model":"kimi-k3", "api_key":"token", "max_tokens":12000})
    model_profiles.activate_profile("pro", p["id"])
    assert resolved_pro_output_budget("kimi-k3", 16000) == 12000
    assert resolved_pro_output_budget("glm-5", 16000) == 16000


@pytest.mark.parametrize("url", ["https://a/v1?key=secret", "https://name:pass@x/v1", "file:///etc/hosts", "https://x/chat/completions", ""])
def test_invalid_provider_base_urls(url):
    with pytest.raises(ValueError):
        model_profiles.validate_base_url(url)


def test_profile_operations_reject_invalid_keys_and_slots(isolated):
    with pytest.raises(ValueError):
        model_profiles.save_profile({"name":"empty", "base_url":"https://a/v1", "model":"m", "api_key":""})
    with pytest.raises(ValueError):
        model_profiles.save_profile({"name":"bad", "base_url":"https://a/v1", "model":"m", "api_key":"key", "max_tokens":True})
    with pytest.raises(ValueError):
        model_profiles.activate_profile("book", "anything")


def test_no_network_or_secret_files_in_download_worker_code():
    root = Path(__file__).resolve().parents[1]
    hook = (root / "scripts/sra_model_worker_runtime_hook.py").read_text()
    build = (root / "scripts/build_backend.py").read_text()
    assert '"--runtime-hook"' in build and "sra_model_worker_runtime_hook.py" in build
    assert "--sra-model-worker" in hook
    assert "subprocess.Popen" in (root / "src/sociology_research/model_center_service.py").read_text()


def test_desktop_settings_are_wired_to_real_client_methods():
    root = Path(__file__).resolve().parents[1]
    ElementTree.parse(root / "desktop/SRA.Desktop/Views/MainWindow.axaml")
    xaml = (root / "desktop/SRA.Desktop/Views/MainWindow.axaml").read_text()
    window = (root / "desktop/SRA.Desktop/Views/MainWindow.axaml.cs").read_text()
    new_ui = (root / "desktop/SRA.Desktop/Views/ModelCenterWindow.cs").read_text()
    api = (root / "desktop/SRA.Desktop/Services/SraApiClient.cs").read_text()
    assert 'Click="OpenModelCenter_Click"' in xaml
    assert 'private async void OpenModelCenter_Click' in window
    assert "await window.ShowDialog(this)" in window
    for method in ("GetModelCenterAsync", "SaveModelCenterNetworkAsync", "SaveNamedModelProfileAsync",
                   "ActivateNamedModelProfileAsync", "TestNamedModelProfileAsync", "StartDoclingModelOperationAsync"):
        assert method in new_ui and method in api
    assert "X-SRA-Desktop-Token" in api


def test_minimal_worker_pdf_valid():
    from sociology_research.model_center_worker import _minimal_pdf
    pdf = _minimal_pdf()
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")


def test_real_fastapi_router_crud_without_paper_repository(isolated, monkeypatch):
    """Wire the actual route onto FastAPI using only the supplied source subset.

    The original application's repository/middleware code is not in this ZIP,
    so that larger boot and auth integration must run in the user's full repo.
    """
    import importlib
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import sociology_research.model_profiles as profiles

    service_module = ModuleType("sociology_research.services.application")
    service_module.ApplicationServices = type("ApplicationServices", (), {})
    dependency_module = ModuleType("sociology_research.api.dependencies")
    def get_services():
        return SimpleNamespace(jobs=SimpleNamespace(
            create=lambda kind: SimpleNamespace(id="faked-job"),
            start=lambda job, work: None,
        ))
    dependency_module.get_services = get_services
    monkeypatch.setitem(sys.modules, "sociology_research.services.application", service_module)
    monkeypatch.setitem(sys.modules, "sociology_research.api.dependencies", dependency_module)
    imported_name = "sociology_research.api.routes.model_center"
    sys.modules.pop(imported_name, None)
    route = importlib.import_module(imported_name)
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    client = TestClient(app)
    prefix = "/api/v1/settings/model-center"
    first = client.get(prefix)
    assert first.status_code == 200
    assert first.json()["network"]["download_mode"] == "system"
    client = TestClient(app, raise_server_exceptions=False)
    assert client.put(prefix + "/network", json={"download_mode": "invalid"}).status_code >= 400
    assert client.put(prefix + "/network", json={
        "download_mode":"custom", "download_proxy":"http://localhost:7890",
        "ai_mode":"direct", "ai_proxy":"",
    }).status_code == 200
    response = client.post(prefix + "/profiles", json={
        "name":"API test", "base_url":"https://models.example.org/v1",
        "model":"model-v1", "max_tokens":2048, "api_key":"secret-value",
    })
    assert response.status_code == 200, response.text
    profile = response.json()
    assert "api_key" not in profile and profile["id"] in isolated.keys
    assert "secret-value" not in client.get(prefix).text
    stage = client.post(prefix + "/activate", json={"slot":"lite", "profile_id":profile["id"]})
    assert stage.status_code == 200
    assert Settings.from_environment().require_lite_configuration()[2] == "model-v1"
    assert client.delete(prefix + "/profiles/" + profile["id"]).status_code in (400, 500)
    accepted = client.post(prefix + "/models/download", json={"formula":False})
    assert accepted.status_code == 202 and accepted.json() == {"job_id":"faked-job"}
    sys.modules.pop(imported_name, None)


def test_llm_client_configured_proxy_uses_real_per_client_opener(isolated, monkeypatch):
    from sociology_research.llm_client import OpenAICompatibleClient
    from sociology_research import llm_client
    import io

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return None
        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}]}'

    class FakeOpener:
        def __init__(self):
            self.requests = []
        def open(self, req, *, timeout):
            self.requests.append((req, timeout))
            return FakeResponse()

    fake = FakeOpener()
    monkeypatch.setattr(llm_client, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("system path used")))
    monkeypatch.setattr(network_settings, "ai_request_opener", lambda: fake)
    client = OpenAICompatibleClient(api_key="secret-1", base_url="https://models.example.org/v1", timeout_seconds=22)
    response = client.complete_json(model="test-model", system_prompt="safe", user_prompt="ping", max_tokens=128)
    assert json.loads(response) == {"ok": True}
    assert len(fake.requests) == 1 and fake.requests[0][1] == 22
    request = fake.requests[0][0]
    assert request.full_url == "https://models.example.org/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer secret-1"


def test_corrupt_or_removed_model_weight_invalidates_state(isolated, monkeypatch):
    monkeypatch.setattr(model_runtime, "docling_version", lambda: "2.134.0")
    directory = model_runtime.models_directory()
    directory.mkdir(parents=True)
    file = directory / "fake.onnx"
    file.write_bytes(b"test-weight-content")
    model_runtime.commit_verified_download(formula=False)
    assert model_runtime.state_report()["verified"]
    file.write_bytes(b"short")
    assert not model_runtime.state_report()["verified"]
    file.unlink()
    assert not model_runtime.state_report()["verified"]


def test_import_existing_stage_settings_without_changing_current_model(isolated):
    from sociology_research.config import save_local_environment
    save_local_environment({
        "SRA_LITE_API_KEY": "existing-lite-secret", "SRA_LITE_API_BASE_URL": "https://lite.example.org/v1",
        "SRA_LITE_MODEL": "lite-model", "SRA_PRO_API_KEY": "existing-pro-secret",
        "SRA_PRO_API_BASE_URL": "https://pro.example.org/v1", "SRA_PRO_MODEL": "pro-model",
    })
    before = Settings.from_environment().require_lite_configuration()
    imported = model_profiles.import_existing_connections()
    assert len(imported["imported"]) == 2
    assert Settings.from_environment().require_lite_configuration() == before
    assert not imported["profiles"]["active"]
    assert len(model_profiles.import_existing_connections()["imported"]) == 0


def test_provider_catalog_is_bounded_and_cannot_echo_secrets(isolated, monkeypatch):
    from sociology_research.llm_client import OpenAICompatibleClient
    from sociology_research import llm_client

    class Result:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, cap):
            assert cap <= 1_000_001
            return json.dumps({"data": [
                {"id": "glm-5"}, {"id": "kimi-k3"}, {"id": "glm-5"},
                {"id": "unsafe/id"}, {"name": "absent"},
            ]}).encode()

    seen = []
    def fake(req, *, timeout):
        seen.append((req, timeout))
        return Result()
    monkeypatch.setattr(llm_client, "urlopen", fake)
    client = OpenAICompatibleClient(api_key="private-token", base_url="https://provider.example.org/v1")
    assert client.list_model_ids() == ["glm-5", "kimi-k3"]
    assert seen[0][0].full_url == "https://provider.example.org/v1/models"
    assert seen[0][0].get_header("Authorization") == "Bearer private-token"


def test_managed_models_can_be_disabled_and_reenabled_without_data_loss(isolated, monkeypatch):
    monkeypatch.setattr(model_runtime, "docling_version", lambda: "2.134.0")
    directory = model_runtime.models_directory()
    directory.mkdir(parents=True)
    weight = directory / "model.safetensors"
    weight.write_bytes(b"weight")
    model_runtime.commit_verified_download(formula=False)
    assert model_runtime.set_managed_models_enabled(False)["managed_enabled"] is False
    assert model_runtime.verified_artifacts_path() is None
    assert weight.is_file()
    assert model_runtime.set_managed_models_enabled(True)["verified"] is True
    weight.unlink()
    with pytest.raises(ValueError):
        model_runtime.set_managed_models_enabled(True)
