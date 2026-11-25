"""研究项目（Projects）与跨文献证据矩阵（Evidence Matrix）路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from ...services.application import ApplicationServices
from ..dependencies import get_services
from ..schemas import (
    ProjectCreateRequest,
    ProjectDeleteResponse,
    ProjectDetailResponse,
    ProjectEvidenceMatrixResponse,
    ProjectListResponse,
    ProjectNoteCreateRequest,
    ProjectNoteDeleteResponse,
    ProjectNoteListResponse,
    ProjectNoteUpdateRequest,
    ProjectNoteView,
    ProjectPaperEntryView,
    ProjectPaperUpsertRequest,
    ProjectPaperRemoveResponse,
    ProjectSummaryView,
    ProjectUpdateRequest,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListResponse)
def list_projects(services: ApplicationServices = Depends(get_services)):
    return {"projects": services.projects.list_projects()}


@router.post("", response_model=ProjectSummaryView, status_code=201)
def create_project(
    payload: ProjectCreateRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.create_project(
        name=payload.name,
        description=payload.description,
        research_question=payload.research_question,
    )


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(
    project_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.get_project_detail(project_id)


@router.put("/{project_id}", response_model=ProjectSummaryView)
def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.update_project(
        project_id,
        name=payload.name,
        description=payload.description,
        research_question=payload.research_question,
    )


@router.delete("/{project_id}", response_model=ProjectDeleteResponse)
def delete_project(
    project_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.delete_project(project_id)


@router.post("/{project_id}/papers/{paper_id}", response_model=ProjectPaperEntryView)
def upsert_project_paper(
    project_id: str,
    paper_id: str,
    payload: ProjectPaperUpsertRequest | None = None,
    services: ApplicationServices = Depends(get_services),
):
    body = payload or ProjectPaperUpsertRequest()
    return services.projects.upsert_project_paper(
        project_id,
        paper_id,
        reading_status=body.reading_status,
        project_note=body.project_note,
        position=body.position,
    )


@router.delete("/{project_id}/papers/{paper_id}", response_model=ProjectPaperRemoveResponse)
def remove_project_paper(
    project_id: str,
    paper_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.remove_project_paper(project_id, paper_id)


@router.get("/{project_id}/evidence-matrix", response_model=ProjectEvidenceMatrixResponse)
def get_project_evidence_matrix(
    project_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.build_evidence_matrix(project_id)


@router.get("/{project_id}/evidence-matrix.csv", response_class=PlainTextResponse)
def export_project_evidence_matrix_csv(
    project_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return PlainTextResponse(
        services.projects.render_evidence_matrix_csv(project_id),
        media_type="text/csv; charset=utf-8",
    )


@router.get("/{project_id}/evidence-matrix.md", response_class=PlainTextResponse)
def export_project_evidence_matrix_markdown(
    project_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return PlainTextResponse(
        services.projects.render_evidence_matrix_markdown(project_id),
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/{project_id}/notes", response_model=ProjectNoteListResponse)
def list_project_notes(
    project_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return {"notes": services.projects.list_project_notes(project_id)}


@router.post("/{project_id}/notes", response_model=ProjectNoteView, status_code=201)
def create_project_note(
    project_id: str,
    payload: ProjectNoteCreateRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.create_project_note(
        project_id,
        title=payload.title,
        note=payload.note,
    )


@router.put("/{project_id}/notes/{note_id}", response_model=ProjectNoteView)
def update_project_note(
    project_id: str,
    note_id: str,
    payload: ProjectNoteUpdateRequest,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.update_project_note(
        project_id,
        note_id,
        title=payload.title,
        note=payload.note,
    )


@router.delete("/{project_id}/notes/{note_id}", response_model=ProjectNoteDeleteResponse)
def delete_project_note(
    project_id: str,
    note_id: str,
    services: ApplicationServices = Depends(get_services),
):
    return services.projects.delete_project_note(project_id, note_id)
