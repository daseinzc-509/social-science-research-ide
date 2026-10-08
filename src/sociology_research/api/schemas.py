"""Stable API DTOs for the local desktop boundary."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "sra"
    api_version: str = "v1"


class JobAccepted(BaseModel):
    job_id: str


class JobSnapshot(BaseModel):
    id: str
    kind: str
    status: str
    messages: list[str] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class PaperSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    original_name: str
    page_count: int
    parse_status: str
    created_at: str
    has_card: bool
    card_error: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    journal: str | None = None
    facts: int = 0
    evidence_spans: int = 0
    analysis_claims: int = 0
    warnings: int = 0


class PaperListResponse(BaseModel):
    papers: list[PaperSummary]


class PaperDetailResponse(BaseModel):
    paper: dict[str, Any]
    card: dict[str, Any] | None = None
    metadata_overrides: dict[str, Any] = Field(default_factory=dict)
    references: list[dict[str, Any]] = Field(default_factory=list)
    citation_mentions: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    research_context: str | None = None
    exclude_after_text: str | None = None
    force: bool = False


class AnalysisPreviewRequest(BaseModel):
    exclude_after_text: str | None = None


class BatchAnalysisRequest(BaseModel):
    paper_ids: list[str] | None = None


class DeletePaperRequest(BaseModel):
    delete_file: bool = True


class MetadataOverrideRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    source_note: str | None = None


class ReferenceBatchRequest(BaseModel):
    paper_ids: list[str] | None = None


class PruneCacheRequest(BaseModel):
    all_runs: bool = False
    apply: bool = False
    vacuum: bool = False


class ModelSettingsView(BaseModel):
    lite_api_key_masked: str = ""
    has_lite_api_key: bool = False
    lite_api_base_url: str = ""
    lite_model: str = ""
    pro_api_key_masked: str = ""
    has_pro_api_key: bool = False
    pro_api_base_url: str = ""
    pro_model: str = ""
    same_connection: bool = False


class ModelSettingsUpdate(BaseModel):
    # Blank/omitted keys keep the existing secret.
    lite_api_key: str | None = None
    lite_api_base_url: str | None = None
    lite_model: str | None = None
    pro_api_key: str | None = None
    pro_api_base_url: str | None = None
    pro_model: str | None = None
    pro_use_lite_connection: bool = False


class DeletePaperResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    paper_id: str
    original_name: str
    stored_path: str
    file_deleted: bool = False
    file_warning: str | None = None
