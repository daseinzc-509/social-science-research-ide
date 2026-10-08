from fastapi import APIRouter, Depends

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import AnalysisPreviewRequest, AnalysisRequest, JobAccepted

router = APIRouter(prefix="/papers", tags=["analysis"])


@router.post("/{paper_id}/analysis/preview")
def preview_analysis(
    paper_id: str,
    payload: AnalysisPreviewRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.analysis.preview(
        paper_id,
        exclude_after_text=payload.exclude_after_text,
    )


@router.post("/{paper_id}/analyze", response_model=JobAccepted, status_code=202)
def start_analysis(
    paper_id: str,
    payload: AnalysisRequest,
    services: ApplicationServices = Depends(get_services),
):
    job_id = services.analysis.start_analysis(
        paper_id,
        research_context=payload.research_context,
        exclude_after_text=payload.exclude_after_text,
        force=payload.force,
    )
    return JobAccepted(job_id=job_id)
