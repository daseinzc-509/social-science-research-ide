from __future__ import annotations

import hashlib
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from sociology_research.api import create_app
from sociology_research.models import ParseStatus, PaperRecord, ParsedDocument, SourceBlock
from sociology_research.repository import ResearchRepository
from sociology_research.services import create_application_services
from sociology_research.services.jobs import JobService
from sociology_research.ui import SRAWebApp


class FakeImportPipeline:
    """Small import double: no PDF library or model/network call is used by API tests."""

    def __init__(self, data_dir: Path, repository: ResearchRepository, *, progress=None, **_kwargs):
        self.data_dir = Path(data_dir)
        self.repository = repository
        self.progress = progress

    def import_pdf(self, pdf_path: Path):
        source = Path(pdf_path)
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        existing = self.repository.get_by_sha256(digest)
        if existing is not None:
            document = ParsedDocument(
                paper_id=existing.id,
                sha256=digest,
                source_path=existing.stored_path,
                page_count=existing.page_count,
                status=existing.parse_status,
                parser_version=existing.parser_version,
                blocks=self.repository.get_source_blocks(existing.id),
            )
            return existing, document, False

        paper_id = str(uuid4())
        destination = self.data_dir / "papers" / f"{digest}.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        block = SourceBlock(
            id=f"block-{paper_id}",
            page_number=1,
            text="Test PDF content for local API import.",
            role="body",
            source_label="test:fake",
        )
        document = ParsedDocument(
            paper_id=paper_id,
            sha256=digest,
            source_path=str(destination),
            page_count=1,
            status=ParseStatus.SUCCESS,
            parser_version="fake-parser-v1",
            blocks=[block],
        )
        record = PaperRecord(
            id=paper_id,
            sha256=digest,
            original_name=source.name,
            stored_path=str(destination),
            page_count=1,
            parse_status=ParseStatus.SUCCESS,
            parser_version=document.parser_version,
            warnings=[],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.repository.save_import(record, document)
        return record, document, True


def wait_for_job(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"done", "error"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_health_and_empty_library(tmp_path: Path):
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    client = TestClient(create_app(data_dir=tmp_path, repository=repository))

    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "sra", "api_version": "v1"}

    response = client.get("/api/v1/papers")
    assert response.status_code == 200
    assert response.json() == {"papers": []}


def test_single_and_batch_import_api(tmp_path: Path):
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    services = create_application_services(
        tmp_path,
        repository,
        import_pipeline_factory=FakeImportPipeline,
    )
    client = TestClient(create_app(services=services))

    response = client.post(
        "/api/v1/papers/import",
        files={"file": ("one.pdf", b"%PDF-fake-one", "application/pdf")},
    )
    assert response.status_code == 202
    job = wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["created"] is True

    response = client.post(
        "/api/v1/papers/import-batch",
        files=[
            ("files", ("two.pdf", b"%PDF-fake-two", "application/pdf")),
            ("files", ("three.pdf", b"%PDF-fake-three", "application/pdf")),
        ],
    )
    assert response.status_code == 202
    job = wait_for_job(client, response.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["imported"] == 2
    assert job["result"]["failed"] == 0

    papers = client.get("/api/v1/papers").json()["papers"]
    assert len(papers) == 3


def test_upload_validation(tmp_path: Path):
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    services = create_application_services(
        tmp_path,
        repository,
        import_pipeline_factory=FakeImportPipeline,
    )
    client = TestClient(create_app(services=services))

    response = client.post(
        "/api/v1/papers/import",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 415

    response = client.post(
        "/api/v1/papers/import",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400


def test_legacy_web_ui_uses_shared_services(tmp_path: Path):
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    app = SRAWebApp(
        tmp_path,
        repository,
        import_pipeline_factory=FakeImportPipeline,
    )
    job_id = app.start_import("legacy.pdf", b"%PDF-legacy")

    for _ in range(200):
        snapshot = app.jobs.get(job_id)
        if snapshot and snapshot["status"] in {"done", "error"}:
            break
        time.sleep(0.01)
    snapshot = app.jobs.get(job_id)
    assert snapshot is not None
    assert snapshot["status"] == "done", snapshot
    assert len(app.list_papers()) == 1
    assert app.services.jobs is app.jobs


def test_job_service_completes():
    jobs = JobService()
    job = jobs.create("test")
    jobs.start(job, lambda progress: (progress("hello"), {"ok": True})[1])

    for _ in range(100):
        snapshot = jobs.get(job.id)
        if snapshot and snapshot["status"] in {"done", "error"}:
            break
        time.sleep(0.01)
    snapshot = jobs.get(job.id)
    assert snapshot is not None
    assert snapshot["status"] == "done"
    assert snapshot["messages"] == ["hello"]
    assert snapshot["result"] == {"ok": True}
