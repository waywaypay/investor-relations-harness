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
    entity: str = Field(description="issuer entity, e.g. 'ATLS'")
    accession: str = Field(description="the 8-K accession number, for the citation ref")
    base_period: str | None = Field(
        default=None, description="the filing's reported period, e.g. 'FY2026-Q1', anchoring period inference"
    )
    as_of: str = Field(default="1970-01-01", description="ISO date the guidance was published")
    label: str | None = Field(default=None, description="human-readable citation label")


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


class PriorPeriodIngestRequest(BaseModel):
    """Request to auto-fetch a prior quarter's 8-K press release from EDGAR."""

    entity: str = Field(description="issuer ticker symbol, e.g. 'MSFT'")
    period: str = Field(description="current period, e.g. 'FY2026-Q2'; prior quarter is derived automatically")
    cik: str | None = Field(default=None, description="10-digit SEC CIK; resolved from ticker if omitted")


class PriorPeriodExhibit(BaseModel):
    accession: str
    filing_date: str
    label: str
    ingested: int
    skipped: int


class PriorPeriodIngestResponse(BaseModel):
    """Summary of a prior-period EDGAR fetch-and-ingest run."""

    prior_period: str | None = Field(description="the derived prior fiscal period, e.g. 'FY2026-Q1'")
    exhibits: list[PriorPeriodExhibit]
    total_ingested: int


class HistoricalSearchRequest(BaseModel):
    """Search the web (via Exa) for an issuer's historical earnings documents."""

    entity: str = Field(min_length=1, description="ticker or company name, e.g. 'PANW'")
    doc_types: list[str] = Field(
        default=["release", "transcript"],
        description="which document classes to search: 'release' and/or 'transcript'",
    )
    quarters: int = Field(
        default=4, ge=1, le=12,
        description="how many recent fiscal periods to return per type (one result per period)",
    )


class HistoricalCandidate(BaseModel):
    """One reviewable search hit (no full text until ingested)."""

    url: str
    title: str
    published_date: str
    source: str
    snippet: str
    doc_type: str
    period: str | None = Field(
        default=None, description="fiscal period read from the doc, e.g. 'FY2026-Q3'"
    )


class HistoricalSearchResponse(BaseModel):
    candidates: list[HistoricalCandidate]


class HistoricalIngestItem(BaseModel):
    """A reviewed candidate the user chose to load."""

    url: str = Field(min_length=1)
    title: str | None = None
    period: str | None = Field(
        default=None, description="optional fiscal period to anchor figures, e.g. 'FY2026-Q1'"
    )


class HistoricalIngestRequest(BaseModel):
    entity: str = Field(min_length=1, description="issuer entity to scope facts under, e.g. 'PANW'")
    items: list[HistoricalIngestItem]


class HistoricalIngestDoc(BaseModel):
    url: str
    title: str
    published_date: str
    ingested: int
    skipped: int


class HistoricalIngestResponse(BaseModel):
    """Summary of an Exa fetch-and-ingest run over the selected documents."""

    documents: list[HistoricalIngestDoc]
    total_ingested: int
