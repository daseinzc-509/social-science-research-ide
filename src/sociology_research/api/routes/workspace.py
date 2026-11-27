"""Workspace-level routes used by the desktop shell."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import (
    BackupCreateRequest,
    BackupVerifyResponse,
    BatchAnalysisRequest,
    DashboardResponse,
    DoctorReportResponse,
    JobAccepted,
    PruneCacheRequest,
    PruneCacheResponse,
    ReferenceBatchRequest,
)

router = APIRouter(tags=["workspace"])

_MAX_VERIFY_BACKUP_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB 流式上限


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(services: ApplicationServices = Depends(get_services)):
    return services.papers.dashboard()


@router.post("/batch-analyze", response_model=JobAccepted, status_code=202)
def batch_analyze(
    payload: BatchAnalysisRequest,
    services: ApplicationServices = Depends(get_services),
):
    return JobAccepted(
        job_id=services.analysis.start_batch_analysis(
            payload.paper_ids,
            project_id=payload.project_id,
        )
    )


@router.post("/references/extract-batch")
def extract_references_batch(
    payload: ReferenceBatchRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.exports.extract_references_batch(payload.paper_ids)


@router.get("/maintenance/doctor", response_model=DoctorReportResponse)
def doctor(
    deep: bool = Query(False),
    services: ApplicationServices = Depends(get_services),
):
    return services.maintenance.doctor(deep=deep)


@router.post("/maintenance/prune-cache", response_model=PruneCacheResponse)
def prune_cache(
    payload: PruneCacheRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.maintenance.prune_cache(
        all_runs=payload.all_runs,
        apply=payload.apply,
        vacuum=payload.vacuum,
    )


@router.post("/maintenance/backup")
def create_backup(
    payload: BackupCreateRequest,
    background_tasks: BackgroundTasks,
    services: ApplicationServices = Depends(get_services),
):
    temp_dir, zip_path, download_name, _manifest = services.maintenance.create_backup_file(
        include_pdfs=payload.include_pdfs,
    )
    background_tasks.add_task(shutil.rmtree, temp_dir, True)
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=download_name,
    )


@router.post("/maintenance/backup/verify", response_model=BackupVerifyResponse)
async def verify_backup(
    file: UploadFile = File(...),
    services: ApplicationServices = Depends(get_services),
):
    temp_dir = Path(tempfile.mkdtemp(prefix="sra-verify-upload-"))
    target = temp_dir / "candidate.zip"
    try:
        total = 0
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_VERIFY_BACKUP_BYTES:
                    raise HTTPException(status_code=413, detail="备份文件超过 2 GiB 校验上限。")
                out.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="上传的备份文件为空。")
        return services.maintenance.verify_backup_bytes(target)
    finally:
        await file.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
