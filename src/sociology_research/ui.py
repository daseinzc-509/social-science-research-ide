"""Zero-dependency local web UI for the SRA workspace.

The server binds to loopback only and is intended for a single-user local workstation.
"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from .analyzer import PROMPT_VERSION, PaperAnalysisPipeline
from .config import Settings, save_local_environment
from .pipeline import ImportPipeline
from .parser import create_default_parser
from .repository import ResearchRepository
from .library import LibraryService
from .references import display_reference_text, extract_references

_MAX_UPLOAD_BYTES = 256 * 1024 * 1024


@dataclass
class _Job:
    id: str
    kind: str
    status: str = "queued"
    messages: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None


class _JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def create(self, kind: str) -> _Job:
        job = _Job(id=str(uuid.uuid4()), kind=kind)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "messages": list(job.messages),
                "result": job.result,
                "error": job.error,
            }

    def start(self, job: _Job, target: Callable[[Callable[[str], None]], dict[str, Any]]) -> None:
        def runner() -> None:
            self._set(job.id, status="running")
            try:
                result = target(lambda message: self.message(job.id, message))
            except Exception as exc:  # noqa: BLE001 - boundary converts failures to user-visible job errors.
                self._set(job.id, status="error", error=str(exc))
            else:
                self._set(job.id, status="done", result=result)

        threading.Thread(target=runner, daemon=True, name=f"sra-{job.kind}-{job.id[:8]}").start()

    def message(self, job_id: str, message: str) -> None:
        clean = str(message).strip()
        if not clean:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.messages.append(clean)
                if len(job.messages) > 200:
                    del job.messages[:-200]

    def _set(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)


class SRAWebApp:
    def __init__(
        self,
        data_dir: Path,
        repository: ResearchRepository,
        *,
        pipeline_factory: Callable[..., PaperAnalysisPipeline] = PaperAnalysisPipeline,
        import_pipeline_factory: Callable[..., ImportPipeline] = ImportPipeline,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.repository = repository
        self.pipeline_factory = pipeline_factory
        self.import_pipeline_factory = import_pipeline_factory
        self.jobs = _JobStore()

    def list_papers(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for paper in self.repository.list_papers():
            card = None
            card_error = None
            try:
                card = self.repository.get_card(paper.id)
            except Exception as exc:  # noqa: BLE001
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
                    "warnings": len(card.warnings) if card is not None else 0,
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

    def model_settings(self) -> dict[str, Any]:
        settings = Settings.from_environment()
        key = settings.api_key or ""
        masked = (key[:4] + "…" + key[-4:]) if len(key) > 10 else ("已设置" if key else "")
        return {
            "api_key_masked": masked,
            "has_api_key": bool(key),
            "api_base_url": settings.api_base_url or "",
            "lite_model": settings.lite_model or "",
            "pro_model": settings.pro_model or "",
        }

    def save_model_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = Settings.from_environment()
        raw_key = payload.get("api_key")
        api_key = current.api_key if raw_key in (None, "", "••••••••") else str(raw_key).strip()
        values = {
            "SRA_API_KEY": api_key,
            "SRA_API_BASE_URL": str(payload.get("api_base_url") or "").strip(),
            "SRA_LITE_MODEL": str(payload.get("lite_model") or "").strip(),
            "SRA_PRO_MODEL": str(payload.get("pro_model") or "").strip(),
        }
        if not values["SRA_API_BASE_URL"].startswith(("http://", "https://")):
            raise ValueError("Base URL 必须以 http:// 或 https:// 开头")
        if not values["SRA_LITE_MODEL"] or not values["SRA_PRO_MODEL"]:
            raise ValueError("Lite 和 Pro 模型名都不能为空")
        save_local_environment(values)
        return self.model_settings()

    def paper_detail(self, paper_id: str) -> dict[str, Any]:
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

    def save_metadata_overrides(self, paper_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        values = payload.get("values", {})
        if not isinstance(values, dict):
            raise ValueError("metadata override values must be an object")
        normalized: dict[str, object] = {}
        text_fields = ("title", "journal", "volume", "issue", "pages", "doi")
        for field_name in text_fields:
            value = values.get(field_name)
            normalized[field_name] = str(value).strip() if value not in (None, "") else None
        raw_year = values.get("year")
        if raw_year in (None, ""):
            normalized["year"] = None
        else:
            try:
                normalized["year"] = int(str(raw_year).strip())
            except ValueError as exc:
                raise ValueError("年份必须是 4 位数字") from exc
        for field_name in ("authors", "keywords"):
            value = values.get(field_name, [])
            if isinstance(value, str):
                parts = re.split(r"[;,；、\n]+", value)
            elif isinstance(value, list):
                parts = [str(item) for item in value]
            else:
                raise ValueError(f"{field_name} must be a string or list")
            normalized[field_name] = [item.strip() for item in parts if item.strip()]
        result = self.repository.save_metadata_overrides(
            paper_id,
            normalized,
            source_note=_optional_string(payload.get("source_note")),
        )
        return {"ok": True, "metadata_overrides": result}

    def clear_metadata_overrides(self, paper_id: str) -> dict[str, Any]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        self.repository.clear_metadata_overrides(paper_id)
        return {"ok": True}

    def start_analysis(
        self,
        paper_id: str,
        *,
        research_context: str | None,
        exclude_after_text: str | None,
        force: bool,
    ) -> str:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        job = self.jobs.create("analyze")

        def work(progress: Callable[[str], None]) -> dict[str, Any]:
            progress("Preparing analysis…")
            pipeline = self.pipeline_factory(self.repository, progress=progress)
            card = pipeline.analyze(
                paper_id,
                research_context=(research_context or "").strip() or None,
                exclude_after_text=(exclude_after_text or "").strip() or None,
                force=force,
            )
            persisted = self.repository.get_card(paper_id)
            if persisted is None:
                raise RuntimeError("分析已返回，但 Paper Card 没有成功落库")
            return {
                "paper_id": card.paper_id,
                "persisted": True,
                "facts": len(card.basic_facts),
                "evidence_spans": len(card.evidence_spans),
                "tables": len(card.tables),
                "analysis_claims": len(card.analysis),
                "warnings": len(card.warnings),
            }

        self.jobs.start(job, work)
        return job.id

    def start_batch_analysis(self, paper_ids: list[str] | None = None) -> str:
        selected = paper_ids or [paper.id for paper in self.repository.list_papers() if self.repository.get_card(paper.id) is None]
        if not selected:
            raise ValueError("没有待分析的论文。")
        job = self.jobs.create("batch-analysis")

        def work(progress: Callable[[str], None]) -> dict[str, Any]:
            done = 0
            failed: list[dict[str, str]] = []
            for index, paper_id in enumerate(selected, start=1):
                paper = self.repository.get_paper(paper_id)
                if paper is None:
                    failed.append({"paper_id": paper_id, "error": "unknown_paper"})
                    continue
                progress(f"批量分析 {index}/{len(selected)}：{paper.original_name}")
                try:
                    pipeline = self.pipeline_factory(self.repository, progress=progress)
                    pipeline.analyze(paper_id)
                    done += 1
                except Exception as exc:  # keep the queue moving
                    failed.append({"paper_id": paper_id, "error": str(exc)})
                    progress(f"失败：{paper.original_name}：{exc}")
            return {"selected": len(selected), "completed": done, "failed": failed}

        self.jobs.start(job, work)
        return job.id

    def start_import(self, filename: str, data: bytes) -> str:
        safe_name = Path(filename).name
        if not safe_name.lower().endswith(".pdf"):
            raise ValueError("Only PDF files can be imported.")
        if not data:
            raise ValueError("Uploaded PDF is empty.")
        temp_dir = Path(tempfile.mkdtemp(prefix="sra-upload-"))
        temp_path = temp_dir / safe_name
        temp_path.write_bytes(data)
        job = self.jobs.create("import")

        def work(progress: Callable[[str], None]) -> dict[str, Any]:
            try:
                progress(f"Importing {safe_name}…")
                pipeline = self.import_pipeline_factory(self.data_dir, self.repository, progress=progress)
                record, parsed, created = pipeline.import_pdf(temp_path)
                progress("PDF parsed and stored." if created else "This exact PDF was already imported; reused existing record. Use ‘重建解析’ to force a fresh Docling parse.")
                return {
                    "paper_id": record.id,
                    "created": created,
                    "page_count": record.page_count,
                    "parse_status": str(record.parse_status),
                    "warnings": parsed.warnings,
                }
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.jobs.start(job, work)
        return job.id

    def start_batch_import(self, uploads: list[tuple[str, bytes]]) -> str:
        if not uploads:
            raise ValueError("No PDF files were uploaded.")
        temp_dir = Path(tempfile.mkdtemp(prefix="sra-batch-upload-"))
        paths: list[Path] = []
        for index, (filename, data) in enumerate(uploads, start=1):
            safe_name = Path(filename).name or f"upload-{index}.pdf"
            if not safe_name.lower().endswith(".pdf"):
                continue
            if not data:
                continue
            path = temp_dir / f"{index:04d}-{safe_name}"
            path.write_bytes(data)
            paths.append(path)
        if not paths:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise ValueError("No non-empty PDF files were uploaded.")
        job = self.jobs.create("batch-import")

        def work(progress: Callable[[str], None]) -> dict[str, Any]:
            try:
                progress(f"Batch import: {len(paths)} PDF files queued…")
                service = LibraryService(self.data_dir, self.repository, progress=progress)
                results = service.batch_import(paths)
                imported = sum(result["status"] == "imported" for result in results)
                duplicates = sum(result["status"] == "duplicate" for result in results)
                failed = sum(result["status"] == "failed" for result in results)
                progress(f"Batch import complete: {imported} imported, {duplicates} duplicate, {failed} failed.")
                return {"results": results, "imported": imported, "duplicates": duplicates, "failed": failed}
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        self.jobs.start(job, work)
        return job.id

    def extract_references(self, paper_id: str) -> dict[str, Any]:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        entries, mentions = extract_references(paper_id, self.repository.get_source_blocks(paper_id))
        self.repository.save_references(paper_id, entries, mentions)
        return {
            "references": [entry.model_dump(mode="json") for entry in entries],
            "citation_mentions": [mention.model_dump(mode="json") for mention in mentions],
        }

    def references_export(self, paper_id: str, fmt: str) -> tuple[str, str]:
        paper = self.repository.get_paper(paper_id)
        if paper is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        entries, mentions = self.repository.get_references(paper_id)
        if not entries:
            self.extract_references(paper_id)
            entries, mentions = self.repository.get_references(paper_id)
        if fmt == "json":
            content = json.dumps({"paper_id": paper_id, "references": [e.model_dump(mode="json") for e in entries], "citation_mentions": [m.model_dump(mode="json") for m in mentions]}, ensure_ascii=False, indent=2)
            return content, "application/json; charset=utf-8"
        lines = []
        for index, entry in enumerate(entries, start=1):
            key = f"ref{entry.year or 'nd'}_{index}"
            lines.append(f"@article{{{key},")
            for name, value in (("author", " and ".join(entry.authors)), ("title", entry.title), ("year", entry.year), ("doi", entry.doi), ("journal", entry.container_title), ("pages", entry.pages)):
                if value not in (None, "", []):
                    escaped = str(value).replace("{", "\\{").replace("}", "\\}")
                    lines.append(f"  {name} = {{{escaped}}},")
            lines.append(f"  note = {{PDF page {entry.source_page}; verify raw_text before citing}},")
            lines.append("}\n")
        return "\n".join(lines), "application/x-bibtex; charset=utf-8"

    def start_reparse(self, paper_id: str) -> str:
        record = self.repository.get_paper(paper_id)
        if record is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        job = self.jobs.create("reparse")

        def work(progress: Callable[[str], None]) -> dict[str, Any]:
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
            progress(
                "Rebuild complete: source blocks refreshed; old Paper Card and model cache cleared."
            )
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

    def delete_paper(self, paper_id: str, *, delete_file: bool) -> dict[str, Any]:
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
                file_warning = (
                    "Database rows were deleted, but the PDF path was outside the managed papers directory "
                    "and was not removed."
                )
            else:
                try:
                    stored_path.unlink()
                    file_deleted = True
                except OSError as exc:
                    file_warning = f"Database rows were deleted, but the PDF file could not be removed: {exc}"

        return {
            **db_result,
            "file_deleted": file_deleted,
            "file_warning": file_warning,
        }

    def doctor(self, *, deep: bool) -> dict[str, Any]:
        return self.repository.diagnose(
            self.data_dir / "papers",
            current_prompt_version=PROMPT_VERSION,
            deep=deep,
        )

    def prune_cache(self, *, all_runs: bool, apply: bool, vacuum: bool) -> dict[str, Any]:
        return self.repository.prune_model_runs(
            current_prompt_version=PROMPT_VERSION,
            all_runs=all_runs,
            apply=apply,
            vacuum=vacuum,
        )

    def export_text(self, paper_id: str) -> str:
        if self.repository.get_paper(paper_id) is None:
            raise KeyError(f"unknown paper id: {paper_id}")
        blocks = self.repository.get_source_blocks(paper_id)
        return "\n\n".join(
            f"[PDF page {block.page_number} | source_block_id={block.id}]\n{block.text}"
            for block in blocks
        )


class _SRAHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: SRAWebApp):
        self.app = app
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: _SRAHTTPServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                return self._send_html(_INDEX_HTML)
            if path == "/api/papers":
                return self._send_json({"papers": self.server.app.list_papers()})
            if path == "/api/doctor":
                deep = parse_qs(parsed.query).get("deep", ["0"])[0] in {"1", "true", "yes"}
                return self._send_json(self.server.app.doctor(deep=deep))
            if path == "/api/dashboard":
                return self._send_json(self.server.app.dashboard())
            if path == "/api/model-settings":
                return self._send_json(self.server.app.model_settings())
            if path.startswith("/api/jobs/"):
                job_id = path.removeprefix("/api/jobs/")
                job = self.server.app.jobs.get(job_id)
                if job is None:
                    return self._send_error(HTTPStatus.NOT_FOUND, "Unknown job.")
                return self._send_json(job)
            if path.startswith("/api/papers/"):
                return self._paper_get(path)
            if path == "/favicon.ico":
                return self._send_bytes(b"", "image/x-icon", status=HTTPStatus.NO_CONTENT)
            return self._send_error(HTTPStatus.NOT_FOUND, "Not found.")
        except (OSError, ValueError, KeyError) as exc:
            return self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001
            return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/import":
                filename, data = self._read_upload()
                job_id = self.server.app.start_import(filename, data)
                return self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
            if path == "/api/import-batch":
                uploads = self._read_uploads()
                job_id = self.server.app.start_batch_import(uploads)
                return self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
            if path == "/api/batch-analyze":
                payload = self._read_json()
                raw_ids = payload.get("paper_ids")
                paper_ids = [str(item) for item in raw_ids] if isinstance(raw_ids, list) else None
                job_id = self.server.app.start_batch_analysis(paper_ids)
                return self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
            if path == "/api/model-settings":
                return self._send_json(self.server.app.save_model_settings(self._read_json()))
            if path.startswith("/api/papers/") and path.endswith("/extract-references"):
                paper_id = path[len("/api/papers/") : -len("/extract-references")].strip("/")
                return self._send_json(self.server.app.extract_references(paper_id))
            if path.startswith("/api/papers/") and path.endswith("/analyze"):
                paper_id = path[len("/api/papers/") : -len("/analyze")].strip("/")
                payload = self._read_json()
                job_id = self.server.app.start_analysis(
                    paper_id,
                    research_context=_optional_string(payload.get("research_context")),
                    exclude_after_text=_optional_string(payload.get("exclude_after_text")),
                    force=bool(payload.get("force", False)),
                )
                return self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
            if path.startswith("/api/papers/") and path.endswith("/metadata"):
                paper_id = path[len("/api/papers/") : -len("/metadata")].strip("/")
                payload = self._read_json()
                return self._send_json(self.server.app.save_metadata_overrides(paper_id, payload))
            if path.startswith("/api/papers/") and path.endswith("/metadata/reset"):
                paper_id = path[len("/api/papers/") : -len("/metadata/reset")].strip("/")
                return self._send_json(self.server.app.clear_metadata_overrides(paper_id))
            if path.startswith("/api/papers/") and path.endswith("/reparse"):
                paper_id = path[len("/api/papers/") : -len("/reparse")].strip("/")
                job_id = self.server.app.start_reparse(paper_id)
                return self._send_json({"job_id": job_id}, status=HTTPStatus.ACCEPTED)
            if path.startswith("/api/papers/") and path.endswith("/delete"):
                paper_id = path[len("/api/papers/") : -len("/delete")].strip("/")
                payload = self._read_json()
                result = self.server.app.delete_paper(
                    paper_id, delete_file=bool(payload.get("delete_file", True))
                )
                return self._send_json(result)
            if path == "/api/prune-cache":
                payload = self._read_json()
                result = self.server.app.prune_cache(
                    all_runs=bool(payload.get("all_runs", False)),
                    apply=bool(payload.get("apply", False)),
                    vacuum=bool(payload.get("vacuum", False)),
                )
                return self._send_json(result)
            if path == "/api/shutdown":
                self._send_json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
            return self._send_error(HTTPStatus.NOT_FOUND, "Not found.")
        except (OSError, ValueError, KeyError) as exc:
            return self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001
            return self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _paper_get(self, path: str) -> None:
        rest = path.removeprefix("/api/papers/")
        parts = rest.split("/")
        paper_id = parts[0]
        if len(parts) == 1:
            return self._send_json(self.server.app.paper_detail(paper_id))
        suffix = "/".join(parts[1:])
        paper = self.server.app.repository.get_paper(paper_id)
        if paper is None:
            return self._send_error(HTTPStatus.NOT_FOUND, "Unknown paper.")
        if suffix == "pdf":
            path_obj = Path(paper.stored_path)
            if not path_obj.is_file():
                return self._send_error(HTTPStatus.NOT_FOUND, "Stored PDF is missing.")
            return self._send_file(path_obj, inline=True, download_name=paper.original_name)
        if suffix == "card.json":
            card = self.server.app.repository.get_card(paper_id)
            if card is None:
                return self._send_error(HTTPStatus.NOT_FOUND, "No Paper Card has been saved yet.")
            data = json.dumps(card.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")
            return self._send_bytes(
                data,
                "application/json; charset=utf-8",
                download_name=f"{_download_stem(paper.original_name)}-paper-card.json",
            )
        if suffix == "text.txt":
            data = self.server.app.export_text(paper_id).encode("utf-8")
            return self._send_bytes(
                data,
                "text/plain; charset=utf-8",
                download_name=f"{_download_stem(paper.original_name)}-parsed.txt",
            )
        if suffix in {"references.json", "references.bib"}:
            fmt = "json" if suffix.endswith(".json") else "bibtex"
            data, content_type = self.server.app.references_export(paper_id, fmt)
            extension = "json" if fmt == "json" else "bib"
            return self._send_bytes(
                data.encode("utf-8"), content_type,
                download_name=f"{_download_stem(paper.original_name)}-references.{extension}",
            )
        return self._send_error(HTTPStatus.NOT_FOUND, "Not found.")

    def _read_json(self) -> dict[str, Any]:
        length = self._content_length(limit=2 * 1024 * 1024)
        data = self.rfile.read(length)
        if not data:
            return {}
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object.")
        return value

    def _read_upload(self) -> tuple[str, bytes]:
        uploads = self._read_uploads()
        if len(uploads) != 1:
            raise ValueError("Exactly one PDF file is required.")
        return uploads[0]

    def _read_uploads(self) -> list[tuple[str, bytes]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type.lower():
            raise ValueError("Upload must use multipart/form-data.")
        length = self._content_length(limit=_MAX_UPLOAD_BYTES)
        body = self.rfile.read(length)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        if not message.is_multipart():
            raise ValueError("Malformed multipart upload.")
        uploads: list[tuple[str, bytes]] = []
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            if part.get_param("name", header="content-disposition") not in {"file", "files"}:
                continue
            filename = part.get_filename() or "upload.pdf"
            payload = part.get_payload(decode=True) or b""
            if len(payload) > _MAX_UPLOAD_BYTES:
                raise ValueError("PDF is larger than the 256 MiB upload limit.")
            uploads.append((filename, payload))
        if not uploads:
            raise ValueError("No PDF file was found in the upload.")
        return uploads

    def _content_length(self, *, limit: int) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ValueError("Missing Content-Length header.")
        try:
            length = int(raw)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header.") from exc
        if length < 0 or length > limit:
            raise ValueError(f"Request is too large; limit is {limit // (1024 * 1024)} MiB.")
        return length

    def _send_html(self, html: str) -> None:
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")

    def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status=status)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_file(self, path: Path, *, inline: bool, download_name: str) -> None:
        media_type = mimetypes.guess_type(download_name)[0] or "application/octet-stream"
        disposition = "inline" if inline else "attachment"
        data = path.read_bytes()
        self._send_bytes(data, media_type, disposition=disposition, download_name=download_name)

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
        disposition: str | None = None,
        download_name: str | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'self'")
        if disposition and download_name:
            ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_" for ch in download_name)
            encoded = quote(download_name, safe="")
            self.send_header(
                "Content-Disposition",
                f'{disposition}; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}',
            )
        elif download_name:
            ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_" for ch in download_name)
            encoded = quote(download_name, safe="")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}',
            )
        self.end_headers()
        if data:
            self.wfile.write(data)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected a string value.")
    return value


def _download_stem(filename: str) -> str:
    stem = Path(filename).stem.strip() or "paper"
    return "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in stem)


def serve_ui(
    data_dir: Path,
    repository: ResearchRepository,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Run the local browser UI until interrupted or closed from the page."""
    app = SRAWebApp(data_dir, repository)
    last_error: OSError | None = None
    server: _SRAHTTPServer | None = None
    candidate_ports = [port] if port == 0 else list(range(port, min(port + 10, 65536)))
    for candidate in candidate_ports:
        try:
            server = _SRAHTTPServer((host, candidate), app)
            break
        except OSError as exc:
            last_error = exc
    if server is None:
        raise OSError(f"Could not start local UI on ports {candidate_ports[0]}-{candidate_ports[-1]}: {last_error}")

    actual_port = int(server.server_address[1])
    url = f"http://{host}:{actual_port}/"
    print(f"SRA UI: {url}")
    print("Local-only server. Close it from the UI or press Ctrl+C in this window.")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open_new_tab(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


_INDEX_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Social Science Research IDE</title>
<style>
:root{--bg:#f5f3ee;--paper:#fffdf8;--ink:#18323a;--muted:#6c7778;--line:#d9d5ca;--accent:#c85f3a;--accent2:#2d6f73;--ok:#2c7a5a;--warn:#a76321;--bad:#a33c3c;--shadow:0 16px 50px rgba(31,48,53,.09)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh}button,input,textarea{font:inherit}button{cursor:pointer}.top{height:72px;display:flex;align-items:center;gap:18px;padding:0 28px;border-bottom:1px solid var(--line);background:rgba(255,253,248,.94);position:sticky;top:0;z-index:20;backdrop-filter:blur(10px)}.brand{font-family:Georgia,"Noto Serif SC",serif;font-size:22px;font-weight:700;letter-spacing:.02em;white-space:nowrap}.issue{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.14em}.top-spacer{flex:1}.btn{border:1px solid var(--line);background:var(--paper);color:var(--ink);border-radius:10px;padding:9px 13px;font-weight:650;text-decoration:none;display:inline-block}.btn:hover{border-color:#a8aaa3}.btn.primary{background:var(--ink);color:white;border-color:var(--ink)}.btn.danger{color:var(--bad)}.layout{display:grid;grid-template-columns:330px minmax(0,1fr);min-height:calc(100vh - 72px)}.side{border-right:1px solid var(--line);padding:18px;background:#f0eee7;overflow:auto}.search{width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--paper);outline:none;margin-bottom:12px}.paper-list{display:flex;flex-direction:column;gap:8px}.paper-item{padding:13px;border:1px solid transparent;border-radius:12px;cursor:pointer}.paper-item:hover{background:rgba(255,255,255,.55)}.paper-item.active{background:var(--paper);border-color:var(--line);box-shadow:0 6px 20px rgba(31,48,53,.06)}.paper-title{font-family:Georgia,"Noto Serif SC",serif;font-size:15px;font-weight:700;line-height:1.35;margin-bottom:7px}.paper-meta{font-size:12px;color:var(--muted);line-height:1.45}.badge{display:inline-flex;align-items:center;gap:5px;font-size:11px;padding:3px 7px;border-radius:999px;background:#e7ebe5;margin-right:5px}.badge.ok{color:var(--ok);background:#e4efe8}.badge.warn{color:var(--warn);background:#f5eadb}.badge.muted{color:var(--muted);background:#e9e7e1}.main{padding:34px;overflow:auto}.empty{max-width:760px;margin:13vh auto;text-align:center}.empty h1{font-family:Georgia,"Noto Serif SC",serif;font-size:38px;margin:0 0 14px}.empty p{color:var(--muted);font-size:16px;line-height:1.8}.hero{max-width:1050px;margin:0 auto 22px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.16em;color:var(--accent2);font-weight:750}.hero h1{font-family:Georgia,"Noto Serif SC",serif;font-size:38px;line-height:1.18;margin:8px 0 10px}.authors{color:var(--muted);font-size:15px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:20px}.tabs{max-width:1050px;margin:0 auto 18px;display:flex;gap:4px;border-bottom:1px solid var(--line)}.tab{padding:10px 14px;border:0;background:transparent;color:var(--muted);font-weight:700}.tab.active{color:var(--ink);border-bottom:2px solid var(--accent)}.panel{max-width:1050px;margin:0 auto}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 5px 20px rgba(31,48,53,.035)}.k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px}.v{font-size:15px;font-weight:650;line-height:1.5;word-break:break-word}.section{margin:22px 0}.section h2{font-family:Georgia,"Noto Serif SC",serif;font-size:22px;margin:0 0 12px}.prose{line-height:1.85;color:#31494f;white-space:pre-wrap}.chips{display:flex;gap:7px;flex-wrap:wrap}.chip{background:#e9eee9;border-radius:999px;padding:5px 9px;font-size:12px}.claim{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:15px 16px;margin:9px 0}.claim-head{display:flex;gap:8px;align-items:center;margin-bottom:7px}.claim-field{font-size:11px;font-weight:800;color:var(--accent2);text-transform:uppercase;letter-spacing:.08em}.claim-text{line-height:1.65}.evidence{margin-top:10px;border-left:2px solid #c6d7d4;padding:7px 10px;color:#52666b;font-size:13px;line-height:1.55}.evidence .page{color:var(--accent2);font-weight:750}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px;background:var(--paper);margin:10px 0}.data-table{border-collapse:collapse;width:100%;font-size:12px}.data-table td{border-bottom:1px solid #ece9e1;border-right:1px solid #ece9e1;padding:7px 8px;vertical-align:top}.data-table tr:last-child td{border-bottom:0}.notice{padding:12px 14px;border-radius:10px;background:#f5eadb;color:#7c531f;margin:10px 0}.good{background:#e4efe8;color:#28664c}.modal-back{position:fixed;inset:0;background:rgba(18,32,36,.35);display:none;align-items:center;justify-content:center;z-index:40;padding:20px}.modal-back.open{display:flex}.modal{width:min(720px,96vw);max-height:88vh;overflow:auto;background:var(--paper);border-radius:18px;border:1px solid var(--line);box-shadow:var(--shadow);padding:22px}.modal h2{font-family:Georgia,"Noto Serif SC",serif;margin:0 0 14px}.field{margin:14px 0}.field label{display:block;font-size:12px;font-weight:750;margin-bottom:6px}.field input,.field textarea{width:100%;border:1px solid var(--line);border-radius:10px;padding:10px 11px;background:#fff;outline:none}.field textarea{min-height:92px;resize:vertical}.check-field{margin:14px 0}.checkbox-label{display:grid!important;grid-template-columns:18px minmax(0,1fr);align-items:center;column-gap:10px;width:100%;margin:0!important;font-size:12px;font-weight:750;line-height:1.45;cursor:pointer}.checkbox-label input[type="checkbox"]{appearance:auto!important;-webkit-appearance:checkbox!important;width:18px!important;height:18px!important;min-width:18px!important;max-width:18px!important;margin:0!important;padding:0!important;border:0!important;border-radius:3px!important;background:transparent!important;box-shadow:none!important;justify-self:start!important;accent-color:var(--ink)}.checkbox-label span{display:block;min-width:0;margin:0}.ui-build{margin-top:8px;font-size:10px;color:var(--muted);letter-spacing:.06em}.metadata-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 12px}.manual-tag{display:inline-flex;align-items:center;padding:3px 7px;border-radius:999px;background:#e7edf5;color:#315f80;font-size:11px;font-weight:750}.source-note{font-size:11px;color:var(--muted);margin-top:6px;word-break:break-all}.job-progress{margin:4px 0 14px}.progress-meta{display:flex;justify-content:space-between;gap:12px;align-items:center;font-size:12px;color:var(--muted);margin-bottom:7px}.progress-track{height:9px;background:#e7e3da;border-radius:999px;overflow:hidden;position:relative}.progress-bar{height:100%;width:6%;border-radius:999px;background:linear-gradient(90deg,var(--accent2),var(--accent));transition:width .35s ease}.progress-track.indeterminate .progress-bar{width:34%;position:absolute;animation:progress-slide 1.25s ease-in-out infinite}@keyframes progress-slide{0%{left:-34%}50%{left:42%}100%{left:100%}}.progress-hint{font-size:11px;color:var(--muted);margin-top:7px;line-height:1.45}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}.log{background:#16282d;color:#dfe9e6;border-radius:10px;padding:12px;font-family:Consolas,monospace;font-size:12px;line-height:1.55;white-space:pre-wrap;max-height:280px;overflow:auto}.health-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.health-stat{padding:12px;border:1px solid var(--line);border-radius:10px}.health-stat b{display:block;font-size:20px;margin-top:3px}.toast{position:fixed;right:22px;bottom:22px;max-width:420px;background:var(--ink);color:white;padding:12px 14px;border-radius:11px;box-shadow:var(--shadow);display:none;z-index:60}.toast.show{display:block}.small{font-size:12px;color:var(--muted)}.split{display:flex;justify-content:space-between;gap:12px;align-items:center}.drop{border:1px dashed #aab5b1;border-radius:12px;padding:18px;text-align:center;color:var(--muted);margin-bottom:14px}.footer-note{font-size:11px;color:var(--muted);margin-top:14px;line-height:1.55}.raw{background:#182b31;color:#dce8e5;padding:14px;border-radius:12px;overflow:auto;font:12px/1.55 Consolas,monospace;white-space:pre-wrap;max-height:560px}
@media(max-width:900px){.layout{grid-template-columns:1fr}.side{display:none}.main{padding:22px}.grid,.health-grid{grid-template-columns:1fr}.hero h1{font-size:30px}.top{padding:0 14px}.issue{display:none}}
</style>
</head>
<body>
<header class="top"><div><div class="brand">Social Science Research IDE</div><div class="issue">Evidence-first local workspace</div></div><div class="top-spacer"></div><button class="btn" id="dashboardBtn">总控制面板</button><button class="btn" id="modelSettingsBtn">模型设置</button><button class="btn" id="healthBtn">数据库健康</button><button class="btn" id="batchImportBtn">批量选择 PDF</button><button class="btn" id="folderImportBtn">导入 PDF 文件夹</button><button class="btn primary" id="importBtn">导入 PDF</button><button class="btn danger" id="closeBtn">关闭</button><input id="fileInput" type="file" accept="application/pdf,.pdf" hidden><input id="batchFileInput" type="file" accept="application/pdf,.pdf" multiple hidden><input id="folderFileInput" type="file" accept="application/pdf,.pdf" multiple webkitdirectory directory hidden></header>
<div class="layout"><aside class="side"><input class="search" id="search" placeholder="搜索论文…"><div id="paperList" class="paper-list"></div></aside><main class="main" id="main"><div class="empty"><h1>你的论文工作台</h1><p>左侧选择一篇论文，或点击“导入 PDF”。分析、查看证据、导出和数据库检查都可以在这里完成，不必再逐条输入命令。</p></div></main></div>
<div class="modal-back" id="metadataModal"><div class="modal"><h2>编辑书目信息</h2><div class="small">手动修订会保留在本地数据库中，重新分析不会覆盖；“恢复自动提取”可撤销全部手动修订。</div><div class="grid" style="margin-top:12px"><div class="field"><label>期刊</label><input id="metaJournal"></div><div class="field"><label>年份</label><input id="metaYear" inputmode="numeric"></div><div class="field"><label>卷</label><input id="metaVolume" placeholder="没有卷号可留空"></div><div class="field"><label>期</label><input id="metaIssue"></div><div class="field"><label>页码</label><input id="metaPages" placeholder="例如 114-140"></div><div class="field"><label>DOI</label><input id="metaDoi"></div></div><div class="field"><label>题名</label><input id="metaTitle"></div><div class="field"><label>作者（用 、 或 ; 分隔）</label><input id="metaAuthors"></div><div class="field"><label>关键词（用 ； 或 ; 分隔）</label><input id="metaKeywords"></div><div class="field"><label>来源 / 网址（可选）</label><input id="metaSource" placeholder="例如 国家哲学社会科学文献中心或 DOI 页面"></div><div class="modal-actions"><button class="btn danger" id="resetMetadata">恢复自动提取</button><button class="btn" data-close="metadataModal">取消</button><button class="btn primary" id="saveMetadata">保存修订</button></div></div></div><div class="modal-back" id="analyzeModal"><div class="modal"><h2>分析论文</h2><div class="field"><label>研究关注（可选）</label><textarea id="researchContext" placeholder="例如：我关心大语言模型如何影响社会互动"></textarea></div><div class="field"><label>在此文本后排除（可选，用于一个 PDF 包含相邻文章）</label><input id="excludeMarker" placeholder="例如：Revisiting Description: Data Deep Description in Quantitative Research"></div><div class="check-field"><label class="checkbox-label" for="forceRun"><input type="checkbox" id="forceRun" style="appearance:auto;-webkit-appearance:checkbox;width:18px!important;height:18px!important;min-width:18px!important;max-width:18px!important;margin:0!important;padding:0!important;justify-self:start"><span>忽略缓存，重新调用 Lite + Pro（通常不要勾）</span></label></div><div class="ui-build">UI build: checkbox-hotfix-2</div><div class="modal-actions"><button class="btn" data-close="analyzeModal">取消</button><button class="btn primary" id="runAnalyze">开始分析</button></div></div></div>
<div class="modal-back" id="jobModal"><div class="modal"><h2 id="jobTitle">处理中</h2><div class="job-progress"><div class="progress-meta"><span id="jobStage">准备中…</span><span id="jobElapsed">0s</span></div><div class="progress-track indeterminate" id="jobTrack"><div class="progress-bar" id="jobBar"></div></div><div class="progress-hint" id="jobHint">当前页面仍显示上一次保存的 Paper Card；新分析完成后会自动刷新。</div></div><div class="log" id="jobLog">准备中…</div><div class="modal-actions"><button class="btn" id="jobClose" style="display:none">完成</button></div></div></div>
<div class="modal-back" id="healthModal"><div class="modal"><div class="split"><h2>数据库健康</h2><button class="btn" data-close="healthModal">关闭</button></div><div id="healthBody">正在检查…</div></div></div><div class="modal-back" id="dashboardModal"><div class="modal"><div class="split"><h2>研究控制面板</h2><button class="btn" data-close="dashboardModal">关闭</button></div><div id="dashboardBody">正在加载…</div></div></div><div class="modal-back" id="modelSettingsModal"><div class="modal"><div class="split"><h2>模型设置</h2><button class="btn" data-close="modelSettingsModal">关闭</button></div><p class="small">Lite 和 Pro 可以使用同一个服务商与 Base URL，但模型名分开。API Key 保存到本地 .env，不会在界面完整显示。</p><div class="field"><label>API Key</label><input id="settingsApiKey" type="password" placeholder="留空表示保持现有 Key"></div><div class="field"><label>Base URL</label><input id="settingsBaseUrl" placeholder="https://.../chat/completions 的上级地址"></div><div class="field"><label>Lite 模型</label><input id="settingsLiteModel"><div class="small">基础事实提取：标题、摘要、方法、样本、发现、关键词。</div></div><div class="field"><label>Pro 模型</label><input id="settingsProModel"><div class="small">深度审读：方法合理性、贡献、局限、阅读价值。</div></div><div class="modal-actions"><button class="btn" data-close="modelSettingsModal">取消</button><button class="btn primary" id="saveModelSettings">保存模型设置</button></div></div></div>
<div class="toast" id="toast"></div>
<script>
const state={papers:[],selected:null,detail:null,tab:'overview'};
const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(msg){const t=$('#toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),3200)}
async function api(url,opts={}){const r=await fetch(url,opts);const ct=r.headers.get('content-type')||'';const data=ct.includes('json')?await r.json():await r.text();if(!r.ok)throw new Error(data.error||data||`HTTP ${r.status}`);return data}
function openModal(id){$('#'+id).classList.add('open')}function closeModal(id){$('#'+id).classList.remove('open')}
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>closeModal(b.dataset.close));
function fmtBytes(n){n=Number(n||0);const u=['B','KiB','MiB','GiB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return (i? n.toFixed(2):Math.round(n))+' '+u[i]}
async function loadPapers(){const d=await api('/api/papers');state.papers=d.papers;renderList();if(state.selected){const exists=state.papers.some(p=>p.id===state.selected);if(exists)await selectPaper(state.selected);else{state.selected=null;renderEmpty()}}else if(state.papers.length){await selectPaper(state.papers[0].id)}}
function renderList(){const q=$('#search').value.trim().toLowerCase();const list=$('#paperList');list.innerHTML='';for(const p of state.papers.filter(p=>`${p.title} ${p.original_name} ${(p.authors||[]).join(' ')}`.toLowerCase().includes(q))){const el=document.createElement('div');el.className='paper-item'+(p.id===state.selected?' active':'');el.onclick=()=>selectPaper(p.id);el.innerHTML=`<div class="paper-title">${esc(p.title)}</div><div class="paper-meta">${esc((p.authors||[]).join('、'))}${p.year?' · '+esc(p.year):''}<br><span class="badge ${p.has_card?'ok':'muted'}">${p.has_card?'已分析':'未分析'}</span>${p.warnings?`<span class="badge warn">${p.warnings} warning</span>`:''}<span class="badge muted">${p.page_count} 页</span></div>`;list.appendChild(el)}}
function renderEmpty(){state.detail=null;$('#main').innerHTML=`<div class="empty"><h1>你的论文工作台</h1><p>左侧选择一篇论文，或点击“导入 PDF”。分析、查看证据、导出和数据库检查都可以在这里完成，不必再逐条输入命令。</p></div>`}
async function selectPaper(id){state.selected=id;localStorage.setItem('sra.selected',id);renderList();try{state.detail=await api('/api/papers/'+encodeURIComponent(id));renderPaper()}catch(e){toast(e.message)}}
function keywordTokens(values){const out=[];for(const raw of (values||[])){for(const part of String(raw).split(/[；;，,、|]+/)){const t=part.trim();if(!t)continue;if(/[\u4e00-\u9fff]/.test(t)&&/\s+/.test(t)){for(const piece of t.split(/\s+/)){if(piece.trim())out.push(piece.trim())}}else out.push(t)}}return [...new Set(out)]}
function referenceDisplayText(value){return String(value??'').replace(/\s+/g,' ').trim().replace(/\s*([，。；：！？、])\s*/g,'$1').replace(/\s*([《》〈〉（）])\s*/g,'$1').replace(/\s+([,.!?;:)\]])/g,'$1').replace(/([\[(])\s+/g,'$1')}
function metadataGrid(m,overrides={}){const rows=[['journal','期刊',m.journal],['year','年份',m.year],['volume','卷',m.volume],['issue','期',m.issue],['pages','页码',m.pages],['doi','DOI',m.doi]];return `<div class="grid">${rows.map(([field,k,v])=>`<div class="card"><div class="k">${k}${Object.prototype.hasOwnProperty.call(overrides,field)?' · 手动':''}</div><div class="v">${esc(v??'—')}</div></div>`).join('')}</div>`}
function evidenceHTML(evs){if(!evs||!evs.length)return'';return evs.map(e=>`<div class="evidence"><span class="page">PDF p.${esc(e.page_number)}</span> · ${esc(e.quote)}</div>`).join('')}
function claimsHTML(items){if(!items||!items.length)return'<div class="small">暂无内容</div>';return items.map(c=>`<div class="claim"><div class="claim-head"><span class="claim-field">${esc(c.field_name)}</span><span class="badge ${c.verification==='SUPPORTED'?'ok':'muted'}">${esc(c.verification)}</span></div><div class="claim-text">${esc(c.statement)}</div>${evidenceHTML(c.evidence)}</div>`).join('')}
function renderPaper(){const {paper,card}=state.detail;const m=card?.metadata||{};const title=m.title||paper.original_name;const authors=(m.authors||[]).join('、');$('#main').innerHTML=`<section class="hero"><div class="eyebrow">${esc(m.journal||'Imported paper')}${m.year?' · '+esc(m.year):''}</div><h1>${esc(title)}</h1><div class="authors">${esc(authors||paper.original_name)} · ${paper.page_count} PDF pages</div><div class="small" style="margin-top:6px">Parser: ${esc(paper.parser_version||'unknown')}${card?.prompt_version?` · Card: ${esc(card.prompt_version)}`:''}</div><div class="actions"><button class="btn primary" id="analyzeBtn">${card?'重新分析':'开始分析'}</button><button class="btn" id="reparseBtn">重建解析</button><button class="btn" id="editMetadataBtn">编辑元数据</button><button class="btn" id="extractRefsBtn">提取参考文献</button><a class="btn" target="_blank" href="/api/papers/${encodeURIComponent(paper.id)}/pdf">打开 PDF</a>${card?`<a class="btn" href="/api/papers/${encodeURIComponent(paper.id)}/card.json">导出 Card JSON</a>`:''}<a class="btn" href="/api/papers/${encodeURIComponent(paper.id)}/text.txt">导出解析文本</a><a class="btn" href="/api/papers/${encodeURIComponent(paper.id)}/references.bib">导出 Zotero BibTeX</a><button class="btn danger" id="deletePaperBtn">删除论文</button></div></section><nav class="tabs"><button class="tab" data-tab="overview">概览</button><button class="tab" data-tab="facts">事实与证据</button><button class="tab" data-tab="review">AI 审读</button><button class="tab" data-tab="references">参考文献</button><button class="tab" data-tab="tables">表格</button><button class="tab" data-tab="raw">原始 Card</button></nav><section class="panel" id="panel"></section>`;$('#analyzeBtn').onclick=showAnalyze;$('#reparseBtn').onclick=reparsePaper;$('#editMetadataBtn').onclick=showMetadataEditor;$('#extractRefsBtn').onclick=extractRefs;$('#deletePaperBtn').onclick=deleteSelectedPaper;document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{state.tab=b.dataset.tab;renderTab()});renderTab()}
function renderTab(){document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===state.tab));const {paper,card}=state.detail;const p=$('#panel');if(!card&&state.tab!=='references'){p.innerHTML=`<div class="notice">这篇 PDF 已导入并解析，但还没有 Paper Card。你仍然可以查看解析状态，或切换到“参考文献”。</div><div class="grid"><div class="card"><div class="k">解析器</div><div class="v">${esc(paper.parser_version||'unknown')}</div></div><div class="card"><div class="k">页数</div><div class="v">${esc(paper.page_count)}</div></div><div class="card"><div class="k">解析状态</div><div class="v">${esc(paper.parse_status)}</div></div></div>`;return}const m=card?.metadata||{};if(state.tab==='overview'){const ov=state.detail.metadata_overrides||{};const hasOverride=ov.values&&Object.keys(ov.values).length>0;p.innerHTML=`${card.warnings?.length?`<div class="notice">${card.warnings.map(esc).join('<br>')}</div>`:`<div class="notice good">Paper Card 当前为 0 warnings。</div>`}<div class="metadata-toolbar"><button class="btn" id="editMetadataInline">编辑元数据</button>${hasOverride?'<span class="manual-tag">含手动修订</span>':''}${ov.source_note?`<span class="source-note">来源：${esc(ov.source_note)}</span>`:''}</div>${metadataGrid(m,ov.values||{})}<div class="section"><h2>关键词</h2><div class="chips">${keywordTokens(m.keywords).length?keywordTokens(m.keywords).map(x=>`<span class="chip">${esc(x)}</span>`).join(''):'<span class="small">未提取</span>'}</div></div><div class="section"><h2>摘要</h2><div class="card prose">${esc(m.abstract||'未提取')}</div></div><div class="grid"><div class="card"><div class="k">Basic facts</div><div class="v">${card.basic_facts.length}</div></div><div class="card"><div class="k">Evidence spans</div><div class="v">${card.evidence_spans.length}</div></div><div class="card"><div class="k">AI analysis</div><div class="v">${card.analysis.length}</div></div></div>`;$('#editMetadataInline').onclick=showMetadataEditor}else if(state.tab==='facts'){p.innerHTML=`<div class="section"><h2>书目信息证据</h2>${claimsHTML(card.metadata_claims)}</div><div class="section"><h2>基础事实</h2>${claimsHTML(card.basic_facts)}</div>`}else if(state.tab==='review'){p.innerHTML=`<div class="section"><h2>方法与贡献审读</h2>${claimsHTML(card.analysis)}</div><div class="section"><h2>潜在局限</h2>${claimsHTML(card.limitations)}</div><div class="section"><h2>阅读建议</h2>${card.reading_recommendation?claimsHTML([card.reading_recommendation]):'<div class="small">暂无</div>'}</div>`}else if(state.tab==='references'){const refs=state.detail.references||[];const mentions=state.detail.citation_mentions||[];p.innerHTML=`<div class="actions"><button class="btn primary" id="extractRefsInline">重新提取</button><a class="btn" href="/api/papers/${encodeURIComponent(paper.id)}/references.json">导出 JSON</a><a class="btn" href="/api/papers/${encodeURIComponent(paper.id)}/references.bib">导出 Zotero BibTeX</a></div><div class="notice ${refs.length?'good':''}">${refs.length?`已保存 ${refs.length} 条参考文献，发现 ${mentions.length} 个正文引用位置。`:'尚未提取参考文献。点击“提取参考文献”。'}</div>${refs.map(r=>`<div class="claim"><div class="claim-head"><span class="claim-field">#${esc(r.ordinal)}</span><span class="badge ${r.parse_status==='structured'?'ok':'warn'}">${esc(r.parse_status)}</span>${r.year?`<span class="badge muted">${esc(r.year)}</span>`:''}</div><div class="claim-text">${referenceDisplayText(r.raw_text)}</div><div class="small">PDF p.${esc(r.source_page)} · ${esc(r.source_block_id)}${r.doi?` · DOI ${esc(r.doi)}`:''}</div></div>`).join('')||'<div class="small">暂无记录</div>'}`;$('#extractRefsInline').onclick=extractRefs}else if(state.tab==='tables'){p.innerHTML=(card.tables||[]).length?(card.tables||[]).map(t=>`<div class="section"><h2>PDF p.${esc(t.page_number)}</h2>${t.table_rows?`<div class="table-wrap"><table class="data-table">${t.table_rows.map(r=>`<tr>${r.map(c=>`<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</table></div>`:`<div class="card prose">${esc(t.text)}</div>`}</div>`).join(''):'<div class="small">没有解析到表格。</div>'}else{p.innerHTML=`<pre class="raw">${esc(JSON.stringify(card,null,2))}</pre>`}}
function showMetadataEditor(){const {card,metadata_overrides}=state.detail||{};if(!card){toast('请先完成一次论文分析。');return}const m=card.metadata||{};$('#metaJournal').value=m.journal||'';$('#metaYear').value=m.year||'';$('#metaVolume').value=m.volume||'';$('#metaIssue').value=m.issue||'';$('#metaPages').value=m.pages||'';$('#metaDoi').value=m.doi||'';$('#metaTitle').value=m.title||'';$('#metaAuthors').value=(m.authors||[]).join('、');$('#metaKeywords').value=(m.keywords||[]).join('；');$('#metaSource').value=metadata_overrides?.source_note||'';openModal('metadataModal')}
$('#saveMetadata').onclick=async()=>{const id=state.selected;if(!id)return;const values={journal:$('#metaJournal').value,year:$('#metaYear').value,volume:$('#metaVolume').value,issue:$('#metaIssue').value,pages:$('#metaPages').value,doi:$('#metaDoi').value,title:$('#metaTitle').value,authors:$('#metaAuthors').value,keywords:$('#metaKeywords').value};try{await api(`/api/papers/${encodeURIComponent(id)}/metadata`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({values,source_note:$('#metaSource').value})});closeModal('metadataModal');await selectPaper(id);toast('元数据修订已保存。重新分析不会覆盖。')}catch(e){toast(e.message)}};
$('#resetMetadata').onclick=async()=>{const id=state.selected;if(!id)return;if(!confirm('清除这篇论文的全部手动元数据修订，恢复自动提取结果？'))return;try{await api(`/api/papers/${encodeURIComponent(id)}/metadata/reset`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});closeModal('metadataModal');await selectPaper(id);toast('已恢复自动提取元数据。')}catch(e){toast(e.message)}};
async function reparsePaper(){const id=state.selected;if(!id)return;const name=state.detail?.paper?.original_name||id;if(!confirm(`重建“${name}”的解析？\n\n这会强制重新运行当前 Docling 解析器，并覆盖 source blocks；旧 Paper Card 和该论文的 Lite/Pro 缓存会被清除。原 PDF 和用户笔记会保留。\n\n重建完成后需要再点一次“开始分析”。`))return;try{const d=await api(`/api/papers/${encodeURIComponent(id)}/reparse`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});watchJob(d.job_id,'重建 PDF 解析')}catch(e){toast(e.message)}}
async function deleteSelectedPaper(){const id=state.selected;if(!id)return;const paper=state.detail?.paper;const name=paper?.original_name||id;if(!confirm(`永久删除“${name}”？\n\n将删除：数据库论文记录、source blocks、Paper Card、模型缓存、用户笔记，以及 SRA data/papers 中保存的 PDF。\n\n此操作不可撤销。`))return;try{const d=await api(`/api/papers/${encodeURIComponent(id)}/delete`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delete_file:true})});localStorage.removeItem('sra.context.'+id);localStorage.removeItem('sra.exclude.'+id);localStorage.removeItem('sra.selected');state.selected=null;state.detail=null;await loadPapers();if(state.papers.length){await selectPaper(state.papers[0].id)}else{$('#main').innerHTML='<div class="empty"><h1>还没有论文</h1><p>点击左上角“导入 PDF”开始。</p></div>'}toast(d.file_warning||'论文已彻底删除。')}catch(e){toast(e.message)}}
function showAnalyze(){const id=state.selected;$('#researchContext').value=localStorage.getItem('sra.context.'+id)||'';$('#excludeMarker').value=localStorage.getItem('sra.exclude.'+id)||'';$('#forceRun').checked=false;openModal('analyzeModal')}
$('#runAnalyze').onclick=async()=>{const id=state.selected;const context=$('#researchContext').value;const marker=$('#excludeMarker').value;localStorage.setItem('sra.context.'+id,context);localStorage.setItem('sra.exclude.'+id,marker);closeModal('analyzeModal');try{const d=await api(`/api/papers/${encodeURIComponent(id)}/analyze`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({research_context:context,exclude_after_text:marker,force:$('#forceRun').checked})});watchJob(d.job_id,'论文分析')}catch(e){toast(e.message)}};
function jobVisual(messages,status){const last=(messages||[]).at(-1)||'';const all=(messages||[]).join('\n');if(status==='done')return{stage:'完成',pct:100,ind:false};if(status==='error')return{stage:'发生错误',pct:100,ind:false};if(/Saving Paper Card|Paper Card saved/i.test(all))return{stage:'保存结果',pct:96,ind:false};if(/Pro analysis/i.test(last)||/Pro analysis: sending/i.test(all))return{stage:'Pro 深度审读',pct:82,ind:/sending request/i.test(last)};if(/Metadata layout recovery|Preparing evidence ledger/i.test(last))return{stage:'整理证据与元数据',pct:52,ind:false};if(/Lite request/i.test(last)||/Lite request.*sending/i.test(all))return{stage:'Lite 事实提取',pct:62,ind:/sending request/i.test(last)};if(/Docling:/i.test(last)){if(/finished|parsed .*structured/i.test(last))return{stage:'Docling 结构化解析完成',pct:44,ind:false};return{stage:'Docling 文档解析',pct:28,ind:true}}if(/Rebuild:/i.test(last))return{stage:'重建 PDF 解析',pct:/complete/i.test(last)?96:18,ind:!/complete/i.test(last)};if(/Document parser refresh|required|parsing complete/i.test(last))return{stage:'准备文档解析',pct:12,ind:true};if(/Import: parsing|PyMuPDF fallback/i.test(last))return{stage:'解析 PDF',pct:30,ind:true};if(/Import:/i.test(last))return{stage:'导入 PDF',pct:14,ind:true};return{stage:'准备中',pct:6,ind:true}}
function updateJobProgress(messages,status,started){const v=jobVisual(messages,status);$('#jobStage').textContent=v.stage;$('#jobBar').style.width=v.pct+'%';$('#jobTrack').classList.toggle('indeterminate',v.ind);$('#jobElapsed').textContent=Math.max(0,Math.round((Date.now()-started)/1000))+'s'}
async function watchJob(jobId,title){const started=Date.now();$('#jobTitle').textContent=title;$('#jobLog').textContent='Preparing...';$('#jobClose').style.display='none';$('#jobStage').textContent='Preparing...';$('#jobElapsed').textContent='0s';$('#jobTrack').classList.add('indeterminate');$('#jobBar').style.width='6%';openModal('jobModal');while(true){let j;try{j=await api('/api/jobs/'+jobId)}catch(e){$('#jobLog').textContent=e.message;$('#jobClose').style.display='inline-block';updateJobProgress([], 'error', started);return}updateJobProgress(j.messages||[],j.status,started);$('#jobLog').textContent=(j.messages||[]).join('\n')||'Processing...';$('#jobLog').scrollTop=$('#jobLog').scrollHeight;if(j.status==='done'){if(j.result)$('#jobLog').textContent+='\n\nCompleted.';$('#jobClose').style.display='inline-block';await loadPapers();if($('#dashboardModal').classList.contains('open'))$('#dashboardBtn').click();if(j.result?.paper_id)await selectPaper(j.result.paper_id);return}if(j.status==='error'){$('#jobLog').textContent+='\n\nError: '+j.error;$('#jobClose').style.display='inline-block';await loadPapers();if($('#dashboardModal').classList.contains('open'))$('#dashboardBtn').click();toast('Task failed: '+j.error);return}await new Promise(r=>setTimeout(r,900))}}
$('#jobClose').onclick=()=>closeModal('jobModal');
$('#importBtn').onclick=()=>$('#fileInput').click();$('#fileInput').onchange=async e=>{const f=e.target.files[0];if(!f)return;const form=new FormData();form.append('file',f);try{const d=await api('/api/import',{method:'POST',body:form});watchJob(d.job_id,'导入 PDF')}catch(err){toast(err.message)}finally{e.target.value=''}};
async function uploadBatch(files){if(!files.length)return;const pdfs=files.filter(f=>f.name.toLowerCase().endsWith('.pdf'));if(!pdfs.length){toast('没有检测到 PDF 文件。请多选 PDF，或选择包含 PDF 的文件夹。');return}const form=new FormData();pdfs.forEach(f=>form.append('files',f));try{const d=await api('/api/import-batch',{method:'POST',body:form});watchJob(d.job_id,`批量导入 ${pdfs.length} 个 PDF`)}catch(err){toast(err.message)}}
$('#batchImportBtn').onclick=()=>$('#batchFileInput').click();$('#folderImportBtn').onclick=()=>$('#folderFileInput').click();$('#batchFileInput').onchange=async e=>{await uploadBatch([...e.target.files]);e.target.value=''};$('#folderFileInput').onchange=async e=>{await uploadBatch([...e.target.files]);e.target.value=''};
async function extractRefs(){const id=state.selected;if(!id)return;try{await api(`/api/papers/${encodeURIComponent(id)}/extract-references`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await selectPaper(id);state.tab='references';renderTab();toast('参考文献提取完成。')}catch(e){toast(e.message)}}
$('#search').oninput=renderList;
$('#modelSettingsBtn').onclick=async()=>{openModal('modelSettingsModal');try{const d=await api('/api/model-settings');$('#settingsApiKey').value='';$('#settingsApiKey').placeholder=d.has_api_key?`已设置：${d.api_key_masked}，留空保持不变`:'请输入 API Key';$('#settingsBaseUrl').value=d.api_base_url;$('#settingsLiteModel').value=d.lite_model;$('#settingsProModel').value=d.pro_model}catch(e){toast(e.message)}};
$('#saveModelSettings').onclick=async()=>{try{const d=await api('/api/model-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:$('#settingsApiKey').value,api_base_url:$('#settingsBaseUrl').value,lite_model:$('#settingsLiteModel').value,pro_model:$('#settingsProModel').value})});closeModal('modelSettingsModal');toast('模型设置已保存。下一次分析会使用新配置。')}catch(e){toast(e.message)}};
$('#dashboardBtn').onclick=async()=>{openModal('dashboardModal');$('#dashboardBody').innerHTML='正在加载…';try{const d=await api('/api/dashboard');$('#dashboardBody').innerHTML=`<div class="health-grid"><div class="health-stat"><span class="small">论文总数</span><b>${d.papers}</b></div><div class="health-stat"><span class="small">已分析</span><b>${d.analyzed}</b></div><div class="health-stat"><span class="small">待分析</span><b>${d.pending_analysis}</b></div><div class="health-stat"><span class="small">待复核</span><b>${d.needs_review}</b></div><div class="health-stat"><span class="small">参考文献</span><b>${d.references}</b></div></div><div class="actions"><button class="btn primary" id="batchAnalyzeBtn">批量分析待处理论文</button><button class="btn" id="batchRefsBtn">批量提取参考文献</button></div><div class="small">批量分析会逐篇执行 Lite → Pro，并保留每篇失败结果，不会因一篇失败中断队列。</div>`;$('#batchAnalyzeBtn').onclick=async()=>{try{const x=await api('/api/batch-analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});closeModal('dashboardModal');watchJob(x.job_id,'批量分析')}catch(e){toast(e.message)}};$('#batchRefsBtn').onclick=async()=>{try{const ids=d.items.map(x=>x.id);for(const id of ids)await api(`/api/papers/${encodeURIComponent(id)}/extract-references`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});toast('批量参考文献提取完成。');await loadPapers()}catch(e){toast(e.message)}}}catch(e){$('#dashboardBody').textContent=e.message}};
$('#healthBtn').onclick=async()=>{openModal('healthModal');$('#healthBody').innerHTML='正在检查…';try{const r=await api('/api/doctor');renderHealth(r)}catch(e){$('#healthBody').textContent=e.message}};
function renderHealth(r){const c=r.counts,s=r.storage,k=r.cache,i=r.issues;const issueCount=Object.values(i).reduce((n,v)=>n+(Array.isArray(v)?v.length:Object.keys(v||{}).length),0);$('#healthBody').innerHTML=`<div class="notice ${r.status==='ok'?'good':''}">${r.status==='ok'?'仓库检查正常。':`有 ${issueCount} 项需要检查。`}</div><div class="health-grid"><div class="health-stat"><span class="small">论文</span><b>${c.papers}</b></div><div class="health-stat"><span class="small">Paper Cards</span><b>${c.paper_cards}</b></div><div class="health-stat"><span class="small">Model cache</span><b>${c.model_runs}</b></div><div class="health-stat"><span class="small">数据库</span><b>${fmtBytes(r.database_bytes)}</b></div><div class="health-stat"><span class="small">PDF</span><b>${fmtBytes(s.papers_dir_pdf_bytes)}</b></div><div class="health-stat"><span class="small">旧缓存</span><b>${k.stale_runs}</b></div></div><div class="section"><h2>缓存清理</h2><p class="small">只会操作 model_runs，不碰 PDF、Paper Card、source blocks 或 notes。</p><div class="actions"><button class="btn" id="previewPrune">预览旧缓存</button><button class="btn danger" id="applyPrune">清理旧缓存</button><button class="btn" id="deepDoctor">深度校验 PDF SHA-256</button></div><div id="healthExtra" class="small" style="margin-top:12px"></div></div>`;$('#previewPrune').onclick=()=>prune(false);$('#applyPrune').onclick=()=>prune(true);$('#deepDoctor').onclick=async()=>{const x=$('#healthExtra');x.textContent='正在计算 PDF SHA-256…';try{const d=await api('/api/doctor?deep=1');x.textContent=d.status==='ok'?'深度检查通过：所有保存的 PDF 哈希与数据库一致。':JSON.stringify(d.issues,null,2)}catch(e){x.textContent=e.message}}}
async function prune(apply){const x=$('#healthExtra');if(apply&&!confirm('只删除旧 prompt 版本的模型缓存。不会删除 PDF、Paper Card、source blocks 或 notes。继续？'))return;try{const d=await api('/api/prune-cache',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all_runs:false,apply,vacuum:apply})});x.textContent=apply?`已删除 ${d.deleted_runs} 条旧缓存；剩余 ${d.total_runs_after} 条。`:`可清理 ${d.matched_runs} 条旧缓存，约 ${fmtBytes(d.matched_bytes)}。`;if(apply){const r=await api('/api/doctor');renderHealth(r)}}catch(e){x.textContent=e.message}}
$('#closeBtn').onclick=async()=>{if(!confirm('关闭本地 SRA 界面？'))return;try{await api('/api/shutdown',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})}catch{}document.body.innerHTML='<div class="empty"><h1>SRA 已关闭</h1><p>这个浏览器标签可以关掉了。</p></div>'};
(async()=>{state.selected=localStorage.getItem('sra.selected');try{await loadPapers()}catch(e){toast(e.message)}})();
</script>
</body>
</html>'''