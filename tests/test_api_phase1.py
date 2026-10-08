from pathlib import Path

from fastapi.testclient import TestClient

from sociology_research.api import create_app
from sociology_research.repository import ResearchRepository
from sociology_research.services.jobs import JobService


def test_health_and_empty_library(tmp_path: Path):
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    client = TestClient(create_app(data_dir=tmp_path, repository=repository))

    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "sra", "api_version": "v1"}

    response = client.get("/api/v1/papers")
    assert response.status_code == 200
    assert response.json() == {"papers": []}


def test_job_service_completes():
    jobs = JobService()
    job = jobs.create("test")
    jobs.start(job, lambda progress: (progress("hello"), {"ok": True})[1])

    import time

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
