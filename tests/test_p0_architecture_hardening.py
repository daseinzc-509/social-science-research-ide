"""P0 架构稳态加固专项回归测试：SQLite WAL 并发、JobService 限流取消回收、防孤儿进程。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from sociology_research.api import create_app
from sociology_research.api.desktop_entry import is_process_alive, start_parent_watchdog
from sociology_research.db import open_sqlite_connection
from sociology_research.models import ParseStatus, PaperRecord, ParsedDocument, SourceBlock
from sociology_research.repository import ResearchRepository
from sociology_research.services import create_application_services
from sociology_research.services.jobs import JobService
from sociology_research.services.region_review import RegionReviewStore
from sociology_research.services.region_review_reanalysis import ReviewReanalysisStore
from sociology_research.services.region_review_versioning import VersionedRegionReviewStore

ROOT = Path(__file__).resolve().parents[1]


def _make_paper(paper_id: str, sha: str, pdf_path: Path) -> tuple[PaperRecord, ParsedDocument]:
    block = SourceBlock(
        id=f"blk-{paper_id}",
        page_number=1,
        text=f"Sample content for {paper_id}",
        role="body",
        source_label="test:p0",
    )
    doc = ParsedDocument(
        paper_id=paper_id,
        sha256=sha,
        source_path=str(pdf_path),
        page_count=1,
        status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        blocks=[block],
    )
    record = PaperRecord(
        id=paper_id,
        sha256=sha,
        original_name=f"{paper_id}.pdf",
        stored_path=str(pdf_path),
        page_count=1,
        parse_status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        warnings=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    return record, doc


def test_sqlite_stores_enable_wal_foreign_keys_and_busy_timeout(tmp_path: Path):
    db_path = tmp_path / "research.sqlite3"
    repo = ResearchRepository(db_path)
    review_store = RegionReviewStore(db_path)
    versioned_store = VersionedRegionReviewStore(db_path)
    reanalysis_store = ReviewReanalysisStore(db_path)

    for store in (repo, review_store, versioned_store, reanalysis_store):
        conn = store._connect()
        try:
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
            busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
            assert journal_mode == "wal"
            assert foreign_keys == 1
            assert busy_timeout >= 15000
        finally:
            conn.close()


def test_sqlite_concurrent_reads_and_writes_without_lock_errors(tmp_path: Path):
    db_path = tmp_path / "research.sqlite3"
    repo = ResearchRepository(db_path)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")

    base_record, base_doc = _make_paper("paper-base", "a" * 64, pdf)
    repo.save_import(base_record, base_doc)

    errors: list[Exception] = []

    def writer(worker_idx: int) -> None:
        try:
            for step in range(15):
                repo.add_user_note("paper-base", f"note-{worker_idx}-{step}", f"note body {step}")
                repo.save_model_run(
                    "paper-base",
                    "lite",
                    "test-model",
                    "v1",
                    f"{worker_idx}-{step}",
                    '{"ok": true}',
                )
        except Exception as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(30):
                assert repo.get_paper("paper-base") is not None
                assert len(repo.get_source_blocks("paper-base")) == 1
                repo.list_papers()
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=writer, args=(0,)),
        threading.Thread(target=writer, args=(1,)),
        threading.Thread(target=reader),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Unexpected concurrent SQLite errors: {errors}"
    conn = open_sqlite_connection(db_path)
    try:
        note_count = conn.execute("SELECT COUNT(*) FROM user_notes").fetchone()[0]
        assert note_count == 30
    finally:
        conn.close()


def test_job_service_bounds_heavy_lane_concurrency_and_cancels_queued_with_cleanup():
    jobs = JobService(max_heavy_workers=1)
    first_started = threading.Event()
    release_first = threading.Event()
    active = 0
    max_active = 0
    lock = threading.Lock()
    second_ran = False
    second_cleaned = threading.Event()

    def run_first(progress):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        first_started.set()
        progress("working first")
        assert release_first.wait(timeout=5)
        with lock:
            active -= 1
        return {"index": 1}

    def run_second(progress):
        nonlocal second_ran
        second_ran = True
        progress("should not run")
        return {"index": 2}

    def run_third(progress):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        progress("working third")
        time.sleep(0.02)
        with lock:
            active -= 1
        return {"index": 3}

    j1 = jobs.create("import")
    j2 = jobs.create("import")
    j3 = jobs.create("reparse")

    jobs.start(j1, run_first)
    assert first_started.wait(timeout=5)
    jobs.start(j2, run_second, cleanup=second_cleaned.set)
    jobs.start(j3, run_third)

    # 排队中的 j2 被取消：应立即转 cancelled、触发 cleanup 且永不执行 target
    cancelled = jobs.cancel(j2.id)
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert second_cleaned.wait(timeout=2)

    release_first.set()

    for jid in (j1.id, j3.id):
        for _ in range(200):
            snap = jobs.get(jid)
            if snap and snap["status"] in {"done", "error", "cancelled"}:
                break
            time.sleep(0.01)
        assert jobs.get(jid)["status"] == "done"

    assert max_active == 1
    assert second_ran is False


def test_job_service_cancels_running_job_at_progress_checkpoint_and_via_api(tmp_path: Path):
    repository = ResearchRepository(tmp_path / "research.sqlite3")
    services = create_application_services(tmp_path, repository)
    client = TestClient(create_app(services=services))

    entered = threading.Event()
    allow_next = threading.Event()
    job = services.jobs.create("batch-analysis")

    def long_work(progress):
        progress("step 1")
        entered.set()
        assert allow_next.wait(timeout=5)
        progress("step 2")  # 此处将触发 JobCancelled
        return {"completed": 2}

    services.jobs.start(job, long_work)
    assert entered.wait(timeout=5)

    cancel_resp = client.post(f"/api/v1/jobs/{job.id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["cancel_requested"] is True
    # 重复请求取消应幂等，不重复追加提示日志
    repeat_resp = client.post(f"/api/v1/jobs/{job.id}/cancel")
    assert repeat_resp.status_code == 200
    assert repeat_resp.json()["messages"].count(
        "Cancellation requested; stopping at next safe checkpoint…"
    ) == 1

    allow_next.set()

    final = None
    for _ in range(200):
        resp = client.get(f"/api/v1/jobs/{job.id}")
        assert resp.status_code == 200
        final = resp.json()
        if final["status"] in {"done", "error", "cancelled"}:
            break
        time.sleep(0.01)

    assert final is not None
    assert final["status"] == "cancelled"
    assert final["error"] is None

    list_resp = client.get("/api/v1/jobs")
    assert list_resp.status_code == 200
    ids = [item["id"] for item in list_resp.json()["jobs"]]
    assert job.id in ids

    assert client.post("/api/v1/jobs/nonexistent/cancel").status_code == 404


def test_job_service_prunes_old_terminal_jobs_without_evicting_active():
    jobs = JobService(max_retained_jobs=20)
    gate = threading.Event()
    active_started = threading.Event()

    active_job = jobs.create("analyze")

    def hold(progress):
        active_started.set()
        gate.wait(timeout=5)
        return {"ok": True}

    jobs.start(active_job, hold)
    assert active_started.wait(timeout=5)

    done_ids: list[str] = []
    for i in range(30):
        j = jobs.create("test")
        jobs.start(j, lambda _p, idx=i: {"i": idx})
        done_ids.append(j.id)

    # 等待所有短任务结束
    for jid in done_ids[-5:]:
        for _ in range(200):
            snap = jobs.get(jid)
            if snap and snap["status"] == "done":
                break
            time.sleep(0.01)

    # 活跃任务必须仍在注册表中，总保留数被压在上限内
    assert jobs.get(active_job.id) is not None
    assert len(jobs.list_jobs(limit=200)) <= 20
    assert jobs.get(done_ids[0]) is None

    gate.set()


def test_parent_process_liveness_and_watchdog_trigger():
    assert is_process_alive(os.getpid()) is True
    assert is_process_alive(0) is False
    assert is_process_alive(-5) is False

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=10)
    assert is_process_alive(child.pid) is False

    triggered = threading.Event()
    checks = {"count": 0}

    def fake_alive(_pid: int) -> bool:
        checks["count"] += 1
        return checks["count"] < 2

    thread, stop = start_parent_watchdog(
        999999,
        triggered.set,
        poll_interval=0.05,
        alive_check=fake_alive,
    )
    try:
        assert triggered.wait(timeout=2.0)
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    finally:
        stop.set()


def test_desktop_p0_orphan_guard_and_job_cancel_contracts():
    host_src = (ROOT / "desktop/SRA.Desktop/Services/LocalBackendHost.cs").read_text(encoding="utf-8")
    job_obj_src = (ROOT / "desktop/SRA.Desktop/Services/WindowsProcessJobObject.cs").read_text(encoding="utf-8")
    client_src = (ROOT / "desktop/SRA.Desktop/Services/SraApiClient.cs").read_text(encoding="utf-8")
    vm_src = (ROOT / "desktop/SRA.Desktop/ViewModels/MainWindowViewModel.cs").read_text(encoding="utf-8")
    xaml_src = (ROOT / "desktop/SRA.Desktop/Views/MainWindow.axaml").read_text(encoding="utf-8")

    assert "--parent-pid" in host_src
    assert "WindowsProcessJobObject" in host_src
    assert "JobObjectLimitKillOnJobClose" in job_obj_src
    assert "AssignProcessToJobObject" in job_obj_src
    assert "CancelJobAsync(" in client_src
    assert "SraJobCancelledException" in client_src
    assert "CancelActiveJobAsync" in vm_src
    assert 'Click="CancelJob_Click"' in xaml_src
