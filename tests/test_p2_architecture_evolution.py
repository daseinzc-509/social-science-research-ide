"""P2 架构演进专项测试：打包冗余净化、SQLite FTS5 双语检索、Projects 证据矩阵与 SSE 任务流。"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_backend, check_bundle_without_pymupdf, prune_release_assets  # noqa: E402
from sociology_research.api import create_app  # noqa: E402
from sociology_research.api.desktop_entry import create_desktop_app  # noqa: E402
from sociology_research.db import sqlite_connection  # noqa: E402
from sociology_research.models import (  # noqa: E402
    ClaimAudit,
    ClaimType,
    EvidenceReference,
    EvidenceSpan,
    ExtractedClaim,
    PaperCard,
    PaperMetadata,
    PaperRecord,
    ParsedDocument,
    ParseStatus,
    Provenance,
    SemanticSupportStatus,
    SourceBlock,
    StudyProfile,
    StudyType,
    VerificationStatus,
)
from sociology_research.repository import ResearchRepository  # noqa: E402
from sociology_research.services import create_application_services  # noqa: E402
from sociology_research.services.region_review import PendingPreview, RegionBounds  # noqa: E402
from sociology_research.services.region_review_versioning import source_snapshots  # noqa: E402


def _seed_paper(
    repo: ResearchRepository,
    papers_dir: Path,
    *,
    paper_id: str,
    filename: str,
    body_text: str,
) -> tuple[PaperRecord, Path]:
    papers_dir.mkdir(parents=True, exist_ok=True)
    pdf_bytes = f"%PDF-1.4\n% synthetic-{paper_id}-{body_text[:20]}".encode("utf-8")
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    pdf_path = papers_dir / f"{paper_id}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    block = SourceBlock(
        id=f"blk-{paper_id}-1",
        page_number=2,
        text=body_text,
        bbox=(20.0, 40.0, 420.0, 120.0),
        role="body",
        source_label="docling:text",
    )
    doc = ParsedDocument(
        paper_id=paper_id,
        sha256=sha,
        source_path=str(pdf_path),
        page_count=5,
        status=ParseStatus.SUCCESS,
        parser_version="docling-test-v1",
        blocks=[block],
    )
    record = PaperRecord(
        id=paper_id,
        sha256=sha,
        original_name=filename,
        stored_path=str(pdf_path),
        page_count=5,
        parse_status=ParseStatus.SUCCESS,
        parser_version="docling-test-v1",
        warnings=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.save_import(record, doc)
    return record, pdf_path


def _make_sample_card(paper_id: str, title: str, block_id: str, quote: str) -> PaperCard:
    span_id = f"evspan-{paper_id}"
    return PaperCard(
        paper_id=paper_id,
        metadata=PaperMetadata(
            title=title,
            authors=["张思远", "Li Mei"],
            year=2025,
            journal="社会学研究",
            keywords=["户籍制度", "双重差分"],
            abstract="本文评估户籍制度改革对流动人口市民化的影响。",
        ),
        basic_facts=[
            ExtractedClaim(
                claim_id=f"claim-rq-{paper_id}",
                field_name="research_question",
                statement="户籍准入放宽是否显著提升农业转移人口长期定居意愿？",
                provenance=Provenance.AUTHOR_STATED,
                verification=VerificationStatus.SUPPORTED,
                claim_type=ClaimType.CAUSAL,
                evidence=[
                    EvidenceReference(
                        evidence_id=span_id,
                        source_block_id=block_id,
                        page_number=2,
                        quote=quote[:120],
                    )
                ],
            ),
            ExtractedClaim(
                claim_id=f"claim-fd-{paper_id}",
                field_name="major_finding",
                statement="双重差分估计表明试点城市定居意愿提升 6.8 个百分点。",
                provenance=Provenance.AUTHOR_STATED,
                verification=VerificationStatus.SUPPORTED,
                claim_type=ClaimType.CAUSAL,
                evidence=[
                    EvidenceReference(
                        evidence_id=span_id,
                        source_block_id=block_id,
                        page_number=2,
                        quote=quote[:120],
                    )
                ],
            ),
        ],
        evidence_spans=[
            EvidenceSpan(
                id=span_id,
                source_block_id=block_id,
                page_number=2,
                start_char=0,
                end_char=min(80, len(quote)),
                text=quote[:80],
                kind="text",
                role="body",
            )
        ],
        analysis=[
            ExtractedClaim(
                claim_id=f"claim-an-{paper_id}",
                field_name="identification",
                statement="平行趋势检验覆盖改革前四期，但需留意周边城市溢出效应。",
                provenance=Provenance.AI_INFERRED,
                verification=VerificationStatus.SUPPORTED,
                semantic_support=SemanticSupportStatus.QUALIFIES,
                evidence=[
                    EvidenceReference(
                        evidence_id=span_id,
                        source_block_id=block_id,
                        page_number=2,
                        quote=quote[:120],
                    )
                ],
            )
        ],
        limitations=[
            ExtractedClaim(
                claim_id=f"claim-lim-{paper_id}",
                field_name="external_validity",
                statement="样本集中于东部地级市，对中西部县域外推受限。",
                provenance=Provenance.AI_INFERRED,
                verification=VerificationStatus.NO_SOURCE,
                evidence=[],
            )
        ],
        study_profile=StudyProfile(
            study_type=StudyType.QUANTITATIVE_OBSERVATIONAL,
            method="交叠双重差分（Staggered DID）",
            identification_strategy="城市分批落户门槛改革外生冲击",
            population="跨省流动人口",
            data_source="CMDS 2011-2018",
        ),
        claim_audits=[
            ClaimAudit(
                claim_id=f"claim-fd-{paper_id}",
                semantic_support=SemanticSupportStatus.SUPPORTS,
                rationale="原文表 3 直接报告了该估计系数。",
                evidence=[
                    EvidenceReference(
                        evidence_id=span_id,
                        source_block_id=block_id,
                        page_number=2,
                        quote=quote[:120],
                    )
                ],
            )
        ],
        lite_model="test-lite",
        pro_model="test-pro",
        prompt_version="v2.2-test",
    )


def test_p2_packaging_hygiene_and_dev_gui_pruning(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(build_backend, "available", lambda _name: True)
    monkeypatch.setattr(build_backend.importlib.metadata, "version", lambda _dist: "1.0.0")

    default_args = build_backend.command_args("full")
    assert "sociology_research.analysis" in [
        default_args[i + 1]
        for i in range(len(default_args) - 1)
        if default_args[i] == "--collect-submodules"
    ]
    strict_args = build_backend.command_args("full", exclude_dev_gui=True)
    excludes = [
        strict_args[i + 1]
        for i in range(len(strict_args) - 1)
        if strict_args[i] == "--exclude-module"
    ]
    for mod in ("faker", "polyfactory", "tkinter", "pythonwin"):
        assert mod in excludes
    for critical in ("torch", "docling", "cv2", "rapidocr", "win32"):
        assert critical not in excludes

    # 验证虚拟环境污染检测
    fake_site = tmp_path / "site-packages"
    for folder in (
        "fastapi-0.143.0.dist-info",
        "fastapi-0.143.2.dist-info",
        "faker-40.1.0.dist-info",
    ):
        (fake_site / folder).mkdir(parents=True)
    env_audit = build_backend.audit_environment_hygiene([fake_site])
    assert env_audit["clean"] is False
    assert env_audit["duplicate_dist_info"] == {
        "fastapi": ["fastapi-0.143.0.dist-info", "fastapi-0.143.2.dist-info"]
    }
    assert "faker-40.1.0.dist-info" in env_audit["unwanted_distributions"]

    # 验证冻结产物冗余清理（保留新版 .dist-info 与运行时动态库，仅删旧版与测试/GUI 残留）
    backend = tmp_path / "dist" / "backend" / "sra-backend"
    desktop = tmp_path / "dist" / "desktop"
    backend.mkdir(parents=True)
    desktop.mkdir(parents=True)
    (backend / "sra-backend.exe").write_bytes(b"exe")
    (desktop / "SRA.Desktop.exe").write_bytes(b"exe")

    bloat_files = [
        backend / "_internal/faker/generator.py",
        backend / "_internal/faker-40.1.0.dist-info/METADATA",
        backend / "_internal/tcl8/init.tcl",
        backend / "_internal/_tk_data/tk.tcl",
        backend / "_internal/pythonwin/mfc140u.dll",
        backend / "_internal/fastapi-0.143.0.dist-info/METADATA",
    ]
    keep_files = [
        backend / "_internal/fastapi-0.143.2.dist-info/METADATA",
        backend / "_internal/torch/lib/torch_cpu.dll",
        backend / "_internal/cv2/cv2.pyd",
        backend / "_internal/win32/win32cred.pyd",
    ]
    for path in [*bloat_files, *keep_files]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"payload")

    before_hygiene = check_bundle_without_pymupdf.audit_frozen_bundle_hygiene(backend)
    assert before_hygiene["clean"] is False

    report = prune_release_assets.prune(
        backend,
        desktop,
        apply=True,
        remove_dev_gui_bloat=True,
    )
    assert report["removed_files"] == len(bloat_files)
    assert not any(path.exists() for path in bloat_files)
    assert all(path.exists() for path in keep_files)

    after_hygiene = check_bundle_without_pymupdf.audit_frozen_bundle_hygiene(backend)
    assert after_hygiene["clean"] is True


def test_p2_fts5_bilingual_search_provenance_and_backfill(tmp_path: Path):
    db_path = tmp_path / "research.sqlite3"
    papers_dir = tmp_path / "papers"
    repo = ResearchRepository(db_path)
    services = create_application_services(tmp_path, repo)

    rec1, _ = _seed_paper(
        repo,
        papers_dir,
        paper_id="p-hukou",
        filename="hukou_reform.pdf",
        body_text="本文采用交叠双重差分（staggered difference-in-differences）与工具变量 instrumental variable 策略检验户籍改革效应。",
    )
    rec2, _ = _seed_paper(
        repo,
        papers_dir,
        paper_id="p-edu",
        filename="education_mobility.pdf",
        body_text="Qualitative interviews examine intergenerational cultural reproduction in rural schools.",
    )
    repo.save_card(
        _make_sample_card(
            rec1.id,
            "落户门槛放宽与流动人口市民化",
            f"blk-{rec1.id}-1",
            "本文采用交叠双重差分与工具变量策略检验户籍改革效应。",
        )
    )
    repo.add_user_note(rec2.id, "note-1", "重点核对田野访谈抽样饱和度与农村学校场域特征。")

    # 中文多字词、英文前缀、以及带各种特殊符号的防注入查询
    hits_cn = repo.search("双重差分 户籍")
    assert any(h["paper_id"] == rec1.id and h["source_kind"] == "SOURCE_BLOCK" for h in hits_cn)
    assert any(h["paper_id"] == rec1.id and h["source_kind"] == "PAPER_CARD" for h in hits_cn)

    hits_en = repo.search("instrument variabl")
    assert len(hits_en) >= 1 and hits_en[0]["paper_id"] == rec1.id

    hits_note = repo.search("抽样饱和度", source_kinds=["USER_NOTE"])
    assert len(hits_note) == 1
    assert hits_note[0]["paper_id"] == rec2.id
    assert hits_note[0]["provenance"] == "USER_NOTE"

    # 极端特殊字符不得引发 FTS5 语法错误
    safe_hits = repo.search('戸籍 "unclosed (p < 0.05) NEAR/3 OR * : R&D 双重差分')
    assert isinstance(safe_hits, list)

    # 人工局部复核写入、修订与撤销的实时索引同步（且严格标示 HUMAN_REVIEWED）
    blocks = repo.get_source_blocks(rec1.id)
    block_ids = (blocks[0].id,)
    fingerprints = tuple(sorted(source_snapshots(blocks, block_ids).items()))
    pending = PendingPreview(
        paper_id=rec1.id,
        pdf_sha256=rec1.sha256,
        file_size=Path(rec1.stored_path).stat().st_size,
        file_mtime_ns=Path(rec1.stored_path).stat().st_mtime_ns,
        page_number=2,
        bounds=RegionBounds(x0=0.1, y0=0.1, x1=0.5, y1=0.3),
        original_text="旧扫描残缺字",
        ocr_text="异质性稳健估计系数 0.068",
        source_block_ids=block_ids,
        created=time.monotonic(),
        parser_version=rec1.parser_version,
        source_fingerprints=fingerprints,
    )
    review_id = services.region_review.review_store.add(
        rec1.id,
        pending,
        "异质性稳健估计系数为 0.068（标准误 0.014）",
        "核对原表三第二列",
    )
    repo.sync_paper_search_index(rec1.id)
    rev_hits = repo.search("异质性稳健估计系数", source_kinds=["HUMAN_REVIEWED"])
    assert len(rev_hits) == 1
    assert rev_hits[0]["provenance"] == "HUMAN_REVIEWED"

    # 撤销该复核后，全文索引中立即不再命中该废弃校订
    services.region_review.revoke_review(rec1.id, review_id)
    assert repo.search("异质性稳健估计系数", source_kinds=["HUMAN_REVIEWED"]) == []

    # 模拟老数据库升级自动全量回填：清空 search_fts 后重新实例化 ResearchRepository
    with sqlite_connection(db_path) as con:
        con.execute("DELETE FROM search_fts")
        assert con.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0] == 0

    reopened = ResearchRepository(db_path)
    assert len(reopened.search("双重差分")) >= 1

    # 删除论文后 FTS5 同步清空
    reopened.delete_paper(rec2.id)
    assert reopened.search("抽样饱和度") == []


def test_p2_projects_and_deterministic_evidence_matrix_api(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    papers_dir = tmp_path / "papers"
    rec1, _ = _seed_paper(
        repo,
        papers_dir,
        paper_id="proj-p1",
        filename="paper_one.pdf",
        body_text="试点地区落户改革显著提高了流动人口长期居留意愿。",
    )
    rec2, _ = _seed_paper(
        repo,
        papers_dir,
        paper_id="proj-p2",
        filename="paper_two.pdf",
        body_text="未分析对照文献正文：讨论县域城镇化与公共服务均等化。",
    )
    repo.save_card(
        _make_sample_card(
            rec1.id,
            "落户改革与市民化实证研究",
            f"blk-{rec1.id}-1",
            "试点地区落户改革显著提高了流动人口长期居留意愿。",
        )
    )

    services = create_application_services(tmp_path, repo)
    client = TestClient(create_app(services=services))

    created = client.post(
        "/api/v1/projects",
        json={
            "name": "人口迁移与制度变迁",
            "description": "比较户籍改革经验研究",
            "research_question": "制度门槛下降如何影响长期定居与社会融入？",
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    # 同名项目冲突返回 400
    dup = client.post("/api/v1/projects", json={"name": "人口迁移与制度变迁"})
    assert dup.status_code == 400

    assert client.post(
        f"/api/v1/projects/{project_id}/papers/{rec1.id}",
        json={"reading_status": "core", "project_note": "基准 DID 文献"},
    ).status_code == 200
    assert client.post(
        f"/api/v1/projects/{project_id}/papers/{rec2.id}",
        json={"reading_status": "queued", "project_note": "待跑模型分析"},
    ).status_code == 200

    # 项目限定全文检索
    scoped_search = client.get(
        "/api/v1/papers/search",
        params={"q": "公共服务均等化", "project_id": project_id},
    )
    assert scoped_search.status_code == 200
    assert scoped_search.json()["total"] == 1
    assert scoped_search.json()["hits"][0]["paper_id"] == rec2.id

    # 跨文献 Evidence Matrix：零模型调用，完整保留 Provenance、审计结论与页码引文
    matrix_resp = client.get(f"/api/v1/projects/{project_id}/evidence-matrix")
    assert matrix_resp.status_code == 200, matrix_resp.text
    matrix = matrix_resp.json()
    assert matrix["makes_llm_calls"] is False
    assert matrix["total_papers"] == 2
    assert matrix["analyzed_papers"] == 1

    row1 = next(r for r in matrix["rows"] if r["paper_id"] == rec1.id)
    assert row1["study_type"] == "quantitative_observational"
    assert row1["reading_status"] == "core"
    assert len(row1["major_findings"]) == 1
    finding = row1["major_findings"][0]
    assert finding["provenance"] == "AUTHOR_STATED"
    assert finding["semantic_support"] == "SUPPORTS"
    assert finding["citations"][0]["page_number"] == 2
    assert row1["ai_limitations"][0]["provenance"] == "AI_INFERRED"

    # 删除项目绝不删除底层论文与卡片
    deleted = client.delete(f"/api/v1/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json()["unlinked_papers"] == 2
    assert repo.get_paper(rec1.id) is not None
    assert repo.get_card(rec1.id) is not None


def test_p2_sse_job_event_stream_and_desktop_contracts(tmp_path: Path):
    token = "s" * 40
    app = create_desktop_app(data_dir=tmp_path, token=token)
    services = app.state.services

    with TestClient(app) as client:
        # 1. 鉴权拦截校验
        unauth = client.get("/api/v1/jobs/nonexistent/events")
        assert unauth.status_code == 401

        auth_headers = {"X-SRA-Desktop-Token": token}
        missing = client.get("/api/v1/jobs/nonexistent/events", headers=auth_headers)
        assert missing.status_code == 404

        # 2. 真实多线程任务边跑边推送 SSE 帧，直至 done 自动闭合
        gate = threading.Event()
        job = services.jobs.create("analyze")

        def slow_work(progress):
            progress("Stage 1/2: extracting grounded spans…")
            gate.wait(timeout=2.0)
            progress("Stage 2/2: auditing methodological claims…")
            return {"paper_id": "p-sse", "ok": True}

        services.jobs.start(job, slow_work)
        releaser = threading.Timer(0.18, gate.set)
        releaser.daemon = True
        releaser.start()

        frames: list[dict] = []
        saw_keepalive = False
        with client.stream(
            "GET",
            f"/api/v1/jobs/{job.id}/events?heartbeat_seconds=0.1",
            headers=auth_headers,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            for raw_line in response.iter_lines():
                line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
                if line.startswith(": keep-alive"):
                    saw_keepalive = True
                elif line.startswith("data:"):
                    frames.append(json.loads(line[5:].strip()))

        assert len(frames) >= 2
        assert frames[-1]["status"] == "done"
        assert frames[-1]["result"] == {"paper_id": "p-sse", "ok": True}
        assert any(
            any("Stage 1/2" in m for m in frame.get("messages", []))
            for frame in frames
        )
        assert saw_keepalive or frames[0]["status"] in {"queued", "running", "done"}

    # 3. 桌面端 C# 客户端契约静态校验
    cs_client = (ROOT / "desktop/SRA.Desktop/Services/SraApiClient.cs").read_text(encoding="utf-8")
    cs_dtos = (ROOT / "desktop/SRA.Desktop/Models/ApiDtos.cs").read_text(encoding="utf-8")
    cs_lib_vm = (ROOT / "desktop/SRA.Desktop/ViewModels/MainWindowViewModel.Library.cs").read_text(encoding="utf-8")

    for required in (
        "TryStreamJobEventsAsync",
        "/events",
        "text/event-stream",
        "SearchLibraryAsync",
        "GetProjectsAsync",
        "CreateProjectAsync",
        "GetProjectEvidenceMatrixAsync",
    ):
        assert required in cs_client, required
    for dto_name in ("SearchHitView", "SearchResponse", "ProjectSummaryView", "ProjectListResponse"):
        assert f"class {dto_name}" in cs_dtos
    assert "RunFullTextSearchFilterAsync" in cs_lib_vm
