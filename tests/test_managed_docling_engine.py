"""独立托管 CPU-only Docling 解析引擎与瘦壳（slim）打包模式全栈回归测试。

全部测试完全离线运行，不发起真实外网下载、不调用付费模型、不接触任何真实用户论文。
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from sociology_research import (
    docling_engine,
    model_center_service,
    model_runtime,
    network_settings,
    parser,
)
from sociology_research.models import ParseStatus, ParsedDocument, SourceBlock
from sociology_research.storage_inventory import storage_inventory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_backend
from scripts import frozen_backend_probe


def _options(args: list[str]) -> set[tuple[str, str]]:
    return {(args[i], args[i + 1]) for i in range(len(args) - 1) if args[i].startswith("--")}


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "sra-home"
    monkeypatch.setenv("SRA_HOME", str(home))
    monkeypatch.delenv("SRA_UV_PATH", raising=False)
    monkeypatch.delenv("SRA_PDF_PARSER", raising=False)
    return home


def test_engine_status_transitions_between_embedded_managed_and_missing(isolated_home, monkeypatch):
    # 1. 内置模式
    monkeypatch.setattr(docling_engine, "embedded_docling_version", lambda: "2.134.0")
    st_embedded = docling_engine.engine_status()
    assert st_embedded["mode"] == "embedded"
    assert st_embedded["ready"] is True
    assert st_embedded["embedded_available"] is True
    assert st_embedded["managed_available"] is False
    assert st_embedded["docling_version"] == "2.134.0"

    # 2. 未安装模式（瘦壳首次启动）
    monkeypatch.setattr(docling_engine, "embedded_docling_version", lambda: None)
    st_missing = docling_engine.engine_status()
    assert st_missing["mode"] == "missing"
    assert st_missing["ready"] is False
    assert st_missing["docling_version"] == "missing"
    assert Path(st_missing["engine_dir"]) == (isolated_home / "engine" / "docling").resolve()

    # 3. 已安装且通过离线自检的独立托管模式
    py_exe = docling_engine.engine_python_executable()
    py_exe.parent.mkdir(parents=True, exist_ok=True)
    py_exe.write_bytes(b"fake-python")
    docling_engine._write_engine_state({
        "verified": True,
        "docling_version": "2.134.0",
        "torch_version": "2.6.0+cpu",
        "python_executable": str(py_exe.resolve()),
        "mirror_preset": "tsinghua",
    })
    st_managed = docling_engine.engine_status()
    assert st_managed["mode"] == "managed"
    assert st_managed["ready"] is True
    assert st_managed["managed_available"] is True
    assert st_managed["docling_version"] == "2.134.0"
    assert st_managed["torch_version"] == "2.6.0+cpu"
    assert st_managed["mirror_preset"] == "tsinghua"
    assert model_runtime.docling_version() == "2.134.0"


def test_install_managed_engine_respects_download_proxy_strips_api_keys_and_uses_cpu_index(
    isolated_home, tmp_path, monkeypatch
):
    monkeypatch.setattr(docling_engine, "embedded_docling_version", lambda: None)
    monkeypatch.setenv("SRA_PRO_API_KEY", "SECRET-SHOULD-NEVER-LEAK")
    monkeypatch.setenv("SRA_DESKTOP_TOKEN", "TOKEN-SHOULD-NEVER-LEAK")
    network_settings.save_network_settings({
        "download_mode": "custom",
        "download_proxy": "http://127.0.0.1:7890",
        "ai_mode": "direct",
    })

    fake_uv = tmp_path / ("uv.exe" if sys.platform == "win32" else "uv")
    fake_uv.write_bytes(b"stub-uv")
    monkeypatch.setenv("SRA_UV_PATH", str(fake_uv))

    invocations: list[tuple[list[str], dict[str, str], str]] = []
    messages: list[str] = []

    def fake_runner(args, *, env, progress, stage_label):
        invocations.append((list(args), dict(env), stage_label))
        if stage_label == "创建虚拟环境":
            py_exe = docling_engine.engine_python_executable()
            py_exe.parent.mkdir(parents=True, exist_ok=True)
            py_exe.write_bytes(b"fake-python")
            return 0, ["Using CPython 3.12.9"]
        if stage_label == "引擎自检":
            payload = {
                "docling_version": "2.134.0",
                "torch_version": "2.6.0+cpu",
                "python_version": "3.12.9",
            }
            return 0, ["SRA_ENGINE_PROBE_JSON:" + json.dumps(payload)]
        return 0, ["Installed packages"]

    result = docling_engine.install_managed_engine(
        mirror_preset="tsinghua",
        progress=messages.append,
        runner=fake_runner,
    )
    assert result["mode"] == "managed"
    assert result["ready"] is True
    assert result["docling_version"] == "2.134.0"
    assert result["torch_version"] == "2.6.0+cpu"
    assert len(invocations) == 4

    # 所有安装步骤均继承自定义下载代理且绝不泄露任何 AI API Key / Desktop Token
    for idx, (cmd, env, label) in enumerate(invocations):
        assert "SRA_PRO_API_KEY" not in env
        assert "SRA_DESKTOP_TOKEN" not in env
        assert env.get("HTTPS_PROXY") == "http://127.0.0.1:7890"
        assert env.get("HTTP_PROXY") == "http://127.0.0.1:7890"
        assert str(isolated_home / "cache" / "uv") == env["UV_CACHE_DIR"]
        if idx < 3:
            assert env.get("HF_HUB_OFFLINE") is None
        else:
            # 最后一步离线自检必须强制离线标志
            assert label == "引擎自检"
            assert env.get("HF_HUB_OFFLINE") == "1"

    # 验证命令参数：uv venv -> torch CPU index -> 清华 PyPI + torch CPU extra-index
    assert invocations[0][0][:4] == [str(fake_uv.resolve()), "venv", "--python", "3.12"]
    assert docling_engine.PYTORCH_CPU_INDEX_URL in invocations[1][0]
    assert "torch" in invocations[1][0] and "torchvision" in invocations[1][0]
    assert "https://pypi.tuna.tsinghua.edu.cn/simple" in invocations[2][0]
    assert f"docling=={docling_engine.PINNED_DOCLING_VERSION}" in invocations[2][0]
    assert "rapidocr" in invocations[2][0]

    # 卸载独立引擎安全清理并恢复未安装态
    after_uninstall = docling_engine.uninstall_managed_engine()
    assert after_uninstall["mode"] == "missing"
    assert after_uninstall["ready"] is False
    assert not docling_engine.engine_directory().exists()


def test_install_rejects_bad_mirror_or_credential_url_and_failed_verify_never_marks_ready(
    isolated_home, tmp_path, monkeypatch
):
    monkeypatch.setattr(docling_engine, "embedded_docling_version", lambda: None)
    with pytest.raises(ValueError, match="mirror_preset"):
        docling_engine.install_managed_engine(mirror_preset="unknown-mirror")
    for bad_url in (
        "http://mirror.example.org/simple",
        "https://user:pass@mirror.example.org/simple",
        "https://mirror.example.org/simple?token=abc",
        "https://mirror.example.org/simple with space",
    ):
        with pytest.raises(ValueError):
            docling_engine.install_managed_engine(custom_pypi_index=bad_url)

    # 模拟安装后离线自检失败：绝不可标记为 ready
    fake_uv = tmp_path / ("uv.exe" if sys.platform == "win32" else "uv")
    fake_uv.write_bytes(b"stub-uv")
    monkeypatch.setenv("SRA_UV_PATH", str(fake_uv))

    def failing_verify_runner(args, *, env, progress, stage_label):
        if stage_label == "创建虚拟环境":
            py_exe = docling_engine.engine_python_executable()
            py_exe.parent.mkdir(parents=True, exist_ok=True)
            py_exe.write_bytes(b"fake-python")
            return 0, []
        if stage_label == "引擎自检":
            return 1, ["ImportError: simulated broken wheel"]
        return 0, []

    with pytest.raises(RuntimeError, match="自检失败"):
        docling_engine.install_managed_engine(runner=failing_verify_runner)
    assert docling_engine.managed_engine_ready() is False
    assert docling_engine.engine_status()["ready"] is False


def test_managed_subprocess_parser_and_model_worker_delegation(isolated_home, tmp_path, monkeypatch):
    monkeypatch.setattr(parser, "_docling_installed", lambda: False)
    monkeypatch.setattr(docling_engine, "embedded_docling_version", lambda: None)

    # 引擎未安装时 create_default_parser 抛出明确提示并保留 'required' 兼容契约
    with pytest.raises(parser.DocumentParseError, match="required"):
        parser.create_default_parser()
    with pytest.raises(RuntimeError, match="本地 Docling 解析引擎"):
        model_center_service.worker_command("download", directory=tmp_path, formula=False)

    # 标记独立托管引擎就绪后，自动选择 ManagedSubprocessDoclingParser 并把模型下载委派给托管 Python
    py_exe = docling_engine.engine_python_executable()
    py_exe.parent.mkdir(parents=True, exist_ok=True)
    py_exe.write_bytes(b"fake-python")
    docling_engine._write_engine_state({
        "verified": True,
        "docling_version": "2.134.0",
        "torch_version": "2.6.0+cpu",
        "python_executable": str(py_exe.resolve()),
    })

    selected = parser.create_default_parser()
    assert isinstance(selected, parser.ManagedSubprocessDoclingParser)
    assert "2.134.0" in selected.version

    worker_cmd = model_center_service.worker_command("verify", directory=tmp_path, formula=True)
    assert worker_cmd[:3] == [str(py_exe), "-m", "sociology_research.model_center_worker"]
    assert worker_cmd[-1] == "--formula"

    # 端到端 mock 子进程解析，验证安全进度转发、API Key 隔离与 ParsedDocument 完整反序列化
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% synthetic stub")
    monkeypatch.setenv("SRA_LITE_API_KEY", "NEVER-PASS-TO-PARSER")
    captured_popen: dict[str, object] = {}

    class FakeProc:
        def __init__(self, cmd, *, env, stdout, stderr, text, encoding, errors):
            captured_popen["cmd"] = cmd
            captured_popen["env"] = dict(env)
            out_path = Path(cmd[-1])
            parsed = ParsedDocument(
                paper_id="paper-managed-1",
                sha256="abc123",
                source_path=str(pdf.resolve()),
                page_count=1,
                status=ParseStatus.SUCCESS,
                parser_version=selected.version,
                blocks=[
                    SourceBlock(
                        id="blk-1",
                        page_number=1,
                        text="Managed Docling engine parsed this evidence block cleanly.",
                        role="body",
                    )
                ],
            )
            out_path.write_text(parsed.model_dump_json(), encoding="utf-8")
            self.stdout = iter([
                "SRA stage: Docling: converting with layout / standard OCR...\n",
                "SRA stage: Docling: parsed 1 source blocks across 1 pages.\n",
            ])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(parser.subprocess, "Popen", FakeProc)
    progress_lines: list[str] = []
    doc = selected.parse(pdf, paper_id="paper-managed-1", sha256="abc123", progress=progress_lines.append)
    assert doc.paper_id == "paper-managed-1"
    assert doc.status == ParseStatus.SUCCESS
    assert len(doc.blocks) == 1
    assert "Managed Docling engine" in doc.blocks[0].text
    assert any("converting with layout" in line for line in progress_lines)
    assert "SRA_LITE_API_KEY" not in captured_popen["env"]
    assert "PYTHONPATH" in captured_popen["env"]


def test_model_center_engine_api_routes_and_storage_inventory(isolated_home, tmp_path, monkeypatch):
    # 1. storage_inventory 准确暴露 engine_dir 与 usage.engine 且无副作用
    data_dir = isolated_home / "data"
    data_dir.mkdir(parents=True)
    engine_file = isolated_home / "engine" / "docling" / "marker.bin"
    engine_file.parent.mkdir(parents=True)
    engine_file.write_bytes(b"0123456789")
    inv = storage_inventory(data_dir)
    assert inv["engine_dir"] == str((isolated_home / "engine").resolve())
    assert inv["usage"]["engine"]["bytes"] == 10
    assert inv["usage"]["engine"]["files"] == 1

    # 2. model_center API 新增 /engine 端点离线路由契约
    from sociology_research import config, model_profiles

    monkeypatch.setattr(config, "local_env_path", lambda: tmp_path / "missing.env")
    monkeypatch.setattr(model_profiles, "read_profile_keys", lambda: {})
    monkeypatch.setattr(config, "read_user_secrets", lambda: {})
    monkeypatch.setattr(docling_engine, "embedded_docling_version", lambda: None)

    started_jobs: list[str] = []
    root = ROOT / "src" / "sociology_research"

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
    dependencies.get_services = lambda: SimpleNamespace(
        jobs=SimpleNamespace(
            create=lambda kind: (started_jobs.append(kind) or SimpleNamespace(id=f"job-{kind}")),
            start=lambda job, callback: None,
        )
    )
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
        async def bad_req(request: Request, exc: ValueError):  # noqa: ARG001
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        with TestClient(app) as client:
            prefix = "/api/v1/settings/model-center"
            overview = client.get(prefix)
            assert overview.status_code == 200
            assert overview.json()["engine"]["mode"] == "missing"

            status_resp = client.get(prefix + "/engine")
            assert status_resp.status_code == 200
            assert status_resp.json()["ready"] is False

            bad_install = client.post(
                prefix + "/engine/install",
                json={"mirror_preset": "invalid"},
            )
            assert bad_install.status_code == 400

            ok_install = client.post(
                prefix + "/engine/install",
                json={"mirror_preset": "tsinghua", "custom_pypi_index": ""},
            )
            assert ok_install.status_code == 202
            assert ok_install.json() == {"job_id": "job-docling-engine-install"}

            ok_verify = client.post(prefix + "/engine/verify")
            assert ok_verify.status_code == 202
            assert ok_verify.json() == {"job_id": "job-docling-engine-verify"}

            deleted = client.delete(prefix + "/engine")
            assert deleted.status_code == 200
            assert deleted.json()["ready"] is False
            assert started_jobs == ["docling-engine-install", "docling-engine-verify"]


def test_slim_backend_profile_excludes_heavy_ml_and_bundles_worker_source(tmp_path, monkeypatch):
    monkeypatch.setattr(build_backend, "available", lambda name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, "version", lambda name: "1.0")
    fake_uv = tmp_path / ("uv.exe" if sys.platform == "win32" else "uv")
    fake_uv.write_bytes(b"uv-binary")

    args = build_backend.command_args("slim", uv_binary=fake_uv)
    pairs = _options(args)

    # 瘦壳模式绝不引入 PyMuPDF，也必须排除全部重型 ML 与 Docling 库
    for excluded in (
        "pymupdf", "fitz", "docling", "torch", "torchvision",
        "transformers", "scipy", "cv2", "rapidocr", "rapidocr_onnxruntime",
        "sympy", "pandas",
    ):
        assert ("--exclude-module", excluded) in pairs
        assert ("--collect-all", excluded) not in pairs

    # 瘦壳模式保留轻量 pypdfium2 页面渲染、纯 Python 源码树与随包 uv 二进制
    assert ("--collect-all", "pypdfium2") in pairs
    assert any(
        flag == "--add-data" and "sra_src/sociology_research" in value
        for flag, value in pairs
    )
    assert any(
        flag == "--add-binary" and value.endswith(f"{os.pathsep}tools")
        for flag, value in pairs
    )
    # 离线 slim 探针直接通过，且强制检验无 PyMuPDF 红线
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, package=None: None if name in {"pymupdf", "fitz"} else real_find_spec(name, package),
    )
    frozen_backend_probe.slim_profile_imports(check_no_legacy=True)
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, package=None: object() if name == "pymupdf" else real_find_spec(name, package),
    )
    with pytest.raises(AssertionError, match="pymupdf"):
        frozen_backend_probe.slim_profile_imports(check_no_legacy=True)


def test_desktop_model_center_and_storage_engine_ui_contracts():
    desktop = ROOT / "desktop" / "SRA.Desktop"
    dialog = (desktop / "Views" / "ModelCenterWindow.cs").read_text(encoding="utf-8")
    client = (desktop / "Services" / "SraApiClient.cs").read_text(encoding="utf-8")
    xaml_path = desktop / "Views" / "MainWindow.axaml"
    ET.parse(xaml_path)
    xaml = xaml_path.read_text(encoding="utf-8")
    codebehind = (desktop / "Views" / "MainWindow.axaml.cs").read_text(encoding="utf-8")

    # API 客户端具备引擎状态/安装/验证/清理方法
    for symbol in (
        "GetDoclingEngineStatusAsync",
        "StartInstallDoclingEngineAsync",
        "StartVerifyDoclingEngineAsync",
        "DeleteDoclingEngineAsync",
        "api/v1/settings/model-center/engine",
    ):
        assert symbol in client

    # 模型配置中心展示独立引擎卡片并保留既有契约按钮
    for text in (
        "本地 PDF 解析引擎（CPU-only Docling）",
        "一键安装 / 更新 Docling 引擎（CPU 版）",
        "验证本地引擎",
        "清理独立引擎",
        "清华大学 PyPI 镜像",
        "StartInstallDoclingEngineAsync",
    ):
        assert text in dialog

    # 存储透明度展示引擎目录 & PDF 导入前友好引导未安装状态
    assert "EngineStoragePathText" in xaml and "OpenEngineDir_Click" in xaml
    assert "OpenEngineDir_Click" in codebehind
    assert "EnsureDoclingEngineReadyBeforeImportAsync" in codebehind
    assert "initialTabIndex: 2" in codebehind
