"""Improved reference extraction with conservative bibliography splitting.

Fixes:
- Avoids creating single-author fragments as references.
- Splits multi-entry SourceBlocks using stronger author/year boundaries.
- Requires stronger evidence before marking a reference structured.
"""

from __future__ import annotations

import re
from uuid import NAMESPACE_URL, uuid5

from .models import (
    CitationMention,
    ReferenceEntry,
    ReferenceMatchStatus,
    ReferenceParseStatus,
    SourceBlock,
)

_HEADING_RE = re.compile(
    r"^(references|bibliography|works cited|参考文献|引用文献)\s*:?\s*$",
    re.I,
)

_YEAR_RE = re.compile(r"(?<!\d)(1[0-9]{3}|20\d{2}|21\d{2})(?!\d)")
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)

_NUMERIC_CITATION_RE = re.compile(
    r"(?<!\w)(?:\[(\d{1,4}(?:\s*[-,;]\s*\d{1,4})*)\]"
    r"|\((\d{1,4}(?:\s*[,;]\s*\d{1,4})*)\))(?!\w)"
)

_AUTHOR_YEAR_START_RE = re.compile(
    r"(?<![\w])"
    r"(?:[一-鿿]{2,8}(?:[、，,]\s*[一-鿿]{2,8}){0,5}"
    r"|[A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,3})"
    r"[，,\s]+(?:19|20)\d{2}[a-z]?[，,.\s]"
)

def extract_references(
    paper_id: str, blocks: list[SourceBlock]
) -> tuple[list[ReferenceEntry], list[CitationMention]]:
    ordered = sorted(
        blocks,
        key=lambda b: (b.page_number, b.bbox[1] if b.bbox else 0, b.id),
    )

    heading_index = next(
        (i for i, block in enumerate(ordered)
         if _HEADING_RE.match(block.text.strip())),
        None,
    )

    if heading_index is None:
        return [], []

    entries = _build_entries(paper_id, ordered[heading_index + 1 :])
    mentions = _build_mentions(paper_id, ordered[:heading_index], entries)
    return entries, mentions


def _build_entries(
    paper_id: str, blocks: list[SourceBlock]
) -> list[ReferenceEntry]:
    raw_entries: list[tuple[SourceBlock, str]] = []

    for block in blocks:
        if block.role == "furniture":
            continue

        text = " ".join(block.text.split())
        if not text:
            continue

        for piece in _split_reference_text(block.text):
            if _is_real_reference(piece):
                raw_entries.append((block, piece))

    entries: list[ReferenceEntry] = []

    for ordinal, (block, raw_text) in enumerate(raw_entries, start=1):
        year_match = _YEAR_RE.search(raw_text)
        doi_match = _DOI_RE.search(raw_text)

        stripped = re.sub(
            r"^\s*(?:\[?\d+\]?|\(\d+\))\s*",
            "",
            raw_text,
        )

        title = _title_guess(stripped)

        # Do not call fragments with only a year or DOI "structured".
        structured = bool(
            year_match
            and title
            and len(stripped) > 25
        )

        status = (
            ReferenceParseStatus.STRUCTURED
            if structured
            else ReferenceParseStatus.NEEDS_REVIEW
        )

        entry_id = str(
            uuid5(
                NAMESPACE_URL,
                f"sra:reference:{paper_id}:{ordinal}:{raw_text}",
            )
        )

        entries.append(
            ReferenceEntry(
                id=entry_id,
                paper_id=paper_id,
                ordinal=ordinal,
                raw_text=raw_text,
                authors=_authors_guess(stripped),
                year=int(year_match.group(1)) if year_match else None,
                title=title,
                doi=doi_match.group(0).rstrip(".,;)") if doi_match else None,
                source_page=block.page_number,
                source_block_id=block.id,
                parse_status=status,
                needs_review_reason=None if structured else "weak_reference_boundary",
            )
        )

    return entries


def _split_reference_text(text: str) -> list[str]:
    """Split only strong bibliography boundaries.

    A common PDF failure is:
    one paragraph contains 10 references, or
    a lonely author name becomes a fake reference.

    We only split when a new author/year pattern appears.
    """

    lines = [" ".join(line.split()) for line in re.split(r"\r?\n+", text) if line.strip()]
    if len(lines) > 1 and sum(bool(_AUTHOR_YEAR_START_RE.search(line)) for line in lines) >= 2:
        return lines
    normalized = " ".join(text.split())

    positions = [
        match.start()
        for match in _AUTHOR_YEAR_START_RE.finditer(normalized)
        if match.start() > 0
    ]

    if len(positions) < 2:
        return [normalized]

    chunks = []
    starts = [0, *positions]

    for start, end in zip(starts, starts[1:] + [len(normalized)]):
        piece = normalized[start:end].strip(" ，,;；")
        if piece:
            chunks.append(piece)

    return chunks


def _is_real_reference(text: str) -> bool:
    compact = text.strip()

    if len(compact) < 20:
        return False

    if not _YEAR_RE.search(compact) and not _DOI_RE.search(compact):
        return False

    return True


def _build_mentions(
    paper_id: str,
    blocks: list[SourceBlock],
    entries: list[ReferenceEntry],
) -> list[CitationMention]:
    mentions = []

    for block in blocks:
        text = " ".join(block.text.split())

        for match in _NUMERIC_CITATION_RE.finditer(text):
            values = match.group(1) or match.group(2) or ""

            for value in re.findall(r"\d+", values):
                ordinal = int(value)
                target = next(
                    (e for e in entries if e.ordinal == ordinal),
                    None,
                )

                mentions.append(
                    CitationMention(
                        id=str(
                            uuid5(
                                NAMESPACE_URL,
                                f"{paper_id}:{block.id}:{match.start()}:{value}",
                            )
                        ),
                        paper_id=paper_id,
                        marker=match.group(0),
                        reference_entry_id=target.id if target else None,
                        page_number=block.page_number,
                        source_block_id=block.id,
                        context_text=text[:1000],
                        match_status=(
                            ReferenceMatchStatus.CONFIRMED
                            if target
                            else ReferenceMatchStatus.NEEDS_REVIEW
                        ),
                    )
                )

    return mentions


def _title_guess(text: str) -> str | None:
    parts = [
        p.strip()
        for p in re.split(r"[。.!?]\s+", text)
        if p.strip()
    ]

    if not parts:
        return None

    candidate = parts[0].strip(" .")
    return candidate[:500] if len(candidate) >= 8 else None


def _authors_guess(text: str) -> list[str]:
    year_match = _YEAR_RE.search(text)
    prefix = text[: year_match.start()] if year_match else text.split("，", 1)[0].split(",", 1)[0]
    prefix = prefix.strip(" ，,、")

    if len(prefix) > 200:
        return []

    # Keep Chinese surname+given-name pairs intact. Split only on explicit list
    # separators; commas inside a Chinese name are treated as typography noise.
    pieces = re.split(r"\s*(?:、|;|\band\b|\s+＆\s+|\s+&\s+)\s*", prefix)
    return [re.sub(r"\s+", "", item.strip(" ，,")) for item in pieces if item.strip()][:12]


def display_reference_text(text: str) -> str:
    """Normalize PDF typography for UI display while keeping raw_text unchanged."""
    value = re.sub(r"\s+", " ", text).strip()
    value = re.sub(r"\s*([，。；：！？、])\s*", r"\1", value)
    value = re.sub(r"\s*([《》〈〉（）])\s*", r"\1", value)
    value = re.sub(r"\s+([,.!?;:)\]])", r"\1", value)
    value = re.sub(r"([\[(])\s+", r"\1", value)
    return value
