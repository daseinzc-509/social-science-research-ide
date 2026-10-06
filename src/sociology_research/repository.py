"""Small SQLite repository for the single-user prototype."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import PaperCard, PaperRecord, ParsedDocument, SourceBlock


class ResearchRepository:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                    table_rows_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_source_blocks_paper_page
                    ON source_blocks(paper_id, page_number);
                CREATE TABLE IF NOT EXISTS paper_cards (
                    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
                    card_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS user_notes (
                    id TEXT PRIMARY KEY,
                    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
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
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_blocks)")}
            if "table_rows_json" not in columns:
                connection.execute("ALTER TABLE source_blocks ADD COLUMN table_rows_json TEXT")
            paper_columns = {row["name"] for row in connection.execute("PRAGMA table_info(papers)")}
            if "parser_version" not in paper_columns:
                connection.execute("ALTER TABLE papers ADD COLUMN parser_version TEXT NOT NULL DEFAULT 'legacy'")

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
                "INSERT INTO source_blocks (id, paper_id, page_number, text, bbox_json, table_rows_json) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (block.id, record.id, block.page_number, block.text,
                     json.dumps(block.bbox) if block.bbox is not None else None,
                     json.dumps(block.table_rows, ensure_ascii=False) if block.table_rows is not None else None)
                    for block in document.blocks
                ],
            )
        return record

    def refresh_parsed_document(self, paper_id: str, document: ParsedDocument) -> None:
        if document.paper_id != paper_id:
            raise ValueError("parsed document paper id does not match")
        with self._connection() as connection:
            exists = connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not exists:
                raise KeyError(f"unknown paper id: {paper_id}")
            connection.execute("DELETE FROM source_blocks WHERE paper_id = ?", (paper_id,))
            connection.executemany(
                """INSERT INTO source_blocks
                (id, paper_id, page_number, text, bbox_json, table_rows_json) VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (block.id, paper_id, block.page_number, block.text,
                     json.dumps(block.bbox) if block.bbox is not None else None,
                     json.dumps(block.table_rows, ensure_ascii=False) if block.table_rows is not None else None)
                    for block in document.blocks
                ],
            )
            connection.execute(
                "UPDATE papers SET page_count = ?, parse_status = ?, parser_version = ?, warnings_json = ? WHERE id = ?",
                (document.page_count, document.status.value, document.parser_version,
                 json.dumps(document.warnings, ensure_ascii=False), paper_id),
            )

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

    def get_card(self, paper_id: str) -> PaperCard | None:
        with self._connection() as connection:
            row = connection.execute("SELECT card_json FROM paper_cards WHERE paper_id = ?", (paper_id,)).fetchone()
        return PaperCard.model_validate_json(row[0]) if row else None

    def get_source_blocks(self, paper_id: str) -> list[SourceBlock]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, page_number, text, bbox_json, table_rows_json FROM source_blocks "
                "WHERE paper_id = ? ORDER BY page_number, rowid", (paper_id,)
            ).fetchall()
        return [
            SourceBlock(
                id=row["id"], page_number=row["page_number"], text=row["text"],
                bbox=tuple(json.loads(row["bbox_json"])) if row["bbox_json"] else None,
                table_rows=json.loads(row["table_rows_json"]) if row["table_rows_json"] else None,
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

    def add_user_note(self, paper_id: str, note_id: str, note: str) -> None:
        if not note.strip():
            raise ValueError("user note cannot be blank")
        with self._connect() as connection:
            exists = connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if not exists:
                raise KeyError(f"unknown paper id: {paper_id}")
            connection.execute(
                "INSERT INTO user_notes (id, paper_id, note) VALUES (?, ?, ?)", (note_id, paper_id, note.strip())
            )

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
