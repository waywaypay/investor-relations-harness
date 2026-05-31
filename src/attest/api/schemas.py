"""Request/response models for the API that aren't already domain types."""

from __future__ import annotations

from pydantic import BaseModel, Field

from attest.domain.verdicts import FigureClaim, FigureVerdict, RuleFinding


class IngestResponse(BaseModel):
    source: str
    ingested: int
    skipped: int
    skipped_tags: list[str]


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


class SignOffRequest(BaseModel):
    actor: str
    scope: str = "document"


class OverrideRequest(BaseModel):
    actor: str
    claim_id: str
    justification: str = Field(min_length=1)


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
