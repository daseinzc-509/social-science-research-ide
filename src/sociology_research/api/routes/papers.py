from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, PlainTextResponse

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import DeletePaperRequest, JobAccepted, PaperDetailResponse, PaperListResponse

router = APIRouter(prefix="/papers", tags=["papers"])


@router.get("", response_model=PaperListResponse)
def list_papers(services: ApplicationServices = Depends(get_services)):
    return {"papers": services.papers.list_papers()}


@router.get("/{paper_id}", response_model=PaperDetailResponse)
def get_paper(paper_id: str, services: ApplicationServices = Depends(get_services)):
    return services.papers.get_paper(paper_id)


@router.get("/{paper_id}/pdf", response_class=FileResponse)
def get_pdf(paper_id: str, services: ApplicationServices = Depends(get_services)):
    path = services.papers.pdf_path(paper_id)
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.get("/{paper_id}/reading-report", response_class=PlainTextResponse)
def get_reading_report(paper_id: str, services: ApplicationServices = Depends(get_services)):
    return PlainTextResponse(
        services.exports.reading_report_markdown(paper_id),
        media_type="text/markdown; charset=utf-8",
    )


@router.post("/{paper_id}/reparse", response_model=JobAccepted, status_code=202)
def reparse_paper(paper_id: str, services: ApplicationServices = Depends(get_services)):
    return JobAccepted(job_id=services.papers.start_reparse(paper_id))


@router.delete("/{paper_id}")
def delete_paper(
    paper_id: str,
    payload: DeletePaperRequest | None = None,
    services: ApplicationServices = Depends(get_services),
):
    delete_file = True if payload is None else payload.delete_file
    return services.papers.delete_paper(paper_id, delete_file=delete_file)
