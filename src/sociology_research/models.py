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
    SUPPORTED = "SUPPORTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_SOURCE = "NO_SOURCE"


class ParseStatus(StrEnum):
    SUCCESS = "success"
    NEEDS_REVIEW = "needs_review"


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


class LLMEvidenceReference(BaseModel):
    """Small citation contract exposed to the model: choose one provided evidence token."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=2, max_length=24)


class LLMClaim(BaseModel):
    """Claim schema returned by a model before deterministic evidence resolution."""

    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=260)
    provenance: Provenance
    verification: VerificationStatus
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

    evidence_id is authoritative for new cards. The remaining fields are a denormalized,
    human-readable snapshot and keep previously saved cards backwards compatible.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str | None = None
    source_block_id: str
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=180)


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=260)
    provenance: Provenance
    verification: VerificationStatus
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


class ProAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: list[LLMClaim] = Field(default_factory=list, max_length=6)
    limitations: list[LLMClaim] = Field(default_factory=list, max_length=3)
    reading_recommendation: LLMClaim | None = None


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
