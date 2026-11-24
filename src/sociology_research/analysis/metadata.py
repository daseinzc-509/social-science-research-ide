"""论文首页面书目启发式提取、重复页眉/页码恢复与多源元数据仲裁模块。"""

from __future__ import annotations

import json
import re
import unicodedata

from pydantic import ValidationError

from ..models import (
    ClaimType,
    EvidenceReference,
    EvidenceRole,
    EvidenceSpan,
    ExtractedClaim,
    LLMClaim,
    LLMMetadataFact,
    LiteExtraction,
    PaperMetadata,
    Provenance,
    SourceBlock,
    VerificationStatus,
)
from .evidence import _resolve_model_claims
from .layout import _compact_statement, _normalize_marker


def _metadata_statement(field_name: str, value: object) -> str:
    if isinstance(value, list):
        text = "；".join(str(item) for item in value)
    else:
        text = str(value)
    return _compact_statement(text, max_chars=260)


def _normalize_doi(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).strip()
    text = re.sub(r"^\s*(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", "", text, flags=re.I)
    return text.rstrip(" \t\r\n.,;)")


def _metadata_compare_key(field_name: str, value: object) -> str:
    """Canonicalize harmless formatting differences before reporting conflicts."""

    if field_name in {"authors", "keywords"} and isinstance(value, list):
        items = [_normalize_marker(str(item)) for item in value]
        items = [item for item in items if item]
        if field_name == "keywords":
            items = sorted(items)
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    if field_name == "doi":
        return _normalize_doi(str(value)).casefold()
    if field_name == "pages":
        numbers = re.findall(r"\d+", unicodedata.normalize("NFKC", str(value)))
        if numbers:
            endpoints = [numbers[0]] if len(numbers) == 1 else [numbers[0], numbers[-1]]
            return "-".join(str(int(number)) for number in endpoints)
    if field_name in {"volume", "issue"}:
        text = unicodedata.normalize("NFKC", str(value)).strip()
        if re.fullmatch(r"\d+", text):
            return str(int(text))
    if field_name == "year":
        return str(value)
    if field_name == "abstract":
        return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())
    return _normalize_marker(str(value))


def _coerce_metadata_value(field_name: str, value: object) -> object | None:
    if field_name in {"authors", "keywords"}:
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, str):
            items = [part.strip() for part in re.split(r"[;；,，]", value) if part.strip()]
        else:
            return None
        return items or None

    if field_name == "year":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            year = value
        else:
            match = re.fullmatch(r"\s*((?:14|15|16|17|18|19|20|21)\d{2})\s*", str(value))
            if not match:
                return None
            year = int(match.group(1))
        return year if 1400 <= year <= 2200 else None

    if isinstance(value, list):
        return None
    text = str(value).strip()
    if not text:
        return None
    if field_name == "doi":
        text = _normalize_doi(text)
        return text or None
    if field_name == "abstract":
        return text[:2000]
    return text


def _metadata_value_supported_by_evidence(
    field_name: str, value: object, claim: ExtractedClaim, source_map: dict[str, SourceBlock]
) -> bool:
    if claim.verification != VerificationStatus.SUPPORTED or not claim.evidence:
        return False

    evidence_text = " ".join(reference.quote for reference in claim.evidence)
    evidence_normalized = _normalize_marker(evidence_text)
    if not evidence_normalized:
        return False

    # Bibliographic identity fields are often split across adjacent front-matter blocks.
    # Keep provenance strict (same cited physical page) without requiring the whole value
    # to fit inside the model-selected sentence fragment. This fixes title/author false
    # negatives while still rejecting translations or aliases absent from the PDF text.
    if field_name in {"title", "authors", "journal", "doi"}:
        if field_name == "journal":
            # A citation to *some other paragraph* on the same page is not
            # valid journal evidence. The first-page re-anchoring pass will
            # locate an exact SourceBlock when one actually exists.
            needle = _normalize_marker(str(value))
            return bool(needle and needle in evidence_normalized)
        pages = {reference.page_number for reference in claim.evidence}
        page_text = " ".join(
            block.text
            for block in source_map.values()
            if block.page_number in pages and block.role != "footnote"
        )
        candidate_text = f"{evidence_text} {page_text}".strip()
        candidate_normalized = _normalize_marker(candidate_text)
        if field_name == "authors":
            if not isinstance(value, list) or not value:
                return False
            return all(
                (needle := _normalize_marker(str(item))) and needle in candidate_normalized
                for item in value
            )
        if field_name == "doi":
            needle = _normalize_marker(_normalize_doi(str(value)))
        else:
            needle = _normalize_marker(str(value))
        return bool(needle and needle in candidate_normalized)

    if field_name in {"authors", "keywords"}:
        if not isinstance(value, list) or not value:
            return False
        return all(
            (needle := _normalize_marker(str(item))) and needle in evidence_normalized
            for item in value
        )

    if field_name == "pages":
        numbers = re.findall(r"\d+", str(value))
        if not numbers:
            return False
        endpoints = [numbers[0]] if len(numbers) == 1 else [numbers[0], numbers[-1]]
        return all(number in evidence_normalized for number in endpoints)

    if field_name in {"volume", "issue"}:
        text = unicodedata.normalize("NFKC", str(value)).strip()
        # An isolated number ('1') found in a paragraph discussing research
        # data does not establish "Volume 1" or "Issue 1".
        if not re.fullmatch(r"\d+", text):
            direct = _normalize_marker(text)
            return bool(direct and direct in evidence_normalized)
        if re.fullmatch(r"\d+", text):
            target = str(int(text))
            if field_name == "issue":
                patterns = (
                    rf"(?:no\.?|issue)\s*0*{re.escape(target)}\b",
                    rf"第\s*0*{re.escape(target)}\s*期",
                    rf"\b(?:19|20)\d{{2}}\s*[.·．/-]\s*0*{re.escape(target)}\b",
                )
            else:
                patterns = (
                    rf"(?:vol\.?|volume)\s*0*{re.escape(target)}\b",
                    rf"第\s*0*{re.escape(target)}\s*卷",
                )
            return any(re.search(pattern, evidence_text, flags=re.I) for pattern in patterns)
        return False

    if field_name == "doi":
        needle = _normalize_marker(_normalize_doi(str(value)))
    else:
        needle = _normalize_marker(str(value))
    return bool(needle and needle in evidence_normalized)


def _resolve_metadata_facts(
    facts: list[LLMMetadataFact],
    evidence_map: dict[str, EvidenceSpan],
    source_map: dict[str, SourceBlock],
    warnings: list[str],
    *,
    raw_span_registry: dict[str, EvidenceSpan] | None = None,
) -> list[tuple[str, object, ExtractedClaim]]:
    candidates: list[tuple[str, object, ExtractedClaim]] = []
    for fact in facts:
        value = _coerce_metadata_value(fact.field_name, fact.value)
        if value is None:
            warnings.append(f"Lite: metadata field '{fact.field_name}' has an invalid value and was omitted.")
            continue
        llm_claim = LLMClaim(
            field_name=f"metadata.{fact.field_name}",
            statement=_metadata_statement(fact.field_name, value),
            provenance=Provenance.AUTHOR_STATED,
            verification=VerificationStatus.SUPPORTED,
            evidence=fact.evidence,
        )
        claim = _resolve_model_claims(
            [llm_claim],
            evidence_map,
            source_map,
            warnings,
            stage="Lite metadata",
            token_prefix="E",
        )[0]
        well_anchored = (
            claim.verification == VerificationStatus.SUPPORTED
            and _metadata_value_supported_by_evidence(
                fact.field_name, value, claim, source_map
            )
        )
        # Treat bibliographic identifiers differently from research claims:
        # a number cited in the introduction may really say "2021" while
        # still NOT proving the journal's publication year. When the original
        # source ledger is available, require a masthead-context match.
        sensitive = {"journal", "year", "volume", "issue", "pages"}
        recovered = (
            _reanchor_lite_bibliography(
                fact.field_name, value, source_map, raw_span_registry
            )
            if raw_span_registry is not None and fact.field_name in sensitive
            else None
        )
        if recovered is not None:
            if (
                not well_anchored
                or {
                    e.evidence_id for e in claim.evidence
                } != {e.evidence_id for e in recovered.evidence}
            ):
                warnings.append(
                    f"Lite: metadata field '{fact.field_name}' was re-anchored "
                    "to an exact first-page SourceBlock after evidence review."
                )
            claim = recovered
        elif not well_anchored or (
            raw_span_registry is not None and fact.field_name in sensitive
        ):
            if claim.verification == VerificationStatus.SUPPORTED:
                warnings.append(
                    f"Lite: metadata field '{fact.field_name}' value is not "
                    "verified by the journal masthead; left unverified."
                )
                claim = claim.model_copy(
                    update={"verification": VerificationStatus.NEEDS_REVIEW}
                )
        candidates.append((fact.field_name, value, claim))
    return candidates


def _merge_lite_results(
    results: list[tuple[list[tuple[str, object, ExtractedClaim]], list[ExtractedClaim]]],
) -> tuple[list[tuple[str, object, ExtractedClaim]], list[ExtractedClaim]]:
    metadata_candidates: list[tuple[str, object, ExtractedClaim]] = []
    claims: list[ExtractedClaim] = []
    seen_metadata: set[tuple[str, str]] = set()
    seen_claims: set[tuple[str, str]] = set()
    for part_metadata, part_claims in results:
        for field_name, value, claim in part_metadata:
            value_key = _metadata_compare_key(field_name, value)
            key = (field_name.casefold(), value_key)
            if key not in seen_metadata:
                seen_metadata.add(key)
                metadata_candidates.append((field_name, value, claim))
        for claim in part_claims:
            key = (claim.field_name.casefold(), claim.statement.casefold())
            if key not in seen_claims:
                seen_claims.add(key)
                claims.append(claim)
    return metadata_candidates, claims


def _evidence_references_for_block(
    block_id: str, span_registry: dict[str, EvidenceSpan], *, max_refs: int = 2
) -> list[EvidenceReference]:
    spans = sorted(
        (span for span in span_registry.values() if span.source_block_id == block_id),
        key=lambda span: (span.start_char, span.end_char),
    )[:max_refs]
    return [
        EvidenceReference(
            evidence_id=span.id,
            source_block_id=span.source_block_id,
            page_number=span.page_number,
            quote=span.text,
        )
        for span in spans
    ]


def _layout_claim(
    field_name: str, value: object, evidence: list[EvidenceReference]
) -> tuple[str, object, ExtractedClaim] | None:
    if not evidence:
        return None
    return (
        field_name,
        value,
        ExtractedClaim(
            field_name=f"metadata.{field_name}",
            statement=_metadata_statement(field_name, value),
            provenance=Provenance.AUTHOR_STATED,
            verification=VerificationStatus.SUPPORTED,
            evidence=evidence[:16],
        ),
    )


def _smart_join_wrapped(parts: list[str]) -> str:
    result = ""
    for raw in parts:
        piece = " ".join(raw.split()).strip()
        if not piece:
            continue
        if not result:
            result = piece
            continue
        left = result[-1]
        right = piece[0]
        left_cjk = "\u4e00" <= left <= "\u9fff"
        right_cjk = "\u4e00" <= right <= "\u9fff"
        if left_cjk and right_cjk:
            result += piece
        elif result.endswith("-") and right.isalpha():
            result += piece
        else:
            result += " " + piece
    return result.strip()


def _split_keyword_segments(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        for item in re.split(r"[;；]+|\s{2,}", line):
            item = item.strip(" \t,，、。.;；:")
            if item:
                items.append(item)
    return items


_BIBLIO_YEAR = r"(?:19|20)\d{2}"


_BIBLIO_VOLUME_RE = re.compile(
    r"(?:第\s*(?P<zh>\d{1,3})\s*卷|"
    r"\bvol(?:ume)?\.?\s*[:：]?\s*(?P<en>\d{1,3})(?!\d)|"
    r"卷\s*号\s*[:：]\s*(?P<label>\d{1,3})(?!\d))",
    re.I,
)


_BIBLIO_ISSUE_RE = re.compile(
    r"(?:第\s*(?P<zh>\d{1,3})\s*期|"
    r"\bno\.?\s*[:：]?\s*(?P<en>\d{1,3})(?!\d)|"
    r"\bissue\s*[:：]?\s*(?P<enlong>\d{1,3})(?!\d)|"
    r"期\s*号\s*[:：]\s*(?P<label>\d{1,3})(?!\d))",
    re.I,
)


_BIBLIO_PAGES_RE = re.compile(
    r"(?:页\s*码(?:范围)?|起止页(?:码)?|"
    r"\bpages?\b|\bpp?\.)(?:\s*[:：.]\s*|\s+)"
    r"(?P<start>\d{1,5})\s*[-–—~～]\s*(?P<end>\d{1,5})(?!\d)",
    re.I,
)


_BIBLIO_CITATION_RE = re.compile(
    rf"(?P<year>{_BIBLIO_YEAR})\s*[,，]\s*"
    r"(?P<volume>\d{1,3})\s*"
    r"\(\s*(?P<issue>\d{1,3})\s*\)\s*"
    r"[:：]\s*(?P<start>\d{1,5})\s*[-–—]\s*(?P<end>\d{1,5})(?!\d)"
)


_BIBLIO_JOURNAL_LABEL = re.compile(
    r"(?:^|[\n\r])\s*(?:来源期刊|期刊名称|期刊名|刊物名称|刊名|"
    r"Journal\s*(?:name|title)?|Published\s+in)\s*[:：]\s*"
    r"(?P<value>[^\n\r;；|]{2,100})",
    re.I,
)


_BIBLIO_JOURNAL_CUES = re.compile(
    r"(?:学报|学刊|杂志|期刊|研究|论坛|评论|社会科学|"
    r"人口与经济|人口研究|journal|review|quarterly|bulletin|annals|"
    r"studies|research|magazine)",
    re.I,
)


def _front_biblio_blocks(blocks: list[SourceBlock]) -> list[SourceBlock]:
    """Small source-linked first/second-page header pool, never references."""
    result: list[SourceBlock] = []
    for block in blocks:
        if block.page_number > 2 or block.table_rows is not None:
            continue
        if block.role in {"table", "footnote"}:
            continue
        text = unicodedata.normalize("NFKC", block.text).strip()
        # A paper TITLE may discuss an issue, volume or year as its research
        # subject; those numbers are not its own journal citation.
        if (block.source_label or "").casefold() == "docling:title" and not (
            _BIBLIO_JOURNAL_LABEL.search(text)
        ):
            continue
        if not (2 <= len(text) <= 360):
            continue
        if re.match(r"^(?:参考文献|References)\b", text, re.I):
            continue
        source_label = (block.source_label or "").casefold()
        labeled = bool(
            _BIBLIO_JOURNAL_LABEL.search(text)
            or re.search(r"^(?:卷号|期号|页码|出版年|发表年份)\s*[:：]", text)
        )
        if "page_footer" in source_label:
            continue
        is_header = (
            "page_header" in source_label
            or block.role == "furniture"
            and "footer" not in source_label
            or block.role == "front_matter"
            and block.bbox is not None and block.bbox[1] <= 300
            or labeled
            or block.bbox is not None
            and block.bbox[1] <= (180.0 if block.page_number == 1 else 120.0)
        )
        if is_header:
            result.append(block)
    return sorted(
        result,
        key=lambda b: (
            b.page_number,
            b.bbox[1] if b.bbox is not None else 9999,
            b.bbox[0] if b.bbox is not None else 9999,
            b.id,
        ),
    )


def _biblio_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", unicodedata.normalize("NFKC", text))


def _clean_journal_masthead(value: str) -> str:
    value = _biblio_text(value).strip()
    value = re.split(
        rf"(?<!\d){_BIBLIO_YEAR}\s*年?|"
        r"\bvol(?:ume)?\.?\s*\d|"
        r"\b(?:no\.?|issue)\s*\d|"
        r"第\s*\d+\s*[卷期]|"
        r"(?:doi|页码|pp\.)\s*[:：]?",
        value,
        maxsplit=1, flags=re.I,
    )[0].strip(" \t,，;；:：·|-")
    value = value.strip("《》\"' ")
    if not (2 <= len(value) <= 76):
        return ""
    if any(c.isdigit() for c in value):
        return ""
    if re.search(
        r"^(?:摘要|关键词|作者|收稿|基金|doi|issn|cn|第.+章|图\d+|表\d+|"
        r"special\s+(?:issue|section)|abstract|keywords?)\b",
        value, re.I
    ):
        return ""
    return value


def _front_biblio_value_matches(field: str, value: object, text: str) -> bool:
    """For numeric identifiers require journal-context markup, not any digit."""
    normalized = _biblio_text(text)
    needle = _normalize_marker(str(value))
    if field == "journal":
        return bool(needle and needle in _normalize_marker(normalized))
    if field == "volume":
        for m in _BIBLIO_VOLUME_RE.finditer(normalized):
            for raw in m.groupdict().values():
                if raw and str(value).isdigit() and int(raw) == int(str(value)):
                    return True
        citation = _BIBLIO_CITATION_RE.search(normalized)
        return bool(
            citation and str(value).isdigit()
            and int(citation["volume"]) == int(str(value))
        )
    if field == "issue":
        for m in _BIBLIO_ISSUE_RE.finditer(normalized):
            if re.search(r"总\s*$", normalized[max(0, m.start()-5):m.start()]):
                continue
            for raw in m.groupdict().values():
                if raw and str(value).isdigit() and int(raw) == int(str(value)):
                    return True
        # Explicit year(issue):pages citation notation.
        m = _BIBLIO_CITATION_RE.search(normalized)
        return bool(m and str(value).isdigit() and int(m["issue"]) == int(str(value)))
    if field == "year":
        if not str(value).isdigit():
            return False
        return bool(re.search(
            rf"(?<!\d){re.escape(str(value))}(?:\s*年)?(?!\d)", normalized
        ))
    if field == "pages":
        numbers = re.findall(r"\d+", str(value))
        if len(numbers) != 2:
            return False
        for m in list(_BIBLIO_PAGES_RE.finditer(normalized)) + list(
            _BIBLIO_CITATION_RE.finditer(normalized)
        ):
            if [str(int(m["start"])), str(int(m["end"]))] == [
                str(int(v)) for v in numbers
            ]:
                return True
        return False
    return False


def _metadata_references_near_value(
    field: str, value: object, block: SourceBlock,
    registry: dict[str, EvidenceSpan],
) -> list[EvidenceReference]:
    """Reference a genuine substring of its physical SourceBlock, not its first lines."""
    spans = sorted(
        (span for span in registry.values()
         if span.source_block_id == block.id
         and span.page_number == block.page_number),
        key=lambda span: span.start_char,
    )
    needle = _normalize_marker(str(value))
    if not needle:
        return []
    matching: list[EvidenceSpan] = []
    for span in spans:
        text = _normalize_marker(span.text)
        if field in {"volume", "issue", "pages"}:
            ok = _front_biblio_value_matches(field, value, span.text)
        elif field == "year":
            ok = bool(re.search(
                rf"(?<!\d){re.escape(str(value))}(?!\d)", span.text
            ))
        else:
            ok = needle in text
        if ok:
            matching.append(span)
    if not matching:
        return []
    # Some sources contain many numbers: choose the exact field-label section.
    def score(span: EvidenceSpan) -> int:
        return {
            "journal": 0,
            "year": 0,
            "volume": int(bool(re.search(r"卷|vol(?:ume)?", span.text, re.I))),
            "issue": int(bool(re.search(r"期|issue|no\.", span.text, re.I))),
            "pages": int(bool(re.search(r"页码|pages?|pp\.|[:：]", span.text, re.I))),
        }.get(field, 0)
    best = max(matching, key=lambda span: (score(span), -span.start_char))
    return [
        EvidenceReference(
            evidence_id=best.id,
            source_block_id=best.source_block_id,
            page_number=best.page_number,
            quote=best.text,
        )
    ]


def _extract_first_page_bibliography(
    blocks: list[SourceBlock],
    span_registry: dict[str, EvidenceSpan],
) -> list[tuple[str, object, ExtractedClaim]]:
    """Recover *explicitly printed* masthead fields even if visible only once.

    Page-header/furniture evidence is deliberately allowed. Unlike the
    repeating-header fallback, we do not require two physical pages when the
    first-page issue/volume/journal typography is explicit.
    """
    front = _front_biblio_blocks(blocks)
    if not front:
        return []
    found: dict[str, tuple[object, SourceBlock]] = {}
    for block in front:
        text = _biblio_text(block.text)
        if re.search(r"(?:^|\n)\s*(?:参考文献|References)\b", text, re.I):
            continue
        # A standalone publication year in a real page header is safe. A
        # year appearing in an article abstract or study sample is not.
        standalone_year = re.fullmatch(rf"\s*({_BIBLIO_YEAR})\s*年?\s*", text)
        if standalone_year and (
            block.role == "furniture"
            or "page_header" in (block.source_label or "").casefold()
        ):
            found.setdefault("year", (int(standalone_year[1]), block))

        quoted = re.search(r"《\s*([^》\n]{2,70})\s*》", text)
        labeled = _BIBLIO_JOURNAL_LABEL.search(text)
        has_context = (
            bool(_BIBLIO_ISSUE_RE.search(text) or _BIBLIO_VOLUME_RE.search(text)
                 or _BIBLIO_CITATION_RE.search(text))
            and bool(re.search(rf"(?<!\d){_BIBLIO_YEAR}(?!\d)", text))
        )
        journal = ""
        if labeled:
            journal = _clean_journal_masthead(labeled["value"])
        elif quoted and has_context:
            journal = _clean_journal_masthead(quoted[1])
        elif has_context:
            date = re.search(rf"(?<!\d){_BIBLIO_YEAR}(?!\d)", text)
            if date:
                journal = _clean_journal_masthead(text[:date.start()])
                if journal and not (
                    _BIBLIO_JOURNAL_CUES.search(journal)
                    or (
                        block.bbox is not None and block.bbox[1] <= 180
                        and block.role != "heading" and len(journal) <= 45
                    )
                ):
                    journal = ""

        if not journal and (
            has_context or _BIBLIO_ISSUE_RE.search(text)
            or _BIBLIO_VOLUME_RE.search(text)
        ) and block.bbox is not None:
            # A separate title/author block must NOT be mistaken for a
            # journal. Permit only an adjacent Docling page_header/furniture
            # block, not a normal article heading.
            neighbors = [
                other for other in front
                if other.page_number == block.page_number
                and other.id != block.id
                and other.bbox is not None
                and (
                    other.role == "furniture"
                    or "page_header" in (other.source_label or "").casefold()
                )
                and abs(other.bbox[1] - block.bbox[1]) <= 105
            ]
            neighbors.sort(key=lambda other: (
                abs(other.bbox[1] - block.bbox[1]), len(other.text)
            ))
            for candidate in neighbors:
                name = _clean_journal_masthead(candidate.text)
                if name and len(name) <= 65:
                    journal = name
                    found.setdefault("journal", (journal, candidate))
                    break
        if journal:
            found.setdefault("journal", (journal, block))

        for volume_match in _BIBLIO_VOLUME_RE.finditer(text):
            raw = next((v for v in volume_match.groupdict().values() if v), None)
            if raw:
                found.setdefault("volume", (str(int(raw)), block))
                break
        for issue_match in _BIBLIO_ISSUE_RE.finditer(text):
            if re.search(r"总\s*$", text[max(0, issue_match.start()-5):issue_match.start()]):
                continue
            raw = next((v for v in issue_match.groupdict().values() if v), None)
            if raw:
                found.setdefault("issue", (str(int(raw)), block))
                break

        citation = _BIBLIO_CITATION_RE.search(text)
        if citation:
            found.setdefault("volume", (str(int(citation["volume"])), block))
            found.setdefault("issue", (str(int(citation["issue"])), block))
            found.setdefault("year", (int(citation["year"]), block))
        if has_context or labeled or citation:
            year_match = re.search(rf"(?<!\d)({_BIBLIO_YEAR})(?:\s*年)?(?!\d)", text)
            if year_match:
                found.setdefault("year", (int(year_match[1]), block))
        elif block.bbox is not None and (
            _BIBLIO_ISSUE_RE.search(text) or _BIBLIO_VOLUME_RE.search(text)
        ):
            # Some journals split year, volume and issue into distinct
            # Docling page_header items. A standalone year within the same
            # printed masthead band is legitimate, but arbitrary body years
            # and DOI digits are not.
            nearby_years = [
                other for other in front
                if other.id != block.id
                and other.page_number == block.page_number
                and other.bbox is not None
                and abs(other.bbox[1] - block.bbox[1]) <= 105
                and re.fullmatch(
                    rf"\s*({_BIBLIO_YEAR})\s*年?\s*",
                    _biblio_text(other.text)
                )
                and (
                    other.role == "furniture"
                    or "page_header" in (other.source_label or "").casefold()
                )
            ]
            if nearby_years:
                closest = min(
                    nearby_years,
                    key=lambda other: abs(other.bbox[1] - block.bbox[1])
                )
                match = re.search(_BIBLIO_YEAR, closest.text)
                if match:
                    found.setdefault("year", (int(match.group()), closest))

        pages = _BIBLIO_PAGES_RE.search(text)
        if pages is None and citation is not None:
            pages = citation
        if pages:
            first, last = int(pages["start"]), int(pages["end"])
            if 1 <= first <= last <= 99999 and last - first <= 600:
                found.setdefault("pages", (f"{first}-{last}", block))

    # If metadata appears in a dedicated issue block while the journal is a
    # separate page-header text block, only accept that adjacent exact source.
    candidates: list[tuple[str, object, ExtractedClaim]] = []
    for field in ("journal", "year", "volume", "issue", "pages"):
        if field not in found:
            continue
        value, block = found[field]
        if not _front_biblio_value_matches(field, value, block.text):
            # For an explicitly labeled journal the name may have been
            # combined from whitespace; never manufacture an evidence quote.
            continue
        evidence = _metadata_references_near_value(
            field, value, block, span_registry
        )
        claim = _layout_claim(field, value, evidence)
        if claim:
            candidates.append(claim)
    return candidates


def _reanchor_lite_bibliography(
    field: str, value: object,
    source_map: dict[str, SourceBlock],
    registry: dict[str, EvidenceSpan],
) -> ExtractedClaim | None:
    """Repair an LLM-selected wrong evidence ID only when the PDF verifies it.

    In particular a bare digit appearing in an unrelated paragraph cannot
    establish journal volume, issue, or printed page range.
    """
    if field not in {"journal", "year", "volume", "issue", "pages"}:
        return None
    front = _front_biblio_blocks(list(source_map.values()))
    for block in front:
        text = _biblio_text(block.text)
        if not _front_biblio_value_matches(field, value, text):
            continue
        if field == "journal":
            if not (
                _BIBLIO_JOURNAL_LABEL.search(text)
                or block.role == "furniture"
                or "page_header" in (block.source_label or "").casefold()
                or (
                    _BIBLIO_JOURNAL_CUES.search(str(value))
                    and re.search(rf"(?<!\d){_BIBLIO_YEAR}(?!\d)", text)
                    and (
                        _BIBLIO_ISSUE_RE.search(text)
                        or _BIBLIO_VOLUME_RE.search(text)
                    )
                )
            ):
                continue
        elif field == "year":
            # A random study year or publication date in the article body
            # is not sufficient for a journal's year metadata.
            if not (
                _BIBLIO_JOURNAL_LABEL.search(text)
                or _BIBLIO_VOLUME_RE.search(text)
                or _BIBLIO_ISSUE_RE.search(text)
                or _BIBLIO_CITATION_RE.search(text)
                or re.search(r"^(?:出版年份|发表年份|出版年)\s*[:：]", text)
                or (
                    re.fullmatch(rf"\s*{_BIBLIO_YEAR}\s*年?\s*", text)
                    and (block.role == "furniture"
                         or "page_header" in (block.source_label or "").casefold())
                )
            ):
                continue
        evidence = _metadata_references_near_value(field, value, block, registry)
        claim = _layout_claim(field, value, evidence)
        if claim:
            return claim[2]
    return None


def _derive_front_matter_metadata(
    blocks: list[SourceBlock], span_registry: dict[str, EvidenceSpan]
) -> list[tuple[str, object, ExtractedClaim]]:
    text_blocks = [
        block for block in blocks
        if block.table_rows is None and block.page_number <= 3 and block.bbox is not None
    ]
    by_page: dict[int, list[SourceBlock]] = {}
    for block in text_blocks:
        by_page.setdefault(block.page_number, []).append(block)
    candidates: list[tuple[str, object, ExtractedClaim]] = []

    keyword_info: tuple[int, list[SourceBlock], int] | None = None
    for page_number in sorted(by_page):
        page_blocks = sorted(by_page[page_number], key=lambda b: (b.bbox[1], b.bbox[0]))
        for idx, block in enumerate(page_blocks):
            if re.search(r"(?:关键词|关键字|Keywords?)\s*[:：]", unicodedata.normalize("NFKC", block.text), re.I):
                keyword_info = (page_number, page_blocks, idx)
                break
        if keyword_info:
            break

    if keyword_info:
        _, page_blocks, idx = keyword_info
        marker_block = page_blocks[idx]
        normalized = unicodedata.normalize("NFKC", marker_block.text)
        match = re.search(r"(?:关键词|关键字|Keywords?)\s*[:：]\s*(.*)", normalized, re.I | re.S)
        keyword_blocks = [marker_block]
        segments = _split_keyword_segments(match.group(1) if match else "")

        page_left = min(block.bbox[0] for block in page_blocks)
        page_right = max(block.bbox[2] for block in page_blocks)
        previous = marker_block
        for block in page_blocks[idx + 1 :]:
            gap = block.bbox[1] - previous.bbox[3]
            if gap > 20 or block.bbox[1] - marker_block.bbox[1] > 70:
                break
            lines = _split_keyword_segments(unicodedata.normalize("NFKC", block.text))
            if not lines:
                previous = block
                continue
            wraps_from_right_edge = (
                previous.bbox[2] >= page_right - 20
                and block.bbox[0] <= page_left + 20
                and bool(segments)
            )
            if wraps_from_right_edge:
                segments[-1] += lines[0]
                lines = lines[1:]
            segments.extend(lines)
            keyword_blocks.append(block)
            previous = block

        keywords: list[str] = []
        seen: set[str] = set()
        for item in segments:
            clean = item.strip(" ,，、。.;；:")
            if not clean or len(clean) > 100:
                continue
            key = _normalize_marker(clean)
            if key and key not in seen:
                seen.add(key)
                keywords.append(clean)
        if keywords:
            evidence: list[EvidenceReference] = []
            for block in keyword_blocks:
                evidence.extend(_evidence_references_for_block(block.id, span_registry, max_refs=2))
            claim = _layout_claim("keywords", keywords, evidence)
            if claim:
                candidates.append(claim)

        abstract_start = None
        for j, block in enumerate(page_blocks[: idx + 1]):
            text = unicodedata.normalize("NFKC", block.text)
            if re.search(r"^\s*(?:摘\s*要|提\s*要|abstract)\s*[:：]?", text, re.I):
                abstract_start = j
                break
        if abstract_start is not None and abstract_start < idx:
            abstract_blocks = page_blocks[abstract_start:idx]
            abstract_parts: list[str] = []
            for j, block in enumerate(abstract_blocks):
                text = unicodedata.normalize("NFKC", block.text)
                if j == 0:
                    text = re.sub(r"^\s*(?:摘\s*要|提\s*要|abstract)\s*[:：]?\s*", "", text, count=1, flags=re.S | re.I)
                abstract_parts.append(text)
            abstract = _smart_join_wrapped(abstract_parts)
            if abstract:
                evidence: list[EvidenceReference] = []
                for block in abstract_blocks:
                    evidence.extend(_evidence_references_for_block(block.id, span_registry, max_refs=2))
                claim = _layout_claim("abstract", abstract[:2000], evidence)
                if claim:
                    candidates.append(claim)

    return candidates


def _derive_abstract_metadata(
    blocks: list[SourceBlock], span_registry: dict[str, EvidenceSpan]
) -> list[tuple[str, object, ExtractedClaim]]:
    """Recover abstracts from Docling front matter without relying on LLM evidence spans."""
    front = [
        block for block in blocks
        if block.table_rows is None and block.page_number <= 3 and block.role != "furniture"
    ]
    front.sort(key=lambda block: (block.page_number, block.bbox[1] if block.bbox else 0, block.bbox[0] if block.bbox else 0))
    marker = re.compile(r"(?:^|\n)\s*(?:摘\s*要|提\s*要|abstract)\s*[:：]?", re.I)
    stop = re.compile(r"(?:关键词|关键字|keywords?|中图分类号|doi)\s*[:：]?", re.I)
    for index, block in enumerate(front):
        normalized = unicodedata.normalize("NFKC", block.text)
        match = marker.search(normalized)
        if not match:
            continue
        parts = [normalized[match.end():].strip()]
        evidence_blocks = [block]
        for following in front[index + 1:]:
            following_text = unicodedata.normalize("NFKC", following.text).strip()
            if stop.search(following_text) or following.role == "heading":
                break
            if following.page_number > block.page_number + 1:
                break
            parts.append(following_text)
            evidence_blocks.append(following)
            if len(" ".join(parts)) >= 2000:
                break
        abstract = _smart_join_wrapped([part for part in parts if part])[:2000]
        if len(abstract) < 40:
            continue
        evidence: list[EvidenceReference] = []
        for source_block in evidence_blocks:
            evidence.extend(_evidence_references_for_block(source_block.id, span_registry, max_refs=2))
        candidate = _layout_claim("abstract", abstract, evidence)
        return [candidate] if candidate else []
    return []


def _derive_repeating_header_metadata(
    blocks: list[SourceBlock], span_registry: dict[str, EvidenceSpan]
) -> list[tuple[str, object, ExtractedClaim]]:
    """Recover journal/year/volume/issue from repeated running headers.

    Academic journals encode running-header metadata in several common forms. In
    particular, Chinese journals often use strings such as::

        Academic Monthly 第57卷 06 Jun 2025

    while other issues use ``2026.2`` or ``Vol. 57 No. 6 Jun 2025``. Docling may
    keep that header in one block or split the journal, volume and date across
    adjacent header blocks. This recovery is deliberately deterministic and only
    accepts a candidate when it repeats on at least two physical pages.
    """
    occurrences: dict[tuple[str, int, str, str | None], list[SourceBlock]] = {}

    month_names = (
        "jan|january|feb|february|mar|march|apr|april|may|jun|june|"
        "jul|july|aug|august|sep|sept|september|oct|october|nov|november|"
        "dec|december"
    )
    metadata_patterns = (
        re.compile(
            r"\b(?P<year>(?:19|20)\d{2})\s*[.·．]\s*(?P<issue>\d{1,2})\b",
            re.I,
        ),
        re.compile(
            rf"第\s*(?P<volume>\d{{1,3}})\s*卷\s*"
            rf"(?:(?:第\s*)?(?P<issue>\d{{1,2}})\s*(?:期\b)?\s*)?"
            rf"(?:(?:{month_names})\.?\s*)?"
            rf"(?P<year>(?:19|20)\d{{2}})\b",
            re.I,
        ),
        re.compile(
            rf"\bvol(?:ume)?\.?\s*(?P<volume>\d{{1,3}})\s*"
            rf"(?:(?:no|issue)\.?\s*(?P<issue>\d{{1,2}})\s*)?"
            rf"(?:(?:{month_names})\.?\s*)?"
            rf"(?P<year>(?:19|20)\d{{2}})\b",
            re.I,
        ),
        re.compile(
            r"\b(?P<year>(?:19|20)\d{2})\s*年?\s*第?\s*"
            r"(?P<volume>\d{1,3})\s*卷\s*第?\s*(?P<issue>\d{1,2})\s*期\b",
            re.I,
        ),
        # Split-header fallback, e.g. one block is ``06 Jun 2025`` while another
        # block on the same page contains ``Academic Monthly`` / ``第57卷``.
        re.compile(
            rf"\b(?P<issue>\d{{1,2}})\s*(?:{month_names})\.?\s*"
            rf"(?P<year>(?:19|20)\d{{2}})\b",
            re.I,
        ),
    )
    standalone_volume = re.compile(
        r"(?:第\s*(?P<cn>\d{1,3})\s*卷|\bvol(?:ume)?\.?\s*(?P<en>\d{1,3})\b)",
        re.I,
    )

    by_page: dict[int, list[SourceBlock]] = {}
    for block in blocks:
        if block.table_rows is None and len(block.text) <= 220:
            by_page.setdefault(block.page_number, []).append(block)

    def normalized_text(text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        text = text.replace("\u00a0", " ")
        return re.sub(r"[ \t]+", " ", text)

    def clean_journal(text: str) -> str:
        text = normalized_text(text)
        lines = [line.strip(" -|:/·") for line in text.splitlines() if line.strip()]
        usable = []
        for line in lines:
            line = re.sub(r"\s+", " ", line).strip()
            if not (2 <= len(line) <= 80):
                continue
            if any(char.isdigit() for char in line):
                continue
            if line.casefold() in {
                "专题研究", "专题", "研究", "special issue", "special section",
            }:
                continue
            if re.fullmatch(rf"(?:{month_names})\.?", line, flags=re.I):
                continue
            usable.append(line)
        return usable[0] if usable else ""

    def parse_metadata(text: str) -> tuple[re.Match[str], int, str, str | None] | None:
        for pattern in metadata_patterns:
            match = pattern.search(text)
            if not match:
                continue
            groups = match.groupdict()
            year = int(groups["year"])
            issue = groups.get("issue") or ""
            volume = groups.get("volume")
            if not issue:
                continue
            return match, year, issue, volume
        return None

    def headerish(block: SourceBlock) -> bool:
        source_label = (block.source_label or "").casefold()
        return (
            block.role == "furniture"
            or "page_header" in source_label
            or "header" in source_label
        )

    for page_blocks in by_page.values():
        for metadata_block in page_blocks:
            text = normalized_text(metadata_block.text)
            parsed = parse_metadata(text)
            if parsed is None:
                continue
            match, year, issue, volume = parsed

            journal = ""
            supporting = [metadata_block]

            # Combined header: everything before the structured metadata is the
            # strongest journal-name candidate.
            prefix = clean_journal(text[: match.start()])
            if prefix:
                journal = prefix

            # Multi-line combined header: the journal may be on the line directly
            # before or after the metadata line.
            if not journal:
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                match_index = next(
                    (i for i, line in enumerate(lines) if parse_metadata(line) is not None),
                    -1,
                )
                neighbors: list[str] = []
                if match_index > 0:
                    neighbors.append(lines[match_index - 1])
                if 0 <= match_index + 1 < len(lines):
                    neighbors.append(lines[match_index + 1])
                for line in neighbors:
                    journal = clean_journal(line)
                    if journal:
                        break

            # Split Docling header: recover a standalone volume and journal from
            # nearby page-header/furniture blocks on the same page.
            nearby: list[tuple[float, int, SourceBlock]] = []
            for other in page_blocks:
                if other.id == metadata_block.id or not headerish(other):
                    continue
                distance = 10000.0
                if metadata_block.bbox is not None and other.bbox is not None:
                    distance = abs(other.bbox[1] - metadata_block.bbox[1])
                nearby.append((distance, len(other.text), other))
            nearby.sort(key=lambda item: (item[0], item[1]))

            if volume is None:
                for _, _, other in nearby:
                    volume_match = standalone_volume.search(normalized_text(other.text))
                    if not volume_match:
                        continue
                    volume = volume_match.group("cn") or volume_match.group("en")
                    supporting.append(other)
                    break

            if not journal:
                for _, _, other in nearby:
                    candidate = clean_journal(other.text)
                    if not candidate:
                        continue
                    journal = candidate
                    supporting.append(other)
                    break

            if not journal:
                continue

            key = (journal, year, issue, volume)
            occurrences.setdefault(key, []).extend(supporting)

    eligible: list[
        tuple[tuple[str, int, str, str | None], list[SourceBlock]]
    ] = []
    for key, value in occurrences.items():
        if len({block.page_number for block in value}) < 2:
            continue
        seen: set[str] = set()
        deduped: list[SourceBlock] = []
        for block in sorted(
            value,
            key=lambda item: (
                item.page_number,
                item.bbox[1] if item.bbox else 0.0,
            ),
        ):
            if block.id not in seen:
                seen.add(block.id)
                deduped.append(block)
        eligible.append((key, deduped))

    if not eligible:
        return []

    (journal, year, issue, volume), matching_blocks = max(
        eligible,
        key=lambda item: (
            len({block.page_number for block in item[1]}),
            1 if item[0][3] is not None else 0,
            -min(block.page_number for block in item[1]),
        ),
    )

    evidence: list[EvidenceReference] = []
    used_sources: set[str] = set()
    for block in matching_blocks:
        for reference in _evidence_references_for_block(
            block.id, span_registry, max_refs=1
        ):
            if reference.source_block_id not in used_sources:
                used_sources.add(reference.source_block_id)
                evidence.append(reference)
        if len(evidence) >= 4:
            break

    candidates: list[tuple[str, object, ExtractedClaim]] = []
    values: list[tuple[str, object]] = [
        ("journal", journal),
        ("year", year),
        ("issue", issue),
    ]
    if volume is not None:
        values.insert(2, ("volume", volume))
    for field_name, value in values:
        candidate = _layout_claim(field_name, value, evidence)
        if candidate:
            candidates.append(candidate)
    return candidates


def _longest_consecutive_page_run(
    records: list[tuple[int, str, SourceBlock]], *, reverse_digits: bool
) -> list[tuple[int, int, SourceBlock]]:
    converted: list[tuple[int, int, SourceBlock]] = []
    for physical_page, raw_digits, block in records:
        digits = raw_digits[::-1] if reverse_digits else raw_digits
        try:
            printed_page = int(digits)
        except ValueError:
            continue
        converted.append((physical_page, printed_page, block))
    if not converted:
        return []

    best: list[tuple[int, int, SourceBlock]] = []
    current: list[tuple[int, int, SourceBlock]] = []
    for item in sorted(converted, key=lambda value: value[0]):
        if current and item[0] == current[-1][0] + 1 and item[1] == current[-1][1] + 1:
            current.append(item)
        else:
            current = [item]
        if len(current) > len(best):
            best = list(current)
    return best


def _derive_page_range_metadata(
    blocks: list[SourceBlock], span_registry: dict[str, EvidenceSpan]
) -> list[tuple[str, object, ExtractedClaim]]:
    """Recover printed page range, preferring deterministic parser page-number blocks."""
    explicit_records: list[tuple[int, str, SourceBlock]] = []
    for block in blocks:
        if (block.source_label or "").casefold() != "sra:printed_page_number":
            continue
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", block.text))
        if re.fullmatch(r"\d{1,4}", compact):
            explicit_records.append((block.page_number, compact, block))

    def best_run(records: list[tuple[int, str, SourceBlock]]) -> list[tuple[int, int, SourceBlock]]:
        direct = _longest_consecutive_page_run(records, reverse_digits=False)
        reversed_run = _longest_consecutive_page_run(records, reverse_digits=True)
        return reversed_run if len(reversed_run) > len(direct) else direct

    run = best_run(explicit_records)

    # Backwards-compatible fallback for cards parsed before deterministic footer blocks
    # were added, or for image-only PDFs where a footer could not be read directly.
    if len(run) < 3:
        by_page: dict[int, list[SourceBlock]] = {}
        for block in blocks:
            if block.bbox is not None and block.table_rows is None:
                by_page.setdefault(block.page_number, []).append(block)

        records: list[tuple[int, str, SourceBlock]] = []
        for page_number, page_blocks in by_page.items():
            max_y = max(block.bbox[3] for block in page_blocks if block.bbox is not None)
            bottom_margin = max(24.0, max_y * 0.045)
            candidates = []
            for block in page_blocks:
                if block.bbox is None:
                    continue
                compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", block.text))
                if not re.fullmatch(r"\d{1,4}", compact):
                    continue
                source_label = (block.source_label or "").casefold()
                is_docling_footer = "page_footer" in source_label
                is_bottom_number = block.bbox[1] >= max_y - bottom_margin
                if is_docling_footer or is_bottom_number:
                    candidates.append(block)
            if not candidates:
                continue
            candidates.sort(
                key=lambda item: (
                    0 if "page_footer" in (item.source_label or "").casefold() else 1,
                    -(item.bbox[1] if item.bbox else -1),
                )
            )
            block = candidates[0]
            raw_digits = re.sub(r"\s+", "", unicodedata.normalize("NFKC", block.text))
            records.append((page_number, raw_digits, block))
        run = best_run(records)

    if len(run) < 3:
        return []

    start_page = run[0][1]
    end_page = run[-1][1]
    if end_page < start_page:
        return []

    evidence: list[EvidenceReference] = []
    for block in (run[0][2], run[-1][2]):
        evidence.extend(_evidence_references_for_block(block.id, span_registry, max_refs=1))

    candidate = _layout_claim("pages", f"{start_page}-{end_page}", evidence)
    return [candidate] if candidate else []


def _derive_layout_metadata_candidates(
    blocks: list[SourceBlock], span_registry: dict[str, EvidenceSpan]
) -> list[tuple[str, object, ExtractedClaim]]:
    return [
        *_derive_front_matter_metadata(blocks, span_registry),
        *_derive_abstract_metadata(blocks, span_registry),
        *_extract_first_page_bibliography(blocks, span_registry),
        *_derive_repeating_header_metadata(blocks, span_registry),
        *_derive_page_range_metadata(blocks, span_registry),
    ]


def _select_metadata(
    candidates: list[tuple[str, object, ExtractedClaim]], warnings: list[str]
) -> tuple[PaperMetadata, list[ExtractedClaim]]:
    values: dict[str, object] = {}
    selected_claims: list[ExtractedClaim] = []
    canonical: dict[str, str] = {}

    for field_name, value, claim in candidates:
        if claim.verification != VerificationStatus.SUPPORTED or not claim.evidence:
            continue
        value_key = _metadata_compare_key(field_name, value)
        if field_name in values:
            if canonical[field_name] != value_key:
                warnings.append(
                    f"Lite: conflicting supported metadata values for '{field_name}'; kept the first grounded value."
                )
            continue
        values[field_name] = value
        canonical[field_name] = value_key
        selected_claims.append(claim)

    try:
        metadata = PaperMetadata.model_validate(values)
    except ValidationError as exc:
        warnings.append(f"Lite: grounded metadata failed validation and was partially omitted: {exc}")
        safe_values: dict[str, object] = {}
        safe_claims: list[ExtractedClaim] = []
        for claim in selected_claims:
            field_name = claim.field_name.removeprefix("metadata.")
            try:
                PaperMetadata.model_validate({field_name: values[field_name]})
            except ValidationError:
                continue
            safe_values[field_name] = values[field_name]
            safe_claims.append(claim)
        metadata = PaperMetadata.model_validate(safe_values)
        selected_claims = safe_claims
    return metadata, selected_claims
