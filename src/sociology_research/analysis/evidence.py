"""确定性原文证据切片、EvidenceSpan 账本构建、Lite 分块与证据回锚模块。"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata

from ..models import (
    ClaimAudit,
    ClaimScope,
    ClaimType,
    EvidenceFragment,
    EvidenceReference,
    EvidenceRole,
    EvidenceSpan,
    ExtractedClaim,
    LLMClaim,
    Provenance,
    SourceBlock,
    VerificationStatus,
)
from .layout import (
    _compose_logical_text,
    _detect_repetitive_furniture,
    _is_page_number_noise,
    _join_separator,
    _logical_units,
    _page_bbox_stats,
    _union_bbox,
)


def _split_evidence_ranges(text: str, *, max_chars: int = 170) -> list[tuple[int, int]]:
    """Return exact source offsets for short, contiguous single-block spans."""
    if max_chars < 40:
        raise ValueError("evidence span length must be at least 40 characters")

    ranges: list[tuple[int, int]] = []
    start = 0
    length = len(text)
    preferred = frozenset(".?!;:\n。！？；：")

    while start < length:
        while start < length and text[start].isspace():
            start += 1
        if start >= length:
            break

        hard_end = min(length, start + max_chars)
        end = hard_end
        if hard_end < length:
            search_floor = start + max(24, max_chars // 2)
            best = -1
            for idx in range(hard_end - 1, search_floor - 1, -1):
                if text[idx] in preferred:
                    best = idx + 1
                    break
            if best < 0:
                for idx in range(hard_end - 1, search_floor - 1, -1):
                    if text[idx].isspace():
                        best = idx
                        break
            if best > start:
                end = best

        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            ranges.append((start, end))
        start = max(start + 1, end)

    return ranges


def _sentence_ranges(text: str, *, max_chars: int = 170) -> list[tuple[int, int]]:
    if not text.strip():
        return []
    atomic: list[tuple[int, int]] = []
    start = 0
    for idx, char in enumerate(text):
        boundary = char in "。！？!?；;"
        if char in ".．":
            prev_digit = idx > 0 and text[idx - 1].isdigit()
            lookahead = idx + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            next_digit = lookahead < len(text) and text[lookahead].isdigit()
            boundary = not (prev_digit and next_digit)
        if boundary:
            end = idx + 1
            if end > start:
                atomic.append((start, end))
            start = end
    if start < len(text):
        atomic.append((start, len(text)))

    expanded: list[tuple[int, int]] = []
    for a, b in atomic:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b <= a:
            continue
        if b - a <= max_chars:
            expanded.append((a, b))
        else:
            for local_start, local_end in _split_evidence_ranges(text[a:b], max_chars=max_chars):
                expanded.append((a + local_start, a + local_end))

    packed: list[tuple[int, int]] = []
    for a, b in expanded:
        if not packed:
            packed.append((a, b))
            continue
        pa, pb = packed[-1]
        combined_len = b - pa
        current_len = pb - pa
        next_len = b - a
        if (current_len < 70 or next_len < 30) and combined_len <= max_chars:
            packed[-1] = (pa, b)
        else:
            packed.append((a, b))
    return packed


def _build_evidence_span(
    block: SourceBlock, start: int, end: int, *, role: str | None = None
) -> EvidenceSpan:
    text = block.text[start:end]
    digest = hashlib.sha256(
        f"single\0{block.id}\0{block.page_number}\0{start}\0{end}\0{text}".encode("utf-8")
    ).hexdigest()[:20]
    kind = "table" if block.table_rows is not None else "text"
    resolved_role = role or ("table" if kind == "table" else "body")
    fragment = EvidenceFragment(
        source_block_id=block.id,
        page_number=block.page_number,
        start_char=start,
        end_char=end,
        bbox=block.bbox,
    )
    return EvidenceSpan(
        id=f"ev_{digest}",
        source_block_id=block.id,
        page_number=block.page_number,
        start_char=start,
        end_char=end,
        text=text,
        kind=kind,
        role=resolved_role,
        bbox=block.bbox,
        fragments=[fragment],
    )


def _build_logical_span(
    text: str,
    segments: list[tuple[int, int, SourceBlock, int, int]],
    start: int,
    end: int,
    *,
    role: str,
) -> EvidenceSpan | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None

    fragments: list[EvidenceFragment] = []
    pieces: list[str] = []
    for combined_start, combined_end, block, source_start, _ in segments:
        overlap_start = max(start, combined_start)
        overlap_end = min(end, combined_end)
        if overlap_end <= overlap_start:
            continue
        local_start = source_start + (overlap_start - combined_start)
        local_end = source_start + (overlap_end - combined_start)
        piece = block.text[local_start:local_end]
        if piece:
            pieces.append(piece)
            fragments.append(
                EvidenceFragment(
                    source_block_id=block.id,
                    page_number=block.page_number,
                    start_char=local_start,
                    end_char=local_end,
                    bbox=block.bbox,
                )
            )
    if not fragments:
        return None

    span_text = text[start:end]
    first = fragments[0]
    digest_material = "|".join(
        f"{fragment.source_block_id}:{fragment.start_char}:{fragment.end_char}"
        for fragment in fragments
    )
    digest = hashlib.sha256(
        f"logical\0{role}\0{digest_material}\0{span_text}".encode("utf-8")
    ).hexdigest()[:20]
    return EvidenceSpan(
        id=f"ev_{digest}",
        source_block_id=first.source_block_id,
        page_number=first.page_number,
        start_char=first.start_char,
        end_char=first.end_char,
        text=span_text,
        kind="text",
        role=role,
        bbox=_union_bbox([fragment.bbox for fragment in fragments]),
        fragments=fragments,
    )


def _build_raw_span_registry(blocks: list[SourceBlock]) -> dict[str, EvidenceSpan]:
    furniture_ids = _detect_repetitive_furniture(blocks)
    page_stats = _page_bbox_stats(blocks)
    registry: dict[str, EvidenceSpan] = {}
    for block in blocks:
        if block.table_rows is not None:
            role = "table"
        elif block.role == "furniture" or block.id in furniture_ids or _is_page_number_noise(block, page_stats):
            role = "furniture"
        elif block.role in {"heading", "front_matter", "footnote"}:
            role = block.role
        elif block.page_number == 1 and block.bbox is not None and block.bbox[1] < 480:
            role = "front_matter"
        else:
            role = "body"
        for start, end in _split_evidence_ranges(block.text):
            span = _build_evidence_span(block, start, end, role=role)
            registry[span.id] = span
    return registry


def _logical_evidence_spans(blocks: list[SourceBlock]) -> list[EvidenceSpan]:
    spans: list[EvidenceSpan] = []
    for role, unit_blocks in _logical_units(blocks):
        if role == "table":
            block = unit_blocks[0]
            for start, end in _split_evidence_ranges(block.text, max_chars=170):
                spans.append(_build_evidence_span(block, start, end, role="table"))
            continue
        logical_text, segments = _compose_logical_text(unit_blocks)
        if not logical_text.strip():
            continue
        ranges = (
            [(0, len(logical_text))]
            if role == "heading" and len(logical_text) <= 170
            else _sentence_ranges(logical_text, max_chars=170)
        )
        for start, end in ranges:
            span = _build_logical_span(logical_text, segments, start, end, role=role)
            if span is not None and span.text.strip():
                spans.append(span)
    return spans


def _prepare_lite_chunks(
    blocks: list[SourceBlock], *, max_chars: int
) -> list[tuple[str, dict[str, EvidenceSpan]]]:
    """Build LLM prompt chunks from cleaned sentence/paragraph evidence, not raw PDF lines."""
    chunks: list[tuple[str, dict[str, EvidenceSpan]]] = []
    current: list[str] = []
    current_chars = 0
    current_evidence: dict[str, EvidenceSpan] = {}

    def flush() -> None:
        nonlocal current, current_chars, current_evidence
        if current:
            chunks.append(("\n".join(current), current_evidence))
        current, current_chars, current_evidence = [], 0, {}

    for span_index, span in enumerate(_logical_evidence_spans(blocks), start=1):
        alias = f"S{span_index:04d}"

        def make_entry(token: str) -> str:
            return f"[{alias}|p{span.page_number}|{span.kind}|{span.role}]\n<{token}> {span.text}"

        token = f"E{len(current_evidence) + 1:04d}"
        entry = make_entry(token)
        if current and current_chars + len(entry) + 1 > max_chars:
            flush()
            token = "E0001"
            entry = make_entry(token)

        current.append(entry)
        current_chars += len(entry) + 1
        current_evidence[token] = span

    flush()
    return chunks


def _lite_input_limit() -> int:
    try:
        return max(12000, min(int(os.getenv("SRA_LITE_INPUT_CHAR_LIMIT", "48000")), 80000))
    except ValueError:
        return 48000


def _evidence_token(value: str, prefix: str) -> str | None:
    match = re.fullmatch(
        rf"\s*[<\[(]?\s*({re.escape(prefix)}\d{{4}})\s*[>\])]?[\s.,;:]*",
        value,
        flags=re.IGNORECASE,
    )
    return match.group(1).upper() if match else None


def _span_matches_source(span: EvidenceSpan, source_map: dict[str, SourceBlock]) -> bool:
    if span.fragments:
        pieces: list[str] = []
        previous_piece = ""
        for fragment in span.fragments:
            block = source_map.get(fragment.source_block_id)
            if block is None or block.page_number != fragment.page_number:
                return False
            if fragment.end_char > len(block.text):
                return False
            piece = block.text[fragment.start_char:fragment.end_char]
            if not piece:
                return False
            separator = _join_separator(previous_piece, piece)
            if separator:
                pieces.append(separator)
            pieces.append(piece)
            previous_piece = piece
        reconstructed = "".join(pieces)
        return " ".join(reconstructed.split()) == " ".join(span.text.split())

    block = source_map.get(span.source_block_id)
    if block is None or block.page_number != span.page_number:
        return False
    if span.end_char > len(block.text):
        return False
    return block.text[span.start_char:span.end_char] == span.text


def _claim_id(field_name: str, statement: str) -> str:
    digest = hashlib.sha1(f"{field_name}\0{statement}".encode("utf-8")).hexdigest()[:16]
    return f"claim-{digest}"


def _infer_claim_type(field_name: str, statement: str) -> ClaimType:
    field = field_name.casefold()
    text = unicodedata.normalize("NFKC", statement).casefold()
    if "limitation" in field or "局限" in text or "限制" in text:
        return ClaimType.LIMITATION
    if field in {"measurement_indicator", "measurement", "measure"} or any(
        token in text for token in ("测量", "量表", "指标", "operational", "measure")
    ):
        return ClaimType.MEASUREMENT
    if field in {"research_method", "method"}:
        return ClaimType.METHOD
    if field in {"author_explanation", "mechanism"} or any(
        token in text for token in ("机制", "mechanism", "中介", "路径")
    ):
        return ClaimType.MECHANISM
    if any(token in text for token in ("导致", "造成", "使得", "因果", "caus", "effect of", "impact of")):
        return ClaimType.CAUSAL
    if any(token in text for token in ("相关", "关联", "associated", "correlat", "relationship")):
        return ClaimType.ASSOCIATION
    if field in {"theory_concept", "theory"}:
        return ClaimType.THEORETICAL
    if field in {"major_finding", "research_sample", "key_number", "research_question"}:
        return ClaimType.DESCRIPTIVE
    return ClaimType.OTHER


def _enrich_lite_claims(claims: list[ExtractedClaim]) -> list[ExtractedClaim]:
    enriched: list[ExtractedClaim] = []
    for claim in claims:
        updates: dict[str, object] = {}
        if not claim.claim_id:
            updates["claim_id"] = _claim_id(claim.field_name, claim.statement)
        if claim.claim_type == ClaimType.OTHER:
            updates["claim_type"] = _infer_claim_type(claim.field_name, claim.statement)
        enriched.append(claim.model_copy(update=updates) if updates else claim)
    return enriched


def _resolve_model_claims(
    claims: list[LLMClaim],
    evidence_map: dict[str, EvidenceSpan],
    source_map: dict[str, SourceBlock],
    warnings: list[str],
    *,
    stage: str,
    token_prefix: str,
) -> list[ExtractedClaim]:
    resolved: list[ExtractedClaim] = []
    for claim in claims:
        valid_evidence: list[EvidenceReference] = []
        invalid_reasons: set[str] = set()
        invalid_count = 0
        seen_ids: set[str] = set()

        for model_reference in claim.evidence:
            token = _evidence_token(model_reference.evidence_id, token_prefix)
            span = evidence_map.get(token) if token else None
            if span is None:
                invalid_count += 1
                invalid_reasons.add("unknown evidence token")
                continue
            if not _span_matches_source(span, source_map):
                invalid_count += 1
                invalid_reasons.add("evidence ledger mismatch")
                continue
            if span.id in seen_ids:
                continue
            seen_ids.add(span.id)
            valid_evidence.append(
                EvidenceReference(
                    evidence_id=span.id,
                    source_block_id=span.source_block_id,
                    page_number=span.page_number,
                    quote=span.text,
                    role=getattr(model_reference, "role", EvidenceRole.ANCHOR),
                )
            )

        if invalid_reasons:
            warnings.append(
                f"{stage}: evidence citation needs review for field '{claim.field_name}' "
                f"({', '.join(sorted(invalid_reasons))})."
            )

        if not valid_evidence:
            status = (
                VerificationStatus.NO_SOURCE
                if not claim.evidence and claim.verification == VerificationStatus.NO_SOURCE
                else VerificationStatus.NEEDS_REVIEW
            )
        elif invalid_count or claim.provenance == Provenance.AI_INFERRED:
            status = VerificationStatus.NEEDS_REVIEW
        else:
            status = VerificationStatus.SUPPORTED

        resolved.append(
            ExtractedClaim(
                claim_id=_claim_id(claim.field_name, claim.statement),
                field_name=claim.field_name,
                statement=claim.statement,
                provenance=claim.provenance,
                verification=status,
                evidence=valid_evidence,
                claim_type=claim.claim_type if claim.claim_type != ClaimType.OTHER else _infer_claim_type(claim.field_name, claim.statement),
                scope=claim.scope,
            )
        )
    return resolved


def _collect_used_evidence_spans(
    metadata_claims: list[ExtractedClaim],
    basic_facts: list[ExtractedClaim],
    analysis: list[ExtractedClaim],
    limitations: list[ExtractedClaim],
    reading: ExtractedClaim | None,
    span_registry: dict[str, EvidenceSpan],
    *,
    claim_audits: list[ClaimAudit] | None = None,
) -> list[EvidenceSpan]:
    wanted: set[str] = set()
    claims = [*metadata_claims, *basic_facts, *analysis, *limitations]
    if reading is not None:
        claims.append(reading)
    for claim in claims:
        for reference in claim.evidence:
            if reference.evidence_id:
                wanted.add(reference.evidence_id)
    for audit in claim_audits or []:
        for reference in audit.evidence:
            if reference.evidence_id:
                wanted.add(reference.evidence_id)
    return sorted(
        (span_registry[evidence_id] for evidence_id in wanted if evidence_id in span_registry),
        key=lambda span: (span.page_number, span.source_block_id, span.start_char, span.end_char),
    )
