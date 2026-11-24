"""基于持久化 PaperCard 确定性构建深度阅读报告与 Markdown 导出模块。"""

from __future__ import annotations

from ..models import DeepReadingReport, ExtractedClaim, PaperCard


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


def render_deep_reading_markdown(card: PaperCard) -> str:
    report = build_deep_reading_report(card)
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
            support = f" · semantic={claim.semantic_support.value}" if claim.semantic_support else ""
            lines.append(f"- **{claim.field_name}**：{claim.statement}  _[{claim.verification.value}{support}]_")
            if claim.rationale:
                lines.append(f"  - 理由：{claim.rationale}")
            scope_items = claim.scope.compact_items()
            if scope_items:
                lines.append("  - 范围：" + "；".join(scope_items))
            for ref in claim.evidence:
                lines.append(f"  - PDF p.{ref.page_number} ({ref.role.value})：{ref.quote}")
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

    lines += ["---", "", "本报告由已保存的 Paper Card 确定性生成；不会为排版再次调用模型。"]
    return "\n".join(lines).rstrip() + "\n"
