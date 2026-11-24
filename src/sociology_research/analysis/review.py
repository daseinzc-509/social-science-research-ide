"""模型 JSON 响应清洗、研究类型路由、Pro 独立上下文与声明审计解析模块。"""

from __future__ import annotations

import json
import re
import unicodedata

from pydantic import BaseModel

from ..models import (
    ClaimAudit,
    ClaimScope,
    ClaimType,
    EvidenceReference,
    EvidenceRole,
    EvidenceSpan,
    ExtractedClaim,
    LLMClaimAudit,
    LiteExtraction,
    PaperMetadata,
    ProAnalysis,
    ProReviewItem,
    Provenance,
    SemanticSupportStatus,
    SourceBlock,
    StudyType,
    VerificationStatus,
)
from .evidence import (
    _claim_id,
    _evidence_token,
    _logical_evidence_spans,
    _span_matches_source,
)
from .layout import _compact_statement


def _json_task(payload: str, schema: type[BaseModel]) -> str:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return f"目标 JSON Schema：\n{schema_json}\n\n输入资料：\n{payload}"


def _parse_json_response(response: str) -> dict:
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response did not contain a JSON object")
    result = json.loads(text[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError("response JSON must be an object")
    return result


def _sanitize_model_response(data: dict, schema: type[BaseModel]) -> dict:
    def compact_claim(item: object) -> None:
        if not isinstance(item, dict):
            return
        statement = item.get("statement")
        if isinstance(statement, str) and len(statement) > 260:
            item["statement"] = _compact_statement(statement, max_chars=260)

    if schema is LiteExtraction:
        # A model may emit JSON null for genuinely absent metadata, but the
        # auditable schema correctly requires a source-supported value.
        # Drop only those invalid optional metadata entries; NEVER synthesize
        # evidence IDs, coerce None to the string "None", or repair a claim
        # whose source grounding was absent.
        facts = data.get("metadata_facts")
        if isinstance(facts, list):
            accepted = []
            skipped = 0
            for item in facts:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                value = item.get("value")
                evidence = item.get("evidence")
                usable_value = (
                    (isinstance(value, str) and bool(value.strip()))
                    or (isinstance(value, int) and not isinstance(value, bool))
                    or (isinstance(value, list) and bool(value)
                        and all(isinstance(v, str) and bool(v.strip()) for v in value))
                )
                if not usable_value or not isinstance(evidence, list) or not evidence:
                    skipped += 1
                    continue
                accepted.append(item)
            if skipped:
                data["metadata_facts"] = accepted
                data["_sra_skipped_metadata_facts"] = skipped
        valid_claim_types = set(ClaimType._value2member_map_)
        structural_aliases = {
            "RESEARCH_METHOD": ClaimType.METHOD.value,
            "MEASUREMENT_INDICATOR": ClaimType.MEASUREMENT.value,
            "THEORY_CONCEPT": ClaimType.THEORETICAL.value,
            "AUTHOR_EXPLANATION": ClaimType.MECHANISM.value,
            "RESEARCH_LIMITATIONS": ClaimType.LIMITATION.value,
            "LIMITATIONS": ClaimType.LIMITATION.value,
            "RESEARCH_SAMPLE": ClaimType.DESCRIPTIVE.value,
            "KEY_NUMBER": ClaimType.DESCRIPTIVE.value,
            "RESEARCH_QUESTION": ClaimType.OTHER.value,
            "MAJOR_FINDING": ClaimType.OTHER.value,
            "FINDING": ClaimType.OTHER.value,
        }
        for item in data.get("basic_facts", []):
            compact_claim(item)
            if not isinstance(item, dict):
                continue
            raw_claim_type = item.get("claim_type")
            if raw_claim_type is not None:
                normalized = str(raw_claim_type).strip().upper().replace(" ", "_").replace("-", "_")
                item["claim_type"] = (
                    normalized
                    if normalized in valid_claim_types
                    else structural_aliases.get(normalized, ClaimType.OTHER.value)
                )
            if item.get("scope") is None:
                item["scope"] = {}
    elif schema is ProAnalysis:
        def compact_review(item: object) -> None:
            if not isinstance(item, dict):
                return
            if isinstance(item.get("assessment"), str):
                item["assessment"] = _compact_statement(item["assessment"], max_chars=500)
            elif isinstance(item.get("statement"), str):
                item["statement"] = _compact_statement(item["statement"], max_chars=500)
            if isinstance(item.get("rationale"), str) and len(item["rationale"]) > 900:
                item["rationale"] = _compact_statement(item["rationale"], max_chars=900)
        for key in ("analysis", "limitations"):
            for item in data.get(key, []):
                compact_review(item)
        compact_review(data.get("reading_recommendation"))
        for item in data.get("claim_audits", []):
            if isinstance(item, dict) and isinstance(item.get("rationale"), str) and len(item["rationale"]) > 900:
                item["rationale"] = _compact_statement(item["rationale"], max_chars=900)
    return data


def _mark_inferred(claims: list[ExtractedClaim]) -> list[ExtractedClaim]:
    return [
        claim.model_copy(
            update={
                "provenance": Provenance.AI_INFERRED,
                "verification": VerificationStatus.NEEDS_REVIEW,
            }
        )
        for claim in claims
    ]


def _pro_context(
    metadata: PaperMetadata,
    verified_facts: list[ExtractedClaim],
    research_context: str | None,
    span_registry: dict[str, EvidenceSpan],
) -> tuple[str, dict[str, EvidenceSpan]]:
    excerpts: list[dict[str, object]] = []
    evidence_chars = 0
    qmap: dict[str, EvidenceSpan] = {}
    stable_to_token: dict[str, str] = {}
    priorities = ("finding", "method", "data", "sample", "limitation", "result", "measure", "mechanism")
    ordered = sorted(
        verified_facts,
        key=lambda claim: min(
            (priorities.index(p) for p in priorities if p in claim.field_name.casefold()),
            default=len(priorities),
        ),
    )

    def token_for(reference: EvidenceReference) -> str | None:
        nonlocal evidence_chars
        if not reference.evidence_id:
            return None
        existing = stable_to_token.get(reference.evidence_id)
        if existing:
            return existing
        span = span_registry.get(reference.evidence_id)
        if span is None:
            return None
        if evidence_chars + len(span.text) > 16000:
            return None
        token = f"Q{len(qmap) + 1:04d}"
        qmap[token] = span
        stable_to_token[span.id] = token
        evidence_chars += len(span.text)
        excerpts.append(
            {
                "evidence_id": token,
                "pdf_page": span.page_number,
                "kind": span.kind,
                "quote": span.text,
            }
        )
        return token

    compact_facts: list[dict[str, object]] = []
    for fact in ordered[:100]:
        tokens: list[str] = []
        for reference in fact.evidence[:3]:
            token = token_for(reference)
            if token:
                tokens.append(token)
        if not tokens:
            continue
        compact_facts.append(
            {
                "field_name": fact.field_name,
                "statement": fact.statement[:800],
                "provenance": fact.provenance.value,
                "verification": fact.verification.value,
                "evidence_ids": tokens,
            }
        )

    payload = {
        "research_context": research_context,
        "metadata": metadata.model_dump(mode="json"),
        "lite_facts": compact_facts,
        "selected_source_excerpts": excerpts,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), qmap


def _route_study_type(blocks: list[SourceBlock], facts: list[ExtractedClaim]) -> StudyType:
    text = "\n".join(block.text for block in blocks[:220])
    text += "\n" + "\n".join(f.statement for f in facts)
    normalized = unicodedata.normalize("NFKC", text).casefold()

    def score(words: tuple[str, ...]) -> int:
        return sum(min(normalized.count(word), 6) for word in words)

    experimental = score(("随机", "实验组", "对照组", "randomized", "randomised", "treatment group", "control group", "experiment"))
    qualitative = score(("访谈", "田野", "民族志", "扎根理论", "编码", "深度访谈", "interview", "ethnograph", "qualitative", "coding"))
    quantitative = score(("回归", "样本量", "问卷", "显著", "标准误", "regression", "survey", "coefficient", "p-value", "dataset"))
    theoretical = score(("理论模型", "命题", "概念框架", "conceptual", "proposition", "theoretical model"))
    review = score(("系统综述", "元分析", "文献综述", "systematic review", "meta-analysis", "scoping review"))

    if review >= 4 and review >= max(experimental, qualitative, quantitative, theoretical):
        return StudyType.REVIEW
    if experimental >= 4 and experimental >= qualitative:
        return StudyType.EXPERIMENTAL
    if qualitative >= 4 and quantitative >= 4:
        return StudyType.MIXED_METHODS
    if qualitative >= 4 and qualitative > quantitative:
        return StudyType.QUALITATIVE
    if quantitative >= 4:
        return StudyType.QUANTITATIVE_OBSERVATIONAL
    if theoretical >= 3:
        return StudyType.THEORETICAL
    return StudyType.UNKNOWN


def _review_dimensions(study_type: StudyType) -> list[str]:
    common = ["research_question", "evidence_alignment"]
    mapping = {
        StudyType.EXPERIMENTAL: [
            "randomization_and_design", "attrition_and_compliance", "measurement_validity",
            "statistical_inference", "multiple_testing_and_robustness", "mechanism", "external_validity", "author_limitations",
        ],
        StudyType.QUANTITATIVE_OBSERVATIONAL: [
            "identification_strategy", "confounding_and_selection", "measurement_validity",
            "model_specification", "robustness", "mechanism", "external_validity", "author_limitations",
        ],
        StudyType.QUALITATIVE: [
            "case_selection", "sampling_and_access", "coding_and_interpretation", "triangulation",
            "negative_cases", "reflexivity", "scope_conditions", "author_limitations",
        ],
        StudyType.THEORETICAL: [
            "assumptions", "propositions", "mechanism", "scope_conditions",
            "alternative_explanations", "empirical_implications", "internal_consistency", "author_limitations",
        ],
        StudyType.MIXED_METHODS: [
            "design_integration", "quantitative_identification", "qualitative_sampling", "measurement_validity",
            "triangulation", "robustness", "scope_conditions", "author_limitations",
        ],
        StudyType.REVIEW: [
            "search_strategy", "inclusion_criteria", "study_quality_assessment", "synthesis_method",
            "heterogeneity", "publication_bias", "scope_conditions", "author_limitations",
        ],
        StudyType.UNKNOWN: [
            "study_design", "sampling_or_case_selection", "measurement_or_operationalization", "analysis_strategy",
            "robustness_or_triangulation", "mechanism", "scope_conditions", "author_limitations",
        ],
    }
    dimensions = mapping[study_type]
    # Exactly eight paper-specific dimensions keeps the output useful but bounded.
    return dimensions[:8] if len(dimensions) >= 8 else (common + dimensions)[:8]


_REVIEW_KEYWORDS: dict[str, tuple[str, ...]] = {
    "research_question": ("研究问题", "research question", "目的", "aim", "hypothesis"),
    "study_design": ("研究设计", "方法", "methods", "design"),
    "design_integration": ("混合方法", "mixed methods", "triangulation", "整合"),
    "randomization_and_design": ("随机", "random", "实验组", "对照组", "treatment", "control"),
    "attrition_and_compliance": ("流失", "attrition", "compliance", "依从", "失访"),
    "identification_strategy": ("识别", "identification", "instrument", "固定效应", "difference-in-differences", "断点", "匹配"),
    "quantitative_identification": ("识别", "回归", "regression", "control variable", "instrument", "fixed effect"),
    "confounding_and_selection": ("混杂", "confound", "选择偏差", "selection", "内生", "endogeneity"),
    "sampling_or_case_selection": ("样本", "抽样", "sample", "case selection", "案例选择"),
    "case_selection": ("案例选择", "case selection", "选案", "case"),
    "qualitative_sampling": ("访谈对象", "sampling", "purposive", "snowball", "理论抽样"),
    "sampling_and_access": ("抽样", "sampling", "进入田野", "access", "recruit"),
    "measurement_validity": ("测量", "measurement", "量表", "指标", "validity", "reliability", "操作化"),
    "measurement_or_operationalization": ("测量", "measurement", "指标", "operational", "变量定义"),
    "model_specification": ("模型", "specification", "回归", "regression", "控制变量", "fixed effect"),
    "analysis_strategy": ("分析", "analysis", "模型", "coding", "estimation"),
    "statistical_inference": ("显著", "confidence interval", "标准误", "p-value", "standard error", "power"),
    "multiple_testing_and_robustness": ("多重", "multiple testing", "robust", "稳健", "sensitivity"),
    "robustness": ("稳健", "robust", "placebo", "sensitivity", "alternative specification"),
    "robustness_or_triangulation": ("稳健", "robust", "triangulation", "三角验证", "敏感性"),
    "coding_and_interpretation": ("编码", "coding", "主题", "thematic", "解释"),
    "triangulation": ("三角验证", "triangulation", "多源", "multiple sources"),
    "negative_cases": ("反例", "negative case", "deviant case", "异常案例"),
    "reflexivity": ("反身性", "reflexiv", "研究者位置", "positionality"),
    "mechanism": ("机制", "mechanism", "中介", "mediator", "路径"),
    "external_validity": ("外推", "generaliz", "外部效度", "代表性", "适用范围"),
    "scope_conditions": ("边界", "scope condition", "适用", "generaliz", "情境"),
    "author_limitations": ("局限", "限制", "limitation", "caveat"),
    "assumptions": ("假设", "assumption", "前提"),
    "propositions": ("命题", "proposition", "hypothesis"),
    "alternative_explanations": ("替代解释", "alternative explanation", "other explanation"),
    "empirical_implications": ("经验含义", "empirical implication", "testable", "可检验"),
    "internal_consistency": ("一致性", "consistency", "逻辑", "logic"),
    "search_strategy": ("检索", "search strategy", "database", "数据库"),
    "inclusion_criteria": ("纳入标准", "排除标准", "inclusion", "exclusion"),
    "study_quality_assessment": ("质量评价", "risk of bias", "quality assessment"),
    "synthesis_method": ("综合", "synthesis", "meta-analysis", "编码"),
    "heterogeneity": ("异质性", "heterogeneity", "subgroup"),
    "publication_bias": ("发表偏差", "publication bias", "funnel"),
    "evidence_alignment": ("结果", "results", "发现", "finding", "conclusion"),
}


def _independent_pro_context(
    metadata: PaperMetadata,
    grounded_facts: list[ExtractedClaim],
    blocks: list[SourceBlock],
    span_registry: dict[str, EvidenceSpan],
    research_context: str | None,
    routed_type: StudyType,
    review_dimensions: list[str],
    *,
    max_excerpts: int,
    char_budget: int,
) -> tuple[str, dict[str, EvidenceSpan]]:
    # Start with Lite anchors so Pro can audit existing claims, then add independently retrieved source text.
    priority: dict[str, float] = {}
    for fact in grounded_facts:
        for ref in fact.evidence:
            if ref.evidence_id and ref.evidence_id in span_registry:
                priority[ref.evidence_id] = max(priority.get(ref.evidence_id, 0.0), 100.0)

    logical = _logical_evidence_spans(blocks)
    for span in logical:
        text = unicodedata.normalize("NFKC", span.text).casefold()
        score = 0.0
        if span.role == "heading":
            score += 4.0
        if span.role == "table":
            score += 2.0
        if span.page_number <= 2:
            score += 0.5
        for dimension in review_dimensions:
            for keyword in _REVIEW_KEYWORDS.get(dimension, ()):
                if keyword.casefold() in text:
                    score += 3.0
        # Generic academic anchors matter when vocabulary is unusual.
        if any(k in text for k in ("方法", "method", "结果", "result", "讨论", "discussion", "局限", "limitation")):
            score += 1.5
        priority[span.id] = max(priority.get(span.id, 0.0), score)
        span_registry.setdefault(span.id, span)

    candidates = [span_registry[sid] for sid in priority if sid in span_registry]
    candidates.sort(key=lambda span: (-priority.get(span.id, 0.0), span.page_number, span.start_char, span.id))

    selected: list[EvidenceSpan] = []
    used_chars = 0
    page_counts: dict[int, int] = {}
    for span in candidates:
        if len(selected) >= max(1, max_excerpts):
            break
        cost = len(span.text)
        if selected and used_chars + cost > max(1000, char_budget):
            continue
        # Keep evidence broad; prevent one dense page from swallowing the review budget.
        if page_counts.get(span.page_number, 0) >= 10 and priority.get(span.id, 0.0) < 100:
            continue
        selected.append(span)
        used_chars += cost
        page_counts[span.page_number] = page_counts.get(span.page_number, 0) + 1

    selected.sort(key=lambda span: (span.page_number, span.source_block_id, span.start_char, span.end_char))
    qmap: dict[str, EvidenceSpan] = {}
    stable_to_token: dict[str, str] = {}
    excerpts: list[dict[str, object]] = []
    for span in selected:
        token = f"Q{len(qmap) + 1:04d}"
        qmap[token] = span
        stable_to_token[span.id] = token
        excerpts.append(
            {
                "evidence_id": token,
                "pdf_page": span.page_number,
                "role": span.role,
                "kind": span.kind,
                "quote": span.text,
            }
        )

    lite_facts: list[dict[str, object]] = []
    for fact in grounded_facts[:24]:
        evidence_ids = [stable_to_token[ref.evidence_id] for ref in fact.evidence if ref.evidence_id in stable_to_token]
        lite_facts.append(
            {
                "claim_id": fact.claim_id,
                "field_name": fact.field_name,
                "claim_type": fact.claim_type.value,
                "statement": fact.statement,
                "scope": fact.scope.model_dump(mode="json"),
                "source_evidence_ids": evidence_ids,
            }
        )

    payload = {
        "research_context": research_context,
        "metadata": metadata.model_dump(mode="json"),
        "heuristic_study_type": routed_type.value,
        "review_plan": review_dimensions,
        "lite_claims_for_audit": lite_facts,
        "selected_source_excerpts": excerpts,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), qmap


def _resolve_pro_reviews(
    items: list[ProReviewItem],
    evidence_map: dict[str, EvidenceSpan],
    source_map: dict[str, SourceBlock],
    warnings: list[str],
    *,
    stage: str,
    force_claim_type: ClaimType | None = None,
) -> list[ExtractedClaim]:
    resolved: list[ExtractedClaim] = []
    for item in items:
        refs: list[EvidenceReference] = []
        seen: set[str] = set()
        for model_ref in item.evidence:
            token = _evidence_token(model_ref.evidence_id, "Q")
            span = evidence_map.get(token) if token else None
            if span is None:
                warnings.append(f"{stage}: ignored unknown evidence token '{model_ref.evidence_id}'.")
                continue
            if not _span_matches_source(span, source_map):
                warnings.append(f"{stage}: evidence ledger mismatch for '{model_ref.evidence_id}'.")
                continue
            if span.id in seen:
                continue
            seen.add(span.id)
            refs.append(
                EvidenceReference(
                    evidence_id=span.id,
                    source_block_id=span.source_block_id,
                    page_number=span.page_number,
                    quote=span.text,
                    role=model_ref.role,
                )
            )
        statement = _compact_statement(item.assessment, max_chars=320)
        resolved.append(
            ExtractedClaim(
                claim_id=_claim_id(item.dimension, statement),
                field_name=item.dimension,
                statement=statement,
                provenance=Provenance.AI_INFERRED,
                verification=VerificationStatus.NEEDS_REVIEW,
                evidence=refs,
                claim_type=force_claim_type or item.claim_type,
                scope=item.scope,
                semantic_support=item.semantic_support,
                rationale=item.rationale,
            )
        )
    return resolved


def _resolve_claim_audits(
    audits: list,
    grounded_facts: list[ExtractedClaim],
    evidence_map: dict[str, EvidenceSpan],
    source_map: dict[str, SourceBlock],
    warnings: list[str],
) -> list[ClaimAudit]:
    known = {claim.claim_id: claim for claim in grounded_facts if claim.claim_id}
    resolved: list[ClaimAudit] = []
    for audit in audits:
        if audit.claim_id not in known:
            warnings.append(f"Pro claim audit ignored unknown claim_id '{audit.claim_id}'.")
            continue
        refs: list[EvidenceReference] = []
        seen: set[str] = set()
        for model_ref in audit.evidence:
            token = _evidence_token(model_ref.evidence_id, "Q")
            span = evidence_map.get(token) if token else None
            if span is None or not _span_matches_source(span, source_map) or span.id in seen:
                continue
            seen.add(span.id)
            refs.append(
                EvidenceReference(
                    evidence_id=span.id,
                    source_block_id=span.source_block_id,
                    page_number=span.page_number,
                    quote=span.text,
                    role=model_ref.role,
                )
            )
        resolved.append(
            ClaimAudit(
                claim_id=audit.claim_id,
                semantic_support=audit.semantic_support,
                rationale=audit.rationale,
                evidence=refs,
            )
        )
    return resolved
