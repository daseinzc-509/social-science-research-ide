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
    cancel_requested: bool = False


class JobListResponse(BaseModel):
    jobs: list[JobSnapshot] = Field(default_factory=list)


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
    reviewed_claims: int = 0
    user_notes_count: int = 0


class PaperListResponse(BaseModel):
    papers: list[PaperSummary]


class PaperDetailResponse(BaseModel):
    paper: dict[str, Any]
    card: dict[str, Any] | None = None
    metadata_overrides: dict[str, Any] = Field(default_factory=dict)
    references: list[dict[str, Any]] = Field(default_factory=list)
    citation_mentions: list[dict[str, Any]] = Field(default_factory=list)
    user_notes: list[dict[str, Any]] = Field(default_factory=list)
    claim_reviews: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    research_context: str | None = None
    exclude_after_text: str | None = None
    force: bool = False


class AnalysisPreviewRequest(BaseModel):
    exclude_after_text: str | None = None


class BatchAnalysisRequest(BaseModel):
    paper_ids: list[str] | None = None
    project_id: str | None = None


class BackupCreateRequest(BaseModel):
    include_pdfs: bool = False


class BackupVerifyResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    valid: bool
    format_version: int = 1
    created_at: str = ""
    include_pdfs: bool = False
    contains_secrets: bool = False
    db_bytes: int = 0
    pdf_count: int = 0
    pdf_bytes: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


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


class DirectoryUsageView(BaseModel):
    bytes: int = 0
    files: int = 0
    limited: bool = False


class ConfiguredCacheView(BaseModel):
    variable: str
    path: str


class StorageInventoryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    user_root: str
    config_dir: str
    data_dir: str
    cache_dir: str
    data_managed: bool
    usage: dict[str, DirectoryUsageView] = Field(default_factory=dict)
    configured_caches: list[ConfiguredCacheView] = Field(default_factory=list)
    notes: str = ""


class DashboardResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    papers: int = 0
    analyzed: int = 0
    pending_analysis: int = 0
    needs_review: int = 0
    references: int = 0
    items: list[PaperSummary] = Field(default_factory=list)


class DoctorReportResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    deep: bool
    database_path: str
    database_bytes: int
    papers_dir: str
    counts: dict[str, int] = Field(default_factory=dict)
    storage: dict[str, int] = Field(default_factory=dict)
    cache: dict[str, Any] = Field(default_factory=dict)
    papers_without_cards: list[str] = Field(default_factory=list)
    issues: dict[str, Any] = Field(default_factory=dict)


class PruneCacheResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str
    current_prompt_version: str | None = None
    apply: bool
    vacuum: bool
    matched_runs: int
    matched_bytes: int
    deleted_runs: int
    total_runs_before: int
    total_runs_after: int
    database_bytes_before: int
    database_bytes_after: int


class VisualSettingsResponse(BaseModel):
    formula_enabled: bool
    vision_enabled: bool
    use_pro_connection: bool
    has_vision_api_key: bool
    vision_api_key_masked: str = ""
    vision_api_base_url: str = ""
    vision_model: str = ""
    note: str = ""


class AnalyzeFigureResponse(BaseModel):
    figure_id: str
    page_number: int
    model: str
    description: str
    provenance: str
    saved_to_card: bool = False
    requires_pdf_verification: bool = True


class RegionSourceBlockView(BaseModel):
    id: str
    text: str
    bbox: Any = None
    role: str = "body"


class RegionPageBlocksResponse(BaseModel):
    page_number: int
    source_blocks: list[RegionSourceBlockView] = Field(default_factory=list)
    truncated: bool = False
    provenance: str = "DOCLING_SOURCE"


class RegionPreviewResponse(BaseModel):
    preview_id: str
    page_number: int
    bounds: dict[str, float]
    original_text: str
    ocr_candidate: str
    ocr_mean_score: float | None = None
    source_block_ids: list[str] = Field(default_factory=list)
    provenance: str
    warning: str


class RegionConfirmResponse(BaseModel):
    id: str
    page_number: int
    provenance: str
    revision_no: int
    source_link_status: str
    applied_to_evidence: bool = False
    message: str


class RegionReviewListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    reviews: list[dict[str, Any]] = Field(default_factory=list)
    warning: str | None = None


class RegionReviseResponse(BaseModel):
    id: str
    thread_id: str
    revision_no: int
    supersedes_id: str
    provenance: str
    applied_to_evidence: bool = False


class RegionRevokeResponse(BaseModel):
    id: str
    provenance: str
    applied_to_evidence: bool = False


class ReanalysisPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    paper_id: str
    snapshot_digest: str
    original_card_sha256: str
    review_count: int
    baseline_claim_count: int
    baseline_claims: list[dict[str, Any]] = Field(default_factory=list)
    review_ids: list[str] = Field(default_factory=list)
    pro_model: str
    provider: str
    provider_configuration_sha256: str
    outbound_payload: str
    approximate_input_chars: int
    maximum_output_tokens: int
    estimated_charge: str
    will_send_to_external_api: bool = True
    requires_explicit_consent: bool = True
    only_one_pro_request: bool = True
    runs_lite: bool = False
    modifies_original_paper_card: bool = False
    modifies_source_blocks: bool = False
    note: str = ""


class ReanalysisReportListResponse(BaseModel):
    reports: list[dict[str, Any]] = Field(default_factory=list)


class SearchHitView(BaseModel):
    doc_key: str
    paper_id: str
    paper_title: str
    source_kind: str
    provenance: str
    page_number: int | None = None
    snippet: str
    score: float = 0.0


class SearchResponse(BaseModel):
    query: str
    total: int = 0
    hits: list[SearchHitView] = Field(default_factory=list)


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    research_question: str = Field(default="", max_length=1000)


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    research_question: str | None = Field(default=None, max_length=1000)


class ProjectPaperUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reading_status: str = "queued"
    project_note: str = Field(default="", max_length=2000)
    position: int | None = Field(default=None, ge=0)


class ProjectSummaryView(BaseModel):
    id: str
    name: str
    description: str = ""
    research_question: str = ""
    paper_count: int = 0
    created_at: str
    updated_at: str


class ProjectListResponse(BaseModel):
    projects: list[ProjectSummaryView] = Field(default_factory=list)


class ProjectPaperEntryView(BaseModel):
    project_id: str
    paper_id: str
    reading_status: str
    project_note: str = ""
    position: int = 0
    added_at: str


class ProjectPaperItemView(ProjectPaperEntryView):
    paper: PaperSummary


class ProjectNoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)
    note: str = Field(min_length=1, max_length=20000)


class ProjectNoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, min_length=1, max_length=20000)


class ProjectNoteView(BaseModel):
    id: str
    project_id: str
    title: str = ""
    note: str
    provenance: str = "USER_NOTE"
    created_at: str
    updated_at: str


class ProjectNoteListResponse(BaseModel):
    notes: list[ProjectNoteView] = Field(default_factory=list)


class ProjectNoteDeleteResponse(BaseModel):
    project_id: str
    note_id: str
    deleted: bool = True


class ProjectSynthesisPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_unreviewed: bool = True
    include_project_notes: bool = True
    focus_instruction: str = Field(default="", max_length=600)


class ProjectSynthesisRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_snapshot_digest: str = Field(min_length=16, max_length=128)
    approve_external_send: bool = False
    acknowledge_api_cost: bool = False
    include_unreviewed: bool = True
    include_project_notes: bool = True
    focus_instruction: str = Field(default="", max_length=600)


class ProjectSynthesisPreviewResponse(BaseModel):
    project_id: str
    project_name: str
    snapshot_digest: str
    pro_model: str
    provider: str
    include_unreviewed: bool
    include_project_notes: bool
    focus_instruction: str = ""
    included_paper_count: int = 0
    included_claim_count: int = 0
    confirmed_claim_count: int = 0
    corrected_claim_count: int = 0
    rejected_claim_count: int = 0
    skipped_unreviewed_count: int = 0
    project_note_count: int = 0
    approximate_input_chars: int = 0
    maximum_output_tokens: int = 4096
    outbound_payload: str = ""
    will_send_to_external_api: bool = True
    requires_explicit_consent: bool = True
    only_one_pro_request: bool = True
    modifies_original_cards: bool = False
    note: str = ""


class ProjectSynthesisSummaryView(BaseModel):
    id: str
    project_id: str
    snapshot_digest: str
    model: str
    prompt_version: str
    include_unreviewed: bool = True
    focus_instruction: str = ""
    included_paper_count: int = 0
    included_claim_count: int = 0
    confirmed_claim_count: int = 0
    corrected_claim_count: int = 0
    rejected_claim_count: int = 0
    created_at: str


class ProjectSynthesisListResponse(BaseModel):
    syntheses: list[ProjectSynthesisSummaryView] = Field(default_factory=list)


class ProjectSynthesisDetailResponse(ProjectSynthesisSummaryView):
    provenance: str = "AI_INFERRED_SYNTHESIS"
    original_cards_unchanged: bool = True
    synthesis: dict[str, Any] = Field(default_factory=dict)
    markdown: str = ""


class ProjectSynthesisDeleteResponse(BaseModel):
    project_id: str
    synthesis_id: str
    deleted: bool = True


class ProjectDetailResponse(BaseModel):
    project: ProjectSummaryView
    papers: list[ProjectPaperItemView] = Field(default_factory=list)
    notes: list[ProjectNoteView] = Field(default_factory=list)


class ProjectDeleteResponse(BaseModel):
    project_id: str
    name: str
    unlinked_papers: int = 0


class ProjectPaperRemoveResponse(BaseModel):
    project_id: str
    paper_id: str
    removed: bool = True


class MatrixCitationView(BaseModel):
    evidence_id: str | None = None
    source_block_id: str
    page_number: int
    quote: str


class ClaimHumanReviewSummaryView(BaseModel):
    user_status: str
    corrected_statement: str = ""
    note: str = ""
    updated_at: str = ""


class MatrixClaimCellView(BaseModel):
    claim_id: str | None = None
    field_name: str
    statement: str
    provenance: str
    verification: str
    semantic_support: str | None = None
    human_review: ClaimHumanReviewSummaryView | None = None
    citations: list[MatrixCitationView] = Field(default_factory=list)


class EvidenceMatrixRowView(BaseModel):
    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    journal: str | None = None
    reading_status: str = "queued"
    project_note: str = ""
    has_card: bool = False
    study_type: str | None = None
    method: str | None = None
    identification_strategy: str | None = None
    population: str | None = None
    data_source: str | None = None
    research_questions: list[MatrixClaimCellView] = Field(default_factory=list)
    theories_and_concepts: list[MatrixClaimCellView] = Field(default_factory=list)
    research_design: list[MatrixClaimCellView] = Field(default_factory=list)
    major_findings: list[MatrixClaimCellView] = Field(default_factory=list)
    author_explanations: list[MatrixClaimCellView] = Field(default_factory=list)
    author_limitations: list[MatrixClaimCellView] = Field(default_factory=list)
    ai_limitations: list[MatrixClaimCellView] = Field(default_factory=list)


class ProjectEvidenceMatrixResponse(BaseModel):
    project: ProjectSummaryView
    total_papers: int = 0
    analyzed_papers: int = 0
    makes_llm_calls: bool = False
    provenance_policy: str = "STRICT_SOURCE_SEPARATION"
    rows: list[EvidenceMatrixRowView] = Field(default_factory=list)


class UserNoteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)
    note: str = Field(min_length=1, max_length=20000)
    page_number: int | None = Field(default=None, ge=1)


class UserNoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, min_length=1, max_length=20000)
    page_number: int | None = Field(default=None, ge=1)


class UserNoteView(BaseModel):
    id: str
    paper_id: str
    title: str = ""
    note: str
    page_number: int | None = None
    provenance: str = "USER_NOTE"
    created_at: str
    updated_at: str


class UserNoteListResponse(BaseModel):
    notes: list[UserNoteView] = Field(default_factory=list)


class UserNoteDeleteResponse(BaseModel):
    paper_id: str
    note_id: str
    deleted: bool = True


class ClaimReviewUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_status: str
    field_name: str = Field(default="", max_length=120)
    original_statement: str = Field(default="", max_length=8000)
    corrected_statement: str = Field(default="", max_length=8000)
    note: str = Field(default="", max_length=4000)


class ClaimReviewView(BaseModel):
    id: str
    paper_id: str
    claim_id: str
    field_name: str = ""
    original_statement: str = ""
    user_status: str
    corrected_statement: str = ""
    note: str = ""
    provenance: str = "HUMAN_REVIEWED"
    created_at: str
    updated_at: str


class ClaimReviewListResponse(BaseModel):
    reviews: list[ClaimReviewView] = Field(default_factory=list)


class ClaimReviewDeleteResponse(BaseModel):
    paper_id: str
    claim_id: str
    deleted: bool = True
