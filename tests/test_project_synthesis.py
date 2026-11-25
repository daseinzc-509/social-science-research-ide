"""基于已审定 Evidence Matrix 的多文献综合（Milestone E · Literature Synthesis）全栈回归测试。"""
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
from sociology_research.services import create_application_services
from sociology_research.services.project_synthesis import (
    ProjectSynthesisPayload,
    SynthesisClaimRef,
    SynthesisPoint,
)

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop" / "SRA.Desktop"


def _seed_paper(
    repo: ResearchRepository,
    tmp_path: Path,
    paper_id: str,
    *,
    title: str,
    page: int,
    block_text: str,
    finding_stmt: str,
    second_finding_stmt: str,
    ai_lim_stmt: str,
) -> tuple[str, str, str]:
    pdf_path = tmp_path / f"{paper_id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% synthetic synthesis pdf\n")

    block = SourceBlock(
        id=f"blk-{paper_id}-p{page}",
        page_number=page,
        text=block_text,
        role="body",
        source_label=f"docling:p{page}",
    )
    doc = ParsedDocument(
        paper_id=paper_id,
        sha256=f"sha-{paper_id}",
        source_path=str(pdf_path),
        page_count=15,
        status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        blocks=[block],
    )
    rec = PaperRecord(
        id=paper_id,
        sha256=f"sha-{paper_id}",
        original_name=f"{paper_id}.pdf",
        stored_path=str(pdf_path),
        page_count=15,
        parse_status=ParseStatus.SUCCESS,
        parser_version="test-v1",
        warnings=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    repo.save_import(rec, doc)

    ev_ref = EvidenceReference(source_block_id=block.id, page_number=page, quote=block.text[:160])
    card = PaperCard(
        paper_id=paper_id,
        metadata=PaperMetadata(
            title=title,
            authors=["李研究"],
            year=2025,
            journal="社会学研究",
            abstract="探讨平台劳动与制度门槛机制。",
        ),
        study_profile=StudyProfile(
            study_type=StudyType.QUANTITATIVE_OBSERVATIONAL,
            method="双重差分",
            identification_strategy="城市试点准自然实验",
            population="城市流动人口",
            data_source="全国抽样调查",
        ),
        basic_facts=[
            ExtractedClaim(
                field_name="major_finding",
                statement=finding_stmt,
                provenance=Provenance.AUTHOR_STATED,
                verification=VerificationStatus.SUPPORTED,
                claim_type=ClaimType.CAUSAL,
                evidence=[ev_ref],
            ),
            ExtractedClaim(
                field_name="major_finding",
                statement=second_finding_stmt,
                provenance=Provenance.AUTHOR_STATED,
                verification=VerificationStatus.SUPPORTED,
                claim_type=ClaimType.DESCRIPTIVE,
                evidence=[ev_ref],
            ),
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
        compute_claim_id("major_finding", second_finding_stmt),
        compute_claim_id("external_validity", ai_lim_stmt),
    )


def _fake_pro_config() -> tuple[str, str, str]:
    return ("sk-test-only", "https://api.example.invalid/v1", "pro-synth-test")


def test_synthesis_preview_enforces_min_papers_filters_rejected_and_prioritizes_corrected(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    services = create_application_services(tmp_path, repo)
    services.project_synthesis._configured_pro = _fake_pro_config  # type: ignore[method-assign]
    client = TestClient(create_app(data_dir=tmp_path, repository=repo, services=services))

    cid_a1, cid_a2, _ = _seed_paper(
        repo,
        tmp_path,
        "p-syn-a",
        title="一线城市户籍放宽效应研究",
        page=5,
        block_text="实证结果显示高学历群体受益最明显。",
        finding_stmt="户籍放宽显著提高了外来人口落户意愿。",
        second_finding_stmt="这条将被人工驳回的错误过度推断主张。",
        ai_lim_stmt="仅覆盖东部沿海大城市。",
    )
    cid_b1, _, _ = _seed_paper(
        repo,
        tmp_path,
        "p-syn-b",
        title="中西部县域市民化障碍研究",
        page=9,
        block_text="在公共服务均等化不足的县域，户籍改革对定居意愿提升有限。",
        finding_stmt="中西部县域户籍放宽对长期定居意愿影响不显著。",
        second_finding_stmt="子女教育随迁仍是首要瓶颈。",
        ai_lim_stmt="横截面问卷难以识别长期动态效应。",
    )

    proj_id = client.post(
        "/api/v1/projects",
        json={"name": "户籍改革综合专题", "research_question": "户籍放宽在何种条件下提升定居意愿？"},
    ).json()["id"]

    # 仅 1 篇论文时拒绝综合（409）
    client.post(f"/api/v1/projects/{proj_id}/papers/p-syn-a", json={"reading_status": "core"})
    assert client.post(f"/api/v1/projects/{proj_id}/synthesis/preview", json={}).status_code == 409

    # 加入第 2 篇论文，并写入驳回、修正、确认
    client.post(f"/api/v1/projects/{proj_id}/papers/p-syn-b", json={"reading_status": "reviewed"})
    client.put(
        f"/api/v1/papers/p-syn-a/claim-reviews/{cid_a1}",
        json={
            "user_status": "corrected",
            "field_name": "major_finding",
            "corrected_statement": "人工修正：仅高学历外来群体在一线城市落户意愿显著上升。",
        },
    )
    client.put(
        f"/api/v1/papers/p-syn-a/claim-reviews/{cid_a2}",
        json={"user_status": "rejected", "field_name": "major_finding", "note": "原文无此结论"},
    )
    client.put(
        f"/api/v1/papers/p-syn-b/claim-reviews/{cid_b1}",
        json={"user_status": "confirmed", "field_name": "major_finding"},
    )

    prev = client.post(
        f"/api/v1/projects/{proj_id}/synthesis/preview",
        json={"include_unreviewed": True, "include_project_notes": True},
    )
    assert prev.status_code == 200
    body = prev.json()
    assert body["included_paper_count"] == 2
    assert body["rejected_claim_count"] == 1
    assert body["corrected_claim_count"] == 1
    assert body["confirmed_claim_count"] == 1
    assert body["will_send_to_external_api"] is True
    assert body["modifies_original_cards"] is False
    # 被驳回的主张绝不出现在外发载荷中；人工修正陈述优先纳入
    assert "这条将被人工驳回的错误过度推断主张" not in body["outbound_payload"]
    assert "人工修正：仅高学历外来群体在一线城市落户意愿显著上升。" in body["outbound_payload"]

    # 严格模式：仅纳入 confirmed + corrected（2 篇各 1 条）
    strict = client.post(
        f"/api/v1/projects/{proj_id}/synthesis/preview",
        json={"include_unreviewed": False},
    )
    assert strict.status_code == 200
    strict_body = strict.json()
    assert strict_body["included_paper_count"] == 2
    assert strict_body["included_claim_count"] == 2
    assert strict_body["skipped_unreviewed_count"] == 3


def test_synthesis_run_enforces_consent_anti_hallucination_drift_and_archival(tmp_path: Path):
    repo = ResearchRepository(tmp_path / "research.sqlite3")
    services = create_application_services(tmp_path, repo)
    services.project_synthesis._configured_pro = _fake_pro_config  # type: ignore[method-assign]
    client = TestClient(create_app(data_dir=tmp_path, repository=repo, services=services))

    cid_a1, _, cid_a_lim = _seed_paper(
        repo,
        tmp_path,
        "p-run-a",
        title="一线城市户籍改革研究",
        page=5,
        block_text="实证结果显示高学历群体受益最明显。",
        finding_stmt="户籍放宽提高了高学历群体定居意愿。",
        second_finding_stmt="社保年限是重要门槛。",
        ai_lim_stmt="仅覆盖东部特大城市。",
    )
    cid_b1, _, cid_b_lim = _seed_paper(
        repo,
        tmp_path,
        "p-run-b",
        title="中西部县域户籍改革研究",
        page=9,
        block_text="县域公共服务薄弱制约市民化进程。",
        finding_stmt="中西部县域户籍放宽效应不显著。",
        second_finding_stmt="教育资源差距主导迁移决策。",
        ai_lim_stmt="横截面数据限制因果识别。",
    )

    proj_id = client.post("/api/v1/projects", json={"name": "跨区域户籍对比"}).json()["id"]
    client.post(f"/api/v1/projects/{proj_id}/papers/p-run-a", json={"reading_status": "core"})
    client.post(f"/api/v1/projects/{proj_id}/papers/p-run-b", json={"reading_status": "reviewed"})
    client.put(
        f"/api/v1/papers/p-run-a/claim-reviews/{cid_a1}",
        json={
            "user_status": "corrected",
            "field_name": "major_finding",
            "corrected_statement": "人工修正：高学历群体在东部大城市受益显著。",
        },
    )

    preview = client.post(f"/api/v1/projects/{proj_id}/synthesis/preview", json={}).json()
    digest = preview["snapshot_digest"]

    # 1. 未勾选显式授权 → 400
    assert (
        client.post(
            f"/api/v1/projects/{proj_id}/synthesis/run",
            json={
                "expected_snapshot_digest": digest,
                "approve_external_send": True,
                "acknowledge_api_cost": False,
            },
        ).status_code
        == 400
    )

    # 2. 摘要不匹配 → 409
    assert (
        client.post(
            f"/api/v1/projects/{proj_id}/synthesis/run",
            json={
                "expected_snapshot_digest": "0" * 64,
                "approve_external_send": True,
                "acknowledge_api_cost": True,
            },
        ).status_code
        == 409
    )

    # 3. 模型编造不存在的 claim_id → 拦截 502 且不入库
    def hallucinating_runner(_plan, **_kwargs):
        return ProjectSynthesisPayload(
            executive_summary="编造引用测试。",
            consensus=[
                SynthesisPoint(
                    title="虚假共识",
                    summary="引用了不存在的主张。",
                    supporting_claims=[SynthesisClaimRef(paper_id="p-run-a", claim_id="claim-invented-999")],
                )
            ],
        )

    try:
        services.project_synthesis.run_synthesis(
            proj_id,
            expected_snapshot_digest=digest,
            approve_external_send=True,
            acknowledge_api_cost=True,
            synthesis_runner=hallucinating_runner,
        )
        raise AssertionError("编造 claim_id 必须被白名单校验拦截")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 502
    assert client.get(f"/api/v1/projects/{proj_id}/syntheses").json()["syntheses"] == []

    # 4. 合法白名单输出 → 成功归档 + 自动回填 PDF 页码/原文块/人工修正状态
    def valid_runner(_plan, **_kwargs):
        return ProjectSynthesisPayload(
            executive_summary="两项研究揭示了户籍放宽效应存在显著的区域与公共服务边界条件。",
            consensus=[
                SynthesisPoint(
                    title="公共服务与准入门槛共同调节迁移决策",
                    summary="无论一线城市还是县域，单纯放宽户籍登记不足以独立决定定居意愿。",
                    supporting_claims=[
                        SynthesisClaimRef(paper_id="p-run-a", claim_id=cid_a1),
                        SynthesisClaimRef(paper_id="p-run-b", claim_id=cid_b1),
                    ],
                )
            ],
            conflicts=[
                SynthesisPoint(
                    title="户籍放宽对定居意愿的净效应方向分歧",
                    summary="东部一线高学历群体显著正向受益，而中西部县域整体不显著。",
                    divergence_axis="区域层级与公共服务供给",
                    supporting_claims=[
                        SynthesisClaimRef(paper_id="p-run-a", claim_id=cid_a1),
                        SynthesisClaimRef(paper_id="p-run-b", claim_id=cid_b1),
                    ],
                )
            ],
            methodological_differences=[
                SynthesisPoint(
                    title="样本外推与识别策略边界差异",
                    summary="两篇研究分别受限于特大城市样本偏差与横截面因果识别约束。",
                    supporting_claims=[
                        SynthesisClaimRef(paper_id="p-run-a", claim_id=cid_a_lim),
                        SynthesisClaimRef(paper_id="p-run-b", claim_id=cid_b_lim),
                    ],
                )
            ],
            research_gaps=[
                SynthesisPoint(
                    title="缺乏跨区域纵向追踪下的公共服务中介检验",
                    summary="后续研究可在面板数据中同时纳入教育与医疗资源交互项。",
                    supporting_claims=[SynthesisClaimRef(paper_id="p-run-b", claim_id=cid_b_lim)],
                )
            ],
            limitations=["本综合仅覆盖 2 篇样本论文，外推至全国政策需补充更多地级市实证证据。"],
        )

    saved = services.project_synthesis.run_synthesis(
        proj_id,
        expected_snapshot_digest=digest,
        approve_external_send=True,
        acknowledge_api_cost=True,
        synthesis_runner=valid_runner,
    )
    syn_id = saved["id"]
    assert saved["provenance"] == "AI_INFERRED_SYNTHESIS"
    assert saved["original_cards_unchanged"] is True

    # 验证本地回填的溯源字段（可直接支持桌面端点击跳 PDF 页高亮）
    first_ref = saved["synthesis"]["conflicts"][0]["supporting_claims"][0]
    assert first_ref["paper_title"] == "一线城市户籍改革研究"
    assert first_ref["page_number"] == 5
    assert first_ref["source_block_id"] == "blk-p-run-a-p5"
    assert first_ref["human_status"] == "corrected"

    # API 列表、详情、Markdown 报告导出与删除
    assert len(client.get(f"/api/v1/projects/{proj_id}/syntheses").json()["syntheses"]) == 1
    assert client.get(f"/api/v1/projects/{proj_id}/syntheses/{syn_id}").status_code == 200
    md_resp = client.get(f"/api/v1/projects/{proj_id}/syntheses/{syn_id}/report.md")
    assert md_resp.status_code == 200
    assert "AI_INFERRED_SYNTHESIS" in md_resp.text
    assert "PDF p.5" in md_resp.text
    assert "区域层级与公共服务供给" in md_resp.text

    # 5. 运行中途状态漂移保护 → 409 且不入库
    def drifting_runner(_plan, **_kwargs):
        repo.upsert_claim_review("p-run-b", cid_b1, user_status="unresolved", note="运行中途更改")
        return valid_runner(_plan)

    try:
        services.project_synthesis.run_synthesis(
            proj_id,
            expected_snapshot_digest=digest,
            approve_external_send=True,
            acknowledge_api_cost=True,
            synthesis_runner=drifting_runner,
        )
        raise AssertionError("运行期间快照漂移必须拦截返回 409")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409

    # 删除单条历史快照绝不影响原 Paper Card
    assert client.delete(f"/api/v1/projects/{proj_id}/syntheses/{syn_id}").status_code == 200
    assert client.get(f"/api/v1/projects/{proj_id}/syntheses").json()["syntheses"] == []
    assert repo.get_card("p-run-a") is not None


def test_desktop_project_synthesis_ui_contracts():
    main_xaml = (DESKTOP / "Views" / "MainWindow.axaml").read_text(encoding="utf-8")
    syn_cs = (DESKTOP / "Views" / "MainWindow.Synthesis.cs").read_text(encoding="utf-8")
    preview_win = (DESKTOP / "Views" / "SynthesisPreviewWindow.cs").read_text(encoding="utf-8")
    client_cs = (DESKTOP / "Services" / "SraApiClient.cs").read_text(encoding="utf-8")

    assert ET.fromstring(main_xaml) is not None
    assert 'Content="•••"' not in main_xaml

    for snippet in (
        'Click="SwitchToSynthesisView_Click"',
        'Click="ContextOpenProjectSynthesis_Click"',
        'IsVisible="{Binding ShowProjectSynthesisView}"',
        "AI_INFERRED_SYNTHESIS",
        'Click="PreviewAndRunSynthesis_Click"',
        'Click="ExportActiveSynthesisMarkdown_Click"',
        'Click="DeleteActiveSynthesis_Click"',
        'Click="SynthesisCitationJump_Click"',
        'Click="SynthesisCitationOpenPaper_Click"',
    ):
        assert snippet in main_xaml, f"MainWindow.axaml 缺少多文献综合契约：{snippet}"

    # 预检弹窗强制双重勾选授权 + 载荷审阅；引文点击支持直跳 PDF 高亮
    assert "OutboundPayload" in preview_win
    assert "sendConsent.IsChecked == true && costConsent.IsChecked == true" in preview_win
    assert "highlightSourceBlockId: blockId" in syn_cs

    for method in (
        "PreviewProjectSynthesisAsync",
        "RunProjectSynthesisAsync",
        "ListProjectSynthesesAsync",
        "GetProjectSynthesisAsync",
        "GetProjectSynthesisMarkdownAsync",
        "DeleteProjectSynthesisAsync",
    ):
        assert f"{method}(" in client_cs, f"SraApiClient 缺少 {method}"
