"""基于持久化 PaperCard 确定性构建深度阅读报告与 Markdown 导出模块。"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import (
    ClaimReviewDecision,
    DeepReadingReport,
    ExtractedClaim,
    PaperCard,
    UserNote,
    compute_claim_id,
)

_HUMAN_STATUS_LABELS: dict[str, str] = {
    "confirmed": "✓ 已确认",
    "corrected": "✎ 人工修正",
    "rejected": "✕ 存疑驳回",
    "unresolved": "? 待定",
}


def build_deep_reading_report(card: PaperCard) -> DeepReadingReport:
    """Build a human-readable paper-reading view without making another model call."""

    by_field: dict[str, list[ExtractedClaim]] = {}
    for claim in card.basic_facts:
        by_field.setdefault(claim.field_name, []).append(claim)
    metadata = card.metadata
    citation_parts = []
    if metadata.authors:
        citation_parts.append("、".join(metadata.authors))
    if metadata.year:
        citation_parts.append(str(metadata.year))
    if metadata.journal:
        citation_parts.append(metadata.journal)
    citation_line = " · ".join(citation_parts) or None
    positioning = metadata.abstract
    if not positioning:
        findings = by_field.get("major_finding", [])
        positioning = findings[0].statement if findings else None

    return DeepReadingReport(
        title=metadata.title or "未识别标题",
        citation_line=citation_line,
        positioning=positioning,
        research_questions=by_field.get("research_question", []),
        theory_and_concepts=by_field.get("theory_concept", []),
        study_design=[
            *by_field.get("research_method", []),
            *by_field.get("research_sample", []),
            *by_field.get("measurement_indicator", []),
        ],
        key_findings=by_field.get("major_finding", []),
        author_explanations=by_field.get("author_explanation", []),
        author_limitations=by_field.get("research_limitations", []),
        ai_review=card.analysis,
        ai_limitations=card.limitations,
        reading_recommendation=card.reading_recommendation,
        study_profile=card.study_profile,
        claim_audits=card.claim_audits,
    )


def render_deep_reading_markdown(
    card: PaperCard,
    *,
    user_notes: Sequence[UserNote] | None = None,
    claim_reviews: Sequence[ClaimReviewDecision] | None = None,
) -> str:
    report = build_deep_reading_report(card)
    review_by_cid: dict[str, ClaimReviewDecision] = {
        rev.claim_id: rev for rev in (claim_reviews or []) if rev.claim_id
    }
    matched_review_ids: set[str] = set()

    lines = [f"# {report.title}", ""]
    if report.citation_line:
        lines += [report.citation_line, ""]
    if report.positioning:
        lines += ["## 一句话定位 / 摘要", "", report.positioning, ""]
    if report.study_profile:
        sp = report.study_profile
        lines += ["## 研究设计画像", "", f"- 研究类型：`{sp.study_type.value}`"]
        for label, value in (
            ("判断依据", sp.rationale), ("研究对象", sp.population), ("数据来源", sp.data_source),
            ("分析单位", sp.unit_of_analysis), ("方法", sp.method), ("识别策略", sp.identification_strategy),
            ("测量", sp.measurement),
        ):
            if value:
                lines.append(f"- {label}：{value}")
        lines.append("")

    def add_claims(title: str, claims: list[ExtractedClaim]) -> None:
        if not claims:
            return
        lines.extend([f"## {title}", ""])
        for claim in claims:
            cid = claim.claim_id or compute_claim_id(claim.field_name, claim.statement)
            support = f" · semantic={claim.semantic_support.value}" if claim.semantic_support else ""
            lines.append(
                f"- `[{claim.provenance.value}]` **{claim.field_name}**：{claim.statement}  _[{claim.verification.value}{support}]_"
            )
            if claim.rationale:
                lines.append(f"  - 理由：{claim.rationale}")
            scope_items = claim.scope.compact_items()
            if scope_items:
                lines.append("  - 范围：" + "；".join(scope_items))
            for ref in claim.evidence:
                lines.append(f"  - PDF p.{ref.page_number} ({ref.role.value})：{ref.quote}")
            review = review_by_cid.get(cid)
            if review is not None:
                matched_review_ids.add(cid)
                status_label = _HUMAN_STATUS_LABELS.get(str(review.user_status.value), str(review.user_status.value))
                lines.append(f"  - **人工审定（HUMAN_REVIEWED）**：{status_label}")
                if review.corrected_statement:
                    lines.append(f"  - **研究者修正表述（HUMAN_REVIEWED）**：{review.corrected_statement}")
                if review.note:
                    lines.append(f"  - **审定备注**：{review.note}")
        lines.append("")

    add_claims("研究问题", report.research_questions)
    add_claims("理论与概念", report.theory_and_concepts)
    add_claims("研究设计", report.study_design)
    add_claims("核心发现", report.key_findings)
    add_claims("作者解释 / 机制", report.author_explanations)
    add_claims("作者自述局限", report.author_limitations)
    add_claims("AI 方法学审读", report.ai_review)
    add_claims("AI 发现的局限", report.ai_limitations)
    if report.reading_recommendation:
        add_claims("阅读建议", [report.reading_recommendation])

    if report.claim_audits:
        lines += ["## Claim / Evidence 审计", ""]
        claim_lookup = {claim.claim_id: claim for claim in card.basic_facts if claim.claim_id}
        for audit in report.claim_audits:
            claim = claim_lookup.get(audit.claim_id)
            lines.append(f"- **{audit.semantic_support.value}** · `{audit.claim_id}`")
            if claim:
                lines.append(f"  - 主张：{claim.statement}")
            if audit.rationale:
                lines.append(f"  - 判断：{audit.rationale}")
            for ref in audit.evidence:
                lines.append(f"  - PDF p.{ref.page_number} ({ref.role.value})：{ref.quote}")
        lines.append("")

    orphan_reviews = [
        rev for rev in (claim_reviews or []) if rev.claim_id and rev.claim_id not in matched_review_ids
    ]
    if orphan_reviews:
        lines += ["## 历史版本人工审定记录（HUMAN_REVIEWED）", ""]
        for rev in orphan_reviews:
            status_label = _HUMAN_STATUS_LABELS.get(str(rev.user_status.value), str(rev.user_status.value))
            field_tag = f" `{rev.field_name}`" if rev.field_name else ""
            lines.append(f"- **{status_label}**{field_tag}（`{rev.claim_id}`）")
            if rev.original_statement:
                lines.append(f"  - 原陈述：{rev.original_statement}")
            if rev.corrected_statement:
                lines.append(f"  - 研究者修正表述：{rev.corrected_statement}")
            if rev.note:
                lines.append(f"  - 审定备注：{rev.note}")
        lines.append("")

    if user_notes:
        lines += ["## 我的阅读笔记（MY NOTE）", ""]
        for note in user_notes:
            heading = note.title.strip() if note.title else "阅读笔记"
            page_tag = f" · PDF p.{note.page_number}" if note.page_number else ""
            time_tag = (note.updated_at or note.created_at)[:16].replace("T", " ")
            lines.append(f"### {heading}{page_tag}")
            if time_tag:
                lines.append(f"_记录时间：{time_tag} · 来源：MY NOTE_")
                lines.append("")
            lines.append(note.note.strip())
            lines.append("")

    lines += ["---", "", "本报告由已保存的 Paper Card、个人笔记与人工审定记录确定性生成；严格区分 AUTHOR / AI REVIEW / HUMAN_REVIEWED / MY NOTE，不会为排版再次调用模型。"]
    return "\n".join(lines).rstrip() + "\n"
