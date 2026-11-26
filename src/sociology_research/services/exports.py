"""Read-only export views over persisted research memory."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from ..analyzer import render_deep_reading_markdown
from ..models import ClaimReviewDecision, PaperCard, PaperRecord, UserNote
from ..references import extract_references
from ..repository import ResearchRepository

_RIS_LINE_BREAK_RE = re.compile(r"[\r\n]+")
_PAGE_RANGE_RE = re.compile(r"^\s*([A-Za-z]?\d+)\s*[-–—~]\s*([A-Za-z]?\d+)\s*$")


def _ris_clean(value: object) -> str:
    return _RIS_LINE_BREAK_RE.sub(" ", "" if value is None else str(value)).strip()


def render_paper_ris_record(
    paper: PaperRecord,
    *,
    card: PaperCard | None = None,
    metadata_overrides: dict[str, Any] | None = None,
    user_notes: Sequence[UserNote] | None = None,
    claim_reviews: Sequence[ClaimReviewDecision] | None = None,
    project_name: str | None = None,
    reading_status: str | None = None,
    project_note: str | None = None,
) -> str:
    """生成 NoteExpress 原生兼容的 UTF-8 RIS 题录记录（含手动题录修订、个人笔记与人工修正）。"""
    meta: dict[str, Any] = card.metadata.model_dump(mode="python") if card is not None else {}
    override_values = (
        metadata_overrides.get("values")
        if isinstance(metadata_overrides, dict) and isinstance(metadata_overrides.get("values"), dict)
        else (metadata_overrides if isinstance(metadata_overrides, dict) else {})
    )
    for key, val in override_values.items():
        if val not in (None, "", []):
            meta[key] = val

    title = _ris_clean(meta.get("title")) or _ris_clean(paper.original_name)
    lines: list[str] = ["TY  - JOUR", f"TI  - {title}"]

    for author in meta.get("authors") or []:
        clean_author = _ris_clean(author)
        if clean_author:
            lines.append(f"AU  - {clean_author}")

    if meta.get("year") not in (None, ""):
        lines.append(f"PY  - {_ris_clean(meta['year'])}")

    journal = _ris_clean(meta.get("journal"))
    if journal:
        lines.append(f"JO  - {journal}")
        lines.append(f"JF  - {journal}")

    for tag, key in (("VL", "volume"), ("IS", "issue"), ("DO", "doi")):
        val = _ris_clean(meta.get(key))
        if val:
            lines.append(f"{tag}  - {val}")

    pages = _ris_clean(meta.get("pages"))
    if pages:
        m = _PAGE_RANGE_RE.match(pages)
        if m:
            lines.append(f"SP  - {m.group(1)}")
            lines.append(f"EP  - {m.group(2)}")
        else:
            lines.append(f"SP  - {pages}")

    for kw in meta.get("keywords") or []:
        clean_kw = _ris_clean(kw)
        if clean_kw:
            lines.append(f"KW  - {clean_kw}")

    abstract = _ris_clean(meta.get("abstract"))
    if abstract:
        lines.append(f"AB  - {abstract}")

    note_segments: list[str] = []
    if project_name:
        status_part = f"（状态：{reading_status}）" if reading_status else ""
        note_segments.append(f"[专题] {project_name}{status_part}")
    if project_note and project_note.strip():
        note_segments.append(f"[专题备注] {_ris_clean(project_note)}")

    for unote in user_notes or []:
        head = f"{unote.title}：" if unote.title.strip() else ""
        page_tag = f"(PDF p.{unote.page_number}) " if unote.page_number else ""
        note_segments.append(f"[MY NOTE] {page_tag}{head}{_ris_clean(unote.note)}")

    for rev in claim_reviews or []:
        status_val = str(rev.user_status.value)
        if status_val == "corrected" and rev.corrected_statement.strip():
            note_segments.append(f"[HUMAN_REVIEWED: 人工修正] {_ris_clean(rev.corrected_statement)}")
        elif rev.note.strip():
            note_segments.append(f"[HUMAN_REVIEWED: {status_val}] {_ris_clean(rev.note)}")

    if note_segments:
        lines.append("N1  - " + " ; ".join(note_segments))

    lines.append(f"ID  - {paper.id}")
    lines.append("ER  - ")
    return "\r\n".join(lines) + "\r\n"


class ExportService:
    def __init__(self, repository: ResearchRepository) -> None:
        self.repository = repository

    def paper_card_json(self, paper_id: str) -> str:
        card = self.repository.get_card(paper_id)
        if card is None:
            raise KeyError(f"no Paper Card has been saved for paper: {paper_id}")
        return json.dumps(card.model_dump(mode="json"), ensure_ascii=False, indent=2)

    def reading_report_markdown(self, paper_id: str) -> str:
        card = self.repository.get_card(paper_id)
        if card is None:
            raise KeyError(f"no Paper Card has been saved for paper: {paper_id}")
        user_notes = self.repository.list_user_notes(paper_id)
        claim_reviews = self.repository.list_claim_reviews(paper_id)
        return render_deep_reading_markdown(
            card,
            user_notes=user_notes,
            claim_reviews=claim_reviews,
        )

    def paper_ris(self, paper_id: str) -> str:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        card = self.repository.get_card(paper_id)
        overrides = self.repository.get_metadata_overrides(paper_id)
        user_notes = self.repository.list_user_notes(paper_id)
        claim_reviews = self.repository.list_claim_reviews(paper_id)
        return render_paper_ris_record(
            paper,
            card=card,
            metadata_overrides=overrides,
            user_notes=user_notes,
            claim_reviews=claim_reviews,
        )

    def parsed_text(self, paper_id: str) -> str:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        blocks = self.repository.get_source_blocks(paper_id)
        return "\n\n".join(
            f"[PDF page {block.page_number} | source_block_id={block.id}]\n{block.text}"
            for block in blocks
        )

    def extract_references(self, paper_id: str) -> dict[str, Any]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        entries, mentions = extract_references(paper_id, self.repository.get_source_blocks(paper_id))
        self.repository.save_references(paper_id, entries, mentions)
        return {
            "references": [entry.model_dump(mode="json") for entry in entries],
            "citation_mentions": [mention.model_dump(mode="json") for mention in mentions],
        }

    def references_json(self, paper_id: str, *, extract_if_missing: bool = True) -> dict[str, Any]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        entries, mentions = self.repository.get_references(paper_id)
        if not entries and extract_if_missing:
            self.extract_references(paper_id)
            entries, mentions = self.repository.get_references(paper_id)
        return {
            "paper_id": paper_id,
            "references": [entry.model_dump(mode="json") for entry in entries],
            "citation_mentions": [mention.model_dump(mode="json") for mention in mentions],
        }

    def extract_references_batch(self, paper_ids: list[str] | None = None) -> dict[str, Any]:
        selected = paper_ids or [paper.id for paper in self.repository.list_papers()]
        results: list[dict[str, Any]] = []
        for paper_id in selected:
            paper = self.repository.get_paper(paper_id)
            if paper is None:
                results.append({"paper_id": paper_id, "status": "missing", "references": 0, "mentions": 0})
                continue
            try:
                payload = self.extract_references(paper_id)
                results.append(
                    {
                        "paper_id": paper_id,
                        "status": "ok",
                        "references": len(payload["references"]),
                        "mentions": len(payload["citation_mentions"]),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "paper_id": paper_id,
                        "status": "failed",
                        "references": 0,
                        "mentions": 0,
                        "error": str(exc),
                    }
                )
        return {
            "selected": len(selected),
            "completed": sum(item["status"] == "ok" for item in results),
            "failed": sum(item["status"] != "ok" for item in results),
            "results": results,
        }

    def references_export(self, paper_id: str, fmt: str) -> tuple[str, str]:
        payload = self.references_json(paper_id, extract_if_missing=True)
        if fmt == "json":
            return json.dumps(payload, ensure_ascii=False, indent=2), "application/json; charset=utf-8"
        if fmt != "bibtex":
            raise ValueError("reference export format must be json or bibtex")

        entries, _ = self.repository.get_references(paper_id)
        blocks: list[str] = []
        for index, entry in enumerate(entries, start=1):
            key = f"ref{entry.year or 'nd'}_{index}"
            lines = [f"@article{{{key},"]
            for name, value in (
                ("author", " and ".join(entry.authors)),
                ("title", entry.title),
                ("year", entry.year),
                ("doi", entry.doi),
                ("journal", entry.container_title),
                ("volume", entry.volume),
                ("number", entry.issue),
                ("pages", entry.pages),
                ("url", entry.url),
            ):
                if value not in (None, "", []):
                    escaped = str(value).replace("{", "\\{").replace("}", "\\}")
                    lines.append(f"  {name} = {{{escaped}}},")
            lines.append(f"  note = {{PDF page {entry.source_page}; review raw_text before citing}},")
            lines.append("}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) + ("\n" if blocks else ""), "application/x-bibtex; charset=utf-8"
