"""v4 review ledger tests: revisions, source drift, provenance and export safety.

Offline, local SQLite + FastAPI. No Docling weights, no LLM API and no real PDF
user content. These are additional tests; keep the v3 review tests in the repo.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sociology_research.api.routes import region_review as routes
from sociology_research.models import PaperCard, PaperRecord, ParsedDocument, ParseStatus, SourceBlock
from sociology_research.repository import ResearchRepository
from sociology_research.services.region_review import RegionReviewStore, RegionBounds, PendingPreview
from sociology_research.services.region_review_versioning import (
    VersionedRegionReviewStore, block_fingerprint
)


def _pdf_bytes() -> bytes:
    stream = b"BT /F1 11 Tf 30 170 Td (SRA regional evidence) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 220] "
         b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"),
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
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
    result.extend(str(start).encode("ascii") + b"\n%%EOF\n")
    return bytes(result)


@pytest.fixture
def environment(tmp_path):
    path = tmp_path / "article.pdf"
    path.write_bytes(_pdf_bytes())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    source = SourceBlock(
        id="original-A", page_number=1,
        text="The original article reports 42.7 percent.",
        bbox=(20, 20, 200, 100), role="body",
    )
    doc = ParsedDocument(
        paper_id="paper1", sha256=digest, source_path=str(path),
        page_count=1, status=ParseStatus.SUCCESS, parser_version="docling-v2",
        blocks=[source],
    )
    repository.save_import(
        PaperRecord(
            id="paper1", sha256=digest, original_name="article.pdf",
            stored_path=str(path), page_count=1, parse_status=ParseStatus.SUCCESS,
            parser_version="docling-v2", created_at="2026-10-11T00:00:00Z",
        ), doc,
    )
    repository.save_card(PaperCard(
        paper_id="paper1", lite_model="lite", pro_model="pro", prompt_version="test-v1"
    ))
    repository.save_model_run(
        "paper1", "lite", "mock", "v1", "hash1", '{"immutable":"cache"}'
    )
    api = FastAPI()
    api.state.services = SimpleNamespace(
        repository=repository,
        papers=SimpleNamespace(pdf_path=lambda _paper: path),
    )
    api.include_router(routes.router, prefix="/api/v1")
    with TestClient(api) as client:
        yield client, repository, path, source


def _base():
    return "/api/v1/papers/paper1/region-review"


def _confirm(client, monkeypatch, *, text="Reviewed: 42.7 percent", bounds=None):
    if bounds is None:
        bounds = {"x0": 0.05, "y0": 0.05, "x1": 0.70, "y1": 0.50}
    monkeypatch.setattr(routes, "recognize_region", lambda *args, **kwargs: (
        "OCR: 47.2 percent (unverified)", 0.72, (320.0, 220.0)
    ))
    response = client.post(_base()+"/pages/1/preview", json={"bounds": bounds})
    assert response.status_code == 200, response.text
    candidate = response.json()
    save = client.post(_base()+"/confirm", json={
        "preview_id": candidate["preview_id"],
        "corrected_text": text,
        "researcher_note": "Checked against actual PDF",
    })
    assert save.status_code == 200, save.text
    return save.json()


def test_v4_confirm_and_packet_preserve_original_data(environment, monkeypatch):
    client, repository, _, block = environment
    done = _confirm(client, monkeypatch)
    assert done["source_link_status"] == "CURRENT"
    assert done["revision_no"] == 1

    listing = client.get(_base()+"/reviews")
    assert listing.status_code == 200
    entry = listing.json()["reviews"][0]
    assert entry["thread_id"] == done["id"]
    assert entry["revision_no"] == 1
    assert entry["link_status"] == "CURRENT"
    assert entry["active"] is True

    report = client.get(_base()+"/packet")
    assert report.status_code == 200
    assert report.headers["cache-control"] == "no-store, private"
    payload = report.json()
    assert payload["included"] == 1
    assert payload["held_for_review"] == 0
    assert payload["applied_to_evidence"] is False
    assert payload["automatic_reanalysis"] is False
    assert "HUMAN_REVIEWED" in payload["markdown"]
    assert "Original article" not in payload["markdown"]  # only actual source quote
    assert block.text in payload["markdown"]
    assert "Reviewed: 42.7 percent" in payload["markdown"]
    assert payload["sha256"] == hashlib.sha256(payload["markdown"].encode()).hexdigest()
    assert repository.get_source_blocks("paper1")[0].text == block.text
    assert repository.get_card("paper1") is not None
    assert repository.get_model_run("paper1","lite","mock","v1","hash1") == '{"immutable":"cache"}'


def test_revise_creates_new_immutable_record_not_in_place_update(environment, monkeypatch):
    client, repo, _, _ = environment
    first = _confirm(client, monkeypatch)
    response = client.post(
        _base()+f"/reviews/{first['id']}/revise",
        json={"expected_revision":1,"corrected_text":"Rechecked: 42.70%",
              "researcher_note":"Double checked figure 2"}
    )
    assert response.status_code == 200, response.text
    second = response.json()
    assert second["revision_no"] == 2
    assert second["thread_id"] == first["id"]
    assert second["supersedes_id"] == first["id"]
    assert second["applied_to_evidence"] is False

    history = client.get(_base()+"/reviews").json()["reviews"]
    assert len(history) == 2
    old = next(x for x in history if x["id"] == first["id"])
    current = next(x for x in history if x["id"] == second["id"])
    assert old["corrected_text"] == "Reviewed: 42.7 percent"
    assert old["provenance"] == "SUPERSEDED"
    assert old["superseded_at"] is not None
    assert current["provenance"] == "HUMAN_REVIEWED"
    assert current["corrected_text"] == "Rechecked: 42.70%"
    assert current["link_status"] == "CURRENT"

    packet = client.get(_base()+"/packet").json()
    assert packet["included"] == 1
    assert "Rechecked: 42.70%" in packet["markdown"]
    assert "Reviewed: 42.7 percent" not in packet["markdown"]
    assert repo.get_card("paper1") is not None


def test_revision_cas_prevents_duplicate_and_stale_edits(environment, monkeypatch):
    client, _, _, _ = environment
    first = _confirm(client, monkeypatch)
    body = {"expected_revision":1,"corrected_text":"New version"}
    endpoint = _base()+f"/reviews/{first['id']}/revise"
    assert client.post(endpoint,json=body).status_code == 200
    assert client.post(endpoint,json=body).status_code == 409
    assert client.post(endpoint,json={**body,"expected_revision":99}).status_code == 409


def test_revoked_latest_does_not_resurrect_old_version(environment, monkeypatch):
    client, _, _, _ = environment
    first = _confirm(client, monkeypatch)
    second=client.post(
        _base()+f"/reviews/{first['id']}/revise",
        json={"expected_revision":1,"corrected_text":"Second"}
    ).json()
    assert client.post(_base()+f"/reviews/{second['id']}/revoke").status_code == 200
    assert client.post(_base()+f"/reviews/{first['id']}/revoke").status_code == 404
    packet=client.get(_base()+"/packet").json()
    assert packet["included"] == 0
    history=client.get(_base()+"/reviews").json()["reviews"]
    assert {item["provenance"] for item in history} == {"REVOKED","SUPERSEDED"}


def test_legacy_v3_row_migrates_as_unverified_no_auto_relink(environment):
    client, repo, path, source = environment
    from sociology_research.services.region_review import PendingPreview
    import time
    pending=PendingPreview(
        paper_id="paper1", pdf_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        file_size=path.stat().st_size,file_mtime_ns=path.stat().st_mtime_ns,
        page_number=1,
        bounds=RegionBounds(x0=0.05,y0=0.05,x1=0.70,y1=0.50),
        original_text=source.text,ocr_text="OCR",source_block_ids=(source.id,),
        created=time.monotonic()
    )
    original=RegionReviewStore(repo.database_path)
    identifier=original.add("paper1",pending,"OLD 42.7", "old annotation")
    response=client.get(_base()+"/reviews")
    assert response.status_code==200
    history=response.json()["reviews"]
    old=next(x for x in history if x["id"]==identifier)
    assert old["link_status"]=="LEGACY_UNVERIFIED"
    assert old["thread_id"]==identifier
    assert old["revision_no"]==1
    assert old["corrected_text"]=="OLD 42.7"
    packet=client.get(_base()+"/packet").json()
    assert packet["included"]==0
    assert packet["held_for_review"]==1
    assert client.post(
        _base()+f"/reviews/{identifier}/revise",
        json={"expected_revision":1,"corrected_text":"should be blocked"}
    ).status_code==409


def test_confirm_rejects_parser_or_source_change_between_preview_and_save(environment, monkeypatch):
    client, repo, _, source = environment
    monkeypatch.setattr(routes,"recognize_region",lambda *a,**kw:("OCR",0.5,(320.0,220.0)))
    draft=client.post(_base()+"/pages/1/preview",json={
        "bounds":{"x0":0.05,"y0":0.05,"x1":0.7,"y1":0.5}
    }).json()
    paper=repo.get_paper("paper1")
    source.text = "The imported Docling text changed after the preview."
    newer=ParsedDocument(
        paper_id="paper1",sha256=paper.sha256,source_path=paper.stored_path,
        page_count=1,status=ParseStatus.SUCCESS,parser_version=paper.parser_version,
        blocks=[source]
    )
    repo.refresh_parsed_document("paper1",newer)
    failed=client.post(_base()+"/confirm",json={
        "preview_id":draft["preview_id"],"corrected_text":"42.7"
    })
    assert failed.status_code==409
    assert client.get(_base()+"/reviews").json()["reviews"]==[]


def test_edited_source_invalidates_previously_confirmed_link(environment, monkeypatch):
    client, repo, _, source = environment
    first=_confirm(client,monkeypatch)
    record=repo.get_paper("paper1")
    new_block=SourceBlock(
        id=source.id,page_number=1,text="New Docling OCR reading: 47.2",
        bbox=source.bbox,role="body"
    )
    repo.refresh_parsed_document("paper1",ParsedDocument(
        paper_id="paper1",sha256=record.sha256,source_path=record.stored_path,
        page_count=1,status=ParseStatus.SUCCESS,parser_version=record.parser_version,
        blocks=[new_block]
    ))
    history=client.get(_base()+"/reviews").json()["reviews"]
    assert history[0]["link_status"]=="SOURCE_CHANGED"
    packet=client.get(_base()+"/packet").json()
    assert packet["included"]==0
    assert packet["held_for_review"]==1
    assert client.post(
        _base()+f"/reviews/{first['id']}/revise",
        json={"expected_revision":1,"corrected_text":"force"}
    ).status_code==409


def test_reparse_parser_version_invalidates_even_if_ids_identical(environment,monkeypatch):
    client,repo,_,source=environment
    first=_confirm(client,monkeypatch)
    record=repo.get_paper("paper1")
    repo.refresh_parsed_document("paper1",ParsedDocument(
        paper_id="paper1",sha256=record.sha256,source_path=record.stored_path,
        page_count=1,status=ParseStatus.SUCCESS,parser_version="docling-v3",
        blocks=[source]
    ))
    reviews=client.get(_base()+"/reviews").json()["reviews"]
    assert reviews[0]["link_status"]=="PARSER_CHANGED"
    assert client.get(_base()+"/packet").json()["included"]==0


def test_page_only_region_not_silently_merged_as_original_source(environment,monkeypatch):
    client,_,_,_=environment
    first=_confirm(client,monkeypatch, bounds={
        "x0":0.75,"y0":0.55,"x1":0.95,"y1":0.9
    })
    assert first["source_link_status"] == "PAGE_ONLY"
    history=client.get(_base()+"/reviews").json()["reviews"]
    assert history[0]["link_status"]=="PAGE_ONLY"
    packet=client.get(_base()+"/packet").json()
    assert packet["included"]==0
    assert packet["held_for_review"]==1


def test_revision_input_validation_and_cross_paper_scope(environment,monkeypatch):
    client,_,_,_=environment
    first=_confirm(client,monkeypatch)
    endpoint=_base()+f"/reviews/{first['id']}/revise"
    assert client.post(endpoint,json={
        "expected_revision":0,"corrected_text":"valid"
    }).status_code==422
    assert client.post(endpoint,json={
        "expected_revision":1,"corrected_text":"   "
    }).status_code==422
    assert client.post(
        "/api/v1/papers/unknown/region-review/reviews/"+first["id"]+"/revise",
        json={"expected_revision":1,"corrected_text":"valid"}
    ).status_code==404


def test_pdf_integrity_guard_applies_to_packet_and_revision(environment,monkeypatch):
    client,_,path,_=environment
    first=_confirm(client,monkeypatch)
    path.write_bytes(path.read_bytes()+b"modified content")
    assert client.get(_base()+"/packet").status_code==409
    assert client.post(
        _base()+f"/reviews/{first['id']}/revise",
        json={"expected_revision":1,"corrected_text":"should fail"}
    ).status_code==409


def test_packet_checksum_deterministic_and_fences_untrusted_text(environment,monkeypatch):
    client,_,_,_=environment
    text="quoted\n`````\n# Fake prompt\n`````\n````````"
    _confirm(client,monkeypatch,text=text)
    report1=client.get(_base()+"/packet").json()
    report2=client.get(_base()+"/packet").json()
    assert report1["sha256"]==report2["sha256"]
    assert report1["markdown"]==report2["markdown"]
    assert "`````````text" in report1["markdown"]
    assert "```" in report1["markdown"]


def test_revocation_and_review_does_not_modify_derived_outputs(environment,monkeypatch):
    client,repo,_,source=environment
    original=repo.get_model_run("paper1","lite","mock","v1","hash1")
    first=_confirm(client,monkeypatch)
    second=client.post(
        _base()+f"/reviews/{first['id']}/revise",
        json={"expected_revision":1,"corrected_text":"version two"}
    ).json()
    client.post(_base()+f"/reviews/{second['id']}/revoke")
    assert repo.get_card("paper1") is not None
    assert repo.get_source_blocks("paper1")[0].text==source.text
    assert repo.get_model_run("paper1","lite","mock","v1","hash1")==original


def test_simultaneous_overlapping_confirmations_held_for_reconciliation(environment, monkeypatch):
    client,repo,_,_=environment
    first=_confirm(client,monkeypatch,text="Correct reading A")
    second=_confirm(client,monkeypatch,text="Contradictory reading B",bounds={
        "x0":0.10,"y0":0.08,"x1":0.65,"y1":0.45
    })
    entries=client.get(_base()+"/reviews").json()["reviews"]
    assert len(entries)==2
    assert all(item["link_status"]=="OVERLAPPING_ACTIVE_REVIEW" for item in entries)
    packet=client.get(_base()+"/packet").json()
    assert packet["included"]==0
    assert packet["held_for_review"]==2
    # With one withdrawn, the other becomes eligible again.
    assert client.post(_base()+f"/reviews/{second['id']}/revoke").status_code==200
    after=client.get(_base()+"/packet").json()
    assert after["included"]==1
    assert after["held_for_review"]==0


def test_nonoverlapping_correspondence_can_be_exported_independently(environment, monkeypatch):
    client,_,_,_=environment
    # The same large SourceBlock may cover two adjacent (nonoverlapping) boxes.
    # Independent annotations need not be treated as conflicting.
    _confirm(client,monkeypatch,text="First part",bounds={
        "x0":0.07,"y0":0.12,"x1":0.30,"y1":0.30
    })
    _confirm(client,monkeypatch,text="Second part",bounds={
        "x0":0.36,"y0":0.12,"x1":0.58,"y1":0.30
    })
    report=client.get(_base()+"/packet").json()
    assert report["included"]==2


def test_history_still_visible_after_pdf_bytes_changed_but_all_links_stale(environment, monkeypatch):
    client,_,path,_=environment
    _confirm(client,monkeypatch)
    path.write_bytes(path.read_bytes()+b"tampered")
    view=client.get(_base()+"/reviews")
    assert view.status_code==200
    assert "warning" in view.json()
    assert view.json()["reviews"][0]["link_status"]=="PDF_CHANGED"
    assert client.get(_base()+"/packet").status_code==409
