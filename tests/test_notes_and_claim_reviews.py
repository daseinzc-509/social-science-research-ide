"""个人阅读笔记（MY NOTE）与 Claim 单条人工审定状态（HUMAN_REVIEWED）全栈回归测试。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient

from sociology_research.analysis.evidence import _claim_id
from sociology_research.api import create_app
from sociology_research.models import (
    ClaimType,
    EvidenceReference,
    ExtractedClaim,
    PaperCard,
    PaperMetadata,
    PaperRecord,
    ParseStatus,
    ParsedDocument,
    Provenance,
    SourceBlock,
    StudyProfile,
    StudyType,
    VerificationStatus,
    compute_claim_id,
)
from sociology_research.repository import ResearchRepository

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "SRA.Desktop"


def _seed_analyzed_paper(repo: ResearchRepository, tmp_path: Path, paper_id: str = "paper-note-1") -> tuple[str, str]:
    """在本地 SQLite 仓库写入一篇已解析并生成 PaperCard 的样本论文，返回 (finding_cid, ai_lim_cid)。"""
    pdf_path = tmp_path / f"{paper_id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% minimal test pdf\n")

    block = SourceBlock(
        id=f"blk-{paper_id}-1",
        page_number=7,
        text="田野调查表明，数字平台算法调度显著压缩了骑手的自主时间边界。",
        role="body",
        source_label="docling:p7",
    )
    doc = ParsedDocument(
        paper_id=paper_id,
        sha256=f"sha-{paper_id}",
        source_path=str(pdf_path),
        page_count=12,
        status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        blocks=[block],
    )
    rec = PaperRecord(
        id=paper_id,
        sha256=f"sha-{paper_id}",
        original_name="platform_labor.pdf",
        stored_path=str(pdf_path),
        page_count=12,
        parse_status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        warnings=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.save_import(rec, doc)

    finding_stmt = "算法派单机制通过实时考核显著缩短了劳动者的自主缓冲时间。"
    ai_lim_stmt = "样本仅覆盖单一特大城市，向县域平台用工情境外推存在边界限制。"
    ev_ref = EvidenceReference(
        source_block_id=block.id,
        page_number=7,
        quote=block.text,
    )
    card = PaperCard(
        paper_id=paper_id,
        metadata=PaperMetadata(
            title="算法调度与骑手时间自主性研究",
            authors=["张三", "李四"],
            year=2025,
            journal="社会学研究",
            abstract="本文探讨数字平台算法对劳动过程时间边界的重塑。",
            keywords=["平台劳动", "算法控制"],
        ),
        study_profile=StudyProfile(
            study_type=StudyType.QUALITATIVE,
            method="参与式观察与深度访谈",
            identification_strategy="比较案例分析",
            population="外卖骑手",
            data_source="田野笔记与 60 份访谈",
        ),
        basic_facts=[
            ExtractedClaim(
                field_name="major_finding",
                statement=finding_stmt,
                provenance=Provenance.AUTHOR_STATED,
                verification=VerificationStatus.SUPPORTED,
                claim_type=ClaimType.DESCRIPTIVE,
                evidence=[ev_ref],
            )
        ],
        limitations=[
            ExtractedClaim(
                field_name="external_validity",
                statement=ai_lim_stmt,
                provenance=Provenance.AI_INFERRED,
                verification=VerificationStatus.SUPPORTED,
                claim_type=ClaimType.LIMITATION,
                evidence=[ev_ref],
            )
        ],
        lite_model="test-lite",
        pro_model="test-pro",
        prompt_version="v2.2-test",
    )
    repo.save_card(card)
    return (
        compute_claim_id("major_finding", finding_stmt),
        compute_claim_id("external_validity", ai_lim_stmt),
    )


def test_compute_claim_id_matches_evidence_module():
    samples = [
        ("major_finding", "算法调度显著影响骑手接单节奏。"),
        ("external_validity", "Cross-sectional survey cannot identify causal mediation."),
        ("research_question", ""),
    ]
    for field_name, statement in samples:
        assert compute_claim_id(field_name, statement) == _claim_id(field_name, statement)


def test_user_notes_full_crud_validation_and_fts_sync(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    _seed_analyzed_paper(repo, tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, repository=repo))

    # 1. 创建笔记
    created_resp = client.post(
        "/api/v1/papers/paper-note-1/notes",
        json={
            "title": "田野反思",
            "note": "第 7 页的骑手反抗策略可以与布若威制造同意理论对照阅读。",
            "page_number": 7,
        },
    )
    assert created_resp.status_code == 201
    created = created_resp.json()
    assert created["title"] == "田野反思"
    assert created["page_number"] == 7
    note_id = created["id"]

    # 2. 列表与详情均可见
    list_resp = client.get("/api/v1/papers/paper-note-1/notes")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["notes"]) == 1

    detail_resp = client.get("/api/v1/papers/paper-note-1")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert len(detail["user_notes"]) == 1
    assert detail["user_notes"][0]["id"] == note_id

    # 3. FTS5 检索立即命中个人笔记
    hits = repo.search("布若威制造同意")
    assert any(h["source_kind"] == "USER_NOTE" and h["page_number"] == 7 for h in hits)

    # 4. 更新笔记并验证 FTS5 同步刷新
    upd_resp = client.put(
        f"/api/v1/papers/paper-note-1/notes/{note_id}",
        json={
            "title": "更新后的理论联想",
            "note": "改用阿伦特劳动概念重新审视平台时间秩序。",
            "page_number": 9,
        },
    )
    assert upd_resp.status_code == 200
    updated = upd_resp.json()
    assert updated["title"] == "更新后的理论联想"
    assert updated["page_number"] == 9
    assert not any(h["source_kind"] == "USER_NOTE" for h in repo.search("布若威制造同意"))
    assert any(h["source_kind"] == "USER_NOTE" and h["page_number"] == 9 for h in repo.search("阿伦特劳动概念"))

    # 5. 输入校验与 404
    assert client.post("/api/v1/papers/paper-note-1/notes", json={"note": "   "}).status_code in (400, 422)
    assert client.post("/api/v1/papers/paper-note-1/notes", json={"note": "ok", "page_number": 0}).status_code in (400, 422)
    assert client.put("/api/v1/papers/paper-note-1/notes/missing-id", json={"note": "x"}).status_code == 404

    # 6. 删除笔记后列表与检索同时清理
    del_resp = client.delete(f"/api/v1/papers/paper-note-1/notes/{note_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True
    assert client.get("/api/v1/papers/paper-note-1/notes").json()["notes"] == []
    assert not any(h["source_kind"] == "USER_NOTE" for h in repo.search("阿伦特劳动概念"))
    assert client.delete(f"/api/v1/papers/paper-note-1/notes/{note_id}").status_code == 404


def test_claim_reviews_four_states_evidence_first_and_rerun_preservation(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    finding_cid, ai_lim_cid = _seed_analyzed_paper(repo, tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, repository=repo))

    # 详情返回的每条 Claim 自动回填确定性 claim_id
    detail_before = client.get("/api/v1/papers/paper-note-1").json()
    assert detail_before["card"]["basic_facts"][0]["claim_id"] == finding_cid
    assert detail_before["card"]["limitations"][0]["claim_id"] == ai_lim_cid
    original_statement = detail_before["card"]["basic_facts"][0]["statement"]

    # 校验：corrected 缺少修正陈述、非法状态均返回 400
    assert (
        client.put(
            f"/api/v1/papers/paper-note-1/claim-reviews/{finding_cid}",
            json={"user_status": "corrected", "corrected_statement": "   "},
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/api/v1/papers/paper-note-1/claim-reviews/{finding_cid}",
            json={"user_status": "invalid_state"},
        ).status_code
        == 400
    )

    # 写入人工修正（corrected）并验证原文未被篡改 + FTS5 可检索
    corrected_text = "算法派单主要在午高峰时段显著压缩全职专送骑手的缓冲时间（众包骑手差异较大）。"
    r1 = client.put(
        f"/api/v1/papers/paper-note-1/claim-reviews/{finding_cid}",
        json={
            "user_status": "corrected",
            "field_name": "major_finding",
            "original_statement": original_statement,
            "corrected_statement": corrected_text,
            "note": "需区分专送与众包群体，原文第 7 页有明确分层。",
        },
    )
    assert r1.status_code == 200
    assert r1.json()["user_status"] == "corrected"

    # 写入已确认（confirmed）、存疑驳回（rejected）、待定（unresolved）
    for status in ("rejected", "unresolved", "confirmed"):
        r2 = client.put(
            f"/api/v1/papers/paper-note-1/claim-reviews/{ai_lim_cid}",
            json={
                "user_status": status,
                "field_name": "external_validity",
                "original_statement": detail_before["card"]["limitations"][0]["statement"],
                "note": f"核验状态={status}",
            },
        )
        assert r2.status_code == 200
        assert r2.json()["user_status"] == status

    # 严守 Evidence-first：paper_cards 原始陈述一字未改
    stored_card = repo.get_card("paper-note-1")
    assert stored_card is not None
    assert stored_card.basic_facts[0].statement == original_statement

    # FTS5 索引收录人工修正陈述（HUMAN_REVIEWED）
    review_hits = repo.search("全职专送骑手")
    assert any(h["source_kind"] == "HUMAN_REVIEWED" for h in review_hits)

    # 重跑保护铁律：先写一条个人笔记，再清空衍生分析并写入新版 PaperCard
    note_resp = client.post(
        "/api/v1/papers/paper-note-1/notes",
        json={"title": "重跑保护测试笔记", "note": "无论重跑多少次都绝不能丢失。", "page_number": 3},
    )
    assert note_resp.status_code == 201

    repo.clear_derived_analysis("paper-note-1")
    new_card = PaperCard(
        paper_id="paper-note-1",
        metadata=PaperMetadata(title="算法调度与骑手时间自主性研究"),
        basic_facts=[
            ExtractedClaim(
                field_name="major_finding",
                statement="重跑后提取出的新表述。",
                provenance=Provenance.AUTHOR_STATED,
                verification=VerificationStatus.NEEDS_REVIEW,
                evidence=[],
            )
        ],
        lite_model="test-lite",
        pro_model="test-pro",
        prompt_version="v2.2-test",
    )
    repo.save_card(new_card)

    # 笔记与所有人工审定记录 100% 完好保留
    notes_after = client.get("/api/v1/papers/paper-note-1/notes").json()["notes"]
    reviews_after = client.get("/api/v1/papers/paper-note-1/claim-reviews").json()["reviews"]
    assert len(notes_after) == 1
    assert notes_after[0]["title"] == "重跑保护测试笔记"
    assert len(reviews_after) == 2
    assert {item["claim_id"] for item in reviews_after} == {finding_cid, ai_lim_cid}

    # 删除单条审定记录
    del_resp = client.delete(f"/api/v1/papers/paper-note-1/claim-reviews/{finding_cid}")
    assert del_resp.status_code == 200
    assert len(client.get("/api/v1/papers/paper-note-1/claim-reviews").json()["reviews"]) == 1
    assert client.delete(f"/api/v1/papers/paper-note-1/claim-reviews/{finding_cid}").status_code == 404


def test_evidence_matrix_passes_human_reviews_deterministically(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    finding_cid, ai_lim_cid = _seed_analyzed_paper(repo, tmp_path)
    client = TestClient(create_app(data_dir=tmp_path, repository=repo))

    # 创建研究专题并纳入论文
    proj = client.post(
        "/api/v1/projects",
        json={"name": "平台劳动专题", "research_question": "算法如何重塑劳动时间？"},
    ).json()
    project_id = proj["id"]
    assert (
        client.post(
            f"/api/v1/projects/{project_id}/papers/paper-note-1",
            json={"reading_status": "core", "project_note": "核心田野案例"},
        ).status_code
        == 200
    )

    # 为两条主张分别写入人工修正与已确认
    client.put(
        f"/api/v1/papers/paper-note-1/claim-reviews/{finding_cid}",
        json={
            "user_status": "corrected",
            "field_name": "major_finding",
            "corrected_statement": "研究者修正后的核心发现表述。",
            "note": "矩阵对比备注",
        },
    )
    client.put(
        f"/api/v1/papers/paper-note-1/claim-reviews/{ai_lim_cid}",
        json={"user_status": "confirmed", "field_name": "external_validity"},
    )

    matrix_resp = client.get(f"/api/v1/projects/{project_id}/evidence-matrix")
    assert matrix_resp.status_code == 200
    matrix = matrix_resp.json()
    assert matrix["makes_llm_calls"] is False
    assert len(matrix["rows"]) == 1
    row = matrix["rows"][0]

    mf = row["major_findings"][0]
    assert mf["claim_id"] == finding_cid
    assert mf["human_review"]["user_status"] == "corrected"
    assert mf["human_review"]["corrected_statement"] == "研究者修正后的核心发现表述。"
    assert mf["human_review"]["note"] == "矩阵对比备注"
    # 原始提取陈述保持不变
    assert "算法派单机制" in mf["statement"]

    lim = row["ai_limitations"][0]
    assert lim["claim_id"] == ai_lim_cid
    assert lim["human_review"]["user_status"] == "confirmed"


def test_desktop_notes_and_claim_review_ui_contracts():
    main_xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")
    claim_xaml = (DESKTOP / "Controls" / "ClaimCard.axaml").read_text(encoding="utf-8")
    claim_cs = (DESKTOP / "Controls" / "ClaimCard.axaml.cs").read_text(encoding="utf-8")
    client_cs = (DESKTOP / "Services" / "SraApiClient.cs").read_text(encoding="utf-8")

    # 两个 XAML 均必须是合法 XML
    assert ET.fromstring(main_xaml) is not None
    assert ET.fromstring(claim_xaml) is not None

    # 严守桌面端 UX 红线：绝无 ••• 杂物抽屉
    assert 'Content="•••"' not in main_xaml

    # 「我的笔记」Tab、快速录入、原生右键菜单、研究画像摘要与矩阵人工审定徽章
    for snippet in (
        'x:Name="PaperTabs"',
        'Header="我的笔记"',
        'Click="OpenNotesTab_Click"',
        'Click="SaveDraftNote_Click"',
        'Click="EditNote_Click"',
        'Click="NoteJumpPage_Click"',
        'Click="CopyNote_Click"',
        'Click="DeleteNote_Click"',
        'Click="DeleteReviewRecord_Click"',
        "HumanReviewBadgeLabel",
        "HasHumanCorrectedStatement",
    ):
        assert snippet in main_xaml, f"MainWindow.axaml 缺少契约片段：{snippet}"

    # ClaimCard 就地 4 态人工审定条与独立修正对照块
    assert "ClaimReviewRequestedEvent" in claim_cs
    for tag in ('Tag="confirmed"', 'Tag="corrected"', 'Tag="rejected"', 'Tag="unresolved"', 'Tag="edit"', 'Tag="clear"'):
        assert tag in claim_xaml, f"ClaimCard.axaml 缺少审定按钮：{tag}"
    assert "HUMAN_REVIEWED" in claim_xaml

    # SraApiClient 完整笔记与审定 API 客户端方法
    for method in (
        "CreatePaperNoteAsync",
        "UpdatePaperNoteAsync",
        "DeletePaperNoteAsync",
        "UpsertClaimReviewAsync",
        "DeleteClaimReviewAsync",
    ):
        assert f"{method}(" in client_cs, f"SraApiClient 缺少 {method}"
