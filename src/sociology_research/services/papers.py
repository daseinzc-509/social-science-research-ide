"""Paper-library application operations independent from any UI toolkit."""

from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from ..models import compute_claim_id
from ..pipeline import ImportPipeline
from ..repository import ResearchRepository
from ..parser import create_default_parser
from .jobs import JobCancelled, JobService

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_BATCH_FILES = 200


class PaperService:
    def __init__(
        self,
        data_dir: Path,
        repository: ResearchRepository,
        jobs: JobService,
        *,
        import_pipeline_factory: Callable[..., ImportPipeline] = ImportPipeline,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.repository = repository
        self.jobs = jobs
        self.import_pipeline_factory = import_pipeline_factory

    def list_papers(
        self,
        *,
        query: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        allowed_ids: set[str] | None = None
        ordered_project_ids: list[str] | None = None
        if project_id is not None and project_id.strip():
            entries = self.repository.list_project_papers(project_id.strip())
            ordered_project_ids = [entry.paper_id for entry in entries]
            allowed_ids = set(ordered_project_ids)

        review_counts = (
            self.repository.count_claim_reviews_by_paper()
            if hasattr(self.repository, "count_claim_reviews_by_paper")
            else {}
        )
        note_counts = (
            self.repository.count_user_notes_by_paper()
            if hasattr(self.repository, "count_user_notes_by_paper")
            else {}
        )

        items: list[dict[str, Any]] = []
        for paper in self.repository.list_papers():
            if allowed_ids is not None and paper.id not in allowed_ids:
                continue
            card = None
            card_error = None
            try:
                card = self.repository.get_card(paper.id)
            except Exception as exc:  # keep a damaged card from hiding the library
                card_error = str(exc)
            metadata = card.metadata.model_dump(mode="json") if card is not None else {}
            items.append(
                {
                    "id": paper.id,
                    "original_name": paper.original_name,
                    "page_count": paper.page_count,
                    "parse_status": str(paper.parse_status),
                    "created_at": paper.created_at,
                    "has_card": card is not None,
                    "card_error": card_error,
                    "title": metadata.get("title") or paper.original_name,
                    "authors": metadata.get("authors") or [],
                    "year": metadata.get("year"),
                    "journal": metadata.get("journal"),
                    "facts": len(card.basic_facts) if card is not None else 0,
                    "evidence_spans": len(card.evidence_spans) if card is not None else 0,
                    "analysis_claims": len(card.analysis) if card is not None else 0,
                    "warnings": len(card.warnings) if card is not None else len(paper.warnings),
                    "reviewed_claims": int(review_counts.get(paper.id, 0)),
                    "user_notes_count": int(note_counts.get(paper.id, 0)),
                }
            )

        if ordered_project_ids is not None and not (query and query.strip()):
            order_index = {pid: idx for idx, pid in enumerate(ordered_project_ids)}
            items.sort(key=lambda item: order_index.get(item["id"], 10_000))

        clean_query = (query or "").strip()
        if not clean_query:
            return items

        fts_rank: dict[str, int] = {}
        if hasattr(self.repository, "search"):
            for hit in self.repository.search(
                clean_query,
                project_id=project_id.strip() if project_id and project_id.strip() else None,
                limit=200,
            ):
                pid = str(hit["paper_id"])
                if pid not in fts_rank:
                    fts_rank[pid] = len(fts_rank)

        lowered = clean_query.casefold()
        matched: list[dict[str, Any]] = []
        for item in items:
            direct_hit = (
                lowered in str(item["title"]).casefold()
                or lowered in str(item["original_name"]).casefold()
                or any(lowered in str(a).casefold() for a in item["authors"])
                or (item["journal"] and lowered in str(item["journal"]).casefold())
                or (item["year"] is not None and lowered in str(item["year"]))
            )
            if item["id"] in fts_rank or direct_hit:
                matched.append(item)

        matched.sort(key=lambda item: fts_rank.get(item["id"], 10_000))
        return matched

    def search_library(
        self,
        query: str,
        *,
        project_id: str | None = None,
        source_kinds: list[str] | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        clean = (query or "").strip()
        if not clean:
            return {"query": "", "total": 0, "hits": []}
        hits = self.repository.search(
            clean,
            project_id=project_id.strip() if project_id and project_id.strip() else None,
            source_kinds=source_kinds,
            limit=limit,
        )
        return {
            "query": clean,
            "total": len(hits),
            "hits": hits,
        }

    def dashboard(self) -> dict[str, Any]:
        papers = self.list_papers()
        return {
            "papers": len(papers),
            "analyzed": sum(bool(item["has_card"]) for item in papers),
            "pending_analysis": sum(not bool(item["has_card"]) for item in papers),
            "needs_review": sum(item["parse_status"] == "needs_review" for item in papers),
            "references": sum(len(self.repository.get_references(item["id"])[0]) for item in papers),
            "items": papers,
        }

    @staticmethod
    def _ensure_card_claim_ids(card_dict: dict[str, Any] | None) -> dict[str, Any] | None:
        if card_dict is None:
            return None
        for group_key in ("metadata_claims", "basic_facts", "analysis", "limitations"):
            items = card_dict.get(group_key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and not str(item.get("claim_id") or "").strip():
                        item["claim_id"] = compute_claim_id(
                            str(item.get("field_name") or ""),
                            str(item.get("statement") or ""),
                        )
        rec = card_dict.get("reading_recommendation")
        if isinstance(rec, dict) and not str(rec.get("claim_id") or "").strip():
            rec["claim_id"] = compute_claim_id(
                str(rec.get("field_name") or "reading_recommendation"),
                str(rec.get("statement") or ""),
            )
        return card_dict

    def get_paper(self, paper_id: str) -> dict[str, Any]:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        card = self.repository.get_card(paper_id)
        references, mentions = self.repository.get_references(paper_id)
        notes = self.repository.list_user_notes(paper_id)
        claim_reviews = self.repository.list_claim_reviews(paper_id)
        card_payload = self._ensure_card_claim_ids(card.model_dump(mode="json")) if card is not None else None
        return {
            "paper": paper.model_dump(mode="json"),
            "card": card_payload,
            "metadata_overrides": self.repository.get_metadata_overrides(paper_id),
            "references": [entry.model_dump(mode="json") for entry in references],
            "citation_mentions": [mention.model_dump(mode="json") for mention in mentions],
            "user_notes": [note.model_dump(mode="json") for note in notes],
            "claim_reviews": [rev.model_dump(mode="json") for rev in claim_reviews],
        }

    def list_user_notes(self, paper_id: str) -> list[dict[str, Any]]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        return [note.model_dump(mode="json") for note in self.repository.list_user_notes(paper_id)]

    def create_user_note(
        self,
        paper_id: str,
        *,
        note: str,
        title: str = "",
        page_number: int | None = None,
    ) -> dict[str, Any]:
        note_id = f"note-{uuid.uuid4().hex[:12]}"
        created = self.repository.add_user_note(
            paper_id,
            note_id,
            note,
            title=title,
            page_number=page_number,
        )
        return created.model_dump(mode="json")

    def update_user_note(
        self,
        paper_id: str,
        note_id: str,
        *,
        title: str | None = None,
        note: str | None = None,
        page_number: int | None = None,
        update_page_number: bool = False,
    ) -> dict[str, Any]:
        updated = self.repository.update_user_note(
            paper_id,
            note_id,
            title=title,
            note=note,
            page_number=page_number,
            update_page_number=update_page_number,
        )
        return updated.model_dump(mode="json")

    def delete_user_note(self, paper_id: str, note_id: str) -> dict[str, Any]:
        if not self.repository.delete_user_note(paper_id, note_id):
            raise KeyError(f"unknown user note id: {note_id}")
        return {"paper_id": paper_id, "note_id": note_id, "deleted": True}

    def list_claim_reviews(self, paper_id: str) -> list[dict[str, Any]]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        return [rev.model_dump(mode="json") for rev in self.repository.list_claim_reviews(paper_id)]

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
    ) -> dict[str, Any]:
        saved = self.repository.upsert_claim_review(
            paper_id,
            claim_id,
            user_status=user_status,
            field_name=field_name,
            original_statement=original_statement,
            corrected_statement=corrected_statement,
            note=note,
        )
        return saved.model_dump(mode="json")

    def delete_claim_review(self, paper_id: str, claim_id: str) -> dict[str, Any]:
        if not self.repository.delete_claim_review(paper_id, claim_id):
            raise KeyError(f"unknown claim review: {claim_id}")
        return {"paper_id": paper_id, "claim_id": claim_id, "deleted": True}

    def pdf_path(self, paper_id: str) -> Path:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        path = Path(paper.stored_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"stored PDF is missing: {path}")
        return path

    def _validate_target_project(self, project_id: str | None) -> str | None:
        clean = (project_id or "").strip()
        if not clean:
            return None
        if self.repository.get_project(clean) is None:
            raise KeyError(f"unknown project id: {clean}")
        return clean

    def start_import(self, filename: str, data: bytes, *, project_id: str | None = None) -> str:
        target_project = self._validate_target_project(project_id)
        safe_name, payload = self._validate_upload(filename, data)
        temp_dir = Path(tempfile.mkdtemp(prefix="sra-upload-"))
        temp_path = temp_dir / safe_name
        temp_path.write_bytes(payload)
        job = self.jobs.create("import")

        def cleanup_temp() -> None:
            shutil.rmtree(temp_dir, ignore_errors=True)

        def work(progress):
            progress(f"Importing {safe_name}…")
            pipeline = self.import_pipeline_factory(
                self.data_dir,
                self.repository,
                progress=progress,
            )
            record, parsed, created = pipeline.import_pdf(temp_path)
            attached = False
            if target_project is not None:
                attached = self.repository.attach_paper_to_project_if_absent(target_project, record.id)
            self.jobs.message(
                job.id,
                "PDF parsed and stored."
                if created
                else "This exact PDF was already imported; reused existing record.",
            )
            return {
                "paper_id": record.id,
                "created": bool(created),
                "page_count": record.page_count,
                "parse_status": str(record.parse_status),
                "warnings": list(parsed.warnings),
                "project_id": target_project,
                "attached_to_project": bool(attached),
            }

        self.jobs.start(job, work, cleanup=cleanup_temp)
        return job.id

    def start_batch_import(
        self,
        uploads: list[tuple[str, bytes]],
        *,
        project_id: str | None = None,
    ) -> str:
        target_project = self._validate_target_project(project_id)
        if not uploads:
            raise ValueError("no PDF files were uploaded")
        if len(uploads) > MAX_BATCH_FILES:
            raise ValueError(f"too many files in one batch; maximum is {MAX_BATCH_FILES}")

        temp_dir = Path(tempfile.mkdtemp(prefix="sra-batch-upload-"))
        prepared: list[tuple[str, Path]] = []
        rejected: list[dict[str, str]] = []

        for index, (filename, data) in enumerate(uploads, start=1):
            try:
                safe_name, payload = self._validate_upload(filename or f"upload-{index}.pdf", data)
                # Isolate identical filenames without changing what ImportPipeline
                # records as the original PDF name.
                item_dir = temp_dir / f"{index:04d}"
                item_dir.mkdir(parents=True, exist_ok=True)
                temp_path = item_dir / safe_name
                temp_path.write_bytes(payload)
                prepared.append((safe_name, temp_path))
            except (OSError, ValueError) as exc:
                rejected.append({
                    "filename": Path(str(filename or f"upload-{index}").replace("\\", "/")).name,
                    "error": str(exc),
                })
                continue

        if not prepared:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError("no valid non-empty PDF files were uploaded")

        job = self.jobs.create("batch-import")

        def cleanup_temp() -> None:
            shutil.rmtree(temp_dir, ignore_errors=True)

        def work(progress):
            results: list[dict[str, Any]] = []
            attached_count = 0
            progress(f"Batch import: {len(prepared)} valid PDF file(s) queued…")
            for index, (safe_name, path) in enumerate(prepared, start=1):
                progress(f"Batch import {index}/{len(prepared)}: {safe_name}")
                try:
                    pipeline = self.import_pipeline_factory(
                        self.data_dir,
                        self.repository,
                        progress=progress,
                    )
                    record, parsed, created = pipeline.import_pdf(path)
                    attached = False
                    if target_project is not None:
                        attached = self.repository.attach_paper_to_project_if_absent(target_project, record.id)
                        if attached:
                            attached_count += 1
                    results.append(
                        {
                            "filename": safe_name,
                            "status": "imported" if created else "duplicate",
                            "paper_id": record.id,
                            "page_count": record.page_count,
                            "parse_status": str(record.parse_status),
                            "warnings": list(parsed.warnings),
                            "attached_to_project": bool(attached),
                            "error": None,
                        }
                    )
                except JobCancelled:
                    raise
                except Exception as exc:  # keep a batch moving after one bad file
                    results.append(
                        {
                            "filename": safe_name,
                            "status": "failed",
                            "paper_id": None,
                            "page_count": None,
                            "parse_status": None,
                            "warnings": [],
                            "error": str(exc),
                        }
                    )
                    progress(f"Failed: {safe_name}: {exc}")

            results.extend(
                {
                    "filename": item["filename"],
                    "status": "rejected",
                    "paper_id": None,
                    "page_count": None,
                    "parse_status": None,
                    "warnings": [],
                    "error": item["error"],
                }
                for item in rejected
            )
            imported = sum(item["status"] == "imported" for item in results)
            duplicates = sum(item["status"] == "duplicate" for item in results)
            failed = sum(item["status"] in {"failed", "rejected"} for item in results)
            self.jobs.message(
                job.id,
                f"Batch import complete: {imported} imported, {duplicates} duplicate, {failed} failed/rejected.",
            )
            return {
                "results": results,
                "imported": imported,
                "duplicates": duplicates,
                "failed": failed,
                "project_id": target_project,
                "attached_count": attached_count,
            }

        self.jobs.start(job, work, cleanup=cleanup_temp)
        return job.id

    def save_metadata_overrides(self, paper_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        values = payload.get("values", {})
        if not isinstance(values, dict):
            raise ValueError("metadata override values must be an object")

        normalized: dict[str, object] = {}
        for field_name in ("title", "journal", "volume", "issue", "pages", "doi"):
            value = values.get(field_name)
            normalized[field_name] = str(value).strip() if value not in (None, "") else None

        raw_year = values.get("year")
        if raw_year in (None, ""):
            normalized["year"] = None
        else:
            try:
                normalized["year"] = int(str(raw_year).strip())
            except ValueError as exc:
                raise ValueError("year must be a four-digit number") from exc

        for field_name in ("authors", "keywords"):
            value = values.get(field_name, [])
            if isinstance(value, str):
                parts = re.split(r"[;,；、\n]+", value)
            elif isinstance(value, list):
                parts = [str(item) for item in value]
            else:
                raise ValueError(f"{field_name} must be a string or list")
            normalized[field_name] = [item.strip() for item in parts if item.strip()]

        source_note = payload.get("source_note")
        if source_note is not None and not isinstance(source_note, str):
            raise ValueError("source_note must be a string")

        result = self.repository.save_metadata_overrides(
            paper_id,
            normalized,
            source_note=(source_note or "").strip() or None,
        )
        return {"ok": True, "metadata_overrides": result}

    def clear_metadata_overrides(self, paper_id: str) -> dict[str, Any]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        self.repository.clear_metadata_overrides(paper_id)
        return {"ok": True}

    def start_reparse(self, paper_id: str) -> str:
        record = self.repository.get_paper(paper_id)
        if record is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        job = self.jobs.create("reparse")

        def work(progress):
            progress("Rebuild: starting a fresh document parse…")
            parser = create_default_parser()
            refreshed = parser.parse(
                Path(record.stored_path),
                paper_id=record.id,
                sha256=record.sha256,
                progress=progress,
            )
            progress("Rebuild: replacing stored source blocks…")
            self.repository.refresh_parsed_document(record.id, refreshed)
            cleared = self.repository.clear_derived_analysis(record.id)
            progress("Rebuild complete: source blocks refreshed; old Paper Card and model cache cleared.")
            return {
                "paper_id": record.id,
                "page_count": refreshed.page_count,
                "source_blocks": len(refreshed.blocks),
                "parser_version": refreshed.parser_version,
                "cleared_paper_cards": cleared["paper_cards"],
                "cleared_model_runs": cleared["model_runs"],
            }

        self.jobs.start(job, work)
        return job.id

    def delete_paper(self, paper_id: str, *, delete_file: bool = True) -> dict[str, Any]:
        record = self.repository.get_paper(paper_id)
        if record is None:
            raise KeyError(f"unknown paper id: {paper_id}")

        stored_path = Path(record.stored_path).expanduser().resolve()
        papers_root = (self.data_dir / "papers").resolve()
        db_result = self.repository.delete_paper(paper_id)
        file_deleted = False
        file_warning = None

        if delete_file and stored_path.exists():
            try:
                stored_path.relative_to(papers_root)
            except ValueError:
                file_warning = "Database rows were deleted, but the PDF path was outside the managed papers directory."
            else:
                try:
                    stored_path.unlink()
                    file_deleted = True
                except OSError as exc:
                    file_warning = f"Database rows were deleted, but the PDF file could not be removed: {exc}"

        return {**db_result, "file_deleted": file_deleted, "file_warning": file_warning}

    @staticmethod
    def _validate_upload(filename: str, data: bytes) -> tuple[str, bytes]:
        safe_name = Path(str(filename or "upload.pdf").replace("\\", "/")).name or "upload.pdf"
        if not safe_name.lower().endswith(".pdf"):
            raise ValueError("only PDF files can be imported")
        payload = bytes(data or b"")
        if not payload:
            raise ValueError("uploaded PDF is empty")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError("PDF is larger than the 256 MiB upload limit")
        return safe_name, payload
