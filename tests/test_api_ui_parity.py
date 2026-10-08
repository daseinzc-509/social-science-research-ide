from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from sociology_research.api import create_app
from sociology_research.models import PaperCard, PaperMetadata, PaperRecord, ParseStatus, ParsedDocument, SourceBlock
from sociology_research.repository import ResearchRepository


def _seed_paper(tmp_path: Path, repository: ResearchRepository) -> str:
    paper_id = "paper-ui-parity"
    stored = tmp_path / "papers" / "paper-ui-parity.pdf"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(b"%PDF-fake-ui-parity")
    block = SourceBlock(
        id="block-ui-parity",
        page_number=1,
        text="References\nSmith, 2024. A useful paper about evidence.",
        role="body",
        source_label="test:fake",
    )
    document = ParsedDocument(
        paper_id=paper_id,
        sha256="a" * 64,
        source_path=str(stored),
        page_count=1,
        status=ParseStatus.SUCCESS,
        parser_version="fake-parser-v1",
        blocks=[block],
    )
    record = PaperRecord(
        id=paper_id,
        sha256=document.sha256,
        original_name="ui-parity.pdf",
        stored_path=str(stored),
        page_count=1,
        parse_status=ParseStatus.SUCCESS,
        parser_version=document.parser_version,
        warnings=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    repository.save_import(record, document)
    repository.save_card(
        PaperCard(
            paper_id=paper_id,
            metadata=PaperMetadata(title="UI Parity Paper", authors=["Researcher"], year=2026),
            lite_model="lite-test",
            pro_model="pro-test",
            prompt_version="ui-parity-test",
        )
    )
    return paper_id


def test_desktop_parity_routes(tmp_path: Path):
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    paper_id = _seed_paper(tmp_path, repository)
    client = TestClient(create_app(data_dir=tmp_path, repository=repository))

    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.json()["papers"] == 1
    assert dashboard.json()["analyzed"] == 1

    detail = client.get(f"/api/v1/papers/{paper_id}")
    assert detail.status_code == 200
    assert detail.json()["card"]["metadata"]["title"] == "UI Parity Paper"

    saved = client.post(
        f"/api/v1/papers/{paper_id}/metadata",
        json={
            "values": {
                "title": "Manual Title",
                "authors": "A；B",
                "year": "2025",
                "journal": "Manual Journal",
                "volume": "1",
                "issue": "2",
                "pages": "1-10",
                "doi": "10.1000/test",
                "keywords": "one；two",
            },
            "source_note": "manual test",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["metadata_overrides"]["values"]["title"] == "Manual Title"

    detail = client.get(f"/api/v1/papers/{paper_id}").json()
    assert detail["card"]["metadata"]["title"] == "Manual Title"

    card_export = client.get(f"/api/v1/papers/{paper_id}/card.json")
    assert card_export.status_code == 200
    assert '"Manual Title"' in card_export.text

    reading = client.get(f"/api/v1/papers/{paper_id}/reading-report")
    assert reading.status_code == 200
    assert "Manual Title" in reading.text

    parsed = client.get(f"/api/v1/papers/{paper_id}/text.txt")
    assert parsed.status_code == 200
    assert "source_block_id=block-ui-parity" in parsed.text

    refs = client.post(f"/api/v1/papers/{paper_id}/references/extract")
    assert refs.status_code == 200
    batch_refs = client.post("/api/v1/references/extract-batch", json={"paper_ids": [paper_id]})
    assert batch_refs.status_code == 200
    assert batch_refs.json()["selected"] == 1

    doctor = client.get("/api/v1/maintenance/doctor")
    assert doctor.status_code == 200
    assert doctor.json()["counts"]["papers"] == 1

    prune = client.post(
        "/api/v1/maintenance/prune-cache",
        json={"all_runs": False, "apply": False, "vacuum": False},
    )
    assert prune.status_code == 200
    assert "matched_runs" in prune.json()

    reset = client.delete(f"/api/v1/papers/{paper_id}/metadata")
    assert reset.status_code == 200
    detail = client.get(f"/api/v1/papers/{paper_id}").json()
    assert detail["card"]["metadata"]["title"] == "UI Parity Paper"
