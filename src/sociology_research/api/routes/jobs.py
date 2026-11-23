from fastapi import APIRouter, Depends, HTTPException, Query

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import JobListResponse, JobSnapshot

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=JobListResponse)
def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    services: ApplicationServices = Depends(get_services),
):
    return {"jobs": services.jobs.list_jobs(limit=limit)}


@router.get("/{job_id}", response_model=JobSnapshot)
def get_job(job_id: str, services: ApplicationServices = Depends(get_services)):
    job = services.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job


@router.post("/{job_id}/cancel", response_model=JobSnapshot)
def cancel_job(job_id: str, services: ApplicationServices = Depends(get_services)):
    job = services.jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job
