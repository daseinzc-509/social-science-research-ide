"""专题级全局 FTS5 全文搜索、专题研究笔记与单篇/矩阵导出升级全栈回归测试。"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient

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


def _seed_paper(
    repo: ResearchRepository,
    tmp_path: Path,
    paper_id: str,
    *,
    title: str,
    block_text: str,
    finding_stmt: str,
    ai_lim_stmt: str,
) -> tuple[str, str]:
    pdf_path = tmp_path / f"{paper_id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% synthetic test pdf\n")

    block = SourceBlock(
        id=f"blk-{paper_id}-1",
        page_number=7,
        text=block_text,
        role="body",
        source_label="docling:p7",
    )
    doc = ParsedDocument(
        paper_id=paper_id,
        sha256=f"sha-{paper_id}",
        source_path=str(pdf_path),
        page_count=14,
        status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        blocks=[block],
    )
    rec = PaperRecord(
        id=paper_id,
        sha256=f"sha-{paper_id}",
        original_name=f"{paper_id}.pdf",
        stored_path=str(pdf_path),
        page_count=14,
        parse_status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        warnings=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.save_import(rec, doc)

    ev_ref = EvidenceReference(
        source_block_id=block.id,
        page_number=7,
        quote=block.text[:160],
    )
    card = PaperCard(
        paper_id=paper_id,
        metadata=PaperMetadata(
            title=title,
            authors=["陈明", "王芳"],
            year=2025,
            journal="社会学研究",
            abstract="聚焦户籍制度改革与平台劳动者城市融入的实证研究。",
            keywords=["平台劳动", "户籍改革"],
        ),
        study_profile=StudyProfile(
            study_type=StudyType.QUANTITATIVE_OBSERVATIONAL,
            method="双重差分与工具变量",
            identification_strategy="政策试点准自然实验",
            population="城市外来务工群体",
            data_source="全国流动人口动态监测调查",
        ),
        basic_facts=[
            ExtractedClaim(
                field_name="major_finding",
                statement=finding_stmt,
                provenance=Provenance.AUTHOR_STATED,
                verification=VerificationStatus.SUPPORTED,
                claim_type=ClaimType.CAUSAL,
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


def test_reading_report_markdown_includes_user_notes_and_human_reviews(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    finding_cid, ai_lim_cid = _seed_paper(
        repo,
        tmp_path,
        "paper-exp-1",
        title="户籍放宽与骑手长期定居意愿",
        block_text="双重差分估计显示，落户门槛放宽使定居意愿显著上升。",
        finding_stmt="落户门槛放宽显著提升了外来劳动者的长期定居意愿。",
        ai_lim_stmt="研究未充分控制同期房价波动对定居意愿的混杂影响。",
    )
    client = TestClient(create_app(data_dir=tmp_path, repository=repo))

    # 写入人工修正、已确认与个人笔记
    assert (
        client.put(
            f"/api/v1/papers/paper-exp-1/claim-reviews/{finding_cid}",
            json={
                "user_status": "corrected",
                "field_name": "major_finding",
                "original_statement": "落户门槛放宽显著提升了外来劳动者的长期定居意愿。",
                "corrected_statement": "落户门槛放宽主要显著提升了高学历外来群体的定居意愿，对低技能群体效应较弱。",
                "note": "详见原文第 7 页异质性分组表。",
            },
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/papers/paper-exp-1/claim-reviews/{ai_lim_cid}",
            json={"user_status": "confirmed", "field_name": "external_validity"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/papers/paper-exp-1/notes",
            json={
                "title": "理论对话思路",
                "note": "可与制度排斥理论对接，在文献综述第二章重点引用。",
                "page_number": 7,
            },
        ).status_code
        == 201
    )

    resp = client.get("/api/v1/papers/paper-exp-1/reading-report")
    assert resp.status_code == 200
    md = resp.text

    # 严格声部标识与原陈述保留
    assert "`[AUTHOR_STATED]`" in md
    assert "落户门槛放宽显著提升了外来劳动者的长期定居意愿。" in md
    assert "**人工审定（HUMAN_REVIEWED）**：✎ 人工修正" in md
    assert "落户门槛放宽主要显著提升了高学历外来群体的定居意愿" in md
    assert "**审定备注**：详见原文第 7 页异质性分组表。" in md
    assert "**人工审定（HUMAN_REVIEWED）**：✓ 已确认" in md

    # 个人笔记独立章节完整导出
    assert "## 我的阅读笔记（MY NOTE）" in md
    assert "### 理论对话思路 · PDF p.7" in md
    assert "可与制度排斥理论对接，在文献综述第二章重点引用。" in md

    # 重跑生成新 Card 后，旧审定记录作为历史版本审定区保留在导出中
    repo.clear_derived_analysis("paper-exp-1")
    repo.save_card(
        PaperCard(
            paper_id="paper-exp-1",
            metadata=PaperMetadata(title="户籍放宽与骑手长期定居意愿"),
            basic_facts=[
                ExtractedClaim(
                    field_name="major_finding",
                    statement="重跑后的新摘要表述。",
                    provenance=Provenance.AUTHOR_STATED,
                    verification=VerificationStatus.NEEDS_REVIEW,
                    evidence=[],
                )
            ],
            lite_model="test-lite",
            pro_model="test-pro",
            prompt_version="v2.2-test",
        )
    )
    md_after = client.get("/api/v1/papers/paper-exp-1/reading-report").text
    assert "## 历史版本人工审定记录（HUMAN_REVIEWED）" in md_after
    assert "落户门槛放宽主要显著提升了高学历外来群体的定居意愿" in md_after
    assert "## 我的阅读笔记（MY NOTE）" in md_after


def test_project_notes_crud_and_matrix_csv_markdown_exports(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    finding_cid, ai_lim_cid = _seed_paper(
        repo,
        tmp_path,
        "paper-mat-1",
        title="=公式标题防注入测试",
        block_text="实证分析显示平台算法显著改变了劳动时间组织。",
        finding_stmt="算法派单压缩了骑手的自主缓冲时间。",
        ai_lim_stmt="横截面设计难以排除不可观测个体异质性。",
    )
    client = TestClient(create_app(data_dir=tmp_path, repository=repo))

    proj = client.post(
        "/api/v1/projects",
        json={
            "name": "平台劳动与城市融入",
            "research_question": "数字算法与制度门槛如何共同塑造外来劳动者的时间与空间边界？",
        },
    ).json()
    pid = proj["id"]
    assert (
        client.post(
            f"/api/v1/projects/{pid}/papers/paper-mat-1",
            json={"reading_status": "core", "project_note": "基准定量案例"},
        ).status_code
        == 200
    )

    # 人工审定
    client.put(
        f"/api/v1/papers/paper-mat-1/claim-reviews/{finding_cid}",
        json={
            "user_status": "corrected",
            "field_name": "major_finding",
            "corrected_statement": "午高峰专送骑手的缓冲时间压缩尤为突出。",
            "note": "附录表格核心依据",
        },
    )
    client.put(
        f"/api/v1/papers/paper-mat-1/claim-reviews/{ai_lim_cid}",
        json={"user_status": "confirmed", "field_name": "external_validity"},
    )

    # 专题研究笔记 CRUD
    created_note = client.post(
        f"/api/v1/projects/{pid}/notes",
        json={"title": "综述框架备忘", "note": "拟按制度门槛 × 算法控制两个维度组织第二章。"},
    )
    assert created_note.status_code == 201
    note_id = created_note.json()["id"]
    assert client.post(f"/api/v1/projects/{pid}/notes", json={"note": "   "}).status_code in (400, 422)

    updated_note = client.put(
        f"/api/v1/projects/{pid}/notes/{note_id}",
        json={"title": "综述框架备忘 v2", "note": "增加性别与城乡户籍异质性对比小节。"},
    )
    assert updated_note.status_code == 200
    assert updated_note.json()["title"] == "综述框架备忘 v2"
    assert len(client.get(f"/api/v1/projects/{pid}").json()["notes"]) == 1

    # 1. 验证 Excel 友好 CSV 导出（UTF-8 BOM + 公式字符转义 + 人工审定列）
    csv_resp = client.get(f"/api/v1/projects/{pid}/evidence-matrix.csv")
    assert csv_resp.status_code == 200
    assert csv_resp.text.startswith("\ufeff")
    rows = list(csv.reader(io.StringIO(csv_resp.text.lstrip("\ufeff"))))
    assert len(rows) == 2
    header, data_row = rows[0], rows[1]
    assert "论文标题" in header
    assert "核心发现与人工审定 (AUTHOR / HUMAN_REVIEWED)" in header
    assert data_row[0].startswith("'=公式标题防注入测试")
    assert "核心文献" in data_row
    assert "基准定量案例" in data_row
    assert "人工修正：午高峰专送骑手的缓冲时间压缩尤为突出。" in "\n".join(data_row)
    assert "p.7" in data_row[-1]

    # 2. 验证 Markdown 矩阵导出包含专题研究笔记与人工审定
    md_resp = client.get(f"/api/v1/projects/{pid}/evidence-matrix.md")
    assert md_resp.status_code == 200
    md = md_resp.text
    assert "## 1. 专题研究笔记（MY NOTE）" in md
    assert "### 综述框架备忘 v2" in md
    assert "增加性别与城乡户籍异质性对比小节。" in md
    assert "[人工审定：人工修正]" in md
    assert "午高峰专送骑手的缓冲时间压缩尤为突出。" in md

    # 删除单条专题笔记 + 删除专题不影响底层论文
    assert client.delete(f"/api/v1/projects/{pid}/notes/{note_id}").status_code == 200
    assert client.get(f"/api/v1/projects/{pid}/notes").json()["notes"] == []
    assert client.delete(f"/api/v1/projects/{pid}").status_code == 200
    assert repo.get_paper("paper-mat-1") is not None


def test_project_scoped_fts5_search_with_source_kind_filters(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    cid_a, _ = _seed_paper(
        repo,
        tmp_path,
        "paper-s-a",
        title="专题内文献 A",
        block_text="算法控制与零工弹性工作制在特大城市形成独特时间规训。",
        finding_stmt="时间规训强化了平台粘性。",
        ai_lim_stmt="缺少纵向追踪样本。",
    )
    _seed_paper(
        repo,
        tmp_path,
        "paper-s-b",
        title="专题外文献 B",
        block_text="算法控制同样广泛存在于制造业智能排班车间。",
        finding_stmt="智能排班提升了流水线节拍。",
        ai_lim_stmt="单厂案例代表性受限。",
    )
    client = TestClient(create_app(data_dir=tmp_path, repository=repo))

    proj = client.post("/api/v1/projects", json={"name": "零工经济专题"}).json()
    pid = proj["id"]
    assert client.post(f"/api/v1/projects/{pid}/papers/paper-s-a", json={"reading_status": "reading"}).status_code == 200

    # 为专题内文献 A 写入个人笔记和人工修正
    client.post(
        "/api/v1/papers/paper-s-a/notes",
        json={"title": "反思", "note": "算法控制在笔记中的延伸思考。", "page_number": 7},
    )
    client.put(
        f"/api/v1/papers/paper-s-a/claim-reviews/{cid_a}",
        json={
            "user_status": "corrected",
            "field_name": "major_finding",
            "corrected_statement": "算法控制下的时间规训主要作用于全职专送群体。",
        },
    )

    # 1. 全局搜索命中 A 和 B
    all_hits = client.get("/api/v1/papers/search", params={"q": "算法控制"}).json()["hits"]
    assert {"paper-s-a", "paper-s-b"}.issubset({h["paper_id"] for h in all_hits})

    # 2. 限定专题搜索严格只返回 A
    scoped_hits = client.get("/api/v1/papers/search", params={"q": "算法控制", "project_id": pid}).json()["hits"]
    assert scoped_hits
    assert {h["paper_id"] for h in scoped_hits} == {"paper-s-a"}

    # 3. 来源类型精准过滤（SOURCE_BLOCK / USER_NOTE / HUMAN_REVIEWED）
    block_hits = client.get(
        "/api/v1/papers/search",
        params={"q": "算法控制", "project_id": pid, "source_kind": ["SOURCE_BLOCK"]},
    ).json()["hits"]
    assert block_hits and all(h["source_kind"] == "SOURCE_BLOCK" and h["doc_key"].startswith("block:") and h["page_number"] == 7 for h in block_hits)

    note_hits = client.get(
        "/api/v1/papers/search",
        params={"q": "算法控制", "project_id": pid, "source_kind": ["USER_NOTE"]},
    ).json()["hits"]
    assert note_hits and all(h["source_kind"] == "USER_NOTE" and h["doc_key"].startswith("note:") and h["page_number"] == 7 for h in note_hits)

    human_hits = client.get(
        "/api/v1/papers/search",
        params={"q": "全职专送", "project_id": pid, "source_kind": ["HUMAN_REVIEWED"]},
    ).json()["hits"]
    assert human_hits and all(h["source_kind"] == "HUMAN_REVIEWED" and h["doc_key"].startswith("claim_review:") for h in human_hits)


def test_desktop_search_panel_project_notes_and_csv_export_contracts():
    main_xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")
    search_cs = (DESKTOP / "Views" / "MainWindow.Search.cs").read_text(encoding="utf-8")
    client_cs = (DESKTOP / "Services" / "SraApiClient.cs").read_text(encoding="utf-8")

    assert ET.fromstring(main_xaml) is not None
    assert 'Content="•••"' not in main_xaml

    # 跨论文 / 专题级全文检索面板、直达跳页/跳笔记与快捷横幅
    for snippet in (
        'x:Name="FullTextSearchInput"',
        'IsVisible="{Binding ShowSearchPanelView}"',
        'Click="OpenFullTextSearch_Click"',
        'Click="SearchSourceFilter_Click"',
        'Click="SearchScopeProject_Click"',
        'Click="SearchScopeAll_Click"',
        'Click="SearchHitPrimaryJump_Click"',
        'Click="SearchHitJumpPdf_Click"',
        'QuickSearchBannerText',
    ):
        assert snippet in main_xaml, f"MainWindow.axaml 缺少全文检索面板契约：{snippet}"

    # 专题研究笔记与矩阵 CSV/Markdown 导出按钮
    for snippet in (
        'Text="专题研究笔记"',
        'Click="SaveDraftProjectNote_Click"',
        'Click="EditProjectNote_Click"',
        'Click="DeleteProjectNote_Click"',
        'Content="导出矩阵 ▾"',
        'Click="ExportActiveMatrixCsv_Click"',
        'Click="ContextExportProjectMatrixCsv_Click"',
    ):
        assert snippet in main_xaml, f"MainWindow.axaml 缺少专题笔记或导出契约：{snippet}"

    # 命中点击可直达 PDF 局部复核高亮与我的笔记 Tab
    assert "highlightSourceBlockId: hit.SourceBlockId" in search_cs
    assert "PaperTabs.SelectedIndex = 2" in search_cs

    # SraApiClient 完整支持 source_kind 检索、专题笔记 CRUD 与矩阵 CSV/Markdown 导出
    for method in (
        "sourceKinds",
        "GetProjectEvidenceMatrixCsvAsync",
        "GetProjectEvidenceMatrixMarkdownAsync",
        "GetProjectNotesAsync",
        "CreateProjectNoteAsync",
        "UpdateProjectNoteAsync",
        "DeleteProjectNoteAsync",
    ):
        assert method in client_cs, f"SraApiClient 缺少 {method}"
