"""Read-only export views over persisted research memory."""

from __future__ import annotations

import json
from typing import Any

from ..analyzer import render_deep_reading_markdown
from ..references import extract_references
from ..repository import ResearchRepository


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
        return render_deep_reading_markdown(card)

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
