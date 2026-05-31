"""Request/response models for the API that aren't already domain types."""

from __future__ import annotations

from pydantic import BaseModel, Field

from attest.domain.verdicts import FigureVerdict, RuleFinding


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


class AuditVerifyResponse(BaseModel):
    intact: bool
    event_count: int
