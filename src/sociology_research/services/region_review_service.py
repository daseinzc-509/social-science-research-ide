"""PDF 局部复核与复核对照分析应用服务。

在服务初始化时统一实例化并复用 VersionedRegionReviewStore 与 ReviewReanalysisStore，
消除此前每次 HTTP 请求在路由层重复初始化表结构与迁移脚本的开销和锁竞争。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Settings
from ..llm_client import ModelRequestError
from ..repository import ResearchRepository
from .papers import PaperService
from .region_review import (
    PendingPreview,
    PreviewExpired,
    RegionBounds,
    as_pdf_box,
    intersecting_blocks,
    pdf_page_dimensions,
    preview_registry,
    recognize_region,
    region_original_text,
    render_png,
)
from .region_review_reanalysis import (
    ReanalysisError,
    ReviewReanalysisStore,
    _public_provider,
    build_plan,
    run_pro_comparison,
)
from .region_review_versioning import (
    ReviewConflict,
    VersionedRegionReviewStore,
    source_snapshots,
)


class RegionReviewServiceError(Exception):
    """区域复核领域异常，携带规范 HTTP 状态码与安全错误信息。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


class RegionReviewService:
    """封装 v3/v4/v5 区域复核全链路用例并复用持久化 Store。"""

    def __init__(self, repository: ResearchRepository, papers: PaperService) -> None:
        self.repository = repository
        self.papers = papers
        self.review_store = VersionedRegionReviewStore(repository.database_path)
        self.reanalysis_store = ReviewReanalysisStore(repository.database_path)

    def _paper_and_pdf(self, paper_id: str) -> tuple[Any, Path]:
        record = self.repository.get_paper(paper_id)
        if record is None:
            raise RegionReviewServiceError(404, "Paper not found")
        path = Path(self.papers.pdf_path(paper_id))
        if not path.is_file():
            raise RegionReviewServiceError(404, "Stored PDF not found")
        return record, path

    @staticmethod
    def _guard_integrity(path: Path, stored_sha256: str) -> None:
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != stored_sha256:
            raise RegionReviewServiceError(
                409,
                "Stored PDF differs from imported paper; re-import before reviewing",
            )

    @staticmethod
    def _configured_pro() -> tuple[str, str, str]:
        try:
            return Settings.from_environment().require_pro_configuration()
        except (ValueError, RuntimeError) as exc:
            raise RegionReviewServiceError(409, f"Pro model is not configured: {str(exc)[:260]}") from None

    def _build_reanalysis_plan(self, record: Any, model: str, url: str) -> dict[str, Any]:
        try:
            return build_plan(
                self.repository,
                record.id,
                model,
                review_store=self.review_store,
                provider_digest=hashlib.sha256(url.strip().rstrip("/").encode("utf-8")).hexdigest(),
            )
        except ReanalysisError as exc:
            raise RegionReviewServiceError(409, str(exc)) from None

    def render_page(self, paper_id: str, page_number: int, *, scale: float = 1.4) -> tuple[bytes, tuple[float, float], int]:
        _record, pdf_path = self._paper_and_pdf(paper_id)
        try:
            return render_png(pdf_path, page_number, scale=scale)
        except ValueError as exc:
            raise RegionReviewServiceError(422, str(exc)) from exc

    def get_page_blocks(self, paper_id: str, page_number: int) -> dict[str, Any]:
        _record, pdf_path = self._paper_and_pdf(paper_id)
        try:
            pdf_page_dimensions(pdf_path, page_number)
        except ValueError as exc:
            raise RegionReviewServiceError(422, str(exc)) from exc
        blocks = [
            b for b in self.repository.get_source_blocks(paper_id)
            if b.page_number == page_number
        ]
        return {
            "page_number": page_number,
            "source_blocks": [
                {"id": block.id, "text": block.text[:20000], "bbox": block.bbox, "role": block.role}
                for block in blocks[:120]
            ],
            "truncated": len(blocks) > 120,
            "provenance": "DOCLING_SOURCE",
        }

    def preview_region(
        self,
        paper_id: str,
        page_number: int,
        *,
        bounds: RegionBounds,
        manual_only: bool = False,
        recognizer: Callable[..., Any] = recognize_region,
    ) -> dict[str, Any]:
        record, pdf_path = self._paper_and_pdf(paper_id)
        self._guard_integrity(pdf_path, record.sha256)
        try:
            if manual_only:
                dimensions = pdf_page_dimensions(pdf_path, page_number)
                candidate, score = "", None
            else:
                candidate, score, dimensions = recognizer(pdf_path, page_number, bounds)
        except ValueError as exc:
            raise RegionReviewServiceError(422, str(exc)) from exc
        except (ImportError, OSError, RuntimeError) as exc:
            raise RegionReviewServiceError(
                503, f"Region OCR unavailable: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc

        rectangle = as_pdf_box(bounds, dimensions)
        matches = intersecting_blocks(self.repository.get_source_blocks(paper_id), rectangle)
        if len(matches) > 256:
            raise RegionReviewServiceError(
                422, "Selection contains too many SourceBlocks; select a smaller region"
            )
        identifiers = tuple(block.id for block in matches)
        fingerprints = source_snapshots(matches, identifiers)
        stat = pdf_path.stat()
        pending = PendingPreview(
            paper_id=paper_id,
            pdf_sha256=record.sha256,
            file_size=stat.st_size,
            file_mtime_ns=stat.st_mtime_ns,
            page_number=page_number,
            bounds=bounds,
            original_text=region_original_text(matches),
            ocr_text=candidate,
            source_block_ids=identifiers,
            created=time.monotonic(),
            parser_version=record.parser_version,
            source_fingerprints=tuple(sorted(fingerprints.items())),
        )
        identifier = preview_registry.add(pending)
        return {
            "preview_id": identifier,
            "page_number": page_number,
            "bounds": bounds.model_dump(),
            "original_text": pending.original_text,
            "ocr_candidate": candidate,
            "ocr_mean_score": score,
            "source_block_ids": list(pending.source_block_ids),
            "provenance": "MANUAL_TRANSCRIPTION_DRAFT" if manual_only else "OCR_CANDIDATE_UNVERIFIED",
            "warning": "Review against the original PDF before confirming; results are not evidence automatically.",
        }

    def confirm_region(
        self,
        paper_id: str,
        *,
        preview_id: str,
        corrected_text: str,
        researcher_note: str,
    ) -> dict[str, Any]:
        record, pdf_path = self._paper_and_pdf(paper_id)
        try:
            pending = preview_registry.get(preview_id, paper_id)
        except PreviewExpired as exc:
            raise RegionReviewServiceError(410, str(exc)) from exc
        stat = pdf_path.stat()
        if (
            record.sha256 != pending.pdf_sha256
            or stat.st_size != pending.file_size
            or stat.st_mtime_ns != pending.file_mtime_ns
        ):
            raise RegionReviewServiceError(409, "PDF changed since OCR preview; please recognize again")
        self._guard_integrity(pdf_path, record.sha256)
        if pending.parser_version != record.parser_version:
            raise RegionReviewServiceError(409, "Parser changed since region preview; select again")
        try:
            actual = source_snapshots(
                self.repository.get_source_blocks(paper_id), pending.source_block_ids
            )
            if actual != dict(pending.source_fingerprints):
                raise ReviewConflict("SourceBlock changed since region preview; select again")
            identifier = self.review_store.add(
                paper_id, pending, corrected_text.strip(), researcher_note.strip()
            )
        except ReviewConflict as exc:
            raise RegionReviewServiceError(409, str(exc)) from exc
        preview_registry.consume(preview_id)
        return {
            "id": identifier,
            "page_number": pending.page_number,
            "provenance": "HUMAN_REVIEWED",
            "revision_no": 1,
            "source_link_status": "CURRENT" if pending.source_block_ids else "PAGE_ONLY",
            "applied_to_evidence": False,
            "message": "Researcher correction saved separately; source blocks and Paper Card unchanged.",
        }

    def list_reviews(self, paper_id: str) -> dict[str, Any]:
        record, path = self._paper_and_pdf(paper_id)
        records = self.review_store.list_with_links(
            paper_id, record, self.repository.get_source_blocks(paper_id)
        )
        try:
            self._guard_integrity(path, record.sha256)
        except RegionReviewServiceError:
            for item in records:
                item["link_status"] = "PDF_CHANGED"
            return {"reviews": records, "warning": "Stored PDF differs from imported original"}
        return {"reviews": records}

    def get_evidence_packet(self, paper_id: str) -> dict[str, Any]:
        record, path = self._paper_and_pdf(paper_id)
        self._guard_integrity(path, record.sha256)
        return self.review_store.evidence_packet(
            paper_id, record, self.repository.get_source_blocks(paper_id)
        )

    def revise_review(
        self,
        paper_id: str,
        review_id: str,
        *,
        expected_revision: int,
        corrected_text: str,
        researcher_note: str,
    ) -> dict[str, Any]:
        record, path = self._paper_and_pdf(paper_id)
        self._guard_integrity(path, record.sha256)
        try:
            return self.review_store.revise(
                paper_id,
                review_id,
                expected_revision,
                corrected_text.strip(),
                researcher_note.strip(),
                record,
            )
        except KeyError as exc:
            raise RegionReviewServiceError(404, str(exc)) from exc
        except ReviewConflict as exc:
            raise RegionReviewServiceError(409, str(exc)) from exc

    def revoke_review(self, paper_id: str, review_id: str) -> dict[str, Any]:
        self._paper_and_pdf(paper_id)
        try:
            self.review_store.revoke(paper_id, review_id)
        except KeyError as exc:
            raise RegionReviewServiceError(404, str(exc)) from exc
        return {"id": review_id, "provenance": "REVOKED", "applied_to_evidence": False}

    def preview_reanalysis(
        self,
        paper_id: str,
        *,
        pro_config_loader: Callable[[], tuple[str, str, str]] | None = None,
    ) -> dict[str, Any]:
        record, pdf_path = self._paper_and_pdf(paper_id)
        self._guard_integrity(pdf_path, record.sha256)
        loader = pro_config_loader or self._configured_pro
        _key, url, model = loader()
        plan = self._build_reanalysis_plan(record, model, url)
        return {
            "paper_id": paper_id,
            "snapshot_digest": plan["snapshot_digest"],
            "original_card_sha256": plan["original_card_sha256"],
            "review_count": plan["review_count"],
            "baseline_claim_count": plan["baseline_claim_count"],
            "baseline_claims": plan["baseline_claims"],
            "review_ids": plan["source_review_ids"],
            "pro_model": model,
            "provider": _public_provider(url),
            "provider_configuration_sha256": plan["provider_digest"],
            "outbound_payload": plan["prompt"],
            "approximate_input_chars": plan["approximate_input_chars"],
            "maximum_output_tokens": plan["maximum_output_tokens"],
            "estimated_charge": "Unknown: depends on API provider, model, and billed tokens",
            "will_send_to_external_api": True,
            "requires_explicit_consent": True,
            "only_one_pro_request": True,
            "runs_lite": False,
            "modifies_original_paper_card": False,
            "modifies_source_blocks": False,
            "note": (
                "Only approved original claims, linked source excerpts, and "
                "researcher-reviewed corrections will be sent. No full PDF upload."
            ),
        }

    def prepare_reanalysis_execution(
        self,
        paper_id: str,
        *,
        pro_config_loader: Callable[[], tuple[str, str, str]] | None = None,
    ) -> tuple[Any, Path, str, str, dict[str, Any]]:
        """校验论文与 PDF 完整性并构建复核对照计划，供路由层执行最终摘要比对。"""
        record, pdf_path = self._paper_and_pdf(paper_id)
        self._guard_integrity(pdf_path, record.sha256)
        loader = pro_config_loader or self._configured_pro
        key, url, model = loader()
        plan = self._build_reanalysis_plan(record, model, url)
        return record, pdf_path, key, url, plan

    def execute_reanalysis(
        self,
        record: Any,
        pdf_path: Path,
        plan: dict[str, Any],
        *,
        api_key: str,
        base_url: str,
        comparison_runner: Callable[..., Any] = run_pro_comparison,
    ) -> dict[str, Any]:
        try:
            comparison = comparison_runner(plan, api_key=api_key, base_url=base_url)
        except (ModelRequestError, ReanalysisError) as exc:
            raise RegionReviewServiceError(
                502,
                f"Pro comparative review failed: {str(exc)[:450]}. "
                "Request cost may already have been incurred; no result was saved.",
            ) from None

        self._guard_integrity(pdf_path, record.sha256)
        try:
            return self.reanalysis_store.save_if_current(
                self.repository, plan, comparison, review_store=self.review_store
            )
        except ReanalysisError as exc:
            raise RegionReviewServiceError(
                409,
                f"{str(exc)}. Model request may already have incurred a charge; "
                "no reviewed result was saved.",
            ) from None

    def list_reanalysis_reports(self, paper_id: str) -> dict[str, Any]:
        self._paper_and_pdf(paper_id)
        return {"reports": self.reanalysis_store.list_reports(paper_id)}

    def get_reanalysis_report(self, paper_id: str, analysis_id: str) -> dict[str, Any]:
        self._paper_and_pdf(paper_id)
        try:
            return self.reanalysis_store.get_report(paper_id, analysis_id)
        except KeyError as exc:
            raise RegionReviewServiceError(404, str(exc)) from None
