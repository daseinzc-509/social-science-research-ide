"""P1 架构重构回归测试：服务下沉、统一视觉客户端代理、OpenAPI 强类型契约与上帝模块拆分。"""

from __future__ import annotations

import json
from pathlib import Path

from sociology_research import analyzer, network_settings
from sociology_research.analysis import evidence, layout, metadata, reporting, review
from sociology_research.api import create_app
from sociology_research.repository import ResearchRepository
from sociology_research.services import create_application_services
from sociology_research.services.model_center import ModelCenterService
from sociology_research.services.region_review_service import RegionReviewService
from sociology_research.services.visual import VisualService, request_vision_description

ROOT = Path(__file__).resolve().parents[1]


class _FakeHttpResponse:
    def __init__(self, payload: dict, headers: dict[str, str] | None = None) -> None:
        self._data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._data[:size] if size >= 0 else self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_vision_requests_use_unified_openai_client_ai_proxy(monkeypatch):
    """验证视觉分析复用 OpenAICompatibleClient 的 AI 代理策略，不再绕过独立代理配置。"""
    captured = []

    class FakeOpener:
        def open(self, request, timeout):
            captured.append((request.full_url, timeout, request.headers.get("Authorization")))
            return _FakeHttpResponse(
                {"choices": [{"message": {"content": "图1 展示了实验组与对照组均值差异。"}}]},
                headers={"x-request-id": "vision-proxy-ok"},
            )

    monkeypatch.setattr(network_settings, "ai_request_opener", lambda: FakeOpener())

    text = request_vision_description(
        "dummy-vision-key",
        "https://mock-vision.example/v1",
        "vision-test-model",
        b"\xff\xd8\xffjpeg-crop",
    )
    assert text == "图1 展示了实验组与对照组均值差异。"
    assert len(captured) == 1
    assert captured[0][0] == "https://mock-vision.example/v1/chat/completions"
    assert captured[0][1] == 180
    assert captured[0][2] == "Bearer dummy-vision-key"


def test_application_services_hosts_refactored_domain_services_and_reuses_stores(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    services = create_application_services(tmp_path, repo)

    assert isinstance(services.visual, VisualService)
    assert isinstance(services.region_review, RegionReviewService)
    assert isinstance(services.model_center, ModelCenterService)

    # 确认区域复核服务在应用生命周期内持有并复用同一组持久化 Store
    assert services.region_review.review_store.database_path == repo.database_path
    assert services.region_review.reanalysis_store.database_path == repo.database_path


def test_openapi_schema_exposes_typed_response_models_for_refactored_routes(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    app = create_app(data_dir=tmp_path, repository=repo)
    schema = app.openapi()
    paths = schema["paths"]

    expected_typed_endpoints = [
        ("/api/v1/dashboard", "get", "200"),
        ("/api/v1/maintenance/doctor", "get", "200"),
        ("/api/v1/maintenance/prune-cache", "post", "200"),
        ("/api/v1/settings/storage", "get", "200"),
        ("/api/v1/settings/visual", "get", "200"),
        ("/api/v1/settings/visual", "put", "200"),
        ("/api/v1/settings/model-center", "get", "200"),
        ("/api/v1/settings/model-center/enhancements", "get", "200"),
        ("/api/v1/settings/model-center/profiles/{profile_id}/models", "get", "200"),
        ("/api/v1/settings/model-center/test/{profile_id}", "post", "200"),
        ("/api/v1/jobs", "get", "200"),
        ("/api/v1/jobs/{job_id}/cancel", "post", "200"),
        ("/api/v1/papers/{paper_id}/figures/{figure_id}/analyze", "post", "200"),
        ("/api/v1/papers/{paper_id}/region-review/pages/{page_number}/blocks", "get", "200"),
        ("/api/v1/papers/{paper_id}/region-review/pages/{page_number}/preview", "post", "200"),
        ("/api/v1/papers/{paper_id}/region-review/confirm", "post", "200"),
        ("/api/v1/papers/{paper_id}/region-review/reviews", "get", "200"),
        ("/api/v1/papers/{paper_id}/region-review/reanalysis/preview", "get", "200"),
        ("/api/v1/papers/{paper_id}/region-review/reanalysis/reports", "get", "200"),
    ]
    for path, method, status in expected_typed_endpoints:
        assert path in paths, f"Missing OpenAPI path: {path}"
        response_schema = paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
        assert "$ref" in response_schema, f"{method.upper()} {path} lacks typed Pydantic response_model"


def test_analyzer_modular_split_preserves_facade_and_reduces_god_module():
    analyzer_lines = (ROOT / "src/sociology_research/analyzer.py").read_text(encoding="utf-8").splitlines()
    assert len(analyzer_lines) < 700

    # 验证五个子模块符号与门面 re-export 一致
    assert analyzer._logical_units is layout._logical_units
    assert analyzer._build_raw_span_registry is evidence._build_raw_span_registry
    assert analyzer._derive_layout_metadata_candidates is metadata._derive_layout_metadata_candidates
    assert analyzer._sanitize_model_response is review._sanitize_model_response
    assert analyzer.build_deep_reading_report is reporting.build_deep_reading_report
    assert analyzer.render_deep_reading_markdown is reporting.render_deep_reading_markdown


def test_desktop_mainwindow_viewmodel_split_and_jobs_client_contract():
    vm_dir = ROOT / "desktop/SRA.Desktop/ViewModels"
    main_vm = (vm_dir / "MainWindowViewModel.cs").read_text(encoding="utf-8")
    assert len(main_vm.splitlines()) < 600
    for part in (
        "MainWindowViewModel.Library.cs",
        "MainWindowViewModel.Projection.cs",
        "MainWindowViewModel.Workspace.cs",
    ):
        part_text = (vm_dir / part).read_text(encoding="utf-8")
        assert "public partial class MainWindowViewModel" in part_text

    client_src = (ROOT / "desktop/SRA.Desktop/Services/SraApiClient.cs").read_text(encoding="utf-8")
    assert "ListJobsAsync(" in client_src
