"""v0.3.0 收口需求（R1–R4）全栈回归测试：
1. R2：导入自动归入专题（含去重幂等不覆写阅读状态 + 不存在专题 404）与按专题精准批量分析；
2. R3：SQLite Online Backup 安全 `.zip` 备份（零凭据 + 防 symlink）、只读校验（防篡改 & zip-slip）与离线原子恢复（自动重写跨机 PDF 路径 & 保留 `.bak`）+ HTTP 端点；
3. R4：`GET /api/v1/papers` 聚合返回 `reviewed_claims` 与 `user_notes_count`；
4. R1–R4 桌面端 XAML / C# 契约校验。
"""

from __future__ import annotations

import hashlib
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from sociology_research.api.app import create_app
from sociology_research.backup import (
    MANIFEST_NAME,
    create_backup_archive,
    restore_backup_archive,
    verify_backup_archive,
)
from sociology_research.models import (
    PaperCard,
    PaperMetadata,
    PaperRecord,
    ParseStatus,
    ParsedDocument,
    SourceBlock,
)
from sociology_research.pipeline import ImportPipeline
from sociology_research.repository import ResearchRepository
from sociology_research.services.analysis import AnalysisService
from sociology_research.services.jobs import JobService
from sociology_research.services.papers import PaperService

ROOT = Path(__file__).resolve().parents[1]


def _wait_job(client: TestClient, job_id: str) -> dict:
    for _ in range(300):
        resp = client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in {"done", "error", "cancelled"}:
            return body
        time.sleep(0.01)
    raise AssertionError(f"Job {job_id} did not finish in time")


def _seed_paper(
    data_dir: Path,
    repo: ResearchRepository,
    *,
    paper_id: str,
    title: str,
    year: int | None = 2024,
    with_card: bool = False,
) -> Path:
    papers_dir = data_dir / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    pdf_bytes = f"%PDF-1.4\n% {paper_id} {title}\n".encode("utf-8")
    pdf_path = papers_dir / f"{paper_id}.pdf"
    pdf_path.write_bytes(pdf_bytes)
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    parsed = ParsedDocument(
        paper_id=paper_id,
        sha256=sha,
        source_path=str(pdf_path.resolve()),
        page_count=3,
        status=ParseStatus.SUCCESS,
        parser_version="test-docling:2.134.0",
        blocks=[
            SourceBlock(
                id=f"blk-{paper_id}-1",
                page_number=1,
                text=f"{title}: empirical evidence on social mobility.",
                role="body",
            )
        ],
    )
    record = PaperRecord(
        id=paper_id,
        sha256=sha,
        original_name=f"{paper_id}.pdf",
        stored_path=str(pdf_path.resolve()),
        page_count=3,
        parse_status=ParseStatus.SUCCESS,
        parser_version="test-docling:2.134.0",
        created_at="2026-01-01T00:00:00+00:00",
    )
    repo.save_import(record, parsed)
    if with_card:
        card = PaperCard(
            paper_id=paper_id,
            lite_model="lite-test",
            pro_model="pro-test",
            prompt_version="v-test",
            metadata=PaperMetadata(title=title, authors=["Zhang San"], year=year, journal="Social Studies"),
        )
        repo.save_card(card)
    return pdf_path


def test_import_auto_attaches_to_project_idempotently_and_rejects_unknown_project(tmp_path):
    data_dir = tmp_path / "data"
    repo = ResearchRepository(data_dir / "research.sqlite3")
    proj = repo.create_project("社会分层专题", research_question="代际流动机制")

    class StubParser:
        version = "stub-docling:2.134.0"

        def parse(self, path, *, paper_id, sha256, progress=None):
            return ParsedDocument(
                paper_id=paper_id,
                sha256=sha256,
                source_path=str(path),
                page_count=1,
                status=ParseStatus.SUCCESS,
                parser_version=self.version,
                blocks=[
                    SourceBlock(
                        id=f"blk-{paper_id}",
                        page_number=1,
                        text="Stub parsed block for project import test.",
                        role="body",
                    )
                ],
            )

    def make_pipeline(d, r, progress=None):
        return ImportPipeline(d, r, parser=StubParser(), progress=progress)

    app = create_app(data_dir=data_dir)
    app.state.services.papers = PaperService(
        data_dir,
        app.state.services.repository,
        app.state.services.jobs,
        import_pipeline_factory=make_pipeline,
    )

    with TestClient(app) as client:
        # 1. 不存在的专题在入队前同步 404
        missing = client.post(
            "/api/v1/papers/import",
            files={"file": ("one.pdf", b"%PDF-1.4\n% one", "application/pdf")},
            data={"project_id": "nonexistent-project"},
        )
        assert missing.status_code == 404

        # 2. 单篇导入自动归入目标专题
        r1 = client.post(
            "/api/v1/papers/import",
            files={"file": ("paper-a.pdf", b"%PDF-1.4\n% content-a", "application/pdf")},
            data={"project_id": proj.id},
        )
        assert r1.status_code == 202
        job1 = _wait_job(client, r1.json()["job_id"])
        assert job1["status"] == "done", job1
        paper_a_id = job1["result"]["paper_id"]
        assert job1["result"]["project_id"] == proj.id
        assert job1["result"]["attached_to_project"] is True

        # 研究者将论文 A 标为 core 并写了专题备注
        app.state.services.repository.add_paper_to_project(
            proj.id, paper_a_id, reading_status="core", project_note="核心基准论文"
        )

        # 3. 批量导入包含重复的 paper-a 和新的 paper-b：paper-b 归入专题，paper-a 绝不覆写 core 状态和备注
        r2 = client.post(
            "/api/v1/papers/import-batch",
            files=[
                ("files", ("paper-a.pdf", b"%PDF-1.4\n% content-a", "application/pdf")),
                ("files", ("paper-b.pdf", b"%PDF-1.4\n% content-b", "application/pdf")),
            ],
            data={"project_id": proj.id},
        )
        assert r2.status_code == 202
        job2 = _wait_job(client, r2.json()["job_id"])
        assert job2["status"] == "done", job2
        assert job2["result"]["imported"] == 1
        assert job2["result"]["duplicates"] == 1
        assert job2["result"]["attached_count"] == 1

        entries = {e.paper_id: e for e in app.state.services.repository.list_project_papers(proj.id)}
        assert len(entries) == 2
        assert entries[paper_a_id].reading_status.value == "core"
        assert entries[paper_a_id].project_note == "核心基准论文"


def test_project_scoped_batch_analysis_only_analyzes_pending_papers_in_target_project(tmp_path):
    data_dir = tmp_path / "data"
    repo = ResearchRepository(data_dir / "research.sqlite3")
    proj_a = repo.create_project("专题 A")
    proj_b = repo.create_project("专题 B")

    _seed_paper(data_dir, repo, paper_id="p-a-done", title="A Done", with_card=True)
    _seed_paper(data_dir, repo, paper_id="p-a-pending", title="A Pending", with_card=False)
    _seed_paper(data_dir, repo, paper_id="p-b-pending", title="B Pending", with_card=False)

    repo.add_paper_to_project(proj_a.id, "p-a-done")
    repo.add_paper_to_project(proj_a.id, "p-a-pending")
    repo.add_paper_to_project(proj_b.id, "p-b-pending")

    analyzed_ids: list[str] = []

    class FakeAnalysisPipeline:
        def __init__(self, r, progress=None):
            self._r = r

        def analyze(self, paper_id):
            analyzed_ids.append(paper_id)
            self._r.save_card(
                PaperCard(
                    paper_id=paper_id,
                    lite_model="lite-test",
                    pro_model="pro-test",
                    prompt_version="v-test",
                    metadata=PaperMetadata(title=f"Analyzed {paper_id}"),
                )
            )

    app = create_app(data_dir=data_dir)
    app.state.services.analysis = AnalysisService(
        app.state.services.repository,
        app.state.services.jobs,
        pipeline_factory=FakeAnalysisPipeline,
    )

    with TestClient(app) as client:
        # 未知专题返回 404
        assert client.post("/api/v1/batch-analyze", json={"project_id": "missing"}).status_code == 404

        # 仅分析专题 A 待处理论文，绝不触碰专题 B 的 p-b-pending 或已分析的 p-a-done
        resp = client.post("/api/v1/batch-analyze", json={"project_id": proj_a.id})
        assert resp.status_code == 202
        job = _wait_job(client, resp.json()["job_id"])
        assert job["status"] == "done"
        assert analyzed_ids == ["p-a-pending"]
        assert job["result"]["project_id"] == proj_a.id

        # 再次请求专题 A（现已全部完成）返回 400
        again = client.post("/api/v1/batch-analyze", json={"project_id": proj_a.id})
        assert again.status_code == 400


def test_backup_verify_and_offline_restore_roundtrip_with_security_guards(tmp_path, monkeypatch):
    home = tmp_path / "sra-home"
    data_dir = home / "data"
    config_dir = home / "config"
    config_dir.mkdir(parents=True)
    # 故意在同级 config 目录放模拟密钥，验证备份包绝不会夹带任何凭据
    (config_dir / "model-keys.dpapi").write_bytes(b"SUPER-SECRET-DPAPI-BLOB")
    (config_dir / ".env").write_text("SRA_PRO_API_KEY=sk-never-backup\n", encoding="utf-8")
    monkeypatch.setenv("SRA_HOME", str(home))

    repo = ResearchRepository(data_dir / "research.sqlite3")
    _seed_paper(data_dir, repo, paper_id="paper-1", title="城市迁移研究", year=2025, with_card=True)
    repo.add_user_note("paper-1", "note-1", "核心变量口径清晰", title="读后感", page_number=2)
    repo.upsert_claim_review(
        "paper-1",
        "claim-001",
        user_status="corrected",
        field_name="major_finding",
        original_statement="原表述",
        corrected_statement="人工修正后的稳健表述",
        note="核对表 3",
    )
    proj = repo.create_project("城市化专题")
    repo.add_paper_to_project(proj.id, "paper-1", reading_status="core")
    repo.add_project_note(proj.id, "pnote-1", "跨篇观察", title="综述线索")

    # 尝试放置目录外符号链接陷阱，验证备份不会跟随 symlink
    secret_outside = tmp_path / "outside-secret.pdf"
    secret_outside.write_bytes(b"%PDF-1.4\n% DO NOT EXFILTRATE")
    try:
        (data_dir / "papers" / "evil-link.pdf").symlink_to(secret_outside)
    except OSError:
        pass

    # 1. 完整归档备份 + 只读校验
    full_zip = tmp_path / "full-backup.zip"
    manifest = create_backup_archive(data_dir, full_zip, include_pdfs=True)
    assert manifest["contains_secrets"] is False
    assert manifest["counts"]["papers"] == 1
    assert manifest["counts"]["user_notes"] == 1
    assert manifest["counts"]["claim_reviews"] == 1
    assert manifest["counts"]["project_notes"] == 1
    assert manifest["pdf_count"] == 1

    with zipfile.ZipFile(full_zip, "r") as zf:
        names = set(zf.namelist())
        assert MANIFEST_NAME in names
        assert "research.sqlite3" in names
        assert "papers/paper-1.pdf" in names
        assert not any("dpapi" in n or ".env" in n or "evil-link" in n for n in names)

    verified = verify_backup_archive(full_zip)
    assert verified["valid"] is True
    assert verified["counts"]["claim_reviews"] == 1
    assert verified["pdf_count"] == 1

    # 2. 篡改包内数据库字节必被拦截
    tampered_zip = tmp_path / "tampered.zip"
    with zipfile.ZipFile(full_zip, "r") as src, zipfile.ZipFile(tampered_zip, "w") as dst:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename == "research.sqlite3":
                raw = raw[:-4] + b"XXXX"
            dst.writestr(info, raw)
    assert verify_backup_archive(tampered_zip)["valid"] is False

    # 3. zip-slip 路径穿越条目必被拦截
    slip_zip = tmp_path / "slip.zip"
    with zipfile.ZipFile(slip_zip, "w") as dst:
        dst.writestr("../escape.txt", "boom")
    slip_report = verify_backup_archive(slip_zip)
    assert slip_report["valid"] is False
    assert any("escapes" in err or "Unsafe" in err for err in slip_report["errors"])

    # 4. 离线原子恢复至全新目录，自动重写 stored_path 并保留全部数据
    restored_dir = tmp_path / "new-machine" / "data"
    outcome = restore_backup_archive(full_zip, restored_dir, replace=False)
    assert outcome["restored"] is True
    assert outcome["rewritten_pdf_paths"] == 1
    restored_repo = ResearchRepository(restored_dir / "research.sqlite3")
    restored_paper = restored_repo.get_paper("paper-1")
    assert restored_paper is not None
    assert Path(restored_paper.stored_path).resolve() == (restored_dir / "papers" / "paper-1.pdf").resolve()
    assert Path(restored_paper.stored_path).is_file()
    assert len(restored_repo.list_claim_reviews("paper-1")) == 1
    assert len(restored_repo.list_user_notes("paper-1")) == 1

    # 5. 非空目录未传 replace 拒绝覆盖；传 replace 时安全留存 .bak
    with pytest.raises(FileExistsError):
        restore_backup_archive(full_zip, restored_dir, replace=False)
    replaced = restore_backup_archive(full_zip, restored_dir, replace=True)
    assert replaced["previous_backup_dir"] is not None
    assert Path(replaced["previous_backup_dir"]).is_dir()

    # 5b. CLI backup / verify-backup / restore-backup 子命令离线端到端验证
    from sociology_research.cli import main as cli_main

    monkeypatch.setenv("SRA_DATA_DIR", str(data_dir))
    cli_zip = tmp_path / "cli-backup.zip"
    assert cli_main(["backup", "--output", str(cli_zip), "--include-pdfs"]) == 0
    assert cli_main(["verify-backup", str(cli_zip), "--json"]) == 0
    cli_restore_dir = tmp_path / "cli-restored"
    assert cli_main(["restore-backup", str(cli_zip), "--target", str(cli_restore_dir)]) == 0
    assert (cli_restore_dir / "research.sqlite3").is_file()

    # 6. FastAPI 端点流式下载备份与只读校验 + R4 PaperSummary 聚合计数
    app = create_app(data_dir=data_dir)
    with TestClient(app) as client:
        papers_resp = client.get("/api/v1/papers")
        assert papers_resp.status_code == 200
        p0 = papers_resp.json()["papers"][0]
        assert p0["reviewed_claims"] == 1
        assert p0["user_notes_count"] == 1

        dl = client.post("/api/v1/maintenance/backup", json={"include_pdfs": False})
        assert dl.status_code == 200
        assert dl.headers["content-type"].startswith("application/zip")
        assert dl.content[:2] == b"PK"

        chk = client.post(
            "/api/v1/maintenance/backup/verify",
            files={"file": ("lite.zip", dl.content, "application/zip")},
        )
        assert chk.status_code == 200
        body = chk.json()
        assert body["valid"] is True
        assert body["include_pdfs"] is False
        assert body["counts"]["papers"] == 1
        assert body["counts"]["claim_reviews"] == 1


def test_desktop_v030_closure_ui_and_api_contracts():
    desktop = ROOT / "desktop" / "SRA.Desktop"
    xaml_path = desktop / "Views" / "MainWindow.axaml"
    ET.parse(xaml_path)
    xaml = xaml_path.read_text(encoding="utf-8")
    client = (desktop / "Services" / "SraApiClient.cs").read_text(encoding="utf-8")
    synthesis_cs = (desktop / "Views" / "MainWindow.Synthesis.cs").read_text(encoding="utf-8")
    projects_cs = (desktop / "Views" / "MainWindow.Projects.cs").read_text(encoding="utf-8")
    main_cs = (desktop / "Views" / "MainWindow.axaml.cs").read_text(encoding="utf-8")

    # R1: 多文献综合一键复制 Markdown
    assert "CopyActiveSynthesisMarkdown_Click" in xaml and "CopyActiveSynthesisMarkdown_Click" in synthesis_cs

    # R2: 专题自动归类与按专题精准批量分析
    assert "AnalyzeActiveProjectPending_Click" in xaml and "AnalyzeActiveProjectPending_Click" in projects_cs
    assert "DropOverlaySubtitle" in xaml
    assert "project_id" in client

    # R3: 数据与健康页安全备份与校验
    for handler in ("ExportLiteBackup_Click", "ExportFullBackup_Click", "VerifyBackup_Click"):
        assert handler in xaml and handler in main_cs
    assert "DownloadBackupArchiveToStreamAsync" in client
    assert "VerifyBackupArchiveAsync" in client

    # R4: 论文库排序与轻量审定/笔记徽章
    assert "LibrarySortOptions" in xaml and "SelectedLibrarySort" in xaml
    assert "HumanBadgeText" in xaml and "HasHumanBadge" in xaml
