"""PDF import endpoints for desktop clients."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ...services.application import ApplicationServices
from ...services.papers import MAX_BATCH_FILES, MAX_UPLOAD_BYTES
from ..dependencies import get_services
from ..schemas import JobAccepted

router = APIRouter(prefix="/papers", tags=["imports"])


async def _read_pdf_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail=f"only PDF files are supported: {filename}")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail=f"uploaded PDF is empty: {filename}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"PDF exceeds the 256 MiB limit: {filename}")
    return filename, data


@router.post("/import", response_model=JobAccepted, status_code=202)
async def import_paper(
    file: UploadFile = File(...),
    services: ApplicationServices = Depends(get_services),
):
    filename, data = await _read_pdf_upload(file)
    return JobAccepted(job_id=services.papers.start_import(filename, data))


@router.post("/import-batch", response_model=JobAccepted, status_code=202)
async def import_papers_batch(
    files: list[UploadFile] = File(...),
    services: ApplicationServices = Depends(get_services),
):
    if not files:
        raise HTTPException(status_code=400, detail="no files were uploaded")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"too many files in one batch; maximum is {MAX_BATCH_FILES}",
        )
    uploads: list[tuple[str, bytes]] = []
    for file in files:
        uploads.append(await _read_pdf_upload(file))
    return JobAccepted(job_id=services.papers.start_batch_import(uploads))
