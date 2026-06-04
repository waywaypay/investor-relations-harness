"""Request/response models for the API that aren't already domain types."""

from __future__ import annotations

from pydantic import BaseModel, Field

from attest.domain.verdicts import FigureClaim, FigureVerdict, RuleFinding


class IngestResponse(BaseModel):
    source: str
    ingested: int
    skipped: int
    skipped_tags: list[str]


class GuidanceIngestRequest(BaseModel):
    """8-K Exhibit 99.1 press-release prose to extract forward guidance from."""

    text: str = Field(description="the EX-99.1 press-release text")
    entity: str = Field(description="issuer entity, e.g. 'MRDN'")
    accession: str = Field(description="the 8-K accession number, for the citation ref")
    base_period: str | None = Field(
        default=None, description="the filing's reported period, e.g. 'FY2026-Q1', anchoring period inference"
    )
    as_of: str = Field(default="1970-01-01", description="ISO date the guidance was published")
    label: str | None = Field(default=None, description="human-readable citation label")


class DisclosureIngestRequest(BaseModel):
    """A prior public disclosure (past release / transcript / deck) to extract
    previously-stated figures from, for consistency checks against later drafts."""

    text: str = Field(description="the prior disclosure's text")
    entity: str = Field(description="issuer entity, e.g. 'MRDN'")
    period: str | None = Field(
        default=None,
        description="the period the disclosure reports, e.g. 'FY2025-Q2', anchoring figures without their own period",
    )
    as_of: str = Field(default="1970-01-01", description="ISO date the disclosure was published")
    source_ref: str = Field(default="prior-disclosure", description="stable id for the source, used in the citation")
    label: str | None = Field(default=None, description="human-readable citation label, e.g. 'Q1 FY2025 call'")


class EdgarIngestRequest(BaseModel):
    """Pull an issuer's real filed facts from SEC EDGAR by ticker."""

    ticker: str = Field(min_length=1, description="issuer ticker symbol, e.g. 'PANW'")
    max_years: int = Field(
        default=3, ge=1, le=10, description="how many recent fiscal years to load"
    )


class VerifyResponse(BaseModel):
    document_id: str
    verdicts: list[FigureVerdict]
    findings: list[RuleFinding]
    counts: dict[str, int]
    publishable: bool


class ClosePackResponse(BaseModel):
    documents: list[VerifyResponse]
    consistency_findings: list[RuleFinding]
    publishable: bool


class AnalyzeResponse(VerifyResponse):
    """Verification of an uploaded/pasted draft, enriched for rendering.

    Carries the recovered prose, the metadata the analysis ran under, the proposed
    claims (with spans, so the figures can be highlighted in place), and any honest
    warnings from text extraction.
    """

    title: str
    kind: str
    entity: str
    period: str | None = None
    text: str
    claims: list[FigureClaim]
    warnings: list[str] = Field(default_factory=list)


class EditRequest(BaseModel):
    actor: str
    before: str
    after: str
    claim_id: str | None = None
    note: str = ""


class SignOffRequest(BaseModel):
    actor: str
    scope: str = "document"


class OverrideRequest(BaseModel):
    actor: str
    claim_id: str
    justification: str = Field(min_length=1)
    reason: str | None = Field(
        default=None,
        description="structured disambiguation: engine_wrong | accepting_risk | dismissing | other",
    )
    metric: str | None = None
    period: str | None = None
    displayed_text: str | None = None


class AuditVerifyResponse(BaseModel):
    intact: bool
    event_count: int


class AliasConfigRequest(BaseModel):
    """Tenant-supplied extraction synonyms: canonical metric id -> phrases."""

    aliases: dict[str, list[str]] = Field(
        description="metric id -> natural-language phrases that attribute a figure to it"
    )
    replace: bool = Field(
        default=False,
        description="overwrite the named metrics' phrases (true) or union them in (false)",
    )


class AliasConfigResponse(BaseModel):
    tenant_id: str
    aliases: dict[str, list[str]]
