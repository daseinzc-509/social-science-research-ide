from fastapi import APIRouter, Depends, HTTPException

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import JobSnapshot

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobSnapshot)
def get_job(job_id: str, services: ApplicationServices = Depends(get_services)):
    job = services.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job
