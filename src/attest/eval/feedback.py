"""Production-feedback exporter — the third corpus bucket.

Projects ``OVERRIDE`` events from the audit log into *candidate* labels for the
golden corpus. This is the design doc's "every human accept/override is labeled
training/eval signal" — implemented with the four cautions from the build
discussion baked in:

1. **An override is ambiguous.** A structured :class:`OverrideReason` distinguishes
   "the engine was wrong" (a false-positive signal) from "I'm accepting the risk"
   (a business decision) and "dismissing noise" (toxic if fed back as truth). Only
   ``ENGINE_WRONG`` becomes a candidate.

2. **MNPI.** With ``anonymize=True`` the literal figure text and free-text
   justification (which may carry material non-public values) are scrubbed; the
   *structural* signal — metric, period, reason, suggested verdict — survives.

3. **Eval signal, not silent automation.** These are ``candidates`` with
   ``promoted=False``; a human adjudicates before any enters the reliability gate.

4. **Separate bucket.** Tagged ``label_source="production_feedback"`` so it is
   never summed with synthetic or human-curated cases.

This bucket is blind to false *negatives* by construction (a wrong number the
engine called ``traced`` produces no override), so it complements — never
replaces — restatement harvesting and red-teaming.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from attest.audit.events import EventType
from attest.audit.log import AuditLog

LABEL_SOURCE = "production_feedback"


class OverrideReason(str, Enum):
    ENGINE_WRONG = "engine_wrong"        # false positive -> candidate "should-not-flag"
    ACCEPTING_RISK = "accepting_risk"    # business decision -> excluded
    DISMISSING = "dismissing"            # noise click-through -> excluded
    OTHER = "other"

    def __str__(self) -> str:  # so service.override(str(reason)) stores the value
        return self.value


@dataclass(frozen=True)
class FeedbackCandidate:
    """A candidate label derived from a human override. Not a promoted label."""

    tenant_id: str
    actor: str
    metric: str | None
    period: str | None
    displayed_text: str | None
    justification: str
    reason: OverrideReason
    suggested_verdict: str
    label_source: str = LABEL_SOURCE
    promoted: bool = False

    def as_row(self) -> dict:
        return {
            "metric": self.metric,
            "period": self.period,
            "displayed_text": self.displayed_text,
            "suggested_verdict": self.suggested_verdict,
            "reason": self.reason.value,
            "label_source": self.label_source,
            "promoted": self.promoted,
        }


def candidates_from_audit(
    audit_log: AuditLog, *, anonymize: bool = False, tenant_id: str | None = None
) -> list[FeedbackCandidate]:
    """Project ENGINE_WRONG overrides in the audit log into candidate labels.

    With ``anonymize=True``, scrubs the figure text and justification (potential
    MNPI), keeping only the structural signal. ``tenant_id`` optionally restricts
    to one tenant (the default per-tenant mode; cross-tenant pooling is a
    deliberate, consent-gated step the caller takes by passing None).
    """
    candidates: list[FeedbackCandidate] = []
    for event in audit_log.events(tenant_id):
        if event.type != EventType.OVERRIDE:
            continue
        raw = event.payload.get("reason")
        if raw is None:
            continue  # legacy override with no structured reason — never guess intent
        try:
            reason = OverrideReason(raw)
        except ValueError:
            continue
        if reason != OverrideReason.ENGINE_WRONG:
            continue  # only false-positive signals feed the corpus

        metric = event.payload.get("metric")
        period = event.payload.get("period")
        displayed = event.payload.get("displayed_text")
        justification = event.payload.get("justification", "")

        candidates.append(
            FeedbackCandidate(
                tenant_id=event.tenant_id,
                actor=("redacted" if anonymize else event.actor),
                metric=metric,
                period=period,
                displayed_text=(None if anonymize else displayed),
                justification=("" if anonymize else justification),
                # An "engine was wrong" override of a flagged figure is, as a
                # candidate, a "should-not-flag" / traced case — pending human review.
                reason=reason,
                suggested_verdict="traced",
            )
        )
    return candidates
