"""Paper editing, reference extraction and export routes."""

from fastapi import APIRouter, Depends, Response
from fastapi.responses import PlainTextResponse

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import (
    ClaimReviewDeleteResponse,
    ClaimReviewListResponse,
    ClaimReviewUpsertRequest,
    ClaimReviewView,
    MetadataOverrideRequest,
    UserNoteCreateRequest,
    UserNoteDeleteResponse,
    UserNoteListResponse,
    UserNoteUpdateRequest,
    UserNoteView,
)

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


@router.get("/{paper_id}/citation.ris", response_class=PlainTextResponse)
def export_paper_ris(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return PlainTextResponse(
        services.exports.paper_ris(paper_id),
        media_type="application/x-research-info-systems; charset=utf-8",
    )


@router.get("/{paper_id}/notes", response_model=UserNoteListResponse)
def list_notes(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return {"notes": services.papers.list_user_notes(paper_id)}


@router.post("/{paper_id}/notes", response_model=UserNoteView, status_code=201)
def create_note(
    paper_id: str,
    payload: UserNoteCreateRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.papers.create_user_note(
        paper_id,
        title=payload.title,
        note=payload.note,
        page_number=payload.page_number,
    )


@router.put("/{paper_id}/notes/{note_id}", response_model=UserNoteView)
def update_note(
    paper_id: str,
    note_id: str,
    payload: UserNoteUpdateRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.papers.update_user_note(
        paper_id,
        note_id,
        title=payload.title,
        note=payload.note,
        page_number=payload.page_number,
        update_page_number="page_number" in payload.model_fields_set,
    )


@router.delete("/{paper_id}/notes/{note_id}", response_model=UserNoteDeleteResponse)
def delete_note(
    paper_id: str,
    note_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.papers.delete_user_note(paper_id, note_id)


@router.get("/{paper_id}/claim-reviews", response_model=ClaimReviewListResponse)
def list_claim_reviews(
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return {"reviews": services.papers.list_claim_reviews(paper_id)}


@router.put("/{paper_id}/claim-reviews/{claim_id}", response_model=ClaimReviewView)
def upsert_claim_review(
    paper_id: str,
    claim_id: str,
    payload: ClaimReviewUpsertRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.papers.upsert_claim_review(
        paper_id,
        claim_id,
        user_status=payload.user_status,
        field_name=payload.field_name,
        original_statement=payload.original_statement,
        corrected_statement=payload.corrected_statement,
        note=payload.note,
    )


@router.delete("/{paper_id}/claim-reviews/{claim_id}", response_model=ClaimReviewDeleteResponse)
def delete_claim_review(
    paper_id: str,
    claim_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.papers.delete_claim_review(paper_id, claim_id)
