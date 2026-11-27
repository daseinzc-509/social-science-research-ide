"""Small SQLite repository for the single-user prototype."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .db import open_sqlite_connection, sqlite_connection
from .models import (
    FigureRegion,
    PaperCard,
    PaperMetadata,
    PaperRecord,
    ParsedDocument,
    ProjectPaperEntry,
    ProjectReadingStatus,
    ResearchProject,
    SourceBlock,
)
from .search import (
    ensure_fts_schema,
    rebuild_all_fts,
    search_documents,
    sync_paper_fts,
    sync_project_notes_fts,
)


class ResearchRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return open_sqlite_connection(self.database_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with sqlite_connection(self.database_path) as connection:
            yield connection

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS papers (
                    id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    original_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    page_count INTEGER NOT NULL CHECK (page_count > 0),
                    parse_status TEXT NOT NULL,
                    parser_version TEXT NOT NULL DEFAULT 'legacy',
                    warnings_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_blocks (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    text TEXT NOT NULL,
                    bbox_json TEXT,
                    table_rows_json TEXT,
                    role TEXT NOT NULL DEFAULT 'body',
                    source_label TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_source_blocks_paper_page
                    ON source_blocks(paper_id, page_number);
                CREATE TABLE IF NOT EXISTS figure_regions (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    page_number INTEGER NOT NULL CHECK (page_number > 0),
                    bbox_json TEXT,
                    caption TEXT NOT NULL DEFAULT '',
                    source_label TEXT NOT NULL DEFAULT 'docling:picture'
                );
                CREATE INDEX IF NOT EXISTS idx_figure_regions_paper_page
                    ON figure_regions(paper_id, page_number);
                CREATE TABLE IF NOT EXISTS paper_cards (
                    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                    card_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS user_notes (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    title TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL,
                    page_number INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS claim_reviews (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    claim_id TEXT NOT NULL,
                    field_name TEXT NOT NULL DEFAULT '',
                    original_statement TEXT NOT NULL DEFAULT '',
                    user_status TEXT NOT NULL CHECK (user_status IN ('confirmed', 'corrected', 'rejected', 'unresolved')),
                    corrected_statement TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (paper_id, claim_id)
                );
                CREATE INDEX IF NOT EXISTS idx_claim_reviews_paper
                    ON claim_reviews(paper_id, updated_at);
                CREATE TABLE IF NOT EXISTS model_runs (
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (paper_id, stage, model, prompt_version, input_sha256)
                );
                CREATE TABLE IF NOT EXISTS paper_metadata_overrides (
                    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                    metadata_json TEXT NOT NULL,
                    source_note TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS reference_entries (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
                    raw_text TEXT NOT NULL,
                    entry_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (paper_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS citation_mentions (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    mention_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_reference_entries_paper
                    ON reference_entries(paper_id, ordinal);
                CREATE INDEX IF NOT EXISTS idx_citation_mentions_paper
                    ON citation_mentions(paper_id);
                CREATE TABLE IF NOT EXISTS research_projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    description TEXT NOT NULL DEFAULT '',
                    research_question TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_papers (
                    project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    reading_status TEXT NOT NULL DEFAULT 'queued',
                    project_note TEXT NOT NULL DEFAULT '',
                    position INTEGER NOT NULL DEFAULT 0,
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, paper_id)
                );
                CREATE INDEX IF NOT EXISTS idx_project_papers_project_pos
                    ON project_papers(project_id, position, added_at);
                CREATE INDEX IF NOT EXISTS idx_project_papers_paper
                    ON project_papers(paper_id);
                CREATE TABLE IF NOT EXISTS project_notes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
                    title TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_project_notes_project
                    ON project_notes(project_id, updated_at);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_blocks)")}
            if "table_rows_json" not in columns:
                connection.execute("ALTER TABLE source_blocks ADD COLUMN table_rows_json TEXT")
            if "table_cells_json" not in columns:
                connection.execute("ALTER TABLE source_blocks ADD COLUMN table_cells_json TEXT")
            if "role" not in columns:
                connection.execute("ALTER TABLE source_blocks ADD COLUMN role TEXT NOT NULL DEFAULT 'body'")
            if "source_label" not in columns:
                connection.execute("ALTER TABLE source_blocks ADD COLUMN source_label TEXT")
            paper_columns = {row["name"] for row in connection.execute("PRAGMA table_info(papers)")}
            if "parser_version" not in paper_columns:
                connection.execute("ALTER TABLE papers ADD COLUMN parser_version TEXT NOT NULL DEFAULT 'legacy'")
            note_columns = {row["name"] for row in connection.execute("PRAGMA table_info(user_notes)")}
            if "title" not in note_columns:
                connection.execute("ALTER TABLE user_notes ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            if "page_number" not in note_columns:
                connection.execute("ALTER TABLE user_notes ADD COLUMN page_number INTEGER")
            if "updated_at" not in note_columns:
                connection.execute("ALTER TABLE user_notes ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
            if ensure_fts_schema(connection):
                has_papers = connection.execute("SELECT 1 FROM papers LIMIT 1").fetchone() is not None
                has_fts = connection.execute("SELECT 1 FROM search_fts LIMIT 1").fetchone() is not None
                if has_papers and not has_fts:
                    rebuild_all_fts(connection)

    def get_by_sha256(self, sha256: str) -> PaperRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM papers WHERE sha256 = ?", (sha256,)).fetchone()
        return self._paper_from_row(row) if row else None

    def save_import(self, record: PaperRecord, document: ParsedDocument) -> PaperRecord:
        if record.id != document.paper_id or record.sha256 != document.sha256:
            raise ValueError("paper record and parsed document identifiers must match")
        with self._connection() as connection:
            existing = connection.execute("SELECT * FROM papers WHERE sha256 = ?", (record.sha256,)).fetchone()
            if existing:
                return self._paper_from_row(existing)
            connection.execute(
                """INSERT INTO papers
                (id, sha256, original_name, stored_path, page_count, parse_status, parser_version, warnings_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.sha256, record.original_name, record.stored_path, record.page_count,
                 record.parse_status.value, record.parser_version,
                 json.dumps(record.warnings, ensure_ascii=False), record.created_at),
            )
            connection.executemany(
                "INSERT INTO source_blocks (id, paper_id, page_number, text, bbox_json, table_rows_json, table_cells_json, role, source_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (block.id, record.id, block.page_number, block.text,
                     json.dumps(block.bbox) if block.bbox is not None else None,
                     json.dumps(block.table_rows, ensure_ascii=False) if block.table_rows is not None else None,
                     json.dumps([cell.model_dump(mode="json") for cell in block.table_cells], ensure_ascii=False)
                     if block.table_cells is not None else None,
                     block.role, block.source_label)
                    for block in document.blocks
                ],
            )
            connection.executemany(
                """INSERT INTO figure_regions
                (id, paper_id, page_number, bbox_json, caption, source_label)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (figure.id, record.id, figure.page_number,
                     json.dumps(figure.bbox) if figure.bbox is not None else None,
                     figure.caption, figure.source_label)
                    for figure in document.figures
                ],
            )
            sync_paper_fts(connection, record.id)
        return record

    def refresh_parsed_document(self, paper_id: str, document: ParsedDocument) -> None:
        if document.paper_id != paper_id:
            raise ValueError("parsed document paper id does not match")
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not exists:
                raise KeyError(f"unknown paper id: {paper_id}")
            connection.execute("DELETE FROM source_blocks WHERE paper_id = ?", (paper_id,))
            connection.execute("DELETE FROM figure_regions WHERE paper_id = ?", (paper_id,))
            connection.executemany(
                """INSERT INTO source_blocks
                (id, paper_id, page_number, text, bbox_json, table_rows_json, table_cells_json, role, source_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (block.id, paper_id, block.page_number, block.text,
                     json.dumps(block.bbox) if block.bbox is not None else None,
                     json.dumps(block.table_rows, ensure_ascii=False) if block.table_rows is not None else None,
                     json.dumps([cell.model_dump(mode="json") for cell in block.table_cells], ensure_ascii=False)
                     if block.table_cells is not None else None,
                     block.role, block.source_label)
                    for block in document.blocks
                ],
            )
            connection.executemany(
                """INSERT INTO figure_regions
                (id, paper_id, page_number, bbox_json, caption, source_label)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (figure.id, paper_id, figure.page_number,
                     json.dumps(figure.bbox) if figure.bbox is not None else None,
                     figure.caption, figure.source_label)
                    for figure in document.figures
                ],
            )
            connection.execute(
                "UPDATE papers SET page_count = ?, parse_status = ?, parser_version = ?, warnings_json = ? WHERE id = ?",
                (document.page_count, document.status.value, document.parser_version,
                 json.dumps(document.warnings, ensure_ascii=False), paper_id),
            )
            sync_paper_fts(connection, paper_id)

    def list_papers(self) -> list[PaperRecord]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM papers ORDER BY created_at DESC").fetchall()
        return [self._paper_from_row(row) for row in rows]

    def get_paper(self, paper_id: str) -> PaperRecord | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return self._paper_from_row(row) if row else None

    def save_card(self, card: PaperCard) -> None:
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM papers WHERE id = ?", (card.paper_id,)).fetchone()
            if not exists:
                raise KeyError(f"unknown paper id: {card.paper_id}")
            connection.execute(
                """INSERT INTO paper_cards (paper_id, card_json) VALUES (?, ?)
                ON CONFLICT(paper_id) DO UPDATE SET card_json = excluded.card_json,
                updated_at = CURRENT_TIMESTAMP""",
                (card.paper_id, card.model_dump_json()),
            )
            sync_paper_fts(connection, card.paper_id)

    def get_card(self, paper_id: str) -> PaperCard | None:
        with self._connection() as connection:
            row = connection.execute("SELECT card_json FROM paper_cards WHERE paper_id = ?", (paper_id,)).fetchone()
        if not row:
            return None
        card = PaperCard.model_validate_json(row[0])
        override = self.get_metadata_overrides(paper_id)
        values = override.get("values", {}) if override else {}
        if values:
            merged = {**card.metadata.model_dump(mode="python"), **values}
            metadata = PaperMetadata.model_validate(merged)
            card = card.model_copy(update={"metadata": metadata})
        return card

    def get_metadata_overrides(self, paper_id: str) -> dict[str, object]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT metadata_json, source_note, updated_at FROM paper_metadata_overrides WHERE paper_id = ?",
                (paper_id,),
            ).fetchone()
        if row is None:
            return {"values": {}, "source_note": None, "updated_at": None}
        values = json.loads(row["metadata_json"])
        return {
            "values": values if isinstance(values, dict) else {},
            "source_note": row["source_note"],
            "updated_at": row["updated_at"],
        }

    def save_metadata_overrides(
        self, paper_id: str, values: dict[str, object], *, source_note: str | None = None
    ) -> dict[str, object]:
        allowed = {"title", "authors", "year", "journal", "volume", "issue", "pages", "doi", "keywords"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported metadata override field(s): {', '.join(sorted(unknown))}")
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not exists:
                raise KeyError(f"unknown paper id: {paper_id}")
            card_row = connection.execute(
                "SELECT card_json FROM paper_cards WHERE paper_id = ?", (paper_id,)
            ).fetchone()
            base = PaperMetadata()
            if card_row is not None:
                base = PaperCard.model_validate_json(card_row[0]).metadata
            # Validate the effective metadata, but persist only fields that actually
            # differ from automatic extraction. This keeps future re-analysis free to
            # update untouched fields instead of silently freezing the whole form.
            base_values = base.model_dump(mode="python")
            validated = PaperMetadata.model_validate({**base_values, **values})
            validated_values = validated.model_dump(mode="python")
            effective_overrides = {
                key: validated_values[key]
                for key in values
                if validated_values.get(key) != base_values.get(key)
            }
            note = (source_note or "").strip() or None
            if not effective_overrides and note is None:
                connection.execute(
                    "DELETE FROM paper_metadata_overrides WHERE paper_id = ?", (paper_id,)
                )
            else:
                connection.execute(
                    """INSERT INTO paper_metadata_overrides (paper_id, metadata_json, source_note)
                    VALUES (?, ?, ?)
                    ON CONFLICT(paper_id) DO UPDATE SET
                        metadata_json = excluded.metadata_json,
                        source_note = excluded.source_note,
                        updated_at = CURRENT_TIMESTAMP""",
                    (paper_id, json.dumps(effective_overrides, ensure_ascii=False), note),
                )
            sync_paper_fts(connection, paper_id)
        return self.get_metadata_overrides(paper_id)

    def clear_metadata_overrides(self, paper_id: str) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM paper_metadata_overrides WHERE paper_id = ?", (paper_id,))
            sync_paper_fts(connection, paper_id)

    def get_source_blocks(self, paper_id: str) -> list[SourceBlock]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, page_number, text, bbox_json, table_rows_json, table_cells_json, role, source_label FROM source_blocks "
                "WHERE paper_id = ? ORDER BY page_number, rowid", (paper_id,)
            ).fetchall()
        return [
            SourceBlock(
                id=row["id"], page_number=row["page_number"], text=row["text"],
                bbox=tuple(json.loads(row["bbox_json"])) if row["bbox_json"] else None,
                table_rows=json.loads(row["table_rows_json"]) if row["table_rows_json"] else None,
                table_cells=json.loads(row["table_cells_json"]) if row["table_cells_json"] else None,
                role=row["role"] or "body",
                source_label=row["source_label"],
            )
            for row in rows
        ]

    def get_figures(self, paper_id: str) -> list[FigureRegion]:
        """Return only located figure references, never guessed image contents."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, page_number, bbox_json, caption, source_label "
                "FROM figure_regions WHERE paper_id = ? ORDER BY page_number, rowid",
                (paper_id,),
            ).fetchall()
        return [
            FigureRegion(
                id=row["id"],
                page_number=row["page_number"],
                bbox=tuple(json.loads(row["bbox_json"])) if row["bbox_json"] else None,
                caption=row["caption"],
                source_label=row["source_label"],
            )
            for row in rows
        ]

    def get_model_run(self, paper_id: str, stage: str, model: str, prompt_version: str, input_sha256: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT output_json FROM model_runs WHERE paper_id = ? AND stage = ? AND model = ?
                AND prompt_version = ? AND input_sha256 = ?""",
                (paper_id, stage, model, prompt_version, input_sha256),
            ).fetchone()
        return row[0] if row else None

    def save_model_run(
        self, paper_id: str, stage: str, model: str, prompt_version: str, input_sha256: str, output_json: str
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO model_runs
                (paper_id, stage, model, prompt_version, input_sha256, output_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_id, stage, model, prompt_version, input_sha256)
                DO UPDATE SET output_json = excluded.output_json, created_at = CURRENT_TIMESTAMP""",
                (paper_id, stage, model, prompt_version, input_sha256, output_json),
            )

    def diagnose(self, papers_dir: Path, *, current_prompt_version: str | None = None, deep: bool = False) -> dict[str, object]:
        """Return a read-only health report for the repository and stored PDF directory."""
        papers_dir = Path(papers_dir).expanduser().resolve()
        database_path = self.database_path.resolve()

        with self._connection() as connection:
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("papers", "source_blocks", "paper_cards", "user_notes", "model_runs")
            }
            integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchall()]
            foreign_key_rows = [
                {
                    "table": str(row[0]),
                    "rowid": row[1],
                    "parent": str(row[2]),
                    "fkid": row[3],
                }
                for row in connection.execute("PRAGMA foreign_key_check").fetchall()
            ]
            paper_rows = connection.execute(
                "SELECT id, sha256, original_name, stored_path FROM papers ORDER BY created_at"
            ).fetchall()
            card_rows = connection.execute("SELECT paper_id, card_json FROM paper_cards").fetchall()
            empty_source_papers = [
                str(row["id"])
                for row in connection.execute(
                    """SELECT p.id FROM papers p
                    LEFT JOIN source_blocks s ON s.paper_id = p.id
                    GROUP BY p.id HAVING COUNT(s.id) = 0"""
                ).fetchall()
            ]
            papers_without_cards = [
                str(row["id"])
                for row in connection.execute(
                    """SELECT p.id FROM papers p
                    LEFT JOIN paper_cards c ON c.paper_id = p.id
                    WHERE c.paper_id IS NULL ORDER BY p.created_at"""
                ).fetchall()
            ]
            prompt_versions = {
                str(row["prompt_version"]): int(row["run_count"])
                for row in connection.execute(
                    "SELECT prompt_version, COUNT(*) AS run_count FROM model_runs GROUP BY prompt_version ORDER BY prompt_version"
                ).fetchall()
            }
            model_cache_bytes = int(
                connection.execute(
                    "SELECT COALESCE(SUM(length(CAST(output_json AS BLOB))), 0) FROM model_runs"
                ).fetchone()[0]
            )
            card_bytes = int(
                connection.execute(
                    "SELECT COALESCE(SUM(length(CAST(card_json AS BLOB))), 0) FROM paper_cards"
                ).fetchone()[0]
            )
            source_text_bytes = int(
                connection.execute(
                    "SELECT COALESCE(SUM(length(CAST(text AS BLOB))), 0) FROM source_blocks"
                ).fetchone()[0]
            )
            if current_prompt_version is None:
                stale_cache_runs = 0
                stale_cache_bytes = 0
            else:
                stale_row = connection.execute(
                    """SELECT COUNT(*) AS run_count,
                    COALESCE(SUM(length(CAST(output_json AS BLOB))), 0) AS byte_count
                    FROM model_runs WHERE prompt_version <> ?""",
                    (current_prompt_version,),
                ).fetchone()
                stale_cache_runs = int(stale_row["run_count"])
                stale_cache_bytes = int(stale_row["byte_count"])

        invalid_cards: list[dict[str, str]] = []
        for row in card_rows:
            try:
                PaperCard.model_validate_json(row["card_json"])
            except (ValueError, TypeError) as exc:
                invalid_cards.append({"paper_id": str(row["paper_id"]), "error": str(exc)[:500]})

        missing_pdfs: list[dict[str, str]] = []
        hash_mismatches: list[dict[str, str]] = []
        referenced_paths: set[Path] = set()
        referenced_pdf_bytes = 0
        duplicate_stored_paths: dict[str, list[str]] = {}
        path_to_ids: dict[Path, list[str]] = {}

        for row in paper_rows:
            stored_path = Path(str(row["stored_path"])).expanduser().resolve()
            referenced_paths.add(stored_path)
            path_to_ids.setdefault(stored_path, []).append(str(row["id"]))
            if not stored_path.is_file():
                missing_pdfs.append({"paper_id": str(row["id"]), "path": str(stored_path)})
                continue
            referenced_pdf_bytes += stored_path.stat().st_size
            if deep:
                actual_sha256 = self._sha256_file(stored_path)
                expected_sha256 = str(row["sha256"])
                if actual_sha256 != expected_sha256:
                    hash_mismatches.append(
                        {
                            "paper_id": str(row["id"]),
                            "path": str(stored_path),
                            "expected_sha256": expected_sha256,
                            "actual_sha256": actual_sha256,
                        }
                    )

        for stored_path, paper_ids in path_to_ids.items():
            if len(paper_ids) > 1:
                duplicate_stored_paths[str(stored_path)] = paper_ids

        disk_pdfs = sorted(path.resolve() for path in papers_dir.glob("*.pdf") if path.is_file()) if papers_dir.is_dir() else []
        untracked_pdfs = [str(path) for path in disk_pdfs if path not in referenced_paths]
        untracked_pdf_bytes = sum(Path(path).stat().st_size for path in untracked_pdfs)
        papers_dir_bytes = sum(path.stat().st_size for path in disk_pdfs)

        integrity_ok = integrity_rows == ["ok"]
        issues = {
            "integrity_errors": [] if integrity_ok else integrity_rows,
            "foreign_key_errors": foreign_key_rows,
            "missing_pdfs": missing_pdfs,
            "hash_mismatches": hash_mismatches,
            "invalid_cards": invalid_cards,
            "papers_without_source_blocks": empty_source_papers,
            "duplicate_stored_paths": duplicate_stored_paths,
            "untracked_pdfs": untracked_pdfs,
        }
        issue_count = sum(
            len(value) if isinstance(value, list) else len(value.keys())
            for value in issues.values()
        )

        return {
            "status": "ok" if issue_count == 0 else "review",
            "deep": deep,
            "database_path": str(database_path),
            "database_bytes": database_path.stat().st_size if database_path.is_file() else 0,
            "papers_dir": str(papers_dir),
            "counts": counts,
            "storage": {
                "referenced_pdf_bytes": referenced_pdf_bytes,
                "papers_dir_pdf_bytes": papers_dir_bytes,
                "untracked_pdf_bytes": untracked_pdf_bytes,
                "source_text_bytes": source_text_bytes,
                "paper_card_json_bytes": card_bytes,
                "model_cache_json_bytes": model_cache_bytes,
            },
            "cache": {
                "current_prompt_version": current_prompt_version,
                "prompt_versions": prompt_versions,
                "stale_runs": stale_cache_runs,
                "stale_bytes": stale_cache_bytes,
            },
            "papers_without_cards": papers_without_cards,
            "issues": issues,
        }

    def prune_model_runs(
        self,
        *,
        current_prompt_version: str | None,
        all_runs: bool = False,
        apply: bool = False,
        vacuum: bool = False,
    ) -> dict[str, object]:
        """Preview or delete model cache rows without touching papers, cards, blocks, or notes."""
        if vacuum and not apply:
            raise ValueError("--vacuum requires --yes because it changes the database file")
        if not all_runs and not current_prompt_version:
            raise ValueError("current_prompt_version is required unless all_runs=True")

        where_sql = "" if all_runs else " WHERE prompt_version <> ?"
        params: tuple[object, ...] = () if all_runs else (current_prompt_version,)
        database_before = self.database_path.stat().st_size if self.database_path.is_file() else 0

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) AS run_count, COALESCE(SUM(length(CAST(output_json AS BLOB))), 0) AS byte_count "
                f"FROM model_runs{where_sql}",
                params,
            ).fetchone()
            matched_runs = int(row["run_count"])
            matched_bytes = int(row["byte_count"])
            total_before = int(connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0])

            deleted_runs = 0
            if apply:
                if matched_runs:
                    cursor = connection.execute(f"DELETE FROM model_runs{where_sql}", params)
                    deleted_runs = int(cursor.rowcount if cursor.rowcount >= 0 else matched_runs)
                    connection.commit()
                if vacuum:
                    connection.execute("VACUUM")
            total_after = int(connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0])
        finally:
            connection.close()

        database_after = self.database_path.stat().st_size if self.database_path.is_file() else 0
        return {
            "mode": "all" if all_runs else "stale",
            "current_prompt_version": current_prompt_version,
            "apply": apply,
            "vacuum": vacuum,
            "matched_runs": matched_runs,
            "matched_bytes": matched_bytes,
            "deleted_runs": deleted_runs,
            "total_runs_before": total_before,
            "total_runs_after": total_after,
            "database_bytes_before": database_before,
            "database_bytes_after": database_after,
        }

    def refresh_parsed_document_and_invalidate(self, paper_id: str, document: ParsedDocument) -> dict[str, int]:
        """Refresh parser output and remove analysis derived from the old parse.

        Source blocks are the evidence base for Paper Cards. When parser output
        changes, old cards/model runs may point at obsolete block ids.
        """
        self.refresh_parsed_document(paper_id, document)
        return self.clear_derived_analysis(paper_id)

    def clear_derived_analysis(self, paper_id: str) -> dict[str, int]:
        """Delete the current Paper Card and model cache for one paper.

        Parsed source blocks and user notes are intentionally preserved. This is
        used after an explicit reparse so the next analysis cannot accidentally
        reuse a Card or model result derived from an older parse.
        """
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if not exists:
                raise KeyError(f"unknown paper id: {paper_id}")
            card_count = connection.execute(
                "SELECT COUNT(*) FROM paper_cards WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            model_run_count = connection.execute(
                "SELECT COUNT(*) FROM model_runs WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            connection.execute("DELETE FROM paper_cards WHERE paper_id = ?", (paper_id,))
            connection.execute("DELETE FROM model_runs WHERE paper_id = ?", (paper_id,))
            sync_paper_fts(connection, paper_id)
        return {
            "paper_cards": int(card_count),
            "model_runs": int(model_run_count),
        }

    def delete_paper(self, paper_id: str) -> dict[str, object]:
        """Delete one paper and every database row owned by it.

        SQLite foreign-key cascades remove source blocks, Paper Card, model cache
        and user notes. The PDF file itself is deliberately handled by the caller
        so filesystem deletion can be guarded separately.
        """
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM papers WHERE id = ?", (paper_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown paper id: {paper_id}")
            counts = {
                "source_blocks": connection.execute(
                    "SELECT COUNT(*) FROM source_blocks WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
                "paper_cards": connection.execute(
                    "SELECT COUNT(*) FROM paper_cards WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
                "model_runs": connection.execute(
                    "SELECT COUNT(*) FROM model_runs WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
                "user_notes": connection.execute(
                    "SELECT COUNT(*) FROM user_notes WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
                "claim_reviews": connection.execute(
                    "SELECT COUNT(*) FROM claim_reviews WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
                "metadata_overrides": connection.execute(
                    "SELECT COUNT(*) FROM paper_metadata_overrides WHERE paper_id = ?", (paper_id,)
                ).fetchone()[0],
            }
            stored_path = row["stored_path"]
            original_name = row["original_name"]
            connection.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
            sync_paper_fts(connection, paper_id)
        return {
            "paper_id": paper_id,
            "original_name": original_name,
            "stored_path": stored_path,
            **{key: int(value) for key, value in counts.items()},
        }

    @staticmethod
    def _note_from_row(row: sqlite3.Row):
        from .models import UserNote

        created = str(row["created_at"] or "")
        updated = str(row["updated_at"] or "") or created
        raw_page = row["page_number"]
        return UserNote(
            id=str(row["id"]),
            paper_id=str(row["paper_id"]),
            title=str(row["title"] or ""),
            note=str(row["note"]),
            page_number=int(raw_page) if raw_page is not None else None,
            created_at=created,
            updated_at=updated,
        )

    def list_user_notes(self, paper_id: str) -> list:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, paper_id, title, note, page_number, created_at, updated_at
                FROM user_notes
                WHERE paper_id = ?
                ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, rowid DESC
                """,
                (paper_id,),
            ).fetchall()
        return [self._note_from_row(row) for row in rows]

    def add_user_note(
        self,
        paper_id: str,
        note_id: str,
        note: str,
        *,
        title: str = "",
        page_number: int | None = None,
    ):
        clean_note = (note or "").strip()
        if not clean_note:
            raise ValueError("user note cannot be blank")
        clean_title = (title or "").strip()
        if page_number is not None and page_number < 1:
            raise ValueError("page_number must be >= 1")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not exists:
                raise KeyError(f"unknown paper id: {paper_id}")
            connection.execute(
                """
                INSERT INTO user_notes (id, paper_id, title, note, page_number, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (note_id, paper_id, clean_title, clean_note, page_number, now, now),
            )
            sync_paper_fts(connection, paper_id)
            row = connection.execute(
                "SELECT id, paper_id, title, note, page_number, created_at, updated_at FROM user_notes WHERE id = ?",
                (note_id,),
            ).fetchone()
        return self._note_from_row(row)

    def update_user_note(
        self,
        paper_id: str,
        note_id: str,
        *,
        title: str | None = None,
        note: str | None = None,
        page_number: int | None = None,
        update_page_number: bool = False,
    ):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, paper_id, title, note, page_number, created_at, updated_at
                FROM user_notes
                WHERE id = ? AND paper_id = ?
                """,
                (note_id, paper_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown user note id: {note_id}")
            next_title = str(row["title"] or "") if title is None else title.strip()
            next_note = str(row["note"]) if note is None else note.strip()
            if not next_note:
                raise ValueError("user note cannot be blank")
            next_page = row["page_number"] if not update_page_number else page_number
            if next_page is not None and int(next_page) < 1:
                raise ValueError("page_number must be >= 1")
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                UPDATE user_notes
                SET title = ?, note = ?, page_number = ?, updated_at = ?
                WHERE id = ? AND paper_id = ?
                """,
                (next_title, next_note, next_page, now, note_id, paper_id),
            )
            sync_paper_fts(connection, paper_id)
            updated = connection.execute(
                "SELECT id, paper_id, title, note, page_number, created_at, updated_at FROM user_notes WHERE id = ?",
                (note_id,),
            ).fetchone()
        return self._note_from_row(updated)

    def delete_user_note(self, paper_id: str, note_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM user_notes WHERE id = ? AND paper_id = ?",
                (note_id, paper_id),
            )
            if cursor.rowcount > 0:
                sync_paper_fts(connection, paper_id)
                return True
            return False

    @staticmethod
    def _claim_review_from_row(row: sqlite3.Row):
        from .models import ClaimReviewDecision, ClaimReviewStatus

        return ClaimReviewDecision(
            id=str(row["id"]),
            paper_id=str(row["paper_id"]),
            claim_id=str(row["claim_id"]),
            field_name=str(row["field_name"] or ""),
            original_statement=str(row["original_statement"] or ""),
            user_status=ClaimReviewStatus(str(row["user_status"])),
            corrected_statement=str(row["corrected_statement"] or ""),
            note=str(row["note"] or ""),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_claim_reviews(self, paper_id: str) -> list:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, paper_id, claim_id, field_name, original_statement,
                       user_status, corrected_statement, note, created_at, updated_at
                FROM claim_reviews
                WHERE paper_id = ?
                ORDER BY updated_at DESC, rowid DESC
                """,
                (paper_id,),
            ).fetchall()
        return [self._claim_review_from_row(row) for row in rows]

    def upsert_claim_review(
        self,
        paper_id: str,
        claim_id: str,
        *,
        user_status: str,
        field_name: str = "",
        original_statement: str = "",
        corrected_statement: str = "",
        note: str = "",
    ):
        from .models import ClaimReviewStatus

        clean_claim_id = (claim_id or "").strip()
        if not clean_claim_id:
            raise ValueError("claim_id cannot be blank")
        try:
            status_enum = ClaimReviewStatus(str(user_status).strip().lower())
        except ValueError as exc:
            raise ValueError(f"unsupported claim review status: {user_status!r}") from exc

        clean_corrected = (corrected_statement or "").strip()
        if status_enum == ClaimReviewStatus.CORRECTED and not clean_corrected:
            raise ValueError("corrected status requires a non-empty corrected_statement")
        clean_note = (note or "").strip()
        clean_field = (field_name or "").strip()
        clean_original = (original_statement or "").strip()
        now = datetime.now(timezone.utc).isoformat()
        review_id = f"crev-{uuid4().hex[:12]}"

        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not exists:
                raise KeyError(f"unknown paper id: {paper_id}")
            connection.execute(
                """
                INSERT INTO claim_reviews (
                    id, paper_id, claim_id, field_name, original_statement,
                    user_status, corrected_statement, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_id, claim_id) DO UPDATE SET
                    field_name = CASE WHEN excluded.field_name != '' THEN excluded.field_name ELSE claim_reviews.field_name END,
                    original_statement = CASE WHEN excluded.original_statement != '' THEN excluded.original_statement ELSE claim_reviews.original_statement END,
                    user_status = excluded.user_status,
                    corrected_statement = excluded.corrected_statement,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    review_id,
                    paper_id,
                    clean_claim_id,
                    clean_field,
                    clean_original,
                    status_enum.value,
                    clean_corrected,
                    clean_note,
                    now,
                    now,
                ),
            )
            sync_paper_fts(connection, paper_id)
            row = connection.execute(
                """
                SELECT id, paper_id, claim_id, field_name, original_statement,
                       user_status, corrected_statement, note, created_at, updated_at
                FROM claim_reviews
                WHERE paper_id = ? AND claim_id = ?
                """,
                (paper_id, clean_claim_id),
            ).fetchone()
        return self._claim_review_from_row(row)

    def delete_claim_review(self, paper_id: str, claim_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM claim_reviews WHERE paper_id = ? AND claim_id = ?",
                (paper_id, claim_id),
            )
            if cursor.rowcount > 0:
                sync_paper_fts(connection, paper_id)
                return True
            return False

    def count_claim_reviews_by_paper(self) -> dict[str, int]:
        """按论文批量统计已人工审定 Claim 条数（单条聚合 SQL，避免列表页 N+1 查询）。"""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT paper_id, COUNT(*) AS cnt FROM claim_reviews GROUP BY paper_id"
            ).fetchall()
        return {str(row["paper_id"]): int(row["cnt"]) for row in rows}

    def count_user_notes_by_paper(self) -> dict[str, int]:
        """按论文批量统计个人阅读笔记条数（单条聚合 SQL，避免列表页 N+1 查询）。"""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT paper_id, COUNT(*) AS cnt FROM user_notes GROUP BY paper_id"
            ).fetchall()
        return {str(row["paper_id"]): int(row["cnt"]) for row in rows}

    def save_references(self, paper_id: str, entries: list, mentions: list) -> None:
        """Replace extracted references for one paper; raw source text remains in source_blocks."""
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
                raise KeyError(f"unknown paper id: {paper_id}")
            connection.execute("DELETE FROM reference_entries WHERE paper_id = ?", (paper_id,))
            connection.execute("DELETE FROM citation_mentions WHERE paper_id = ?", (paper_id,))
            connection.executemany(
                "INSERT INTO reference_entries (id, paper_id, ordinal, raw_text, entry_json) VALUES (?, ?, ?, ?, ?)",
                [(entry.id, paper_id, entry.ordinal, entry.raw_text, entry.model_dump_json()) for entry in entries],
            )
            connection.executemany(
                "INSERT INTO citation_mentions (id, paper_id, mention_json) VALUES (?, ?, ?)",
                [(mention.id, paper_id, mention.model_dump_json()) for mention in mentions],
            )

    def get_references(self, paper_id: str) -> tuple[list, list]:
        from .models import CitationMention, ReferenceEntry

        with self._connection() as connection:
            entries = connection.execute(
                "SELECT entry_json FROM reference_entries WHERE paper_id = ? ORDER BY ordinal", (paper_id,)
            ).fetchall()
            mentions = connection.execute(
                "SELECT mention_json FROM citation_mentions WHERE paper_id = ? ORDER BY rowid", (paper_id,)
            ).fetchall()
        return (
            [ReferenceEntry.model_validate_json(row[0]) for row in entries],
            [CitationMention.model_validate_json(row[0]) for row in mentions],
        )

    def sync_paper_search_index(self, paper_id: str) -> int:
        """同步单篇论文的 FTS5 索引（供局部复核等外部表更新后触发）。"""
        with self._connection() as connection:
            return sync_paper_fts(connection, paper_id)

    def rebuild_search_index(self) -> int:
        """全量重建本地研究库的 SQLite FTS5 全文索引。"""
        with self._connection() as connection:
            return rebuild_all_fts(connection)

    def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        source_kinds: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        """跨文献全文检索，支持按研究项目（project_id）或来源类别过滤。"""
        with self._connection() as connection:
            scoped_ids: set[str] | None = None
            if project_id is not None:
                proj = connection.execute(
                    "SELECT 1 FROM research_projects WHERE id = ?",
                    (project_id,),
                ).fetchone()
                if proj is None:
                    raise KeyError(f"unknown project id: {project_id}")
                scoped_ids = {
                    str(row["paper_id"])
                    for row in connection.execute(
                        "SELECT paper_id FROM project_papers WHERE project_id = ?",
                        (project_id,),
                    ).fetchall()
                }
            return search_documents(
                connection,
                query,
                paper_ids=scoped_ids,
                project_id=project_id,
                source_kinds=set(source_kinds) if source_kinds else None,
                limit=limit,
            )

    def create_project(
        self,
        name: str,
        *,
        description: str = "",
        research_question: str = "",
        project_id: str | None = None,
    ) -> ResearchProject:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("project name cannot be blank")
        pid = (project_id or "").strip() or str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO research_projects (id, name, description, research_question, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        clean_name,
                        (description or "").strip(),
                        (research_question or "").strip(),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"project already exists: {clean_name}") from exc
            row = connection.execute(
                """
                SELECT p.*, 0 AS paper_count
                FROM research_projects p
                WHERE p.id = ?
                """,
                (pid,),
            ).fetchone()
        return self._project_from_row(row)

    def list_projects(self) -> list[ResearchProject]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT p.*, COUNT(pp.paper_id) AS paper_count
                FROM research_projects p
                LEFT JOIN project_papers pp ON pp.project_id = p.id
                GROUP BY p.id
                ORDER BY p.updated_at DESC, p.created_at DESC
                """
            ).fetchall()
        return [self._project_from_row(row) for row in rows]

    def get_project(self, project_id: str) -> ResearchProject | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT p.*, COUNT(pp.paper_id) AS paper_count
                FROM research_projects p
                LEFT JOIN project_papers pp ON pp.project_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (project_id,),
            ).fetchone()
        return self._project_from_row(row) if row is not None else None

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        research_question: str | None = None,
    ) -> ResearchProject:
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM research_projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"unknown project id: {project_id}")
            new_name = existing["name"] if name is None else name.strip()
            if not new_name:
                raise ValueError("project name cannot be blank")
            new_desc = existing["description"] if description is None else description.strip()
            new_rq = (
                existing["research_question"]
                if research_question is None
                else research_question.strip()
            )
            now = datetime.now(timezone.utc).isoformat()
            try:
                connection.execute(
                    """
                    UPDATE research_projects
                    SET name = ?, description = ?, research_question = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_name, new_desc, new_rq, now, project_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"project already exists: {new_name}") from exc
            sync_project_notes_fts(connection, project_id)
            row = connection.execute(
                """
                SELECT p.*, COUNT(pp.paper_id) AS paper_count
                FROM research_projects p
                LEFT JOIN project_papers pp ON pp.project_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (project_id,),
            ).fetchone()
        return self._project_from_row(row)

    def delete_project(self, project_id: str) -> dict[str, object]:
        """删除研究项目及关联关系，绝不删除底层论文与证据卡片。"""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id, name FROM research_projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown project id: {project_id}")
            member_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM project_papers WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0]
            )
            connection.execute("DELETE FROM research_projects WHERE id = ?", (project_id,))
            sync_project_notes_fts(connection, project_id)
        return {
            "project_id": str(row["id"]),
            "name": str(row["name"]),
            "unlinked_papers": member_count,
        }

    def add_paper_to_project(
        self,
        project_id: str,
        paper_id: str,
        *,
        reading_status: ProjectReadingStatus | str = ProjectReadingStatus.QUEUED,
        project_note: str = "",
        position: int | None = None,
    ) -> ProjectPaperEntry:
        status_enum = (
            reading_status
            if isinstance(reading_status, ProjectReadingStatus)
            else ProjectReadingStatus(str(reading_status).strip().lower())
        )
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM research_projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise KeyError(f"unknown project id: {project_id}")
            if connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
                raise KeyError(f"unknown paper id: {paper_id}")
            current = connection.execute(
                "SELECT position, added_at FROM project_papers WHERE project_id = ? AND paper_id = ?",
                (project_id, paper_id),
            ).fetchone()
            if position is not None:
                pos = max(0, int(position))
            elif current is not None:
                pos = int(current["position"])
            else:
                max_pos = connection.execute(
                    "SELECT COALESCE(MAX(position), -1) FROM project_papers WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0]
                pos = int(max_pos) + 1

            added_at = str(current["added_at"]) if current is not None else now
            connection.execute(
                """
                INSERT INTO project_papers (project_id, paper_id, reading_status, project_note, position, added_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, paper_id) DO UPDATE SET
                    reading_status = excluded.reading_status,
                    project_note = excluded.project_note,
                    position = excluded.position
                """,
                (project_id, paper_id, status_enum.value, (project_note or "").strip(), pos, added_at),
            )
            connection.execute(
                "UPDATE research_projects SET updated_at = ? WHERE id = ?",
                (now, project_id),
            )
            row = connection.execute(
                "SELECT * FROM project_papers WHERE project_id = ? AND paper_id = ?",
                (project_id, paper_id),
            ).fetchone()
        return self._project_paper_from_row(row)

    def attach_paper_to_project_if_absent(self, project_id: str, paper_id: str) -> bool:
        """幂等归入专题：仅当尚未关联时建立默认关联，绝不覆写既有阅读状态或专题备注。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM research_projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise KeyError(f"unknown project id: {project_id}")
            if connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
                raise KeyError(f"unknown paper id: {paper_id}")
            exists = connection.execute(
                "SELECT 1 FROM project_papers WHERE project_id = ? AND paper_id = ?",
                (project_id, paper_id),
            ).fetchone()
            if exists is not None:
                return False
            max_pos = connection.execute(
                "SELECT COALESCE(MAX(position), -1) FROM project_papers WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO project_papers (project_id, paper_id, reading_status, project_note, position, added_at)
                VALUES (?, ?, ?, '', ?, ?)
                """,
                (project_id, paper_id, ProjectReadingStatus.QUEUED.value, int(max_pos) + 1, now),
            )
            connection.execute(
                "UPDATE research_projects SET updated_at = ? WHERE id = ?",
                (now, project_id),
            )
        return True

    def remove_paper_from_project(self, project_id: str, paper_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM research_projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise KeyError(f"unknown project id: {project_id}")
            cursor = connection.execute(
                "DELETE FROM project_papers WHERE project_id = ? AND paper_id = ?",
                (project_id, paper_id),
            )
            removed = cursor.rowcount > 0
            if removed:
                connection.execute(
                    "UPDATE research_projects SET updated_at = ? WHERE id = ?",
                    (now, project_id),
                )
        return removed

    def list_project_papers(self, project_id: str) -> list[ProjectPaperEntry]:
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM research_projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise KeyError(f"unknown project id: {project_id}")
            rows = connection.execute(
                """
                SELECT * FROM project_papers
                WHERE project_id = ?
                ORDER BY position ASC, added_at ASC
                """,
                (project_id,),
            ).fetchall()
        return [self._project_paper_from_row(row) for row in rows]

    def get_project_paper_ids(self, project_id: str) -> set[str]:
        return {entry.paper_id for entry in self.list_project_papers(project_id)}

    @staticmethod
    def _project_note_from_row(row: sqlite3.Row):
        from .models import ProjectNote

        created = str(row["created_at"] or "")
        updated = str(row["updated_at"] or "") or created
        return ProjectNote(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            title=str(row["title"] or ""),
            note=str(row["note"]),
            created_at=created,
            updated_at=updated,
        )

    def list_project_notes(self, project_id: str) -> list:
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM research_projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise KeyError(f"unknown project id: {project_id}")
            rows = connection.execute(
                """
                SELECT id, project_id, title, note, created_at, updated_at
                FROM project_notes
                WHERE project_id = ?
                ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, rowid DESC
                """,
                (project_id,),
            ).fetchall()
        return [self._project_note_from_row(row) for row in rows]

    def add_project_note(
        self,
        project_id: str,
        note_id: str,
        note: str,
        *,
        title: str = "",
    ):
        clean_note = (note or "").strip()
        if not clean_note:
            raise ValueError("project note cannot be blank")
        clean_title = (title or "").strip()
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM research_projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise KeyError(f"unknown project id: {project_id}")
            connection.execute(
                """
                INSERT INTO project_notes (id, project_id, title, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (note_id, project_id, clean_title, clean_note, now, now),
            )
            connection.execute(
                "UPDATE research_projects SET updated_at = ? WHERE id = ?",
                (now, project_id),
            )
            sync_project_notes_fts(connection, project_id)
            row = connection.execute(
                "SELECT id, project_id, title, note, created_at, updated_at FROM project_notes WHERE id = ?",
                (note_id,),
            ).fetchone()
        return self._project_note_from_row(row)

    def update_project_note(
        self,
        project_id: str,
        note_id: str,
        *,
        title: str | None = None,
        note: str | None = None,
    ):
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id, project_id, title, note, created_at, updated_at
                FROM project_notes
                WHERE id = ? AND project_id = ?
                """,
                (note_id, project_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown project note id: {note_id}")
            next_title = str(row["title"] or "") if title is None else title.strip()
            next_note = str(row["note"]) if note is None else note.strip()
            if not next_note:
                raise ValueError("project note cannot be blank")
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                UPDATE project_notes
                SET title = ?, note = ?, updated_at = ?
                WHERE id = ? AND project_id = ?
                """,
                (next_title, next_note, now, note_id, project_id),
            )
            connection.execute(
                "UPDATE research_projects SET updated_at = ? WHERE id = ?",
                (now, project_id),
            )
            sync_project_notes_fts(connection, project_id)
            updated = connection.execute(
                "SELECT id, project_id, title, note, created_at, updated_at FROM project_notes WHERE id = ?",
                (note_id,),
            ).fetchone()
        return self._project_note_from_row(updated)

    def delete_project_note(self, project_id: str, note_id: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM project_notes WHERE id = ? AND project_id = ?",
                (note_id, project_id),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                connection.execute(
                    "UPDATE research_projects SET updated_at = ? WHERE id = ?",
                    (now, project_id),
                )
                sync_project_notes_fts(connection, project_id)
        return deleted

    @staticmethod
    def _sha256_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _paper_from_row(row: sqlite3.Row) -> PaperRecord:
        return PaperRecord(
            id=row["id"], sha256=row["sha256"], original_name=row["original_name"],
            stored_path=row["stored_path"], page_count=row["page_count"], parse_status=row["parse_status"],
            parser_version=row["parser_version"], warnings=json.loads(row["warnings_json"]), created_at=row["created_at"],
        )

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> ResearchProject:
        return ResearchProject(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            research_question=str(row["research_question"] or ""),
            paper_count=int(row["paper_count"] or 0),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _project_paper_from_row(row: sqlite3.Row) -> ProjectPaperEntry:
        return ProjectPaperEntry(
            project_id=str(row["project_id"]),
            paper_id=str(row["paper_id"]),
            reading_status=ProjectReadingStatus(str(row["reading_status"] or "queued")),
            project_note=str(row["project_note"] or ""),
            position=int(row["position"] or 0),
            added_at=str(row["added_at"]),
        )
