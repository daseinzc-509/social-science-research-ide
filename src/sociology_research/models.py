"""Validated domain models for auditable paper understanding."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Provenance(StrEnum):
    AUTHOR_STATED = "AUTHOR_STATED"
    AI_INFERRED = "AI_INFERRED"
    USER_NOTE = "USER_NOTE"


class VerificationStatus(StrEnum):
    """Traceability only: whether a claim can be located in source material."""

    SUPPORTED = "SUPPORTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_SOURCE = "NO_SOURCE"


class SemanticSupportStatus(StrEnum):
    """How selected source evidence relates to the claim's meaning."""

    SUPPORTS = "SUPPORTS"
    PARTIALLY_SUPPORTS = "PARTIALLY_SUPPORTS"
    QUALIFIES = "QUALIFIES"
    CONTRADICTS = "CONTRADICTS"
    BACKGROUND_ONLY = "BACKGROUND_ONLY"
    UNCLEAR = "UNCLEAR"


class EvidenceRole(StrEnum):
    ANCHOR = "ANCHOR"
    CONTEXT = "CONTEXT"
    QUALIFIER = "QUALIFIER"
    TABLE = "TABLE"


class ClaimType(StrEnum):
    DESCRIPTIVE = "DESCRIPTIVE"
    ASSOCIATION = "ASSOCIATION"
    CAUSAL = "CAUSAL"
    MECHANISM = "MECHANISM"
    MEASUREMENT = "MEASUREMENT"
    THEORETICAL = "THEORETICAL"
    NORMATIVE = "NORMATIVE"
    METHOD = "METHOD"
    LIMITATION = "LIMITATION"
    OTHER = "OTHER"


class StudyType(StrEnum):
    QUANTITATIVE_OBSERVATIONAL = "quantitative_observational"
    EXPERIMENTAL = "experimental"
    QUALITATIVE = "qualitative"
    THEORETICAL = "theoretical"
    MIXED_METHODS = "mixed_methods"
    REVIEW = "review"
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
    """Deterministic provenance record built from source text, never authored by an LLM."""

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
    model_config = ConfigDict(extra="ignore")

    population: str | None = None
    setting: str | None = None
    time: str | None = None
    subgroup: str | None = None
    treatment_or_exposure: str | None = None
    outcome: str | None = None
    conditions: str | None = None

    def compact_items(self) -> list[str]:
        labels = (
            ("population", self.population),
            ("setting", self.setting),
            ("time", self.time),
            ("subgroup", self.subgroup),
            ("exposure", self.treatment_or_exposure),
            ("outcome", self.outcome),
            ("conditions", self.conditions),
        )
        return [f"{key}: {value}" for key, value in labels if value]


class LLMEvidenceReference(BaseModel):
    """Small citation contract exposed to a model: choose one provided evidence token."""

    model_config = ConfigDict(extra="ignore")

    evidence_id: str = Field(min_length=2, max_length=24)
    role: EvidenceRole = EvidenceRole.ANCHOR

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value):
        if isinstance(value, EvidenceRole):
            return value
        text = str(value or "ANCHOR").strip().upper()
        return EvidenceRole(text) if text in EvidenceRole._value2member_map_ else EvidenceRole.ANCHOR


class LLMClaim(BaseModel):
    """Claim schema returned by Lite before deterministic evidence resolution.

    The model boundary is deliberately tolerant: language models sometimes copy a
    ``field_name`` category such as ``RESEARCH_QUESTION`` or ``MAJOR_FINDING`` into
    ``claim_type``. Those are structural field labels, not semantic ClaimType values.
    Normalize harmless drift here, then let the analyzer infer a semantic type from the
    field name and statement whenever the normalized value is ``OTHER``.
    """

    model_config = ConfigDict(extra="ignore")

    field_name: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=260)
    provenance: Provenance
    verification: VerificationStatus
    evidence: list[LLMEvidenceReference] = Field(default_factory=list, max_length=3)
    claim_type: ClaimType = ClaimType.OTHER
    scope: ClaimScope = Field(default_factory=ClaimScope)

    @field_validator("claim_type", mode="before")
    @classmethod
    def normalize_claim_type(cls, value):
        if isinstance(value, ClaimType):
            return value
        text = str(value or "OTHER").strip().upper().replace(" ", "_").replace("-", "_")
        if text in ClaimType._value2member_map_:
            return ClaimType(text)

        structural_aliases = {
            "RESEARCH_METHOD": ClaimType.METHOD,
            "MEASUREMENT_INDICATOR": ClaimType.MEASUREMENT,
            "THEORY_CONCEPT": ClaimType.THEORETICAL,
            "AUTHOR_EXPLANATION": ClaimType.MECHANISM,
            "RESEARCH_LIMITATIONS": ClaimType.LIMITATION,
            "LIMITATIONS": ClaimType.LIMITATION,
            "RESEARCH_SAMPLE": ClaimType.DESCRIPTIVE,
            "KEY_NUMBER": ClaimType.DESCRIPTIVE,
            # A research question or a finding label alone does not tell us whether
            # the underlying statement is descriptive, associational, or causal.
            # Keep OTHER so analyzer._infer_claim_type can inspect the statement.
            "RESEARCH_QUESTION": ClaimType.OTHER,
            "MAJOR_FINDING": ClaimType.OTHER,
            "FINDING": ClaimType.OTHER,
        }
        return structural_aliases.get(text, ClaimType.OTHER)

    @field_validator("scope", mode="before")
    @classmethod
    def normalize_scope(cls, value):
        return {} if value is None else value

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
    model_config = ConfigDict(extra="ignore")

    field_name: MetadataField
    value: str | int | list[str]
    evidence: list[LLMEvidenceReference] = Field(min_length=1, max_length=3)


class EvidenceReference(BaseModel):
    """Resolved citation stored in a Paper Card."""

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
    statement: str = Field(min_length=1, max_length=320)
    provenance: Provenance
    verification: VerificationStatus
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=16)
    claim_type: ClaimType = ClaimType.OTHER
    scope: ClaimScope = Field(default_factory=ClaimScope)
    semantic_support: SemanticSupportStatus | None = None
    rationale: str | None = Field(default=None, max_length=900)

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
    abstract: str | None = Field(default=None, max_length=4000)


class LiteExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    metadata_facts: list[LLMMetadataFact] = Field(default_factory=list, max_length=10)
    basic_facts: list[LLMClaim] = Field(default_factory=list, max_length=24)


class StudyProfile(BaseModel):
    """Paper-level study design profile. Pro may refine the heuristic route."""

    model_config = ConfigDict(extra="ignore")

    study_type: StudyType = StudyType.UNKNOWN
    rationale: str | None = Field(default=None, max_length=900)

    @field_validator("study_type", mode="before")
    @classmethod
    def normalize_study_type(cls, value):
        if isinstance(value, StudyType):
            return value
        text = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
        aliases = {
            "quantitative": StudyType.QUANTITATIVE_OBSERVATIONAL,
            "observational": StudyType.QUANTITATIVE_OBSERVATIONAL,
            "quantitative_observational": StudyType.QUANTITATIVE_OBSERVATIONAL,
            "experiment": StudyType.EXPERIMENTAL,
            "experimental": StudyType.EXPERIMENTAL,
            "qualitative": StudyType.QUALITATIVE,
            "theory": StudyType.THEORETICAL,
            "theoretical": StudyType.THEORETICAL,
            "mixed": StudyType.MIXED_METHODS,
            "mixed_method": StudyType.MIXED_METHODS,
            "mixed_methods": StudyType.MIXED_METHODS,
            "review": StudyType.REVIEW,
            "systematic_review": StudyType.REVIEW,
            "unknown": StudyType.UNKNOWN,
        }
        return aliases.get(text, StudyType.UNKNOWN)
    population: str | None = None
    data_source: str | None = None
    unit_of_analysis: str | None = None
    method: str | None = None
    identification_strategy: str | None = None
    measurement: str | None = None


class ProReviewItem(BaseModel):
    """One Pro review item with tolerant shape evolution at the model boundary."""

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_claim_shape(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("dimension") and data.get("field_name"):
            data["dimension"] = data.get("field_name")
        if not data.get("assessment") and data.get("statement"):
            data["assessment"] = data.get("statement")
        return data

    @field_validator("semantic_support", mode="before")
    @classmethod
    def normalize_semantic_support(cls, value):
        if isinstance(value, SemanticSupportStatus):
            return value
        text = str(value or "UNCLEAR").strip().upper().replace(" ", "_").replace("-", "_")
        return SemanticSupportStatus(text) if text in SemanticSupportStatus._value2member_map_ else SemanticSupportStatus.UNCLEAR

    @field_validator("claim_type", mode="before")
    @classmethod
    def normalize_claim_type(cls, value):
        if isinstance(value, ClaimType):
            return value
        text = str(value or "OTHER").strip().upper().replace(" ", "_").replace("-", "_")
        return ClaimType(text) if text in ClaimType._value2member_map_ else ClaimType.OTHER

    dimension: str = Field(min_length=1, max_length=80)
    assessment: str = Field(min_length=1, max_length=500)
    rationale: str | None = Field(default=None, max_length=900)
    evidence: list[LLMEvidenceReference] = Field(default_factory=list, max_length=5)
    semantic_support: SemanticSupportStatus = SemanticSupportStatus.UNCLEAR
    claim_type: ClaimType = ClaimType.OTHER
    scope: ClaimScope = Field(default_factory=ClaimScope)


class LLMClaimAudit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim_id: str = Field(min_length=6, max_length=80)
    semantic_support: SemanticSupportStatus = SemanticSupportStatus.UNCLEAR

    @field_validator("semantic_support", mode="before")
    @classmethod
    def normalize_semantic_support(cls, value):
        if isinstance(value, SemanticSupportStatus):
            return value
        text = str(value or "UNCLEAR").strip().upper().replace(" ", "_").replace("-", "_")
        return SemanticSupportStatus(text) if text in SemanticSupportStatus._value2member_map_ else SemanticSupportStatus.UNCLEAR
    rationale: str | None = Field(default=None, max_length=900)
    evidence: list[LLMEvidenceReference] = Field(default_factory=list, max_length=5)


class ProAnalysis(BaseModel):
    """Paper Understanding v2 response contract.

    ``extra='ignore'`` is deliberate at this outer model and at ProReviewItem/LLMClaimAudit:
    output-shape drift must not crash a completed Pro call. Required semantic fields and
    evidence-token resolution remain strict where they matter.
    """

    model_config = ConfigDict(extra="ignore")

    study_profile: StudyProfile | None = None
    review_plan: list[str] = Field(default_factory=list, max_length=12)
    analysis: list[ProReviewItem] = Field(default_factory=list, max_length=10)
    limitations: list[ProReviewItem] = Field(default_factory=list, max_length=6)
    reading_recommendation: ProReviewItem | None = None
    claim_audits: list[LLMClaimAudit] = Field(default_factory=list, max_length=24)


class ClaimAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    semantic_support: SemanticSupportStatus
    rationale: str | None = None
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=16)


class PaperCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    metadata: PaperMetadata = Field(default_factory=PaperMetadata)
    metadata_claims: list[ExtractedClaim] = Field(default_factory=list)
    basic_facts: list[ExtractedClaim] = Field(default_factory=list)
    tables: list[SourceBlock] = Field(default_factory=list)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    analysis: list[ExtractedClaim] = Field(default_factory=list)
    limitations: list[ExtractedClaim] = Field(default_factory=list)
    reading_recommendation: ExtractedClaim | None = None
    study_profile: StudyProfile | None = None
    claim_audits: list[ClaimAudit] = Field(default_factory=list)
    review_plan: list[str] = Field(default_factory=list)
    lite_model: str
    pro_model: str
    prompt_version: str
    warnings: list[str] = Field(default_factory=list)


class DeepReadingReport(BaseModel):
    """Deterministic reading view rendered from a saved PaperCard; never a new LLM call."""

    model_config = ConfigDict(extra="forbid")

    title: str
    citation_line: str | None = None
    positioning: str | None = None
    research_questions: list[ExtractedClaim] = Field(default_factory=list)
    theory_and_concepts: list[ExtractedClaim] = Field(default_factory=list)
    study_design: list[ExtractedClaim] = Field(default_factory=list)
    key_findings: list[ExtractedClaim] = Field(default_factory=list)
    author_explanations: list[ExtractedClaim] = Field(default_factory=list)
    author_limitations: list[ExtractedClaim] = Field(default_factory=list)
    ai_review: list[ExtractedClaim] = Field(default_factory=list)
    ai_limitations: list[ExtractedClaim] = Field(default_factory=list)
    reading_recommendation: ExtractedClaim | None = None
    study_profile: StudyProfile | None = None
    claim_audits: list[ClaimAudit] = Field(default_factory=list)


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
