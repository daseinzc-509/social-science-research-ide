"""No-network regression tests for SRA table theme, Lite/Pro and visual model settings."""
from __future__ import annotations

import io
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sociology_research.analyzer import _sanitize_model_response
from sociology_research.config import Settings
from sociology_research.llm_client import _http_error_message
from sociology_research.models import LiteExtraction
from sociology_research.parser import DoclingDocumentParser
from sociology_research.api.routes import visual


def test_lite_null_and_unevidenced_metadata_omitted_not_fabricated():
    response = {
        "metadata_facts": [
            {"field_name":"title","value":"真实论文题名",
             "evidence":[{"evidence_id":"E1"}]},
            {"field_name":"year","value":None,"evidence":[]},
            {"field_name":"journal","value":"","evidence":[{"evidence_id":"E2"}]},
            {"field_name":"volume","value":"5","evidence":[]},
            {"field_name":"authors","value":["王明","李敏"],
             "evidence":[{"evidence_id":"E3"}]},
        ],
        "basic_facts": [],
    }
    normalized = _sanitize_model_response(response, LiteExtraction)
    assert normalized["_sra_skipped_metadata_facts"] == 3
    parsed = LiteExtraction.model_validate({
        k:v for k,v in normalized.items() if not k.startswith("_sra_")
    })
    assert [x.field_name for x in parsed.metadata_facts] == ["title","authors"]
    assert parsed.metadata_facts[0].evidence[0].evidence_id == "E1"
    assert "year" not in [x.field_name for x in parsed.metadata_facts]


def test_lite_unsupported_claim_still_fails_schema():
    with pytest.raises(Exception):
        LiteExtraction.model_validate({
            "metadata_facts": [
                {"field_name":"title","value":"unsupported claim","evidence":[]}
            ],
        })
    # Sanitizer does not make an unsupported metadata claim "SUPPORTED".
    cleaned = _sanitize_model_response({
        "metadata_facts": [
            {"field_name":"title","value":"unsupported claim","evidence":[]}
        ],
    }, LiteExtraction)
    assert cleaned["metadata_facts"] == []


def make_http_error(code, payload, headers=None):
    return HTTPError(
        "https://api.example.invalid/chat/completions", code,
        "test provider error", headers or {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def test_429_insufficient_quota_is_not_misdiagnosed_as_ocr_failure():
    msg = _http_error_message(make_http_error(429, {
        "error":{"type":"insufficient_quota", "message":"billing exhausted"}
    }))
    assert "quota/balance exhausted" in msg
    assert "429" in msg
    assert "retry" in msg.lower()


def test_429_rate_limit_exposes_retry_after_not_private_payload():
    msg = _http_error_message(make_http_error(429, {
        "error":{"code":"rate_limit_exceeded","message":"PRIVATE_PDF_TEXT secret-key-xyz"}
    }, {"Retry-After":"45"}))
    assert "rate limit" in msg
    assert "45 seconds" in msg
    assert "PRIVATE_PDF_TEXT" not in msg
    assert "secret-key-xyz" not in msg


def test_config_has_distinct_secure_vision_model_and_toggle():
    settings = Settings.from_values({
        "SRA_PRO_API_KEY":"pro-key",
        "SRA_PRO_API_BASE_URL":"https://pro.example/v1",
        "SRA_VISION_ENABLED":"1",
        "SRA_VISION_USE_PRO":"1",
        "SRA_VISION_MODEL":"vision-example",
        "SRA_FORMULA_ENRICHMENT":"1",
    })
    assert settings.formula_enrichment_enabled
    assert settings.vision_analysis_enabled
    assert settings.uses_pro_vision_connection
    assert settings.require_vision_configuration() == (
        "pro-key","https://pro.example/v1","vision-example"
    )
    separate = Settings.from_values({
        "SRA_VISION_ENABLED":"1","SRA_VISION_USE_PRO":"0",
        "SRA_VISION_MODEL":"visual-only",
        "SRA_VISION_API_BASE_URL":"https://v.example/v1",
        "SRA_VISION_API_KEY":"secret-v",
    })
    assert separate.require_vision_configuration() == (
        "secret-v","https://v.example/v1","visual-only"
    )


def test_formula_preference_applies_to_parser_and_environment_overrides(monkeypatch):
    monkeypatch.delenv("SRA_DOCLING_FORMULA_ENRICHMENT", raising=False)
    monkeypatch.delenv("SRA_FORMULA_ENRICHMENT", raising=False)
    import sociology_research.user_storage as store
    monkeypatch.setattr(store,"read_user_preferences",
                        lambda: {"SRA_FORMULA_ENRICHMENT":"1"})
    parser = DoclingDocumentParser()
    assert parser.formula_enabled
    assert "formula" in parser.version
    monkeypatch.setenv("SRA_FORMULA_ENRICHMENT","0")
    assert not DoclingDocumentParser().formula_enabled


def test_visual_settings_api_returns_no_key_and_saves_separately(monkeypatch):
    initial=Settings.from_values({
        "SRA_VISION_ENABLED":"1",
        "SRA_VISION_API_KEY":"vision-private-key",
        "SRA_VISION_MODEL":"model-a",
        "SRA_VISION_USE_PRO":"0",
    })
    monkeypatch.setattr(visual.Settings,"from_environment",classmethod(lambda cls: initial))
    written={}
    monkeypatch.setattr(visual,"save_local_environment",lambda config: written.update(config))
    app=FastAPI()
    app.include_router(visual.settings_router,prefix="/api/v1")
    with TestClient(app) as client:
        view=client.get("/api/v1/settings/visual")
        assert view.status_code==200
        assert "vision-private-key" not in view.text
        assert view.json()["has_vision_api_key"]
        update=client.put("/api/v1/settings/visual",json={
            "formula_enabled":True,"vision_enabled":True,
            "use_pro_connection":False,
            "vision_api_base_url":"https://v.example/v1",
            "vision_model":"model-a",
            "vision_api_key":"",
        })
        assert update.status_code==200,update.text
    assert written["SRA_VISION_API_KEY"]=="vision-private-key"
    assert written["SRA_FORMULA_ENRICHMENT"]=="1"
    assert written["SRA_VISION_MODEL"]=="model-a"


def test_vision_model_needs_explicit_request_and_never_writes_card(monkeypatch,tmp_path):
    cfg=Settings.from_values({
        "SRA_VISION_ENABLED":"1","SRA_VISION_USE_PRO":"0",
        "SRA_VISION_MODEL":"mock-vision",
        "SRA_VISION_API_KEY":"secret",
        "SRA_VISION_API_BASE_URL":"https://mock.example/v1"
    })
    monkeypatch.setattr(visual.Settings,"from_environment",classmethod(lambda cls:cfg))
    pdf=tmp_path/"paper.pdf"
    pdf.write_bytes(b"%PDF-test")
    digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
    figure=SimpleNamespace(
        id="figure-1",page_number=2,bbox=(12.0,15.0,50.0,90.0)
    )
    record=SimpleNamespace(id="p1",sha256=digest)
    repo=SimpleNamespace(
        get_paper=lambda id:record if id=="p1" else None,
        get_figures=lambda id:[figure],
    )
    app=FastAPI()
    app.include_router(visual.figure_router,prefix="/api/v1")
    app.dependency_overrides[visual.get_services]=lambda:SimpleNamespace(
        repository=repo,papers=SimpleNamespace(pdf_path=lambda id:pdf)
    )
    sends=[]
    monkeypatch.setattr(visual,"_render_figure",
                        lambda path,page,bounds:b"pretend-jpeg")
    monkeypatch.setattr(visual,"_request_vision",
                        lambda key,url,model,pixels:sends.append(
                            (key,url,model,pixels)) or "只能辨认坐标轴文字")
    with TestClient(app) as client:
        route="/api/v1/papers/p1/figures/figure-1/analyze"
        refused=client.post(route,json={"approve_external_send":False})
        assert refused.status_code==422
        assert sends==[]
        response=client.post(route,json={"approve_external_send":True})
        assert response.status_code==200,response.text
        assert response.json()["provenance"]=="AI_INFERRED_UNVERIFIED"
        assert response.json()["saved_to_card"] is False
        assert response.json()["requires_pdf_verification"] is True
        assert len(sends)==1
        assert sends[0][2]=="mock-vision"


def test_ui_contract_theme_and_visual_settings():
    project_root=Path(__file__).resolve().parents[1]
    xaml=(project_root/"desktop/SRA.Desktop/Views/MainWindow.axaml").read_text(encoding="utf-8")
    import xml.etree.ElementTree as ET
    ET.fromstring(xaml)
    table=xaml.split('<TabItem Header="表格">',1)[1].split(
        '<TabItem Header="公式与图片">',1)[0]
    assert 'Background="{DynamicResource CodeBrush}"' not in table
    assert table.count('Foreground="{DynamicResource InkBrush}"') >= 2
    assert 'Click="SaveVisualSettings_Click"' in xaml
    assert 'Click="AnalyzeFigure_Click"' in xaml
    assert 'Click="RegionReview_Click"' in xaml
    assert 'Settings.VisionModel' in xaml
    api=(project_root/"desktop/SRA.Desktop/Services/SraApiClient.cs").read_text(encoding="utf-8")
    assert "GetVisualSettingsAsync(" in api
    assert "AnalyzeFigureAsync(" in api
    assert "GetRegionReviewPageAsync(" in api
    assert "RunReviewedComparisonAsync(" in api



def test_secure_visual_model_preferences_survive_lite_pro_saving(monkeypatch, tmp_path):
    from sociology_research import config as module
    # No OS DPAPI/Keychain is invoked. Intercept the two actual storage calls.
    stored_secrets={}
    stored_prefs={}
    original=Settings.from_values({
        "SRA_LITE_API_KEY":"lite-key",
        "SRA_PRO_API_KEY":"pro-key",
        "SRA_VISION_API_KEY":"vision-key",
        "SRA_VISION_ENABLED":"1",
        "SRA_VISION_MODEL":"vision-X",
        "SRA_FORMULA_ENRICHMENT":"1",
    })
    monkeypatch.setattr(module.Settings,"from_environment",
                        classmethod(lambda cls:original))
    monkeypatch.setattr(module,"write_user_secrets",
                        lambda values:stored_secrets.update(values))
    monkeypatch.setattr(module,"write_user_preferences",
                        lambda values:stored_prefs.update(values))
    monkeypatch.setattr(module,"_scrub_project_model_settings",lambda:None)
    # Other tests shouldn't be affected by these process environment updates.
    with monkeypatch.context() as scoped:
        for key in module._MODEL_FIELDS:
            scoped.delenv(key,raising=False)
        module.save_local_environment({"SRA_PRO_MODEL":"new-pro"})
        assert stored_secrets["SRA_VISION_API_KEY"]=="vision-key"
        assert stored_prefs["SRA_VISION_MODEL"]=="vision-X"
        assert stored_prefs["SRA_FORMULA_ENRICHMENT"]=="1"
        assert "SRA_VISION_API_KEY" not in stored_prefs
        assert stored_prefs["SRA_PRO_MODEL"]=="new-pro"


def _tiny_pdf_bytes() -> bytes:
    stream = b"BT /F1 11 Tf 30 170 Td (Study figures) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 220] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(result)
    result.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n")
    result.extend(str(start).encode() + b"\n%%EOF\n")
    return bytes(result)


def test_pdfium_crops_a_genuine_pdf_figure_without_remote_call(tmp_path):
    pytest.importorskip("pypdfium2")
    pdf=tmp_path/"one.pdf"
    pdf.write_bytes(_tiny_pdf_bytes())
    crop=visual._render_figure(pdf,1,(10,10,180,120))
    assert crop[:3] == b"\xff\xd8\xff"
    assert len(crop)>100


def test_vision_no_provider_request_after_pdf_integrity_failure(monkeypatch,tmp_path):
    pdf=tmp_path/"paper.pdf"
    pdf.write_bytes(b"%PDF-valid-original")
    valid_hash=hashlib.sha256(pdf.read_bytes()).hexdigest()
    pdf.write_bytes(b"%PDF-changed-after-import")
    with pytest.raises(Exception) as exc:
        visual._verify_pdf(pdf,valid_hash)
    assert getattr(exc.value,"status_code",None)==409


def test_http_429_client_does_not_repeat_billable_request(monkeypatch):
    from sociology_research import llm_client as module
    count=[]
    def fail(request,timeout):
        count.append(1)
        raise make_http_error(429, {
            "error":{"code":"rate_limit_exceeded","message":"rate limited"}
        },{"Retry-After":"20"})
    monkeypatch.setattr(module,"urlopen",fail)
    client=module.OpenAICompatibleClient(
        api_key="test-key",base_url="https://v.example/v1")
    with pytest.raises(module.ModelRequestError,match="HTTP 429"):
        client.complete_json(
            model="mock-model",system_prompt="test",user_prompt="{}",
            max_tokens=100)
    assert len(count)==1
