"""Workspace-level routes used by the desktop shell."""

from fastapi import APIRouter, Depends, Query

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import BatchAnalysisRequest, JobAccepted, PruneCacheRequest, ReferenceBatchRequest

router = APIRouter(tags=["workspace"])


@router.get("/dashboard")
def dashboard(services: ApplicationServices = Depends(get_services)):
    return services.papers.dashboard()


@router.post("/batch-analyze", response_model=JobAccepted, status_code=202)
def batch_analyze(
    payload: BatchAnalysisRequest,
    services: ApplicationServices = Depends(get_services),
):
    return JobAccepted(job_id=services.analysis.start_batch_analysis(payload.paper_ids))


@router.post("/references/extract-batch")
def extract_references_batch(
    payload: ReferenceBatchRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.exports.extract_references_batch(payload.paper_ids)


@router.get("/maintenance/doctor")
def doctor(
    deep: bool = Query(False),
    services: ApplicationServices = Depends(get_services),
):
    return services.maintenance.doctor(deep=deep)


@router.post("/maintenance/prune-cache")
def prune_cache(
    payload: PruneCacheRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.maintenance.prune_cache(
        all_runs=payload.all_runs,
        apply=payload.apply,
        vacuum=payload.vacuum,
    )
