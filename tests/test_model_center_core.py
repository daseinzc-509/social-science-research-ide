"""Offline tests for SRA Model Center -- no model weights or external APIs required."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE = Path(__file__).resolve().parents[1] / "src/sociology_research/model_center.py"
spec = importlib.util.spec_from_file_location("model_center_tested", MODULE)
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("SRA_MODEL_CENTER_DIR", str(tmp_path))
    for name in core._RUNTIME_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("value", [
    "", "localhost:7890", "ftp://localhost:7890", "http://x:0",
    "http://x:65536", "http://a:22/path", "http://bob:pw@host:8080",
    "http://127.0.0.1:7890?x=1", "https://example.com", "socks5://x:abc",
])
def test_proxy_invalid(value):
    with pytest.raises(ValueError):
        core.validate_proxy(value)


@pytest.mark.parametrize("value", [
    "http://127.0.0.1:7890", "https://localhost:8443", "socks5://127.0.0.1:1080", "socks5h://localhost:1080",
])
def test_proxy_valid(value):
    assert core.validate_proxy(value) == value


def test_network_configuration_atomic_and_secretless():
    cfg = core.save_config(core.NetworkConfig(mode="manual", proxy_url="http://127.0.0.1:7890", formula_models=True))
    assert core.read_config() == cfg
    path = core._config_path()
    data = path.read_text(encoding="utf8")
    assert json.loads(data)["formula_models"] is True
    assert "api_key" not in data
    assert not list(path.parent.glob("*.tmp"))


def test_invalid_config_leaves_previous_setting_untouched():
    core.save_config(core.NetworkConfig(mode="direct"))
    with pytest.raises(ValueError):
        core.save_config(core.NetworkConfig(mode="manual", proxy_url="https://bad"))
    assert core.read_config().mode == "direct"


def test_scoped_proxy_restores_previous_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://company:2020")
    old_env = {k: os.environ.get(k) for k in core._RUNTIME_ENV_KEYS}
    config = core.NetworkConfig(mode="manual", proxy_url="http://127.0.0.1:7890")
    with core.scoped_network(config, offline=True):
        assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert "127.0.0.1" in os.environ["NO_PROXY"]
        assert os.environ["DOCLING_ARTIFACTS_PATH"] == str(core.artifacts_path())
    assert {k: os.environ.get(k) for k in core._RUNTIME_ENV_KEYS} == old_env


def test_download_selected_models_without_declaring_ready():
    core.save_config(core.NetworkConfig(mode="direct", formula_models=False))
    calls = []
    def downloader(output_dir, *, with_layout, with_tableformer, with_rapidocr,
                   with_code_formula=False, with_picture_classifier=True, with_easyocr=True,
                   force=False, progress=False):
        calls.append(dict(ocr=with_rapidocr, layout=with_layout, table=with_tableformer,
                          formula=with_code_formula, picture=with_picture_classifier))
        (output_dir / "fake-weight.bin").write_bytes(b"fake")
    status = core.download_models(downloader=downloader)
    assert calls == [dict(ocr=True, layout=True, table=True, formula=False, picture=False)]
    assert status["has_files"] is True
    assert status["verified_offline"] is False


def test_downloader_drift_rejected():
    with pytest.raises(RuntimeError, match="缺少参数"):
        core.download_models(downloader=lambda output_dir: None)


def test_verify_requires_real_successful_checker():
    core.save_config(core.NetworkConfig(mode="direct", use_managed_models=True))
    path = core.artifacts_path()
    path.mkdir(parents=True)
    (path / "something").write_text("mock")
    with pytest.raises(RuntimeError, match="busted"):
        core.verify_offline(lambda _: (_ for _ in ()).throw(RuntimeError("busted")))
    assert core.model_status()["verified_offline"] is False
    calls = []
    core.verify_offline(lambda p: calls.append((p, os.environ.get("HF_HUB_OFFLINE"))))
    assert calls == [(path, "1")]
    assert core.model_status()["verified_offline"] is True
    assert core.apply_verified_artifacts_at_startup() is True
    assert os.environ["DOCLING_ARTIFACTS_PATH"] == str(path)


def test_verify_empty_dir_rejected():
    with pytest.raises(RuntimeError, match="为空"):
        core.verify_offline(lambda p: None)


class MemoryKeys:
    def __init__(self): self.keys = {}
    def set_password(self, service, ident, key): self.keys[service, ident] = key
    def get_password(self, service, ident): return self.keys.get((service, ident))
    def delete_password(self, service, ident): del self.keys[service, ident]


def test_profiles_secure_and_role_switch():
    keys = MemoryKeys()
    repo = core.ProviderProfiles(keys)
    first = repo.save(name="Kimi-K3", base_url="https://api.example.com/api/plan/v3",
                      model="kimi-k3", role="pro", key="SECRET-PRIVATE")
    second = repo.save(name="GLM-FLASH", base_url="https://api.example.com/v1",
                       model="glm-5.3-flash", role="lite", key="SECOND-SECRET")
    stored = repo.file.read_text(encoding="utf8")
    assert "SECRET-PRIVATE" not in stored and "SECOND-SECRET" not in stored
    assert len(repo.list()) == 2
    writes = []
    got = repo.activate(first["id"], writer=lambda x: writes.append(x))
    assert got["model"] == "kimi-k3"
    assert writes[0]["SRA_PRO_MODEL"] == "kimi-k3"
    assert writes[0]["SRA_PRO_API_KEY"] == "SECRET-PRIVATE"
    repo.delete(first["id"])
    assert len(repo.list()) == 1
    assert second["id"] == repo.list()[0]["id"]


def test_profile_keychain_failure_does_not_persist_profile():
    class BrokenKeys:
        def set_password(self, *a): raise RuntimeError("keychain unavailable")
    store = core.ProviderProfiles(BrokenKeys())
    with pytest.raises(RuntimeError):
        store.save(name="Fail", base_url="https://example.com/v1", model="x", role="pro", key="abc")
    assert store.list() == []


def test_profile_bad_role_and_url():
    for role, url in [("bad", "https://example.com/v1"), ("pro", "file:///etc/passwd"),
                      ("pro", "http://joe:pw@example.com/v1")]:
        with pytest.raises(ValueError):
            core.validate_profile("Demo", url, "model", role)


def test_pdf_smoke_fixture_valid(tmp_path):
    import pypdfium2 as pdfium
    out = tmp_path / "test.pdf"
    core._make_smoke_pdf(out)
    pdf = pdfium.PdfDocument(str(out))
    assert len(pdf) == 1
    page = pdf[0]
    assert "SRA OFFLINE TEST" in page.get_textpage().get_text_range()


def test_small_connection_probe_success_and_429():
    import httpx
    keys = MemoryKeys()
    repo = core.ProviderProfiles(keys)
    profile = repo.save(name="Probe", base_url="https://provider.test/v1", model="m", role="pro", key="SUPERSECRET")
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(429, json={"error": "do not leak me"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = core.test_profile_connection(profile["id"], repo=repo, client=client)
    assert result == {"ok": False, "http_status": 429, "category": "quota_or_rate_limited", "scope": "small_chat_only", "auto_retry": False}
    assert len(seen) == 1
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer SUPERSECRET"
    assert "SUPERSECRET" not in str(result)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}))) as client:
        result = core.test_profile_connection(profile["id"], repo=repo, client=client)
    assert result["ok"] is True
    assert result["scope"] == "small_chat_only"
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": []}))) as client:
        invalid = core.test_profile_connection(profile["id"], repo=repo, client=client)
    assert invalid["ok"] is False
    assert invalid["category"] == "invalid_chat_response"


def test_wininet_system_proxy_propagated_without_persisting(monkeypatch):
    monkeypatch.setattr(core, "getproxies", lambda: {"http": "127.0.0.1:7890", "https": "127.0.0.1:7890"})
    env = core._env_from_network(core.NetworkConfig(mode="system"), offline=False)
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert "HF_HUB_OFFLINE" in env and env["HF_HUB_OFFLINE"] is None


def test_external_plain_http_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        core.validate_profile("test", "http://provider.example/v1", "model", "pro")
    assert core.validate_profile("test", "http://127.0.0.1:1234/v1", "local", "lite")["model"] == "local"


def test_activation_restores_active_role_on_startup():
    keys = MemoryKeys()
    store = core.ProviderProfiles(keys)
    active = store.save(name="ProModel", base_url="https://a.test/v1", model="m-a", role="pro", key="SECRET")
    store.activate(active["id"], writer=lambda _: None)
    restored = []
    store.restore_active(writer=restored.append)
    assert len(restored) == 1
    assert restored[0]["SRA_PRO_MODEL"] == "m-a"
    assert restored[0]["SRA_PRO_API_KEY"] == "SECRET"
    assert store.active_ids() == {"pro": active["id"]}
    store.delete(active["id"])
    assert store.active_ids() == {}


def test_proxy_configuration_does_not_block_legacy_imports():
    core.save_config(core.NetworkConfig(mode="manual", proxy_url="http://127.0.0.1:7890"))
    assert core.verified_artifacts_for_parser() is None
    core.save_config(core.NetworkConfig(mode="manual", proxy_url="http://127.0.0.1:7890", use_managed_models=True))
    with pytest.raises(RuntimeError, match="未验证"):
        core.verified_artifacts_for_parser()
