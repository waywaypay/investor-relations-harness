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

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict

from attest.audit.log import AuditLog
from attest.audit.events import EventType
from attest.domain.document import Document
from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.metrics import MetricRegistry, MetricSpec
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
    CURRENT_PERIOD_KINDS,
    PRIOR_PERIOD_KINDS,
    check_cross_document_consistency,
    check_intra_document_consistency,
    check_derived_consistency,
    check_directional_language,
    check_forward_looking,
    check_range_midpoint,
    check_range_sanity,
    check_reg_g,
    check_unit_consistency,
    recompute_current_period,
    recompute_prior_period,
)


def _identity_of(spec: MetricSpec) -> str:
    """A human-readable identity for a same-period derived metric ("a / b")."""
    if spec.derived_kind in ("ratio", "ratio_pct"):
        return f"{spec.derived_numerator} / {spec.derived_denominator}"
    return " + ".join(spec.derived_components)


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

        # No stored fact of its own: a *derived* metric (YoY growth, a ratio like
        # the medical care ratio) can still be verified by recomputing its identity
        # from filed operands. This keeps the spine's invariant — only an
        # exact-with-tolerance match against filed sources may say `traced` — while
        # no longer leaving an exactly-correct growth percent untraced just because
        # XBRL only carries the levels.
        if not versions:
            derived = self._bind_derived(claim, tenant_id)
            if derived is not None:
                return derived
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

    def _bind_derived(self, claim: FigureClaim, tenant_id: str) -> FigureVerdict | None:
        """Verify a derived metric by recomputing its identity from filed operands.

        Same-period identities (a ratio, a sum) recompute from this period's filed
        facts; prior-period kinds (YoY/QoQ growth, bps delta) recompute from this
        period's and the comparison period's — so "revenue grew 9.8%" ties out (or
        conflicts) against the filed levels for any live-ingested issuer, exactly
        as the demo's cloud-growth story does over stored facts. Returns ``None``
        when the metric isn't derived or its operands aren't all filed — the
        caller falls through to untraced and never asserts a number it cannot
        source.
        """
        spec = self.registry.get(claim.metric)
        if spec is None:
            return None
        if spec.derived_kind in CURRENT_PERIOD_KINDS:
            recomputed = recompute_current_period(
                spec, self.store, tenant_id, claim.entity, claim.period, require_filed=True
            )
            if recomputed is None:
                return None
            expected, operands = recomputed
            identity = _identity_of(spec)
        elif spec.derived_kind in PRIOR_PERIOD_KINDS:
            prior_recomputed = recompute_prior_period(
                spec, self.store, tenant_id, claim.entity, claim.period, require_filed=True
            )
            if prior_recomputed is None:
                return None
            expected, operands, prior_period = prior_recomputed
            identity = f"{spec.derived_base} {claim.period} vs {prior_period}"
        else:
            return None

        # The verdict is decided on the exact recompute; the synthetic provenance
        # fact carries a 4-decimal rendering so the UI cites "18.0055%", not a
        # 25-digit Decimal expansion.
        shown = expected.value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP).normalize()

        def synth(quantum: Decimal) -> Fact:
            return Fact(
                id=f"derived:{claim.entity}:{claim.metric}:{claim.period}",
                tenant_id=tenant_id,
                entity=claim.entity,
                metric=claim.metric,
                period=claim.period,
                value=shown,
                unit=expected.unit,
                quantum=quantum,
                source_type=SourceType.DERIVED,
                source_ref=f"derived:{identity}",
                source_label=f"Recomputed from filed sources ({identity})",
                source_url=next((o.source_url for o in operands if o.source_url), None),
                as_of=max(o.as_of for o in operands),
                confidence=Confidence.HIGH,
            )

        try:
            draft_qty = parse_quantity(claim.displayed_text)
        except QuantityParseError:
            return self._verdict(
                claim,
                Verdict.NEEDS_REVIEW,
                reason=f"Could not normalize the figure as written "
                f"('{claim.displayed_text}'); manual comparison required.",
                fact=synth(Decimal(0)),
            )
        if claim.detect_confidence == Confidence.LOW:
            return self._verdict(
                claim,
                Verdict.NEEDS_REVIEW,
                reason="Low detection confidence — routed for human review.",
                fact=synth(Decimal(0)),
            )

        # Compare at the figure-as-written's precision, like the filed-source path.
        synthetic = synth(draft_qty.quantum)
        if draft_qty.matches(expected, self.policy):
            return self._verdict(
                claim,
                Verdict.TRACED,
                reason=f"Recomputed from filed sources ({identity}) "
                f"within the rounding policy.",
                fact=synthetic,
            )
        return self._verdict(
            claim,
            Verdict.CONFLICT,
            reason=f"Differs from the value recomputed from filed sources: "
            f"{identity} = {synthetic.quantity().display()}.",
            fact=synthetic,
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
        findings.extend(check_derived_consistency(document, self.registry, self.store))
        findings.extend(check_directional_language(document, self.registry, self.store))
        findings.extend(check_unit_consistency(document, self.registry, self.store))
        findings.extend(check_intra_document_consistency(document))
        findings.extend(check_range_sanity(document, self.registry))
        findings.extend(check_range_midpoint(document, self.registry))
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
