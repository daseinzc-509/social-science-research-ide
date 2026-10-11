"""Offline API and data-safety tests for rectangle OCR and human confirmation.

No external credentials/models/Docling weights are used. OCR is stubbed in
API integration tests. pypdfium2 renders a tiny generated PDF for the real
page image test; the RapidOCR output adapter is tested with a fake engine.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sociology_research.api.routes import region_review as routes
from sociology_research.models import PaperCard, PaperRecord, ParseStatus, ParsedDocument, SourceBlock
from sociology_research.repository import ResearchRepository
from sociology_research.services import region_review as service


def one_page_pdf():
    content = b"BT /F1 11 Tf 30 170 Td (Original PDF text) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 220] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, value in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{i} 0 obj\n".encode() + value + b"\nendobj\n")
    index = len(result)
    result.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(b"trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n")
    result.extend(str(index).encode("ascii") + b"\n%%EOF\n")
    return bytes(result)


@pytest.fixture
def app_context(tmp_path):
    src = tmp_path / "real-research-paper.pdf"
    src.write_bytes(one_page_pdf())
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    doc = ParsedDocument(
        paper_id="paper1", sha256=digest, source_path=str(src),
        page_count=1, status=ParseStatus.SUCCESS,
        parser_version="fixture", blocks=[
            SourceBlock(id="original-A", page_number=1,
                        text="Original author statement 42.7", role="body",
                        bbox=(20, 20, 200, 100)),
            SourceBlock(id="original-B", page_number=1,
                        text="A different unrelated section", role="body",
                        bbox=(215, 150, 300, 200)),
        ],
    )
    record = PaperRecord(
        id="paper1", sha256=digest, original_name=src.name,
        stored_path=str(src), page_count=1, parse_status=ParseStatus.SUCCESS,
        parser_version="fixture", created_at="2026-10-10T00:00:00Z",
    )
    repository.save_import(record, doc)
    card = PaperCard(
        paper_id="paper1", lite_model="lite", pro_model="pro", prompt_version="test",
    )
    repository.save_card(card)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        repository=repository,
        papers=SimpleNamespace(pdf_path=lambda paper_id: src),
    )
    app.include_router(routes.router, prefix="/api/v1")
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, repository, src


@pytest.mark.parametrize("coords", [
    {"x0": -0.1, "y0": 0, "x1": 0.4, "y1": 0.4},
    {"x0": 0.5, "y0": 0.1, "x1": 0.4, "y1": 0.8},
    {"x0": 0.1, "y0": 0.1, "x1": 0.1, "y1": 0.2},
    {"x0": 0.1, "y0": 0.1, "x1": 1.1, "y1": 0.9},
    {"x0": float("nan"), "y0": 0.1, "x1": 0.3, "y1": 0.5},
])
def test_invalid_selection_is_rejected(coords):
    with pytest.raises(ValidationError):
        service.RegionBounds.model_validate(coords)


def test_pdfium_raster_is_real_and_headers_are_bounded(app_context):
    pytest.importorskip("pypdfium2")
    client, repository, src = app_context
    url = "/api/v1/papers/paper1/region-review/pages/1.png"
    result = client.get(url)
    assert result.status_code == 200
    assert result.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert int(result.headers["x-sra-page-count"]) == 1
    assert float(result.headers["x-sra-page-width"]) == 320.0
    assert float(result.headers["x-sra-page-height"]) == 220.0
    assert result.headers["cache-control"] == "no-store, private"
    assert client.get(url+"?scale=0.1").status_code == 422
    assert client.get("/api/v1/papers/paper1/region-review/pages/2.png").status_code == 422


def test_page_blocks_read_only_and_include_original_bboxes(app_context):
    client, repository, src = app_context
    response = client.get("/api/v1/papers/paper1/region-review/pages/1/blocks")
    assert response.status_code == 200
    data = response.json()
    assert data["provenance"] == "DOCLING_SOURCE"
    assert data["source_blocks"][0]["bbox"] == [20.0, 20.0, 200.0, 100.0]
    assert len(repository.get_source_blocks("paper1")) == 2


def test_confirmed_annotation_keeps_author_evidence_and_paper_card_unchanged(
    app_context, monkeypatch
):
    client, repository, src = app_context
    monkeypatch.setattr(routes, "recognize_region", lambda *args, **kwargs: (
        "Unverified OCR: 47.2", 0.8, (320.0, 220.0)
    ))
    base = "/api/v1/papers/paper1/region-review"
    candidate = client.post(base + "/pages/1/preview", json={
        "bounds": {"x0": 0.05, "y0": 0.05, "x1": 0.68, "y1": 0.48}
    })
    assert candidate.status_code == 200, candidate.text
    proposal = candidate.json()
    assert proposal["provenance"] == "OCR_CANDIDATE_UNVERIFIED"
    assert proposal["source_block_ids"] == ["original-A"]
    assert proposal["original_text"] == "Original author statement 42.7"
    assert proposal["ocr_candidate"] == "Unverified OCR: 47.2"
    assert repository.get_card("paper1") is not None

    saved = client.post(base + "/confirm", json={
        "preview_id": proposal["preview_id"],
        "corrected_text": "Author text manually verified: 42.7",
        "researcher_note": "Verified visually from PDF page 1",
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["applied_to_evidence"] is False
    reviews = client.get(base + "/reviews").json()["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["provenance"] == "HUMAN_REVIEWED"
    assert reviews[0]["ocr_candidate"] == "Unverified OCR: 47.2"
    assert reviews[0]["extracted_text"] == "Original author statement 42.7"
    assert reviews[0]["corrected_text"] == "Author text manually verified: 42.7"
    assert repository.get_source_blocks("paper1")[0].text == "Original author statement 42.7"
    assert repository.get_card("paper1") is not None

    duplicate = client.post(base + "/confirm", json={
        "preview_id": proposal["preview_id"],
        "corrected_text": "Should not save twice",
    })
    assert duplicate.status_code == 410
    assert len(client.get(base + "/reviews").json()["reviews"]) == 1

    revoke = client.post(base + f"/reviews/{reviews[0]['id']}/revoke")
    assert revoke.status_code == 200
    assert client.get(base + "/reviews").json()["reviews"][0]["provenance"] == "REVOKED"
    assert repository.get_source_blocks("paper1")[0].text == "Original author statement 42.7"


def test_preview_cannot_be_confirmed_after_pdf_changes(app_context, monkeypatch):
    client, _, src = app_context
    monkeypatch.setattr(routes, "recognize_region", lambda *args, **kwargs: (
        "OCR text", None, (320., 220.)
    ))
    base = "/api/v1/papers/paper1/region-review"
    candidate = client.post(base + "/pages/1/preview", json={
        "bounds": {"x0": 0.1, "y0": 0.1, "x1": 0.6, "y1": 0.5}
    }).json()
    src.write_bytes(src.read_bytes() + b"modified")
    response = client.post(base + "/confirm", json={
        "preview_id": candidate["preview_id"],
        "corrected_text": "reviewed",
    })
    assert response.status_code == 409


def test_empty_ocr_candidate_can_be_corrected_only_by_human(app_context, monkeypatch):
    client, _, _ = app_context
    monkeypatch.setattr(routes, "recognize_region", lambda *args, **kwargs: (
        "", None, (320., 220.)
    ))
    base = "/api/v1/papers/paper1/region-review"
    candidate = client.post(base + "/pages/1/preview", json={
        "bounds": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.3}
    }).json()
    assert candidate["ocr_candidate"] == ""
    assert client.post(base + "/confirm", json={
        "preview_id": candidate["preview_id"],
        "corrected_text": "  ",
    }).status_code == 422
    assert client.post(base + "/confirm", json={
        "preview_id": candidate["preview_id"],
        "corrected_text": "verified manually from original PDF",
    }).status_code == 200


def test_no_change_to_existing_source_table_schema_by_region_review(app_context):
    _, repository, src = app_context
    with sqlite3.connect(repository.database_path) as connection:
        cols = [r[1] for r in connection.execute("PRAGMA table_info(source_blocks)")]
        assert "text" in cols
    store = service.RegionReviewStore(repository.database_path)
    assert store.list("paper1") == []
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='region_reviews'"
        ).fetchone() is not None
        assert connection.execute("SELECT COUNT(*) FROM source_blocks").fetchone()[0] == 2


def test_rapidocr_adapter_handles_empty_and_scores(monkeypatch):
    class FakeEngine:
        def __call__(self, image):
            return SimpleNamespace(txts=("row A", "row B"), scores=(0.90, 0.85))
    class RapidModule:
        RapidOCR = lambda *args: FakeEngine()
    import sys
    monkeypatch.setitem(sys.modules, "rapidocr", RapidModule())
    monkeypatch.setattr(service, "_ocr_engine", None)
    text, score = service._rapidocr_lines(object())
    assert text == "row A\nrow B"
    assert score == pytest.approx(0.875)
    monkeypatch.setattr(service, "_ocr_engine", lambda image: SimpleNamespace(txts=None, scores=None))
    text, score = service._rapidocr_lines(object())
    assert text == "" and score is None


def test_storage_delete_cascades_to_researcher_confirmations(app_context, monkeypatch):
    client, repository, src = app_context
    monkeypatch.setattr(routes, "recognize_region", lambda *args, **kwargs: (
        "OCR", None, (320.,220.)
    ))
    base = "/api/v1/papers/paper1/region-review"
    preview = client.post(base + "/pages/1/preview", json={
        "bounds": {"x0":0.1,"y0":0.1,"x1":0.4,"y1":0.4}
    }).json()
    client.post(base + "/confirm", json={
        "preview_id": preview["preview_id"], "corrected_text": "human correction"
    })
    with sqlite3.connect(repository.database_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM papers WHERE id=?",("paper1",))
        assert conn.execute("SELECT COUNT(*) FROM region_reviews").fetchone()[0] == 0


def test_missing_paper_not_exposed(app_context):
    client, _, _ = app_context
    assert client.get("/api/v1/papers/nonexistent/region-review/pages/1.png").status_code == 404


def test_manual_transcription_preview_works_without_ocr_model(app_context, monkeypatch):
    client, repository, src = app_context
    def fail_if_ocr_called(*args, **kwargs):
        raise AssertionError("manual transcription must not load OCR models")
    monkeypatch.setattr(routes, "recognize_region", fail_if_ocr_called)
    base = "/api/v1/papers/paper1/region-review"
    response = client.post(base + "/pages/1/preview", json={
        "bounds": {"x0":0.10, "y0":0.10, "x1":0.60, "y1":0.50},
        "manual_only": True,
    })
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["provenance"] == "MANUAL_TRANSCRIPTION_DRAFT"
    assert preview["ocr_candidate"] == ""
    assert client.post(base + "/confirm", json={
        "preview_id": preview["preview_id"],
        "corrected_text": "Human reviewed value: 42.7",
    }).status_code == 200
    assert repository.get_source_blocks("paper1")[0].text == "Original author statement 42.7"


def test_ocr_preview_expiry_and_paper_scope():
    from dataclasses import replace
    import time
    registry = service.RegionPreviewRegistry(ttl_seconds=10, limit=2)
    bounds = service.RegionBounds(x0=0.1,y0=0.1,x1=0.5,y1=0.6)
    pending = service.PendingPreview(
        paper_id="paper-A", pdf_sha256="x", file_size=100,
        file_mtime_ns=1, page_number=1, bounds=bounds,
        original_text="author", ocr_text="ocr",
        source_block_ids=(), created=time.monotonic()
    )
    key = registry.add(pending)
    with pytest.raises(service.PreviewExpired):
        registry.get(key, "paper-B")
    assert registry.get(key, "paper-A") == pending
    registry.consume(key)
    with pytest.raises(service.PreviewExpired):
        registry.get(key, "paper-A")
    old_key = registry.add(replace(pending, created=time.monotonic()-100))
    with pytest.raises(service.PreviewExpired):
        registry.get(old_key, "paper-A")


def test_client_backend_wiring_source_contract():
    """Catch forgotten router registration when shipping via narrow installer."""
    import importlib.util
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    route_module = root / "src/sociology_research/api/routes/region_review.py"
    assert route_module.is_file()
    # Installed project app must register the router; test executable source,
    # not just whether the standalone route functions exist.
    app_source = (root / "src/sociology_research/api/app.py").read_text(encoding="utf-8")
    assert "app.include_router(region_review.router, prefix=API_PREFIX)" in app_source
    gui = (root / "desktop/SRA.Desktop/Views/MainWindow.axaml").read_text(encoding="utf-8")
    assert 'Click="RegionReview_Click"' in gui
    api_client = (root / "desktop/SRA.Desktop/Services/SraApiClient.cs").read_text(encoding="utf-8")
    for name in (
        "GetRegionReviewPageAsync", "GetRegionReviewBlocksAsync", "PreviewPdfRegionAsync",
        "ConfirmPdfRegionAsync", "GetPdfRegionReviewsAsync", "RevokePdfRegionReviewAsync",
    ):
        assert name in api_client


def test_region_ocr_receives_opencv_bgr_pixels(monkeypatch, tmp_path):
    pytest.importorskip("numpy")
    from PIL import Image
    seen = []
    monkeypatch.setattr(
        service, "open_pdf_page",
        lambda pdf, page, scale: (Image.new("RGB", (120, 80), (10, 20, 30)), (320., 220.), 1)
    )
    def fake_engine(pixels):
        seen.append(tuple(int(v) for v in pixels[0, 0]))
        return "seen", 0.91
    text, score, dims = service.recognize_region(
        tmp_path / "stub.pdf", 1,
        service.RegionBounds(x0=0.1,y0=0.1,x1=0.8,y1=0.8),
        engine=fake_engine,
    )
    assert seen == [(30, 20, 10)]
    assert text == "seen" and score == 0.91 and dims == (320., 220.)


def test_desktop_token_middleware_protects_new_region_review_routes(app_context):
    from sociology_research.api.desktop_entry import DesktopTokenMiddleware
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    _client, repository, pdf_path = app_context
    token = "b" * 64
    app = FastAPI()
    app.state.services = SimpleNamespace(
        repository=repository,
        papers=SimpleNamespace(pdf_path=lambda paper_id: pdf_path),
    )
    app.include_router(routes.router, prefix="/api/v1")
    app.add_middleware(DesktopTokenMiddleware, token=token)
    uri = "/api/v1/papers/paper1/region-review/pages/1/blocks"
    with TestClient(app) as client:
        assert client.get(uri).status_code == 401
        assert client.get(uri, headers={"X-SRA-Desktop-Token":"bad"}).status_code == 401
        assert client.get(uri, headers={"X-SRA-Desktop-Token":token}).status_code == 200


def test_new_ocr_does_not_update_llm_model_runs(app_context, monkeypatch):
    client, repository, src = app_context
    monkeypatch.setattr(routes, "recognize_region", lambda *a, **kw: (
        "text from image", 0.9, (320.,220.)
    ))
    repository.save_model_run("paper1","lite","m","prompt","inputhash",'{"raw":"preserved"}')
    before = repository.get_model_run("paper1","lite","m","prompt","inputhash")
    base = "/api/v1/papers/paper1/region-review"
    draft = client.post(base+"/pages/1/preview", json={
        "bounds":{"x0":0.1,"y0":0.1,"x1":0.7,"y1":0.6}
    }).json()
    client.post(base+"/confirm", json={
        "preview_id":draft["preview_id"],"corrected_text":"Human verified"
    })
    assert repository.get_model_run("paper1","lite","m","prompt","inputhash") == before
