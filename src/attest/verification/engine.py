"""The verification engine — the deterministic core that *disposes*.

Pipeline for one figure claim:

    normalize   the figure as written -> Quantity
    resolve     facts for (tenant, entity, metric, period), restatement-aware
    bind        match the draft Quantity against the latest filed value
    verdict     traced | needs_review | conflict | untraced
    provenance  on a match, attach the source pointer so the UI/export can prove it

The engine writes a VERDICT event to the audit log for every disposition, so the
audit trail is a faithful record of what the system asserted and when.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from attest.audit.log import AuditLog
from attest.audit.events import EventType
from attest.domain.document import Document
from attest.domain.facts import Confidence, Fact
from attest.domain.metrics import MetricRegistry
from attest.domain.money import (
    DEFAULT_POLICY,
    QuantityParseError,
    RoundingPolicy,
    parse_quantity,
)
from attest.domain.verdicts import (
    FigureClaim,
    FigureVerdict,
    RuleFinding,
    Verdict,
)
from attest.factstore.repository import FactStore
from attest.verification.rules import (
    check_cross_document_consistency,
    check_forward_looking,
    check_reg_g,
)


class VerificationResult(BaseModel):
    """Everything the engine produced for a single document."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    verdicts: tuple[FigureVerdict, ...]
    findings: tuple[RuleFinding, ...]

    @property
    def counts(self) -> dict[str, int]:
        out = {v.value: 0 for v in Verdict}
        for verdict in self.verdicts:
            out[verdict.verdict.value] += 1
        return out

    @property
    def publishable(self) -> bool:
        """A document is publishable only when nothing blocks it."""
        no_blocking_figures = not any(v.is_blocking for v in self.verdicts)
        no_blocking_rules = not any(f.severity.value == "block" for f in self.findings)
        return no_blocking_figures and no_blocking_rules


class VerificationEngine:
    """Stateless over its inputs; holds references to the store, registry, policy."""

    def __init__(
        self,
        store: FactStore,
        registry: MetricRegistry,
        *,
        policy: RoundingPolicy = DEFAULT_POLICY,
        audit_log: AuditLog | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.policy = policy
        self.audit_log = audit_log

    # -- figure verification -------------------------------------------------

    def verify_claim(self, claim: FigureClaim, tenant_id: str) -> FigureVerdict:
        """Render the deterministic verdict for a single claim."""
        verdict = self._bind(claim, tenant_id)
        if self.audit_log is not None:
            self.audit_log.append(
                actor="system:verification-engine",
                type=EventType.VERDICT,
                tenant_id=tenant_id,
                payload={
                    "claim_id": claim.claim_id,
                    "document_id": claim.document_id,
                    "metric": claim.metric,
                    "period": claim.period,
                    "displayed_text": claim.displayed_text,
                    "verdict": verdict.verdict.value,
                    "source_ref": verdict.provenance.ref if verdict.provenance else None,
                },
            )
        return verdict

    def _bind(self, claim: FigureClaim, tenant_id: str) -> FigureVerdict:
        versions = self.store.versions(tenant_id, claim.entity, claim.metric, claim.period)

        # No source at all: the figure is untraced, full stop.
        if not versions:
            return self._verdict(
                claim,
                Verdict.UNTRACED,
                reason=f"No source bound for '{claim.metric}' in {claim.period}.",
            )

        latest = versions[-1]

        # A non-filed source (guidance / management input) can never be "traced",
        # regardless of how the figure is written (it may even be a range).
        if not latest.is_filed:
            return self._verdict(
                claim,
                Verdict.NEEDS_REVIEW,
                reason="Bound to a non-filed source (e.g. forward guidance); "
                "requires human sign-off and safe-harbor language.",
                fact=latest,
            )

        # Normalize the figure as written. If we cannot — but a filed source
        # exists — we route to a human rather than guess a comparison.
        try:
            draft_qty = parse_quantity(claim.displayed_text)
        except QuantityParseError:
            return self._verdict(
                claim,
                Verdict.NEEDS_REVIEW,
                reason=f"Could not normalize the figure as written "
                f"('{claim.displayed_text}'); manual comparison required.",
                fact=latest,
            )

        # Low-confidence candidates from the edge are routed to a human, never asserted.
        if claim.detect_confidence == Confidence.LOW:
            return self._verdict(
                claim,
                Verdict.NEEDS_REVIEW,
                reason="Low detection confidence — routed for human review.",
                fact=latest,
            )

        # Exact-with-tolerance match against the latest filed value.
        if draft_qty.matches(latest.quantity(), self.policy):
            return self._verdict(
                claim,
                Verdict.TRACED,
                reason="Matched the as-filed source within the rounding policy.",
                fact=latest,
            )

        # Not a match to the latest value. Does it match a *superseded* version?
        for older in versions[:-1]:
            if older.is_filed and draft_qty.matches(older.quantity(), self.policy):
                return self._verdict(
                    claim,
                    Verdict.CONFLICT,
                    reason=f"Matches a superseded value as of {older.as_of}; the figure "
                    f"was restated to {latest.quantity().display()} as of {latest.as_of}.",
                    fact=latest,
                )

        return self._verdict(
            claim,
            Verdict.CONFLICT,
            reason=f"Differs from the filed source "
            f"({latest.quantity().display()} as of {latest.as_of}).",
            fact=latest,
        )

    def _verdict(
        self,
        claim: FigureClaim,
        verdict: Verdict,
        *,
        reason: str,
        fact: Fact | None = None,
    ) -> FigureVerdict:
        return FigureVerdict(
            claim_id=claim.claim_id,
            document_id=claim.document_id,
            entity=claim.entity,
            metric=claim.metric,
            period=claim.period,
            displayed_text=claim.displayed_text,
            verdict=verdict,
            reason=reason,
            provenance=fact.provenance() if fact else None,
            source_value=fact.quantity().display() if fact else None,
            as_of=fact.as_of if fact else None,
        )

    # -- document-level verification ----------------------------------------

    def verify_document(self, document: Document) -> VerificationResult:
        """Verify every claim in a document and run the deterministic rule engines."""
        verdicts = tuple(
            self.verify_claim(claim, document.tenant_id) for claim in document.claims
        )
        findings: list[RuleFinding] = []
        findings.extend(check_reg_g(document, self.registry, self.store))
        findings.extend(check_forward_looking(document, self.store))
        return VerificationResult(
            document_id=document.id,
            verdicts=verdicts,
            findings=tuple(findings),
        )

    def verify_close_pack(
        self, documents: list[Document]
    ) -> tuple[list[VerificationResult], list[RuleFinding]]:
        """Verify a set of documents and run cross-document consistency over them."""
        results = [self.verify_document(doc) for doc in documents]
        consistency = check_cross_document_consistency(documents)
        return results, consistency
