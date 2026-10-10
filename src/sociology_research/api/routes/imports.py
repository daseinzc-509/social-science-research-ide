"""PDF import endpoints for desktop clients."""

from __future__ import annotations

from email.header import decode_header, make_header
from email.message import Message
from email.utils import collapse_rfc2231_value
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ...services.application import ApplicationServices
from ...services.papers import MAX_BATCH_FILES, MAX_UPLOAD_BYTES
from ..dependencies import get_services
from ..schemas import JobAccepted

router = APIRouter(prefix="/papers", tags=["imports"])


def _normalized_pdf_filename(filename: str | None) -> str:
    """Decode legacy .NET RFC 2047 multipart filenames without weakening PDF checks.

    New desktop clients send RFC 5987 filename*; some Starlette/multipart
    versions prefer an ASCII filename= fallback, so we decode filename* below.
    Older clients may transmit non-ASCII names as =?utf-8?B?...?=.
    """
    original = filename or "upload.pdf"
    if original.startswith("=?") and "?=" in original:
        try:
            original = str(make_header(decode_header(original)))
        except (LookupError, UnicodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid encoded upload filename") from exc

    # Do not let a caller-supplied filename contain path components. Both
    # Windows and POSIX paths need to be recognized on either backend platform.
    basename = original.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not basename or any(ord(char) < 32 for char in basename):
        raise HTTPException(status_code=400, detail="invalid PDF upload filename")
    if not basename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail=f"only PDF files are supported: {basename}")
    return basename


def _preferred_upload_filename(file: UploadFile) -> str | None:
    """Prefer RFC 5987 filename* over the ASCII multipart fallback.

    Some versions of the multipart parser set UploadFile.filename from the
    first `filename=` parameter, even when the same part also contains a
    UTF-8 `filename*=`. The original Content-Disposition header is retained
    in UploadFile.headers, allowing independent, standards-based decoding.
    """
    disposition = file.headers.get("content-disposition", "")
    if not re.search(r"(?:^|;)\s*filename\*\s*=", disposition, re.IGNORECASE):
        return file.filename

    message = Message()
    message["Content-Disposition"] = disposition
    parameters = message.get_params(header="content-disposition", unquote=True) or []
    extended = [
        value
        for name, value in parameters
        if name.lower() == "filename" and isinstance(value, tuple)
    ]
    if len(extended) != 1:
        raise HTTPException(status_code=400, detail="invalid extended upload filename")

    try:
        charset, _language, _encoded_name = extended[0]
        if not charset or charset.lower() not in {"utf-8", "iso-8859-1"}:
            raise ValueError("unsupported filename* charset")
        return collapse_rfc2231_value(extended[0], errors="strict")
    except (LookupError, UnicodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid extended upload filename") from exc


async def _read_pdf_upload(file: UploadFile) -> tuple[str, bytes]:
    try:
        filename = _normalized_pdf_filename(_preferred_upload_filename(file))
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if not data:
            raise HTTPException(status_code=400, detail=f"uploaded PDF is empty: {filename}")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"PDF exceeds the 256 MiB limit: {filename}")
        return filename, data
    finally:
        await file.close()


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
