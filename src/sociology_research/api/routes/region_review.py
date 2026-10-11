"""Authenticated in-app region review API. Mounted under the existing /api/v1.

No external model endpoints, no plaintext PDF URL tokens, no automatic writes to
source_blocks/Paper Card. Authentication is inherited from DesktopTokenMiddleware.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from ...config import Settings
from ...llm_client import ModelRequestError
from ...services.region_review_reanalysis import (
    ReanalysisError, ReviewReanalysisStore, build_plan, run_pro_comparison,
    _public_provider,
)

from ...services.region_review import (
    ConfirmRequest, PendingPreview, PreviewExpired, PreviewRequest,
    as_pdf_box, intersecting_blocks, pdf_page_dimensions, preview_registry, recognize_region,
    region_original_text, render_png,
)
from ...services.region_review_versioning import (
    ReviewConflict, VersionedRegionReviewStore, source_snapshots,
)
import time

router = APIRouter(prefix="/papers/{paper_id}/region-review", tags=["region-review"])


class ReviseRequest(BaseModel):
    """Explicit researcher operation; never accepts OCR output without review."""
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    corrected_text: str = Field(min_length=1, max_length=12000)
    researcher_note: str = Field(default="", max_length=1500)

    @model_validator(mode="after")
    def meaningful(self):
        if not self.corrected_text.strip():
            raise ValueError("Review text cannot be blank")
        return self


class RunReviewedAnalysisRequest(BaseModel):
    """Requires a fresh plan plus explicit external-data and cost approval."""

    model_config = ConfigDict(extra="forbid")
    expected_snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approve_external_send: StrictBool
    acknowledge_api_cost: StrictBool


def _configured_pro() -> tuple[str, str, str]:
    try:
        return Settings.from_environment().require_pro_configuration()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            409, f"Pro model is not configured: {str(exc)[:260]}"
        ) from None


def _reanalysis_plan(services, record, model, url):
    review_store = VersionedRegionReviewStore(services.repository.database_path)
    try:
        plan = build_plan(
            services.repository, record.id, model,
            review_store=review_store,
            provider_digest=hashlib.sha256(url.strip().rstrip("/").encode("utf-8")).hexdigest(),
        )
    except ReanalysisError as exc:
        raise HTTPException(409, str(exc)) from None
    return review_store, plan


def _resources(request: Request, paper_id: str):
    services = request.app.state.services
    record = services.repository.get_paper(paper_id)
    if record is None:
        raise HTTPException(404, "Paper not found")
    path = Path(services.papers.pdf_path(paper_id))
    if not path.is_file():
        raise HTTPException(404, "Stored PDF not found")
    return services, record, path


def _guard_integrity(path: Path, stored_sha256: str):
    # Reject a replaced or altered PDF before associating a human correction
    # with the old paper's original evidence IDs.
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != stored_sha256:
        raise HTTPException(
            409, "Stored PDF differs from imported paper; re-import before reviewing"
        )


@router.get("/pages/{page_number}.png")
def page_image(request: Request, paper_id: str, page_number: int, scale: float = 1.4):
    _services, _record, pdf_path = _resources(request, paper_id)
    try:
        png, dimensions, count = render_png(pdf_path, page_number, scale=scale)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(
        png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, private",
            "X-SRA-Page-Count": str(count),
            "X-SRA-Page-Width": str(dimensions[0]),
            "X-SRA-Page-Height": str(dimensions[1]),
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/pages/{page_number}/blocks")
def page_blocks(request: Request, paper_id: str, page_number: int):
    services, _record, pdf_path = _resources(request, paper_id)
    # Use the original PDF's page count, not Docling's potentially partial
    # parse count. A failed-to-parse page is precisely what users must review.
    try:
        pdf_page_dimensions(pdf_path, page_number)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    blocks = [b for b in services.repository.get_source_blocks(paper_id)
              if b.page_number == page_number]
    return {
        "page_number": page_number,
        "source_blocks": [
            {"id": block.id, "text": block.text[:20000], "bbox": block.bbox, "role": block.role}
            for block in blocks[:120]
        ],
        "truncated": len(blocks) > 120,
        "provenance": "DOCLING_SOURCE",
    }


@router.post("/pages/{page_number}/preview")
def preview_region(request: Request, paper_id: str, page_number: int, payload: PreviewRequest):
    services, record, pdf_path = _resources(request, paper_id)
    _guard_integrity(pdf_path, record.sha256)
    try:
        if payload.manual_only:
            dimensions = pdf_page_dimensions(pdf_path, page_number)
            candidate, score = "", None
        else:
            candidate, score, dimensions = recognize_region(pdf_path, page_number, payload.bounds)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ImportError, OSError, RuntimeError) as exc:
        # Most likely a frozen OCR model/configuration file is missing.
        raise HTTPException(503, f"Region OCR unavailable: {type(exc).__name__}: {str(exc)[:300]}") from exc
    rectangle = as_pdf_box(payload.bounds, dimensions)
    matches = intersecting_blocks(services.repository.get_source_blocks(paper_id), rectangle)
    if len(matches) > 256:
        raise HTTPException(422, "Selection contains too many SourceBlocks; select a smaller region")
    identifiers = tuple(block.id for block in matches)
    fingerprints = source_snapshots(matches, identifiers)
    stat = pdf_path.stat()
    pending = PendingPreview(
        paper_id=paper_id,
        pdf_sha256=record.sha256,
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
        page_number=page_number,
        bounds=payload.bounds,
        original_text=region_original_text(matches),
        ocr_text=candidate,
        source_block_ids=tuple(block.id for block in matches),
        created=time.monotonic(),
        parser_version=record.parser_version,
        source_fingerprints=tuple(sorted(fingerprints.items())),
    )
    identifier = preview_registry.add(pending)
    return {
        "preview_id": identifier,
        "page_number": page_number,
        "bounds": payload.bounds.model_dump(),
        "original_text": pending.original_text,
        "ocr_candidate": candidate,
        "ocr_mean_score": score,
        "source_block_ids": pending.source_block_ids,
        "provenance": "MANUAL_TRANSCRIPTION_DRAFT" if payload.manual_only else "OCR_CANDIDATE_UNVERIFIED",
        "warning": "Review against the original PDF before confirming; results are not evidence automatically.",
    }


@router.post("/confirm")
def confirm_region(request: Request, paper_id: str, payload: ConfirmRequest):
    services, record, pdf_path = _resources(request, paper_id)
    try:
        pending = preview_registry.get(payload.preview_id, paper_id)
    except PreviewExpired as exc:
        raise HTTPException(410, str(exc)) from exc
    stat = pdf_path.stat()
    if (
        record.sha256 != pending.pdf_sha256
        or stat.st_size != pending.file_size
        or stat.st_mtime_ns != pending.file_mtime_ns
    ):
        raise HTTPException(409, "PDF changed since OCR preview; please recognize again")
    _guard_integrity(pdf_path, record.sha256)
    if pending.parser_version != record.parser_version:
        raise HTTPException(409, "Parser changed since region preview; select again")
    try:
        actual = source_snapshots(
            services.repository.get_source_blocks(paper_id), pending.source_block_ids
        )
        if actual != dict(pending.source_fingerprints):
            raise ReviewConflict("SourceBlock changed since region preview; select again")
        store = VersionedRegionReviewStore(services.repository.database_path)
        identifier = store.add(
            paper_id, pending, payload.corrected_text.strip(), payload.researcher_note.strip()
        )
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    preview_registry.consume(payload.preview_id)
    return {
        "id": identifier,
        "page_number": pending.page_number,
        "provenance": "HUMAN_REVIEWED",
        "revision_no": 1,
        "source_link_status": "CURRENT" if pending.source_block_ids else "PAGE_ONLY",
        "applied_to_evidence": False,
        "message": "Researcher correction saved separately; source blocks and Paper Card unchanged.",
    }


@router.get("/reviews")
def list_regions(request: Request, paper_id: str):
    services, record, path = _resources(request, paper_id)
    store = VersionedRegionReviewStore(services.repository.database_path)
    records = store.list_with_links(
        paper_id, record, services.repository.get_source_blocks(paper_id)
    )
    # Unlike the packet/export endpoint, history should remain viewable after a
    # tampered PDF. In that case all links are marked stale, never "CURRENT".
    try:
        _guard_integrity(path, record.sha256)
    except HTTPException:
        for item in records:
            item["link_status"] = "PDF_CHANGED"
        return {"reviews": records, "warning": "Stored PDF differs from imported original"}
    return {"reviews": records}


@router.get("/packet")
def reviewed_evidence_packet(request: Request, paper_id: str):
    """Only a user-requested local export; never automatically feeds an LLM."""
    services, record, path = _resources(request, paper_id)
    _guard_integrity(path, record.sha256)
    store = VersionedRegionReviewStore(services.repository.database_path)
    result = store.evidence_packet(
        paper_id, record, services.repository.get_source_blocks(paper_id)
    )
    return JSONResponse(
        result, headers={"Cache-Control": "no-store, private",
                         "X-Content-Type-Options": "nosniff"}
    )


@router.post("/reviews/{review_id}/revise")
def revise_region(request: Request, paper_id: str, review_id: str, payload: ReviseRequest):
    services, record, path = _resources(request, paper_id)
    _guard_integrity(path, record.sha256)
    store = VersionedRegionReviewStore(services.repository.database_path)
    try:
        result = store.revise(
            paper_id, review_id, payload.expected_revision,
            payload.corrected_text.strip(), payload.researcher_note.strip(), record,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return result


@router.post("/reviews/{review_id}/revoke")
def revoke_region(request: Request, paper_id: str, review_id: str):
    services, _record, _path = _resources(request, paper_id)
    try:
        VersionedRegionReviewStore(services.repository.database_path).revoke(
            paper_id, review_id
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": review_id, "provenance": "REVOKED", "applied_to_evidence": False}

@router.get("/reanalysis/preview")
def preview_reviewed_analysis(request: Request, paper_id: str):
    """Display data scope and potential API cost WITHOUT making any model request."""
    services, record, pdf_path = _resources(request, paper_id)
    _guard_integrity(pdf_path, record.sha256)
    _key, url, model = _configured_pro()
    _review_store, plan = _reanalysis_plan(services, record, model, url)
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
        "note": "Only approved original claims, linked source excerpts, and "
                "researcher-reviewed corrections will be sent. No full PDF upload.",
    }


@router.post("/reanalysis/run")
def run_reviewed_analysis(
    request: Request, paper_id: str, payload: RunReviewedAnalysisRequest
):
    """Run ONE explicit Pro comparative audit; never call full card-analyze()."""
    if payload.approve_external_send is not True or payload.acknowledge_api_cost is not True:
        raise HTTPException(
            422, "Explicit approval for sending source excerpts and API charges is required"
        )
    services, record, pdf_path = _resources(request, paper_id)
    _guard_integrity(pdf_path, record.sha256)
    key, url, model = _configured_pro()
    review_store, plan = _reanalysis_plan(services, record, model, url)
    if plan["snapshot_digest"] != payload.expected_snapshot_digest:
        raise HTTPException(
            409, "Paper, original Card or researcher correction changed; preview again"
        )

    try:
        comparison = run_pro_comparison(
            plan, api_key=key, base_url=url
        )
    except (ModelRequestError, ReanalysisError) as exc:
        # A timed out/rejected model request may still incur provider charges.
        raise HTTPException(
            502, f"Pro comparative review failed: {str(exc)[:450]}. "
                 "Request cost may already have been incurred; no result was saved."
        ) from None

    _guard_integrity(pdf_path, record.sha256)
    store = ReviewReanalysisStore(services.repository.database_path)
    try:
        result = store.save_if_current(
            services.repository, plan, comparison, review_store=review_store
        )
    except ReanalysisError as exc:
        raise HTTPException(
            409, f"{str(exc)}. Model request may already have incurred a charge; "
                 "no reviewed result was saved."
        ) from None
    return JSONResponse(
        result,
        headers={"Cache-Control": "no-store, private",
                 "X-Content-Type-Options": "nosniff"},
    )


@router.get("/reanalysis/reports")
def list_reviewed_analyses(request: Request, paper_id: str):
    services, _record, _path = _resources(request, paper_id)
    return {
        "reports": ReviewReanalysisStore(
            services.repository.database_path
        ).list_reports(paper_id)
    }


@router.get("/reanalysis/reports/{analysis_id}")
def get_reviewed_analysis(request: Request, paper_id: str, analysis_id: str):
    services, _record, _path = _resources(request, paper_id)
    try:
        report = ReviewReanalysisStore(
            services.repository.database_path
        ).get_report(paper_id, analysis_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from None
    return JSONResponse(
        report,
        headers={"Cache-Control": "no-store, private",
                 "X-Content-Type-Options": "nosniff"},
    )

