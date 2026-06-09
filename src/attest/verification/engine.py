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
from attest.domain.facts import UNDATED_AS_OF, Confidence, Fact, SourceType
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


def _operand_ids(spec) -> list[str]:
    """The metric ids a derived identity is computed from, in reading order."""
    if spec.derived_kind in ("ratio", "ratio_pct"):
        return [spec.derived_numerator or "?", spec.derived_denominator or "?"]
    if spec.derived_kind == "difference":
        return [spec.derived_base or "?", spec.derived_subtrahend or "?"]
    return list(spec.derived_components)


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

        # No stored fact of its own: a *derived* metric (gross/operating margin, free
        # cash flow) can still be verified by recomputing its identity from filed
        # operands. This keeps the spine's invariant — only an exact-with-tolerance
        # match against filed sources may say `traced` — while no longer leaving an
        # exactly-correct margin untraced just because XBRL doesn't tag it.
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

        # A prior disclosure (a figure the company already stated publicly, with no
        # filed XBRL source — e.g. a non-GAAP or operational number from a past
        # release / transcript / deck) is not a filing, so it can't be "traced".
        # But it IS the reference for a consistency check: a draft that restates it
        # must agree, and one that doesn't is flagged as contradicting prior
        # disclosure. This is the one non-filed source we value-compare.
        if latest.source_type == SourceType.PRIOR_DISCLOSURE:
            try:
                draft_qty = parse_quantity(claim.displayed_text)
            except QuantityParseError:
                return self._verdict(
                    claim,
                    Verdict.NEEDS_REVIEW,
                    reason=f"Could not normalize the figure as written "
                    f"('{claim.displayed_text}') to compare against the prior disclosure.",
                    fact=latest,
                )
            if claim.detect_confidence == Confidence.LOW:
                return self._verdict(
                    claim,
                    Verdict.NEEDS_REVIEW,
                    reason="Low detection confidence — routed for human review.",
                    fact=latest,
                )
            as_of_clause = "" if latest.as_of == UNDATED_AS_OF else f" as of {latest.as_of}"
            if draft_qty.matches(latest.quantity(), self.policy):
                return self._verdict(
                    claim,
                    Verdict.NEEDS_REVIEW,
                    reason=f"Consistent with the prior disclosure "
                    f"({latest.quantity().display()}{as_of_clause}); not a filed "
                    f"source, so confirm before publish.",
                    fact=latest,
                )
            return self._verdict(
                claim,
                Verdict.CONFLICT,
                reason=f"Contradicts a prior disclosure: previously stated "
                f"{latest.quantity().display()}{as_of_clause}.",
                fact=latest,
            )

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

    # The join symbol that reads the metric's identity for a human ("a / b", "a - b").
    _IDENTITY_JOIN = {"ratio": " / ", "ratio_pct": " / ", "difference": " - ", "sum": " + "}

    def _bind_derived(self, claim: FigureClaim, tenant_id: str) -> FigureVerdict | None:
        """Verify a derived metric by recomputing its identity from filed operands.

        Same-period identities (margin / FCF) recompute from this period's filed
        facts; prior-period kinds (YoY/QoQ growth, bps delta) recompute from this
        period's and the comparison period's — so "revenue grew 14%" ties out (or
        conflicts) against the filed levels for any live-ingested issuer, exactly
        as the reference pack's cloud-growth story does over stored facts. Returns
        ``None`` when the metric isn't derived or its operands aren't all filed —
        the caller falls through to untraced and never asserts a number it cannot
        source."""
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
            identity = self._IDENTITY_JOIN[spec.derived_kind].join(_operand_ids(spec))
        elif spec.derived_kind in PRIOR_PERIOD_KINDS:
            prior_recomputed = recompute_prior_period(
                spec,
                self.store,
                tenant_id,
                claim.entity,
                claim.period,
                require_filed=True,
                registry=self.registry,
            )
            if prior_recomputed is None:
                return None
            expected, operands, prior_period = prior_recomputed
            identity = f"{spec.derived_base} {claim.period} vs {prior_period}"
        else:
            return None

        def synth(quantum):
            as_of = max((o.as_of for o in operands), default=UNDATED_AS_OF)
            return Fact(
                id=f"derived:{claim.entity}:{claim.metric}:{claim.period}",
                tenant_id=tenant_id,
                entity=claim.entity,
                metric=claim.metric,
                period=claim.period,
                value=expected.value,
                unit=expected.unit,
                quantum=quantum,
                source_type=SourceType.DERIVED,
                source_ref=f"derived:{identity}",
                source_label=f"Recomputed from filed sources ({identity})",
                as_of=as_of,
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
                fact=synth(expected.quantum),
            )
        if claim.detect_confidence == Confidence.LOW:
            return self._verdict(
                claim,
                Verdict.NEEDS_REVIEW,
                reason="Low detection confidence — routed for human review.",
                fact=synth(expected.quantum),
            )

        # Compare at the figure-as-written's precision, like the filed-source path.
        synthetic = synth(draft_qty.quantum)
        if draft_qty.matches(synthetic.quantity(), self.policy):
            return self._verdict(
                claim,
                Verdict.TRACED,
                reason=f"Recomputed from filed sources ({identity}) within the rounding policy.",
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
