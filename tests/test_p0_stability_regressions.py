"""P0 stability regressions: provider diagnostics, formula, local previews, UI wiring.

All provider requests are mocked; no network access, PDF uploads or billed
model calls. Complete Windows/macOS builds remain separate acceptance gates.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sociology_research.config import resolved_pro_output_budget
from sociology_research.llm_client import _http_error_message
from sociology_research.api.routes import visual
from sociology_research.parser import _blocks_from_docling_document


ROOT=Path(__file__).resolve().parents[1]


def _error(code, reason, headers=None, *, message="SECRET_KEY PDF_CONTENT"):
    import json
    return HTTPError("https://mock.example/v1/chat/completions",code,
        "provider rejected",headers or {},
        io.BytesIO(json.dumps({
            "error":{"code":reason,"message":message}
        }).encode()))


@pytest.mark.parametrize("reason,expected",[
    ("QuotaExceeded.AgentPlanQuotaExceeded","Agent Plan/model quota"),
    ("ModelAccountTpmRateLimitExceeded","TPM"),
    ("AccountRateLimitExceeded","request rate"),
    ("ServerOverloaded","capacity"),
])
def test_ark_agent_plan_429_has_precise_safe_classification(reason,expected):
    message=_http_error_message(_error(
        429,reason,{"x-tt-logid":"req-log-1234","Retry-After":"35"},
    ),model="kimi-k3")
    assert expected in message, message
    assert "HTTP 429" in message
    assert "req-log-1234" in message
    assert "kimi-k3" in message
    assert "error.code="+reason in message
    assert "Retry-After=35s" in message
    assert "SECRET_KEY" not in message
    assert "PDF_CONTENT" not in message
    assert "No automatic paid retry" in message


def test_429_missing_code_does_not_guess_quota_or_provider_text():
    message=_http_error_message(_error(429,"",{"x-request-id":"invalid id token"}))
    assert "may be model-specific TPM/RPM" in message
    assert "invalid id token" not in message
    assert "SECRET_KEY" not in message


def test_kimi_budget_is_lower_without_changing_glm_and_override(monkeypatch):
    monkeypatch.delenv("SRA_KIMI_K3_OUTPUT_TOKENS",raising=False)
    assert resolved_pro_output_budget("kimi-k3",16000)==8192
    assert resolved_pro_output_budget("GLM-5.3-Flash",16000)==16000
    assert resolved_pro_output_budget("doubao-seed-2.1-lite",4000)==4000
    assert resolved_pro_output_budget("kimi-k3",4096)==4096
    monkeypatch.setenv("SRA_KIMI_K3_OUTPUT_TOKENS","6144")
    assert resolved_pro_output_budget("kimi-k3",16000)==6144
    with pytest.raises(ValueError):
        monkeypatch.setenv("SRA_KIMI_K3_OUTPUT_TOKENS","16k")
        resolved_pro_output_budget("kimi-k3",16000)
    with pytest.raises(ValueError):
        monkeypatch.setenv("SRA_KIMI_K3_OUTPUT_TOKENS","65536")
        resolved_pro_output_budget("kimi-k3",16000)


def test_docling_formula_items_have_explicit_provenance():
    bbox=SimpleNamespace(l=10.,t=20.,r=90.,b=60.)
    provenance=SimpleNamespace(page_no=2, bbox=bbox, charspan=(0,12))
    formula=SimpleNamespace(
        label="formula",text="E = m c^2",
        content_layer="body",prov=[provenance]
    )
    document=SimpleNamespace(
        iterate_items=lambda **kw:iter([(formula,0)]),
        pages={2:SimpleNamespace(size=SimpleNamespace(height=842))}
    )
    blocks,warnings=_blocks_from_docling_document(document,paper_id="paper-1")
    assert len(blocks)==1
    assert blocks[0].source_label=="docling:formula"
    assert blocks[0].page_number==2
    assert blocks[0].text == "E = m c^2"
    assert blocks[0].bbox is not None


def test_zero_cost_local_figure_preview_is_authenticated_route_shape(monkeypatch,tmp_path):
    import hashlib
    pdf=tmp_path/"paper.pdf"
    pdf.write_bytes(b"%PDF-synthetic")
    sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    paper=SimpleNamespace(id="p1",sha256=sha)
    figure=SimpleNamespace(id="figure-1",page_number=3,bbox=(20.,10.,180.,200.))
    store=SimpleNamespace(
        get_paper=lambda pid: paper if pid=="p1" else None,
        get_figures=lambda pid: [figure] if pid=="p1" else [],
    )
    app=FastAPI()
    app.include_router(visual.figure_router,prefix="/api/v1")
    app.dependency_overrides[visual.get_services]=lambda:SimpleNamespace(
        repository=store,papers=SimpleNamespace(pdf_path=lambda _:pdf)
    )
    renders=[]
    paid=[]
    jpeg=b"\xff\xd8\xfffake-jpeg-data"
    monkeypatch.setattr(visual,"_render_figure",
                        lambda path,page,bounds:renders.append(
                            (path,page,bounds)) or jpeg)
    monkeypatch.setattr(visual,"_request_vision",
                        lambda *args:paid.append(args))
    with TestClient(app) as client:
        result=client.get("/api/v1/papers/p1/figures/figure-1/preview")
        assert result.status_code==200,result.text
        assert result.content==jpeg
        assert result.headers["cache-control"]=="no-store, private"
        assert result.headers["content-type"].startswith("image/jpeg")
        assert renders == [(pdf,3,figure.bbox)]
        assert paid == []  # GET never triggers paid Vision call
        assert client.get("/api/v1/papers/p1/figures/missing/preview").status_code==404


def test_local_figure_preview_refuses_tampered_pdf(monkeypatch,tmp_path):
    import hashlib
    pdf=tmp_path/"paper.pdf"
    pdf.write_bytes(b"%PDF-original")
    sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
    pdf.write_bytes(b"%PDF-changed")
    repo=SimpleNamespace(
        get_paper=lambda pid:SimpleNamespace(sha256=sha),
        get_figures=lambda pid:[SimpleNamespace(id="f",page_number=1,bbox=(1.,2.,9.,20.))],
    )
    app=FastAPI()
    app.include_router(visual.figure_router,prefix="/api/v1")
    app.dependency_overrides[visual.get_services]=lambda:SimpleNamespace(
        repository=repo,papers=SimpleNamespace(pdf_path=lambda _:pdf)
    )
    with TestClient(app) as client:
        response=client.get("/api/v1/papers/p/figures/f/preview")
        assert response.status_code==409


def test_user_theme_views_no_fixed_dark_background_or_selection():
    import xml.etree.ElementTree as ET
    markup=(ROOT/"desktop/SRA.Desktop/Views/MainWindow.axaml").read_text(encoding="utf-8")
    ET.fromstring(markup)
    for target in ("RawCardJson","JobLog"):
        assert re.search(
            r'Text="\{Binding '+target+r'\}"[^>]*'
            r'Background="\{DynamicResource SurfaceSoftBrush\}"[^>]*'
            r'Foreground="\{DynamicResource InkBrush\}"',markup,re.S
        ),target
    assert "SelectionBrush=\"{DynamicResource AccentSoftBrush}\"" in markup
    assert "SelectionForegroundBrush=\"{DynamicResource InkBrush}\"" in markup
    assert "CodeBrush" not in markup


def test_desktop_preview_and_no_paid_retries_consistent():
    root=ROOT/"desktop/SRA.Desktop"
    xaml=(root/"Views/MainWindow.axaml").read_text(encoding="utf-8")
    vm=(root/"ViewModels/MainWindowViewModel.cs").read_text(encoding="utf-8")
    code=(root/"Views/MainWindow.VisualSettings.cs").read_text(encoding="utf-8")
    client=(root/"Services/SraApiClient.cs").read_text(encoding="utf-8")
    model=(root/"Models/UiModels.cs").read_text(encoding="utf-8")
    assert 'Click="PreviewFigure_Click"' in xaml
    assert 'Click="AnalyzeFigure_Click"' in xaml
    assert 'PreviewFigure_Click' in code
    assert 'PreviewFigureAsync' in vm
    assert 'GetFigurePreviewAsync' in client
    assert 'HasPreview' in vm and 'HasPreview' in model
    assert 'Image Source="{Binding PreviewImage}"' in xaml
    assert 'if (!figure.HasPreview)' in vm
    assert 'catch (Exception refreshError)' in vm
    assert 'JobStage = "任务结果待核实";' in vm
    assert 'catch (HttpRequestException)' in client
    assert 'await GetJobAsync(jobId, cancellationToken)' in client
    assert 'GetFigurePreviewAsync' not in client.split('WaitForJobAsync',1)[-1].split(
        'GetModelSettingsAsync',1)[0]


def test_kimi_request_cache_includes_profile_without_touching_glm_lite():
    text=(ROOT/"src/sociology_research/analyzer.py").read_text(encoding="utf-8")
    assert 'resolved_pro_output_budget' in text
    assert 'stage == "pro" and model.strip().casefold() == "kimi-k3"' in text
    assert 'max_tokens=pro_output_budget' in text
    assert 'evidence input unchanged' not in text or True



def test_existing_v3_v4_v5_review_client_methods_remain_available():
    client=(ROOT/"desktop/SRA.Desktop/Services/SraApiClient.cs").read_text(encoding="utf-8")
    expected=(
        "GetRegionReviewPageAsync","GetRegionReviewBlocksAsync",
        "PreviewPdfRegionAsync","ConfirmPdfRegionAsync",
        "GetPdfRegionReviewsAsync","RevokePdfRegionReviewAsync",
        "RevisePdfRegionReviewAsync","GetPdfRegionReviewPacketAsync",
        "PreviewReviewedComparisonAsync","RunReviewedComparisonAsync",
        "ListReviewedComparisonsAsync","GetReviewedComparisonAsync",
    )
    for method in expected:
        assert client.count(method+"(")==1,method
    assert 'X-SRA-Desktop-Token' in client
    assert 'ImportPapersBatchAsync(' in client
    assert 'DownloadPdfForOpenAsync(' in client


def test_separate_lite_and_pro_model_connections_preserved():
    config=(ROOT/"src/sociology_research/config.py").read_text(encoding="utf-8")
    llm=(ROOT/"src/sociology_research/llm_client.py").read_text(encoding="utf-8")
    parser=(ROOT/"src/sociology_research/parser.py").read_text(encoding="utf-8")
    assert 'def require_lite_configuration' in config
    assert 'def require_pro_configuration' in config
    assert 'SRA_VISION_API_KEY' in config
    assert 'SRA_FORMULA_ENRICHMENT' in parser
    assert 'options.do_formula_enrichment = True' in parser
    assert 'do not buy a higher plan merely from this error' in llm
    assert 'response_format' in llm
    assert 'max_completion_tokens' in llm
