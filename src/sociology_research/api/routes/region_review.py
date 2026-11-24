"""Authenticated in-app region review API. Mounted under the existing /api/v1.

薄 HTTP 适配层：持久化 Store 与复核编排统一由 ApplicationServices.region_review
托管并复用；此处保留前置 fail-closed 护栏与严格响应模型边界。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from ...services.region_review import ConfirmRequest, PreviewRequest, recognize_region
from ...services.region_review_reanalysis import run_pro_comparison
from ...services.region_review_service import RegionReviewService, RegionReviewServiceError
from ..schemas import (
    ReanalysisPreviewResponse,
    ReanalysisReportListResponse,
    RegionConfirmResponse,
    RegionPageBlocksResponse,
    RegionPreviewResponse,
    RegionReviewListResponse,
    RegionReviseResponse,
    RegionRevokeResponse,
)

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
        return RegionReviewService._configured_pro()
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


def _region_service(request: Request) -> RegionReviewService:
    services = request.app.state.services
    existing = getattr(services, "region_review", None)
    if existing is not None:
        return existing
    return RegionReviewService(services.repository, services.papers)


@router.get("/pages/{page_number}.png")
def page_image(request: Request, paper_id: str, page_number: int, scale: float = 1.4):
    try:
        png, dimensions, count = _region_service(request).render_page(
            paper_id, page_number, scale=scale
        )
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
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


@router.get("/pages/{page_number}/blocks", response_model=RegionPageBlocksResponse)
def page_blocks(request: Request, paper_id: str, page_number: int):
    try:
        return _region_service(request).get_page_blocks(paper_id, page_number)
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.post("/pages/{page_number}/preview", response_model=RegionPreviewResponse)
def preview_region(request: Request, paper_id: str, page_number: int, payload: PreviewRequest):
    try:
        return _region_service(request).preview_region(
            paper_id,
            page_number,
            bounds=payload.bounds,
            manual_only=payload.manual_only,
            recognizer=recognize_region,
        )
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.post("/confirm", response_model=RegionConfirmResponse)
def confirm_region(request: Request, paper_id: str, payload: ConfirmRequest):
    try:
        return _region_service(request).confirm_region(
            paper_id,
            preview_id=payload.preview_id,
            corrected_text=payload.corrected_text,
            researcher_note=payload.researcher_note,
        )
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.get("/reviews", response_model=RegionReviewListResponse)
def list_regions(request: Request, paper_id: str):
    try:
        return _region_service(request).list_reviews(paper_id)
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.get("/packet")
def reviewed_evidence_packet(request: Request, paper_id: str):
    """Only a user-requested local export; never automatically feeds an LLM."""
    try:
        result = _region_service(request).get_evidence_packet(paper_id)
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return JSONResponse(
        result,
        headers={
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/reviews/{review_id}/revise", response_model=RegionReviseResponse)
def revise_region(request: Request, paper_id: str, review_id: str, payload: ReviseRequest):
    try:
        return _region_service(request).revise_review(
            paper_id,
            review_id,
            expected_revision=payload.expected_revision,
            corrected_text=payload.corrected_text,
            researcher_note=payload.researcher_note,
        )
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.post("/reviews/{review_id}/revoke", response_model=RegionRevokeResponse)
def revoke_region(request: Request, paper_id: str, review_id: str):
    try:
        return _region_service(request).revoke_review(paper_id, review_id)
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.get("/reanalysis/preview", response_model=ReanalysisPreviewResponse)
def preview_reviewed_analysis(request: Request, paper_id: str):
    """Display data scope and potential API cost WITHOUT making any model request."""
    try:
        return _region_service(request).preview_reanalysis(
            paper_id, pro_config_loader=_configured_pro
        )
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.post("/reanalysis/run")
def run_reviewed_analysis(
    request: Request, paper_id: str, payload: RunReviewedAnalysisRequest
):
    """Run ONE explicit Pro comparative audit; never call full card-analyze()."""
    if payload.approve_external_send is not True or payload.acknowledge_api_cost is not True:
        raise HTTPException(
            422, "Explicit approval for sending source excerpts and API charges is required"
        )
    service = _region_service(request)
    try:
        record, pdf_path, key, url, plan = service.prepare_reanalysis_execution(
            paper_id, pro_config_loader=_configured_pro
        )
        if plan["snapshot_digest"] != payload.expected_snapshot_digest:
            raise HTTPException(
                409, "Paper, original Card or researcher correction changed; preview again"
            )
        result = service.execute_reanalysis(
            record,
            pdf_path,
            plan,
            api_key=key,
            base_url=url,
            comparison_runner=run_pro_comparison,
        )
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return JSONResponse(
        result,
        headers={
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/reanalysis/reports", response_model=ReanalysisReportListResponse)
def list_reviewed_analyses(request: Request, paper_id: str):
    try:
        return _region_service(request).list_reanalysis_reports(paper_id)
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None


@router.get("/reanalysis/reports/{analysis_id}")
def get_reviewed_analysis(request: Request, paper_id: str, analysis_id: str):
    try:
        report = _region_service(request).get_reanalysis_report(paper_id, analysis_id)
    except RegionReviewServiceError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return JSONResponse(
        report,
        headers={
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )
