"""Validated domain models used by the local research prototype."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Provenance(StrEnum):
    AUTHOR_STATED = "AUTHOR_STATED"
    AI_INFERRED = "AI_INFERRED"
    USER_NOTE = "USER_NOTE"


class VerificationStatus(StrEnum):
    """Traceability status.

    ``SUPPORTED`` is retained for stored-card compatibility. In v2 it means that the
    claim has source-located provenance, not that semantic entailment has been proven.
    Semantic support is represented independently by :class:`SemanticSupportStatus`.
    """

    SUPPORTED = "SUPPORTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_SOURCE = "NO_SOURCE"


class SemanticSupportStatus(StrEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    SUPPORTS = "SUPPORTS"
    PARTIALLY_SUPPORTS = "PARTIALLY_SUPPORTS"
    QUALIFIES = "QUALIFIES"
    CONTRADICTS = "CONTRADICTS"
    BACKGROUND_ONLY = "BACKGROUND_ONLY"
    UNCLEAR = "UNCLEAR"


class ReviewState(StrEnum):
    MACHINE_GENERATED = "MACHINE_GENERATED"
    USER_CONFIRMED = "USER_CONFIRMED"
    USER_CORRECTED = "USER_CORRECTED"


class EvidenceRole(StrEnum):
    ANCHOR = "ANCHOR"
    CONTEXT = "CONTEXT"
    QUALIFIER = "QUALIFIER"
    TABLE = "TABLE"


class ClaimType(StrEnum):
    METADATA = "METADATA"
    RESEARCH_QUESTION = "RESEARCH_QUESTION"
    THEORY = "THEORY"
    DESCRIPTIVE = "DESCRIPTIVE"
    ASSOCIATION = "ASSOCIATION"
    CAUSAL = "CAUSAL"
    MECHANISM = "MECHANISM"
    METHOD = "METHOD"
    SAMPLE = "SAMPLE"
    MEASUREMENT = "MEASUREMENT"
    LIMITATION = "LIMITATION"
    CONTRIBUTION = "CONTRIBUTION"
    RELEVANCE = "RELEVANCE"
    OTHER = "OTHER"


class StudyType(StrEnum):
    QUANTITATIVE_OBSERVATIONAL = "quantitative_observational"
    EXPERIMENTAL = "experimental"
    QUALITATIVE = "qualitative"
    THEORETICAL = "theoretical"
    MIXED_METHODS = "mixed_methods"
    REVIEW = "review"
    OTHER = "other"
    UNKNOWN = "unknown"


class ParseStatus(StrEnum):
    SUCCESS = "success"
    NEEDS_REVIEW = "needs_review"


class ReferenceParseStatus(StrEnum):
    RAW = "raw"
    STRUCTURED = "structured"
    NEEDS_REVIEW = "needs_review"


class ReferenceMatchStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class ReferenceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    paper_id: str
    ordinal: int = Field(ge=1)
    raw_text: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1400, le=2200)
    title: str | None = None
    container_title: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    url: str | None = None
    source_page: int = Field(ge=1)
    source_block_id: str
    parse_status: ReferenceParseStatus = ReferenceParseStatus.STRUCTURED
    needs_review_reason: str | None = None


class CitationMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    paper_id: str
    marker: str = Field(min_length=1)
    reference_entry_id: str | None = None
    page_number: int = Field(ge=1)
    source_block_id: str
    context_text: str = Field(min_length=1, max_length=1000)
    match_status: ReferenceMatchStatus = ReferenceMatchStatus.CANDIDATE


class SourceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)
    bbox: tuple[float, float, float, float] | None = None
    table_rows: list[list[str]] | None = None
    role: Literal["body", "heading", "front_matter", "footnote", "table", "furniture"] = "body"
    source_label: str | None = None

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source block text cannot be blank")
        return value


class EvidenceFragment(BaseModel):
    """Exact slice of one persisted SourceBlock used to compose a logical evidence span."""

    model_config = ConfigDict(extra="forbid")

    source_block_id: str
    page_number: int = Field(ge=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    bbox: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("evidence fragment end_char must be greater than start_char")
        return self


class EvidenceSpan(BaseModel):
    """Deterministic provenance record built from source text, never authored by an LLM.

    New cards may compose one logical sentence-level span from several adjacent persisted
    SourceBlocks. The legacy single-block fields remain populated for compatibility;
    ``fragments`` is authoritative when present.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=8)
    source_block_id: str
    page_number: int = Field(ge=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=180)
    kind: Literal["text", "table"]
    role: Literal["body", "heading", "front_matter", "footnote", "table", "furniture"] = "body"
    bbox: tuple[float, float, float, float] | None = None
    fragments: list[EvidenceFragment] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("evidence span end_char must be greater than start_char")
        return self


class ClaimScope(BaseModel):
    """Optional scope/boundary information attached to a scholarly claim."""

    model_config = ConfigDict(extra="forbid")

    population: str | None = Field(default=None, max_length=180)
    setting: str | None = Field(default=None, max_length=180)
    time_scope: str | None = Field(default=None, max_length=180)
    subgroup: str | None = Field(default=None, max_length=180)
    exposure_or_treatment: str | None = Field(default=None, max_length=180)
    outcome: str | None = Field(default=None, max_length=180)
    conditions: list[str] = Field(default_factory=list, max_length=6)


class LLMEvidenceReference(BaseModel):
    """Small citation contract exposed to the model: choose one provided evidence token."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=2, max_length=24)
    role: EvidenceRole = EvidenceRole.ANCHOR


class LLMClaim(BaseModel):
    """Claim schema returned by a model before deterministic evidence resolution."""

    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=260)
    provenance: Provenance
    verification: VerificationStatus
    claim_type: ClaimType = ClaimType.OTHER
    scope: ClaimScope = Field(default_factory=ClaimScope)
    evidence: list[LLMEvidenceReference] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def supported_claim_requires_evidence(self) -> Self:
        if self.verification == VerificationStatus.SUPPORTED and not self.evidence:
            raise ValueError("SUPPORTED claims must include at least one evidence reference")
        if self.verification == VerificationStatus.NO_SOURCE and self.evidence:
            raise ValueError("NO_SOURCE claims cannot include evidence references")
        return self


MetadataField = Literal[
    "title",
    "authors",
    "year",
    "journal",
    "volume",
    "issue",
    "pages",
    "doi",
    "keywords",
    "abstract",
]


class LLMMetadataFact(BaseModel):
    """One bibliographic field plus evidence IDs; no duplicate free-standing metadata object."""

    model_config = ConfigDict(extra="forbid")

    field_name: MetadataField
    value: str | int | list[str]
    evidence: list[LLMEvidenceReference] = Field(min_length=1, max_length=3)


class EvidenceReference(BaseModel):
    """Resolved citation stored in a Paper Card.

    ``evidence_id`` is authoritative for new cards. The remaining fields are a
    denormalized, human-readable snapshot and keep previously saved cards backwards
    compatible. ``role`` turns a flat quote list into a lightweight evidence bundle:
    one quote may be the anchor, while others provide context, qualifications, or a
    table result.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str | None = None
    source_block_id: str
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=180)
    role: EvidenceRole = EvidenceRole.ANCHOR


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str | None = None
    field_name: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=260)
    provenance: Provenance
    verification: VerificationStatus
    claim_type: ClaimType = ClaimType.OTHER
    scope: ClaimScope = Field(default_factory=ClaimScope)
    semantic_support: SemanticSupportStatus = SemanticSupportStatus.NOT_ASSESSED
    support_note: str | None = Field(default=None, max_length=500)
    review_state: ReviewState = ReviewState.MACHINE_GENERATED
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def supported_claim_requires_evidence(self) -> Self:
        if self.verification == VerificationStatus.SUPPORTED and not self.evidence:
            raise ValueError("SUPPORTED claims must include at least one evidence reference")
        if self.verification == VerificationStatus.NO_SOURCE and self.evidence:
            raise ValueError("NO_SOURCE claims cannot include evidence references")
        return self


class PaperMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1400, le=2200)
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    keywords: list[str] = Field(default_factory=list)
    abstract: str | None = Field(default=None, max_length=2000)


class LiteExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata_facts: list[LLMMetadataFact] = Field(default_factory=list, max_length=10)
    basic_facts: list[LLMClaim] = Field(default_factory=list, max_length=24)


class LLMStudyProfile(BaseModel):
    """Study profile drafted by Pro from independently retrieved source excerpts."""

    model_config = ConfigDict(extra="forbid")

    study_type: StudyType = StudyType.UNKNOWN
    design_summary: str | None = Field(default=None, max_length=500)
    population: str | None = Field(default=None, max_length=260)
    setting: str | None = Field(default=None, max_length=260)
    time_scope: str | None = Field(default=None, max_length=260)
    data_source: str | None = Field(default=None, max_length=320)
    sample_summary: str | None = Field(default=None, max_length=320)
    identification_strategy: str | None = Field(default=None, max_length=320)
    measurement_strategy: str | None = Field(default=None, max_length=320)
    evidence: list[LLMEvidenceReference] = Field(default_factory=list, max_length=6)


class StudyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_type: StudyType = StudyType.UNKNOWN
    router_hint: StudyType = StudyType.UNKNOWN
    design_summary: str | None = Field(default=None, max_length=500)
    population: str | None = Field(default=None, max_length=260)
    setting: str | None = Field(default=None, max_length=260)
    time_scope: str | None = Field(default=None, max_length=260)
    data_source: str | None = Field(default=None, max_length=320)
    sample_summary: str | None = Field(default=None, max_length=320)
    identification_strategy: str | None = Field(default=None, max_length=320)
    measurement_strategy: str | None = Field(default=None, max_length=320)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=12)


class LLMClaimAudit(BaseModel):
    """Semantic support judgment for one Lite fact; separate from source traceability."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=8, max_length=64)
    semantic_support: SemanticSupportStatus
    rationale: str = Field(min_length=1, max_length=500)
    evidence: list[LLMEvidenceReference] = Field(default_factory=list, max_length=4)


class ClaimAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=8, max_length=64)
    semantic_support: SemanticSupportStatus
    rationale: str = Field(min_length=1, max_length=500)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=8)
    review_state: ReviewState = ReviewState.MACHINE_GENERATED


class ProAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_profile: LLMStudyProfile | None = None
    claim_audits: list[LLMClaimAudit] = Field(default_factory=list, max_length=24)
    analysis: list[LLMClaim] = Field(default_factory=list, max_length=8)
    limitations: list[LLMClaim] = Field(default_factory=list, max_length=5)
    reading_recommendation: LLMClaim | None = None


class PaperCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    metadata: PaperMetadata = Field(default_factory=PaperMetadata)
    metadata_claims: list[ExtractedClaim] = Field(default_factory=list)
    basic_facts: list[ExtractedClaim] = Field(default_factory=list)
    study_profile: StudyProfile | None = None
    claim_audits: list[ClaimAudit] = Field(default_factory=list)
    review_plan: list[str] = Field(default_factory=list, max_length=16)
    tables: list[SourceBlock] = Field(default_factory=list)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    analysis: list[ExtractedClaim] = Field(default_factory=list)
    limitations: list[ExtractedClaim] = Field(default_factory=list)
    reading_recommendation: ExtractedClaim | None = None
    lite_model: str
    pro_model: str
    prompt_version: str
    warnings: list[str] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    sha256: str
    source_path: str
    page_count: int = Field(ge=1)
    status: ParseStatus
    parser_version: str = "legacy"
    warnings: list[str] = Field(default_factory=list)
    blocks: list[SourceBlock] = Field(default_factory=list)


class PaperRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    sha256: str
    original_name: str
    stored_path: str
    page_count: int
    parse_status: ParseStatus
    parser_version: str = "legacy"
    warnings: list[str] = Field(default_factory=list)
    created_at: str
