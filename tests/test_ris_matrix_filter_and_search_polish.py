"""NoteExpress RIS 导出、专题笔记入 FTS5、检索词高亮与 Evidence Matrix 多维快速筛选全栈测试。"""
from __future__ import annotations

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


def _seed_paper(repo: ResearchRepository, tmp_path: Path, paper_id: str, *, with_card: bool = True) -> str:
    pdf_path = tmp_path / f"{paper_id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% minimal ris test pdf\n")

    block = SourceBlock(
        id=f"blk-{paper_id}-1",
        page_number=4,
        text="田野观察指出，平台配送算法重塑了基层劳动者的时间边界。",
        role="body",
        source_label="docling:p4",
    )
    doc = ParsedDocument(
        paper_id=paper_id,
        sha256=f"sha-{paper_id}",
        source_path=str(pdf_path),
        page_count=10,
        status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        blocks=[block],
    )
    rec = PaperRecord(
        id=paper_id,
        sha256=f"sha-{paper_id}",
        original_name=f"{paper_id}.pdf",
        stored_path=str(pdf_path),
        page_count=10,
        parse_status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        warnings=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.save_import(rec, doc)

    finding = "平台派单机制压缩了全职骑手的自主休息窗口。"
    if with_card:
        card = PaperCard(
            paper_id=paper_id,
            metadata=PaperMetadata(
                title="算法调度与劳动时间秩序",
                authors=["周社会", "Lin Wei"],
                year=2025,
                journal="社会学研究",
                volume="40",
                issue="3",
                pages="45-68",
                doi="10.1234/sra.2025.003",
                keywords=["平台劳动", "时间规训"],
                abstract="本文基于田野调查分析算法控制下的时间自主性。",
            ),
            study_profile=StudyProfile(
                study_type=StudyType.QUALITATIVE,
                method="深度访谈",
            ),
            basic_facts=[
                ExtractedClaim(
                    field_name="major_finding",
                    statement=finding,
                    provenance=Provenance.AUTHOR_STATED,
                    verification=VerificationStatus.SUPPORTED,
                    claim_type=ClaimType.DESCRIPTIVE,
                    evidence=[EvidenceReference(source_block_id=block.id, page_number=4, quote=block.text)],
                )
            ],
            lite_model="test-lite",
            pro_model="test-pro",
            prompt_version="v2.2-test",
        )
        repo.save_card(card)
    return compute_claim_id("major_finding", finding)


def test_noteexpress_ris_export_for_single_paper_and_project(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    cid_1 = _seed_paper(repo, tmp_path, "paper-ris-1", with_card=True)
    _seed_paper(repo, tmp_path, "paper-ris-2", with_card=False)
    client = TestClient(create_app(data_dir=tmp_path, repository=repo))

    # 为未分析论文补充手动题录修订，确保未分析论文也能干净导出 RIS
    assert (
        client.post(
            "/api/v1/papers/paper-ris-2/metadata",
            json={
                "values": {
                    "title": "手动补录的未分析文献标题",
                    "authors": ["吴田野"],
                    "year": 2024,
                    "journal": "社会",
                    "pages": "101–125",
                },
                "source_note": "人工核对封皮",
            },
        ).status_code
        == 200
    )

    # 为论文 1 添加个人笔记和人工修正
    assert (
        client.post(
            "/api/v1/papers/paper-ris-1/notes",
            json={"title": "文献笔记", "note": "适合在理论框架部分重点引用。", "page_number": 4},
        ).status_code
        == 201
    )
    assert (
        client.put(
            f"/api/v1/papers/paper-ris-1/claim-reviews/{cid_1}",
            json={
                "user_status": "corrected",
                "field_name": "major_finding",
                "corrected_statement": "专送与众包骑手受算法派单影响的幅度存在显著分化。",
            },
        ).status_code
        == 200
    )

    # 1. 单篇 RIS 导出（NoteExpress 原生兼容格式）
    single_resp = client.get("/api/v1/papers/paper-ris-1/citation.ris")
    assert single_resp.status_code == 200
    ris = single_resp.text
    assert "TY  - JOUR" in ris
    assert "TI  - 算法调度与劳动时间秩序" in ris
    assert "AU  - 周社会" in ris
    assert "AU  - Lin Wei" in ris
    assert "PY  - 2025" in ris
    assert "JO  - 社会学研究" in ris
    assert "VL  - 40" in ris
    assert "IS  - 3" in ris
    assert "SP  - 45" in ris
    assert "EP  - 68" in ris
    assert "DO  - 10.1234/sra.2025.003" in ris
    assert "KW  - 平台劳动" in ris
    assert "KW  - 时间规训" in ris
    assert "[MY NOTE] (PDF p.4) 文献笔记：适合在理论框架部分重点引用。" in ris
    assert "[HUMAN_REVIEWED: 人工修正] 专送与众包骑手受算法派单影响的幅度存在显著分化。" in ris
    assert "ER  - " in ris

    # 2. 整专题 RIS 导出（包含已分析与未分析手动题录，且附带专题状态与专题备注）
    proj_id = client.post("/api/v1/projects", json={"name": "平台劳动专题"}).json()["id"]
    client.post(f"/api/v1/projects/{proj_id}/papers/paper-ris-1", json={"reading_status": "core", "project_note": "核心文献A"})
    client.post(f"/api/v1/projects/{proj_id}/papers/paper-ris-2", json={"reading_status": "queued", "project_note": "待精读B"})

    proj_ris_resp = client.get(f"/api/v1/projects/{proj_id}/citations.ris")
    assert proj_ris_resp.status_code == 200
    proj_ris = proj_ris_resp.text
    assert proj_ris.count("TY  - JOUR") == 2
    assert proj_ris.count("ER  - ") == 2
    assert "TI  - 手动补录的未分析文献标题" in proj_ris
    assert "SP  - 101" in proj_ris
    assert "EP  - 125" in proj_ris
    assert "[专题] 平台劳动专题（状态：核心文献）" in proj_ris
    assert "[专题备注] 核心文献A" in proj_ris


def test_project_notes_indexed_and_synced_in_fts5_search(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    client = TestClient(create_app(data_dir=tmp_path, repository=repo))

    proj_a = client.post("/api/v1/projects", json={"name": "数字治理专题"}).json()["id"]
    proj_b = client.post("/api/v1/projects", json={"name": "乡村教育专题"}).json()["id"]

    # 即使专题 A 尚未添加任何论文，创建专题研究笔记后也必须能被 FTS5 搜索命中
    note_a = client.post(
        f"/api/v1/projects/{proj_a}/notes",
        json={"title": "框架思考", "note": "官僚科层弹性与数字平台透明化的张力构成了本研究的核心谜题。"},
    ).json()
    note_id = note_a["id"]

    client.post(
        f"/api/v1/projects/{proj_b}/notes",
        json={"title": "教育备忘", "note": "县域寄宿制学校的同伴效应观察。"},
    )

    # 1. 全局搜索命中 PROJECT_NOTE 并正确解析专题标题
    global_hits = client.get("/api/v1/papers/search", params={"q": "官僚科层弹性"}).json()["hits"]
    assert len(global_hits) == 1
    assert global_hits[0]["source_kind"] == "PROJECT_NOTE"
    assert global_hits[0]["paper_id"] == f"project:{proj_a}"
    assert global_hits[0]["paper_title"] == "专题 · 数字治理专题"

    # 2. 按专题范围 + source_kind=PROJECT_NOTE 精确隔离
    scoped_a = client.get(
        "/api/v1/papers/search",
        params={"q": "官僚科层弹性", "project_id": proj_a, "source_kind": ["PROJECT_NOTE"]},
    ).json()["hits"]
    assert len(scoped_a) == 1

    scoped_b = client.get(
        "/api/v1/papers/search",
        params={"q": "官僚科层弹性", "project_id": proj_b, "source_kind": ["PROJECT_NOTE"]},
    ).json()["hits"]
    assert scoped_b == []

    # 3. 更新专题笔记同步刷新 FTS5；删除专题笔记后从 FTS5 清除
    assert (
        client.put(
            f"/api/v1/projects/{proj_a}/notes/{note_id}",
            json={"title": "更新框架", "note": "转向考察基层减负与算法考核指标的制度耦合。"},
        ).status_code
        == 200
    )
    assert client.get("/api/v1/papers/search", params={"q": "官僚科层弹性"}).json()["hits"] == []
    assert len(client.get("/api/v1/papers/search", params={"q": "制度耦合"}).json()["hits"]) == 1

    assert client.delete(f"/api/v1/projects/{proj_a}/notes/{note_id}").status_code == 200
    assert client.get("/api/v1/papers/search", params={"q": "制度耦合"}).json()["hits"] == []


def test_desktop_ris_matrix_filter_and_highlight_snippet_contracts():
    main_xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")
    highlight_cs = (DESKTOP / "Controls" / "HighlightedSnippetText.cs").read_text(encoding="utf-8")
    client_cs = (DESKTOP / "Services" / "SraApiClient.cs").read_text(encoding="utf-8")

    assert ET.fromstring(main_xaml) is not None
    assert 'Content="•••"' not in main_xaml

    # 1. 检索词荧光高亮控件（使用 MD3 动态令牌，零硬编码色值）
    assert "controls:HighlightedSnippetText" in main_xaml
    assert "AmberSoftBrush" in highlight_cs
    assert "AmberInkBrush" in highlight_cs
    assert 'Tag="PROJECT_NOTE"' in main_xaml

    # 2. Evidence Matrix 多维快速筛选条
    for snippet in (
        'Click="MatrixReadingFilter_Click"',
        'Click="MatrixReviewFilter_Click"',
        'Click="ResetMatrixFilters_Click"',
        "MatrixStudyTypeOptions",
        "MatrixFilterSummary",
        "HumanReviewSummaryBadge",
    ):
        assert snippet in main_xaml, f"MainWindow.axaml 缺少矩阵快速筛选契约：{snippet}"

    # 3. NoteExpress RIS 导出入口与客户端方法
    for snippet in (
        'Click="ExportRis_Click"',
        'Click="ContextExportRis_Click"',
        'Click="ExportActiveProjectRis_Click"',
        'Click="ContextExportProjectRis_Click"',
    ):
        assert snippet in main_xaml, f"MainWindow.axaml 缺少 NoteExpress RIS 导出入口：{snippet}"

    assert "GetPaperRisAsync(" in client_cs
    assert "GetProjectRisAsync(" in client_cs


def test_raw_card_json_viewer_uses_non_blocking_preview_and_utf8_encoder():
    main_xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")
    styles_xaml = (DESKTOP / "Styles" / "ControlStyles.axaml").read_text(encoding="utf-8")
    proj_cs = (DESKTOP / "ViewModels" / "MainWindowViewModel.Projection.cs").read_text(encoding="utf-8")

    # 防止大 PaperCard JSON 在只读 TextBox 获焦/失焦时冻结 UI 线程
    assert "UnsafeRelaxedJsonEscaping" in proj_cs
    assert "RawCardPreviewCharLimit" in proj_cs
    assert 'Classes="readonly-code"' in main_xaml
    assert 'IsUndoEnabled="False"' in main_xaml
    assert 'Click="CopyRawCardJson_Click"' in main_xaml
    assert 'Click="ToggleFullRawCard_Click"' in main_xaml
    assert 'Selector="TextBox.readonly-code"' in styles_xaml


def test_delete_paper_bypasses_isbusy_refresh_shortcircuit():
    workspace_cs = (DESKTOP / "ViewModels" / "MainWindowViewModel.Workspace.cs").read_text(encoding="utf-8")
    delete_block = workspace_cs.split("DeleteSelectedAsync()", 1)[1].split("private static string JsonValue", 1)[0]
    # 删除流程中 IsBusy 为 true，严禁调用会被 `if (IsBusy) return;` 静默跳过的 RefreshAsync()
    assert "await RefreshAsync();" not in delete_block
    assert "await RefreshCoreAsync(" in delete_block
    assert "_allPapers.RemoveAll(" in delete_block
