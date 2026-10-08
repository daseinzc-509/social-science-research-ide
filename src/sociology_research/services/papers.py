"""Paper-library application operations independent from any UI toolkit."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..pipeline import ImportPipeline
from ..repository import ResearchRepository
from ..parser import create_default_parser
from .jobs import JobService

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

    def list_papers(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for paper in self.repository.list_papers():
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
                }
            )
        return items

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

    def get_paper(self, paper_id: str) -> dict[str, Any]:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        card = self.repository.get_card(paper_id)
        references, mentions = self.repository.get_references(paper_id)
        return {
            "paper": paper.model_dump(mode="json"),
            "card": card.model_dump(mode="json") if card is not None else None,
            "metadata_overrides": self.repository.get_metadata_overrides(paper_id),
            "references": [entry.model_dump(mode="json") for entry in references],
            "citation_mentions": [mention.model_dump(mode="json") for mention in mentions],
        }

    def pdf_path(self, paper_id: str) -> Path:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        path = Path(paper.stored_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"stored PDF is missing: {path}")
        return path

    def start_import(self, filename: str, data: bytes) -> str:
        safe_name, payload = self._validate_upload(filename, data)
        temp_dir = Path(tempfile.mkdtemp(prefix="sra-upload-"))
        temp_path = temp_dir / safe_name
        temp_path.write_bytes(payload)
        job = self.jobs.create("import")

        def work(progress):
            try:
                progress(f"Importing {safe_name}…")
                pipeline = self.import_pipeline_factory(
                    self.data_dir,
                    self.repository,
                    progress=progress,
                )
                record, parsed, created = pipeline.import_pdf(temp_path)
                progress(
                    "PDF parsed and stored."
                    if created
                    else "This exact PDF was already imported; reused existing record."
                )
                return {
                    "paper_id": record.id,
                    "created": bool(created),
                    "page_count": record.page_count,
                    "parse_status": str(record.parse_status),
                    "warnings": list(parsed.warnings),
                }
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.jobs.start(job, work)
        return job.id

    def start_batch_import(self, uploads: list[tuple[str, bytes]]) -> str:
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
            except ValueError as exc:
                rejected.append({"filename": Path(filename or f"upload-{index}").name, "error": str(exc)})
                continue
            temp_path = temp_dir / f"{index:04d}-{safe_name}"
            temp_path.write_bytes(payload)
            prepared.append((safe_name, temp_path))

        if not prepared:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError("no valid non-empty PDF files were uploaded")

        job = self.jobs.create("batch-import")

        def work(progress):
            try:
                results: list[dict[str, Any]] = []
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
                        results.append(
                            {
                                "filename": safe_name,
                                "status": "imported" if created else "duplicate",
                                "paper_id": record.id,
                                "page_count": record.page_count,
                                "parse_status": str(record.parse_status),
                                "warnings": list(parsed.warnings),
                                "error": None,
                            }
                        )
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
                progress(
                    f"Batch import complete: {imported} imported, {duplicates} duplicate, {failed} failed/rejected."
                )
                return {
                    "results": results,
                    "imported": imported,
                    "duplicates": duplicates,
                    "failed": failed,
                }
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.jobs.start(job, work)
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
        safe_name = Path(str(filename or "upload.pdf")).name or "upload.pdf"
        if not safe_name.lower().endswith(".pdf"):
            raise ValueError("only PDF files can be imported")
        payload = bytes(data or b"")
        if not payload:
            raise ValueError("uploaded PDF is empty")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError("PDF is larger than the 256 MiB upload limit")
        return safe_name, payload
