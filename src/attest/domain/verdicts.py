"""Verdicts and claims — what the verification engine produces and consumes.

A :class:`FigureClaim` is what the probabilistic edge proposes: "this numeric
span in the draft is asserting ``total_revenue`` for ``FY2026-Q1``". The
deterministic engine then *disposes*, returning a :class:`FigureVerdict`. The
model never gets to assert a tie-out; it only nominates candidates.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from attest.domain.facts import Confidence, Provenance


class Verdict(str, Enum):
    """The four states a figure can resolve to. These are mutually exclusive."""

    TRACED = "traced"            # exact match to a filed source, within rounding policy
    NEEDS_REVIEW = "needs_review"  # bound to a non-filed source (e.g. guidance)
    CONFLICT = "conflict"        # bound to a source but the value differs / was restated
    UNTRACED = "untraced"        # no source could be bound


class FigureClaim(BaseModel):
    """A candidate figure proposed for verification (the LLM/edge output)."""

    model_config = ConfigDict(frozen=True)

    claim_id: str
    document_id: str
    entity: str
    metric: str
    period: str
    displayed_text: str = Field(description="the figure exactly as written in the draft")
    span: tuple[int, int] | None = Field(default=None, description="char offsets in the doc")
    detect_confidence: Confidence = Confidence.HIGH


class FigureVerdict(BaseModel):
    """The deterministic disposition of a single :class:`FigureClaim`."""

    model_config = ConfigDict(frozen=True)

    claim_id: str
    document_id: str
    entity: str
    metric: str
    period: str
    displayed_text: str

    verdict: Verdict
    reason: str
    provenance: Provenance | None = None
    source_value: str | None = Field(default=None, description="the source value, if bound")
    as_of: str | None = None

    @property
    def is_blocking(self) -> bool:
        """Conflicts and untraced figures must be resolved before publish."""
        return self.verdict in (Verdict.CONFLICT, Verdict.UNTRACED)


class RuleSeverity(str, Enum):
    BLOCK = "block"      # cannot publish until resolved
    WARN = "warn"        # requires human attention
    INFO = "info"        # soft observation


class RuleFinding(BaseModel):
    """A finding from a deterministic rules engine (Reg G, FLS, consistency)."""

    model_config = ConfigDict(frozen=True)

    rule: str = Field(description="rule id, e.g. 'reg_g.reconciliation_required'")
    severity: RuleSeverity
    document_id: str | None = None
    metric: str | None = None
    message: str
    detail: str = ""
