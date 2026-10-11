"""Versioned, provenance-checked researcher corrections for SRA region review.

Important trust boundary:
  - source_blocks, PDF bytes, paper_cards and model_runs are READ-ONLY here.
  - each correction revision is a new SQLite row, never an in-place text update.
  - superseded and revoked revisions remain available as an audit history.
  - exports are HUMAN_REVIEWED supplements, not replacement author quotations.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .region_review import PendingPreview, RegionReviewStore


class ReviewConflict(ValueError):
    """A source/preview/revision changed and must be verified again."""


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def block_fingerprint(block: Any) -> str:
    """Hash all persisted provenance fields, including table cell metadata."""
    values = {
        "id": block.id,
        "page_number": block.page_number,
        "text": block.text,
        "bbox": block.bbox,
        "table_rows": block.table_rows,
        "table_cells": getattr(block, "table_cells", None),
        "role": block.role,
        "source_label": block.source_label,
    }
    # Pydantic v2 serializes TableCellSpan and other nested models as JSON.
    canonical = json.dumps(
        {
            key: (
                [value.model_dump(mode="json") for value in item]
                if key == "table_cells" and item is not None
                else item
            )
            for key, item in values.items()
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_snapshots(blocks: list[Any], source_ids: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Return id->digest or fail closed if a source went missing."""
    seen = {block.id: block for block in blocks}
    if len(set(source_ids)) != len(source_ids):
        raise ReviewConflict("Duplicate SourceBlock identifiers in selected PDF region")
    if any(key not in seen for key in source_ids):
        raise ReviewConflict("Linked SourceBlock changed or disappeared; select region again")
    return {key: block_fingerprint(seen[key]) for key in source_ids}


def _legacy_safe_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["bounds"] = json.loads(data.pop("bounds_json"))
    data["source_block_ids"] = json.loads(data.pop("source_block_ids_json"))
    data["source_fingerprints"] = json.loads(data.pop("source_fingerprints_json") or "{}")
    return data


def _link_status(row: Mapping[str, Any], paper: Any,
                 live_fingerprints: Mapping[str, str]) -> str:
    if row["pdf_sha256"] != paper.sha256:
        return "PDF_CHANGED"
    if not row["parser_version"]:
        return "LEGACY_UNVERIFIED"
    if row["parser_version"] != paper.parser_version:
        return "PARSER_CHANGED"
    ids = row["source_block_ids"]
    hashes = row["source_fingerprints"]
    if not ids:
        return "PAGE_ONLY"
    if len(hashes) != len(ids) or any(
        not hashes.get(key) for key in ids
    ):
        return "LEGACY_UNVERIFIED"
    if any(live_fingerprints.get(key) != hashes[key] for key in ids):
        return "SOURCE_CHANGED"
    return "CURRENT"


def _overlapping_active_regions(first: Mapping[str, Any],
                                second: Mapping[str, Any]) -> bool:
    """Flag only physically overlapping corrections to the same SourceBlock."""
    if first["page_number"] != second["page_number"]:
        return False
    if not set(first["source_block_ids"]).intersection(second["source_block_ids"]):
        return False
    a, b = first["bounds"], second["bounds"]
    return (
        max(a["x0"], b["x0"]) < min(a["x1"], b["x1"])
        and max(a["y0"], b["y0"]) < min(a["y1"], b["y1"])
    )


def _fenced(text: str) -> str:
    """Make model-untrusted source content inert in a Markdown audit packet."""
    # Fence must be longer than runs in user-controlled text.
    import re
    runs = [len(match.group()) for match in re.finditer(r"`+", text)]
    fence = "`" * max(4, max(runs, default=0) + 1)
    return f"{fence}text\n{text}\n{fence}"


class VersionedRegionReviewStore(RegionReviewStore):
    """Additive upgrade over the v3 RegionReviewStore schema."""

    def __init__(self, database_path: Path):
        super().__init__(database_path)
        self._upgrade()

    def _upgrade(self):
        with self._connect() as conn:
            present = {
                row["name"] for row in conn.execute("PRAGMA table_info(region_reviews)")
            }
            fields = {
                "thread_id": "TEXT",
                "revision_no": "INTEGER NOT NULL DEFAULT 1",
                "supersedes_id": "TEXT",
                "superseded_at": "TEXT",
                "parser_version": "TEXT NOT NULL DEFAULT ''",
                "source_fingerprints_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for column, sql_type in fields.items():
                if column not in present:
                    conn.execute(f"ALTER TABLE region_reviews ADD COLUMN {column} {sql_type}")
            conn.execute(
                "UPDATE region_reviews SET thread_id = id WHERE thread_id IS NULL"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_region_revision_thread "
                "ON region_reviews(thread_id, revision_no)"
            )

    @staticmethod
    def _fingerprints_in_transaction(conn: sqlite3.Connection,
                                    ids: list[str]) -> dict[str, str]:
        """Observe current persisted SourceBlocks under the same SQLite write lock."""
        if not ids:
            return {}
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(source_blocks)")
        }
        has_spans = "table_cells_json" in columns
        rows = conn.execute(
            "SELECT id, page_number, text, bbox_json, table_rows_json, "
            + ("table_cells_json, " if has_spans else "")
            + "role, source_label FROM source_blocks WHERE id IN ("
            + ",".join("?" for _ in ids) + ")",
            ids,
        ).fetchall()
        from ..models import SourceBlock
        blocks = []
        for row in rows:
            blocks.append(SourceBlock(
                id=row["id"], page_number=row["page_number"], text=row["text"],
                bbox=json.loads(row["bbox_json"]) if row["bbox_json"] else None,
                table_rows=json.loads(row["table_rows_json"]) if row["table_rows_json"] else None,
                table_cells=(
                    json.loads(row["table_cells_json"])
                    if has_spans and row["table_cells_json"] else None
                ),
                role=row["role"] or "body",
                source_label=row["source_label"],
            ))
        return source_snapshots(blocks, ids)

    def add(self, paper_id: str, pending: PendingPreview,
            corrected_text: str, note: str) -> str:
        if not pending.parser_version:
            raise ReviewConflict("Preview lacks parser-version snapshot; select region again")
        fingerprints = dict(pending.source_fingerprints)
        if len(fingerprints) != len(pending.source_block_ids):
            raise ReviewConflict("Preview lacks source fingerprints; select region again")
        identifier = secrets.token_hex(16)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            paper = conn.execute(
                "SELECT sha256, parser_version FROM papers WHERE id=?", (paper_id,)
            ).fetchone()
            if paper is None or paper["sha256"] != pending.pdf_sha256 or (
                paper["parser_version"] != pending.parser_version
            ):
                raise ReviewConflict("PDF or parser changed after preview; select again")
            current = self._fingerprints_in_transaction(
                conn, list(pending.source_block_ids)
            )
            if current != fingerprints:
                raise ReviewConflict("SourceBlocks changed after preview; select again")
            conn.execute(
                """INSERT INTO region_reviews
                   (id,paper_id,pdf_sha256,page_number,bounds_json,
                    source_block_ids_json,extracted_text,ocr_candidate,corrected_text,
                    researcher_note,created_at,thread_id,revision_no,
                    parser_version,source_fingerprints_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identifier, paper_id, pending.pdf_sha256, pending.page_number,
                    pending.bounds.model_dump_json(),
                    json.dumps(pending.source_block_ids, ensure_ascii=False),
                    pending.original_text, pending.ocr_text, corrected_text, note,
                    _stamp(), identifier, 1, pending.parser_version,
                    json.dumps(fingerprints, ensure_ascii=False, sort_keys=True),
                ),
            )
        return identifier

    def revise(self, paper_id: str, review_id: str, expected_revision: int,
               corrected_text: str, note: str, paper: Any) -> dict[str, Any]:
        """Create vN+1 and archive vN, atomically and without changing evidence."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous_row = conn.execute(
                "SELECT * FROM region_reviews WHERE id=? AND paper_id=?",
                (review_id, paper_id),
            ).fetchone()
            if previous_row is None:
                raise KeyError("Revision not found")
            previous = _legacy_safe_row(previous_row)
            if (previous["revoked_at"] is not None
                    or previous["superseded_at"] is not None
                    or previous["revision_no"] != expected_revision):
                raise ReviewConflict("Revision is no longer active; refresh history")
            stored_paper = conn.execute(
                "SELECT sha256, parser_version FROM papers WHERE id=?", (paper_id,)
            ).fetchone()
            if (stored_paper is None or stored_paper["sha256"] != paper.sha256 or
                    stored_paper["parser_version"] != paper.parser_version):
                raise ReviewConflict("Paper changed; refresh review before editing")
            current = self._fingerprints_in_transaction(
                conn, previous["source_block_ids"]
            )
            state = _link_status(previous, paper, current)
            if state not in {"CURRENT", "PAGE_ONLY"}:
                raise ReviewConflict(
                    f"Source linkage is {state}; reselect and verify the PDF region"
                )
            identifier = secrets.token_hex(16)
            timestamp = _stamp()
            conn.execute(
                """INSERT INTO region_reviews
                   (id,paper_id,pdf_sha256,page_number,bounds_json,
                    source_block_ids_json,extracted_text,ocr_candidate,corrected_text,
                    researcher_note,created_at,thread_id,revision_no,
                    supersedes_id,parser_version,source_fingerprints_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identifier, paper_id, previous["pdf_sha256"],
                    previous["page_number"],
                    json.dumps(previous["bounds"], ensure_ascii=False),
                    json.dumps(previous["source_block_ids"], ensure_ascii=False),
                    previous["extracted_text"], previous["ocr_candidate"],
                    corrected_text, note, timestamp,
                    previous["thread_id"], previous["revision_no"] + 1,
                    previous["id"], previous["parser_version"],
                    json.dumps(previous["source_fingerprints"], ensure_ascii=False, sort_keys=True),
                ),
            )
            updated = conn.execute(
                "UPDATE region_reviews SET superseded_at=? "
                "WHERE id=? AND paper_id=? AND superseded_at IS NULL AND revoked_at IS NULL",
                (timestamp, review_id, paper_id),
            )
            if updated.rowcount != 1:
                raise ReviewConflict("Previous revision changed concurrently")
        return {
            "id": identifier,
            "thread_id": previous["thread_id"],
            "revision_no": previous["revision_no"] + 1,
            "supersedes_id": review_id,
            "provenance": "HUMAN_REVIEWED",
            "applied_to_evidence": False,
        }

    def revoke(self, paper_id: str, review_id: str) -> None:
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE region_reviews SET revoked_at=? WHERE id=? AND paper_id=? "
                "AND revoked_at IS NULL AND superseded_at IS NULL",
                (_stamp(), review_id, paper_id),
            )
        if result.rowcount != 1:
            raise KeyError("Active, non-superseded researcher review not found")

    def list_with_links(self, paper_id: str, paper: Any,
                        live_source_blocks: list[Any]) -> list[dict[str, Any]]:
        live = {block.id: block_fingerprint(block) for block in live_source_blocks}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM region_reviews WHERE paper_id=? "
                "ORDER BY created_at DESC, id DESC LIMIT 300", (paper_id,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            data = _legacy_safe_row(row)
            status = ("REVOKED" if data["revoked_at"] else
                      "SUPERSEDED" if data["superseded_at"] else "HUMAN_REVIEWED")
            data["provenance"] = status
            data["link_status"] = _link_status(data, paper, live)
            data["active"] = status == "HUMAN_REVIEWED"
            del data["source_fingerprints"]
            result.append(data)

        # Two active corrections covering the same source area may contradict
        # one another. Hold BOTH for researcher reconciliation; do not silently
        # decide which human text is authoritative.
        candidates = [
            item for item in result
            if item["active"] and item["link_status"] == "CURRENT"
        ]
        ambiguous = set()
        for index, first in enumerate(candidates):
            for second in candidates[index + 1:]:
                if _overlapping_active_regions(first, second):
                    ambiguous.update((first["id"], second["id"]))
        for item in result:
            if item["id"] in ambiguous:
                item["link_status"] = "OVERLAPPING_ACTIVE_REVIEW"
        return result

    def evidence_packet(self, paper_id: str, paper: Any,
                        live_source_blocks: list[Any]) -> dict[str, Any]:
        """Create a deterministic supplement. NO updates and NO AI model calls.

        CURRENT corrections appear only as *researcher commentary*; revisions
        without current SourceBlock linkage are listed separately, never merged
        into an author's quotation or sent to a model automatically.
        """
        reviews = self.list_with_links(paper_id, paper, live_source_blocks)
        included = [
            item for item in reviews
            if item["active"] and item["link_status"] == "CURRENT"
        ]
        held = [
            item for item in reviews
            if item["active"] and item["link_status"] != "CURRENT"
        ]
        lines = [
            "# SRA · 人工修订补充材料（非作者原文）",
            "",
            "来源约定：下列 corrected_text 是研究者确认的版本，"
            "不是 Docling 原始 SourceBlock，也没有被自动纳入 AI 分析。",
            f"论文 ID：`{paper_id}`",
            f"PDF SHA-256：`{paper.sha256}`",
            f"解析器版本：`{paper.parser_version}`",
            f"当前可关联修订：{len(included)} 项；暂不提供给证据核验的记录：{len(held)} 项",
            "",
        ]
        for index, item in enumerate(included, start=1):
            lines.extend([
                f"## 复核记录 {index} · PDF p.{item['page_number']} · v{item['revision_no']}",
                f"复核 ID：`{item['id']}`",
                "来源 SourceBlock：" + ", ".join(f"`{ref}`" for ref in item["source_block_ids"]),
                "PDF 框选区域（页面归一化坐标）：" + ", ".join(
                    f"{key}={item['bounds'][key]:.5f}"
                    for key in ("x0", "y0", "x1", "y1")
                ),
                "人工确认文字（HUMAN_REVIEWED，非作者原文）：",
                _fenced(item["corrected_text"]),
                "Docling 原始提取文字（需对照原 PDF）：",
                _fenced(item["extracted_text"]),
                "初始 OCR 候选（未核验，不等于原文）：",
                _fenced(item["ocr_candidate"] or "(人工抄录，无 OCR 候选)"),
                "研究者备注：",
                _fenced(item["researcher_note"] or "(无)"),
                "",
            ])
        if held:
            lines.extend([
                "## 需要人工重新关联的记录（不纳入当前证据补充）",
                "",
            ])
            for item in held:
                lines.append(
                    f"- p.{item['page_number']} · v{item['revision_no']} · "
                    f"`{item['id']}` · {item['link_status']}"
                )
        lines.extend([
            "",
            "**重要：**这份材料仅供研究者审查、复制或后续显式重新分析；"
            "不会修改原论文、原始证据块、Paper Card 或模型缓存。"
        ])
        markdown = "\n".join(lines) + "\n"
        return {
            "paper_id": paper_id,
            "included": len(included),
            "held_for_review": len(held),
            "provenance": "HUMAN_REVIEWED_SUPPLEMENT",
            "applied_to_evidence": False,
            "automatic_reanalysis": False,
            "markdown": markdown,
            "sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        }
