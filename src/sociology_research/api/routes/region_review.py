"""Authenticated in-app region review API. Mounted under the existing /api/v1.

No external model endpoints, no plaintext PDF URL tokens, no automatic writes to
source_blocks/Paper Card. Authentication is inherited from DesktopTokenMiddleware.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ...services.region_review import (
    ConfirmRequest, PendingPreview, PreviewExpired, PreviewRequest, RegionReviewStore,
    as_pdf_box, intersecting_blocks, pdf_page_dimensions, preview_registry, recognize_region,
    region_original_text, render_png,
)
import time

router = APIRouter(prefix="/papers/{paper_id}/region-review", tags=["region-review"])


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
    store = RegionReviewStore(services.repository.database_path)
    identifier = store.add(
        paper_id, pending, payload.corrected_text.strip(), payload.researcher_note.strip()
    )
    preview_registry.consume(payload.preview_id)
    return {
        "id": identifier,
        "page_number": pending.page_number,
        "provenance": "HUMAN_REVIEWED",
        "applied_to_evidence": False,
        "message": "Researcher correction saved separately; source blocks and Paper Card unchanged.",
    }


@router.get("/reviews")
def list_regions(request: Request, paper_id: str):
    services, _record, _path = _resources(request, paper_id)
    return {"reviews": RegionReviewStore(services.repository.database_path).list(paper_id)}


@router.post("/reviews/{review_id}/revoke")
def revoke_region(request: Request, paper_id: str, review_id: str):
    services, _record, _path = _resources(request, paper_id)
    try:
        RegionReviewStore(services.repository.database_path).revoke(paper_id, review_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": review_id, "provenance": "REVOKED", "applied_to_evidence": False}
