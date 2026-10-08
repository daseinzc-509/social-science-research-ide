"""Paper editing, reference extraction and export routes."""

from fastapi import APIRouter, Depends, Response
from fastapi.responses import PlainTextResponse

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import MetadataOverrideRequest

router = APIRouter(prefix="/papers", tags=["paper-tools"])


@router.post("/{paper_id}/metadata")
def save_metadata(
    paper_id: str,
    payload: MetadataOverrideRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.papers.save_metadata_overrides(paper_id, payload.model_dump())


@router.delete("/{paper_id}/metadata")
def reset_metadata(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.papers.clear_metadata_overrides(paper_id)


@router.post("/{paper_id}/references/extract")
def extract_references(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.exports.extract_references(paper_id)


@router.get("/{paper_id}/card.json")
def export_card(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return Response(
        services.exports.paper_card_json(paper_id),
        media_type="application/json; charset=utf-8",
    )


@router.get("/{paper_id}/text.txt", response_class=PlainTextResponse)
def export_parsed_text(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return PlainTextResponse(
        services.exports.parsed_text(paper_id),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/{paper_id}/references.json")
def export_references_json(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    content, media_type = services.exports.references_export(paper_id, "json")
    return Response(content, media_type=media_type)


@router.get("/{paper_id}/references.bib", response_class=PlainTextResponse)
def export_references_bibtex(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    content, media_type = services.exports.references_export(paper_id, "bibtex")
    return PlainTextResponse(content, media_type=media_type)
