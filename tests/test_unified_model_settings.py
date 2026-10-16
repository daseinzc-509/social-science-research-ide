"""Single model-settings UI and safe profile-to-runtime synchronization.

All tests are offline. Real provider billing, Windows Avalonia compilation and
real Docling model downloads require end-to-end verification on a user's machine.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from sociology_research import config, model_profiles
from sociology_research.config import Settings


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    previous_env = os.environ.copy()
    monkeypatch.setenv("SRA_HOME", str(tmp_path))
    monkeypatch.setattr(config, "local_env_path", lambda: tmp_path / "missing.env")
    keys: dict[str, str] = {}
    active_keys: dict[str, str] = {}
    monkeypatch.setattr(model_profiles, "read_profile_keys", lambda: dict(keys))
    monkeypatch.setattr(model_profiles, "write_profile_keys", lambda v: (keys.clear(), keys.update(v)))
    monkeypatch.setattr(config, "read_user_secrets", lambda: dict(active_keys))
    monkeypatch.setattr(config, "write_user_secrets", lambda v: (active_keys.clear(), active_keys.update(v)))
    for name in ("SRA_LITE_API_KEY", "SRA_LITE_API_BASE_URL", "SRA_LITE_MODEL",
                 "SRA_PRO_API_KEY", "SRA_PRO_API_BASE_URL", "SRA_PRO_MODEL",
                 "SRA_VISION_API_KEY", "SRA_VISION_API_BASE_URL", "SRA_VISION_MODEL",
                 "SRA_VISION_ENABLED", "SRA_VISION_USE_PRO", "SRA_FORMULA_ENRICHMENT"):
        monkeypatch.delenv(name, raising=False)
    yield SimpleNamespace(keys=keys, stage_keys=active_keys, root=tmp_path)
    os.environ.clear()
    os.environ.update(previous_env)


def _make_profile(*, name="GLM", key="key-v1", model="glm-v1"):
    return model_profiles.save_profile({
        "name": name, "model": model, "base_url": "https://api.example.net/v1",
        "api_key": key, "max_tokens": 4096,
    })


def test_editing_active_provider_updates_real_lite_and_pro_atomically(isolated):
    original = _make_profile()
    model_profiles.activate_profile("lite", original["id"])
    model_profiles.activate_profile("pro", original["id"])
    edited = model_profiles.save_profile({
        "id": original["id"], "name": "Kimi", "model": "kimi-k3",
        "base_url": "https://ark.example.net/v1", "api_key": "secret-2", "max_tokens": 6000,
    })
    assert edited["id"] == original["id"]
    actual = Settings.from_environment()
    assert actual.require_lite_configuration() == ("secret-2", "https://ark.example.net/v1", "kimi-k3")
    assert actual.require_pro_configuration() == ("secret-2", "https://ark.example.net/v1", "kimi-k3")
    assert model_profiles.active_token_budget("lite", "kimi-k3") == 6000
    assert model_profiles.active_token_budget("pro", "kimi-k3") == 6000
    shown = model_profiles.effective_connections()
    assert shown["lite"]["profile_id"] == edited["id"]
    assert shown["pro"]["profile_id"] == edited["id"]
    assert shown["lite"]["profile_name"] == "Kimi"
    assert "secret-2" not in json.dumps(shown)
    assert "secret-2" not in (isolated.root / "config/model-profiles.json").read_text(encoding="utf-8")


def test_external_settings_override_cannot_silently_claim_profile_or_budget(isolated):
    saved = _make_profile()
    model_profiles.activate_profile("pro", saved["id"])
    assert model_profiles.active_token_budget("pro", "glm-v1") == 4096
    # Same endpoint/model but different key: this is a different connection.
    config.save_local_environment({"SRA_PRO_API_KEY": "legacy-user-override"})
    assert model_profiles.active_token_budget("pro", "glm-v1") is None
    shown = model_profiles.effective_connections()["pro"]
    assert shown["origin"] == "legacy"
    assert shown["profile_id"] is None
    assert "legacy-user-override" not in json.dumps(shown)


def test_edit_stale_profile_does_not_overwrite_external_settings(isolated):
    saved = _make_profile()
    model_profiles.activate_profile("pro", saved["id"])
    config.save_local_environment({"SRA_PRO_API_KEY": "external-secret"})
    assert model_profiles.effective_connections()["pro"]["origin"] == "legacy"
    model_profiles.save_profile({
        "id": saved["id"], "name": "edited-but-inactive", "model": "glm-v2",
        "base_url": "https://another.example/v1", "max_tokens": 3000,
    })
    assert Settings.from_environment().effective_pro_api_key == "external-secret"
    assert Settings.from_environment().pro_model == "glm-v1"
    assert "pro" not in model_profiles.list_profiles()["active"]


def test_editing_active_vision_provider_keeps_disabled_opt_in(isolated):
    saved = _make_profile()
    model_profiles.activate_profile("vision", saved["id"])
    assert Settings.from_environment().vision_analysis_enabled
    config.save_local_environment({"SRA_VISION_ENABLED": "0"})
    assert not Settings.from_environment().vision_analysis_enabled
    model_profiles.save_profile({
        "id": saved["id"], "name": "vision-changed", "base_url": "https://api.new.example/v1",
        "model": "vision-v2", "max_tokens": 5000,
    })
    settings = Settings.from_environment()
    assert not settings.vision_analysis_enabled
    assert settings.effective_vision_api_base_url == "https://api.new.example/v1"
    assert settings.vision_model == "vision-v2"
    assert model_profiles.effective_connections()["vision"]["enabled"] is False


def test_editing_inactive_profile_does_not_switch_live_model(isolated):
    selected = _make_profile()
    inactive = _make_profile(name="other", model="other-model")
    model_profiles.activate_profile("lite", selected["id"])
    before = Settings.from_environment().require_lite_configuration()
    model_profiles.save_profile({
        "id": inactive["id"], "name": "renamed", "base_url": "https://else.example/v1",
        "model": "new-model", "max_tokens": 2048,
    })
    assert Settings.from_environment().require_lite_configuration() == before
    assert model_profiles.effective_connections()["lite"]["profile_id"] == selected["id"]


def test_active_profile_bad_save_does_not_replace_catalog(isolated, monkeypatch):
    saved = _make_profile()
    model_profiles.activate_profile("lite", saved["id"])
    before_keys = dict(isolated.keys)
    before_lite = Settings.from_environment().require_lite_configuration()
    real_save = model_profiles.save_local_environment
    calls = []

    def reject(values):
        calls.append(values)
        if len(calls) == 1:
            raise RuntimeError("Failed to write secured stage settings")
        return real_save(values)

    monkeypatch.setattr(model_profiles, "save_local_environment", reject)
    with pytest.raises(RuntimeError, match="secured stage"):
        model_profiles.save_profile({
            "id": saved["id"], "name": "broken", "base_url": "https://new.example/v1",
            "model": "new-model", "api_key": "new-key", "max_tokens": 7000,
        })
    assert isolated.keys == before_keys
    assert Settings.from_environment().require_lite_configuration() == before_lite
    assert model_profiles.list_profiles()["profiles"][0]["name"] == "GLM"


def test_legacy_connections_are_visible_and_importable_without_switching(isolated):
    config.save_local_environment({
        "SRA_LITE_API_KEY": "old-key", "SRA_LITE_API_BASE_URL": "https://old.example/v1",
        "SRA_LITE_MODEL": "old-model",
    })
    status = model_profiles.effective_connections()
    assert status["lite"]["origin"] == "legacy"
    assert status["lite"]["configured"]
    assert status["lite"]["profile_id"] is None
    imported = model_profiles.import_existing_connections()
    assert len(imported["imported"]) == 1
    assert Settings.from_environment().require_lite_configuration() == ("old-key", "https://old.example/v1", "old-model")
    assert not model_profiles.import_existing_connections()["imported"]


def test_single_ui_no_duplicate_credential_editor_and_feature_switches_routed():
    root = Path(__file__).resolve().parents[1]
    xaml_path = root / "desktop/SRA.Desktop/Views/MainWindow.axaml"
    ElementTree.parse(xaml_path)
    xaml = xaml_path.read_text(encoding="utf-8")
    codebehind = (root / "desktop/SRA.Desktop/Views/MainWindow.axaml.cs").read_text(encoding="utf-8")
    dialog = (root / "desktop/SRA.Desktop/Views/ModelCenterWindow.cs").read_text(encoding="utf-8")
    client = (root / "desktop/SRA.Desktop/Services/SraApiClient.cs").read_text(encoding="utf-8")
    route = (root / "src/sociology_research/api/routes/model_center.py").read_text(encoding="utf-8")

    assert xaml.count('x:Name="ModelCenterLaunchButton"') == 1
    assert 'Click="OpenModelCenter_Click"' not in xaml
    assert codebehind.count("ModelCenterLaunchButton.Click += OpenModelCenter_Click;") == 1
    assert codebehind.index("InitializeComponent();") < codebehind.index(
        "ModelCenterLaunchButton.Click += OpenModelCenter_Click;"
    )
    assert "private async void OpenModelCenter_Click(object? sender, RoutedEventArgs e)" in codebehind
    assert 'Text="模型连接"' not in xaml
    assert 'Text="模型与网络中心"' not in xaml
    assert 'Text="公式与图片增强"' not in xaml
    for legacy in ("Settings.LiteApiKey", "Settings.ProApiKey", "Settings.VisionApiKey", "Settings.ProModel"):
        assert legacy not in xaml
    assert "SaveEmbeddedSettings_Click" not in xaml
    assert "SaveEmbeddedSettings_Click" not in codebehind
    assert "TabControl" in dialog and "AI 服务商" in dialog and "网络代理" in dialog
    assert "Docling / 增强" in dialog
    assert "SaveModelCenterEnhancementsAsync" in dialog and "SaveModelCenterEnhancementsAsync" in client
    assert '"/enhancements"' in route
    assert '"SRA_VISION_API_KEY"' not in route.split('def update_enhancements(body: EnhancementUpdate):')[1].split('@router.', 1)[0]
    assert "保存并应用到选定阶段" in dialog
    assert "StageDescription" in dialog and "effective_connections" in route


def test_saved_preference_switch_only_changes_two_flags(isolated):
    # The route calls this legacy secure writer with only the two flags.
    config.save_local_environment({
        "SRA_VISION_API_KEY": "unchanged-secret", "SRA_VISION_MODEL": "existing-vision",
        "SRA_VISION_API_BASE_URL": "https://vision.example/v1", "SRA_VISION_USE_PRO": "0",
        "SRA_VISION_ENABLED": "1", "SRA_FORMULA_ENRICHMENT": "0",
    })
    before = Settings.from_environment()
    config.save_local_environment({"SRA_FORMULA_ENRICHMENT": "1", "SRA_VISION_ENABLED": "0"})
    after = Settings.from_environment()
    assert after.formula_enrichment_enabled
    assert not after.vision_analysis_enabled
    assert before.vision_model == after.vision_model
    assert before.effective_vision_api_key == after.effective_vision_api_key
    assert before.effective_vision_api_base_url == after.effective_vision_api_base_url


def test_real_router_returns_effective_stage_and_saves_switches_only(isolated, monkeypatch):
    """Exercise the real route with the repo's heavy imports isolated."""
    import importlib
    import sys
    from types import ModuleType
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    repo = Path(__file__).resolve().parents[1] / "src/sociology_research"

    def pkg(name, folder):
        module = ModuleType(name)
        module.__package__ = name
        module.__path__ = [str(folder)]
        return module

    service = ModuleType("sociology_research.services.application")
    service.ApplicationServices = type("ApplicationServices", (), {})
    deps = ModuleType("sociology_research.api.dependencies")
    deps.get_services = lambda: SimpleNamespace(jobs=SimpleNamespace(create=lambda kind: None, start=lambda *a: None))
    package = importlib.import_module("sociology_research")
    name = "sociology_research.api.routes.model_center"
    with monkeypatch.context() as mocks:
        api_pkg = pkg("sociology_research.api", repo / "api")
        service_pkg = pkg("sociology_research.services", repo / "services")
        mocks.setitem(sys.modules, "sociology_research.api", api_pkg)
        mocks.setitem(sys.modules, "sociology_research.api.routes", pkg("sociology_research.api.routes", repo / "api/routes"))
        mocks.setitem(sys.modules, "sociology_research.services", service_pkg)
        mocks.setitem(sys.modules, "sociology_research.services.application", service)
        mocks.setitem(sys.modules, "sociology_research.api.dependencies", deps)
        mocks.setattr(package, "api", api_pkg, raising=False)
        mocks.setattr(package, "services", service_pkg, raising=False)
        mocks.delitem(sys.modules, name, raising=False)
        route = importlib.import_module(name)
        mocks.setitem(sys.modules, name, route)
        app = FastAPI()
        app.include_router(route.router, prefix="/api/v1")

        @app.exception_handler(ValueError)
        async def bad_request(req: Request, exc: ValueError):  # noqa: ARG001
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        with TestClient(app) as api:
            prefix = "/api/v1/settings/model-center"
            initial = api.get(prefix).json()
            assert initial["effective"]["lite"]["origin"] == "unconfigured"
            assert initial["enhancements"] == {"formula_enabled": False, "vision_enabled": False}
            saved = api.post(prefix + "/profiles", json={
                "name": "Demo", "base_url": "https://api.example.net/v1", "model": "glm-v1",
                "api_key": "route-super-secret", "max_tokens": 4096,
            })
            assert saved.status_code == 200
            pid = saved.json()["id"]
            activated = api.post(prefix + "/activate", json={"slot": "lite", "profile_id": pid})
            assert activated.status_code == 200
            first = api.get(prefix)
            assert first.json()["effective"]["lite"]["profile_id"] == pid
            assert "route-super-secret" not in first.text
            updated = api.post(prefix + "/profiles", json={
                "id": pid, "name": "New", "base_url": "https://provider.example/v1",
                "model": "glm-v2", "max_tokens": 5000,
            })
            assert updated.status_code == 200
            assert Settings.from_environment().lite_model == "glm-v2"
            assert api.get(prefix).json()["effective"]["lite"]["profile_name"] == "New"
            assert api.put(prefix + "/enhancements", json={
                "formula_enabled": True, "vision_enabled": False,
                "vision_api_key": "unexpected-key",
            }).status_code == 422
            assert api.put(prefix + "/enhancements", json={
                "formula_enabled": True, "vision_enabled": False,
            }).status_code == 200
            assert api.get(prefix + "/enhancements").json() == {
                "formula_enabled": True, "vision_enabled": False,
            }
            assert isolated.stage_keys["SRA_LITE_API_KEY"] == "route-super-secret"


def test_model_center_click_registration_is_compile_safe():
    """The new model-center button must not rely on XAML event-name resolution."""
    root = Path(__file__).resolve().parents[1]
    xaml = (root / "desktop/SRA.Desktop/Views/MainWindow.axaml").read_text(encoding="utf-8")
    code = (root / "desktop/SRA.Desktop/Views/MainWindow.axaml.cs").read_text(encoding="utf-8")
    assert xaml.count('x:Name="ModelCenterLaunchButton"') == 1
    assert 'Click="OpenModelCenter_Click"' not in xaml
    assert code.count('ModelCenterLaunchButton.Click += OpenModelCenter_Click;') == 1
    assert code.index('InitializeComponent();') < code.index(
        'ModelCenterLaunchButton.Click += OpenModelCenter_Click;'
    )
    assert 'private async void OpenModelCenter_Click(object? sender, RoutedEventArgs e)' in code
