"""The document model.

A document is the unit submitted for verification: a release, an earnings script,
a Q&A prep doc. It carries its prose (for the wording / FLS rules) and the set of
:class:`FigureClaim` s the edge proposed within it. Because claims reference facts
by their canonical scope, a correction to a fact propagates across every document
that cites it — consistency is enforced at the data layer, not by re-reading prose.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from attest.domain.verdicts import FigureClaim


class DocumentKind(str, Enum):
    RELEASE = "release"      # earnings release / 8-K Ex.99.1
    SCRIPT = "script"        # prepared remarks / call script
    QA = "qa"                # Q&A preparation
    OTHER = "other"


class Document(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    title: str
    kind: DocumentKind = DocumentKind.OTHER
    text: str = Field(default="", description="full prose, used by wording/FLS rules")
    claims: tuple[FigureClaim, ...] = ()
