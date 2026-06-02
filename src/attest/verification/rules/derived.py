"""Derived-figure recomputation.

Some headline figures are not booked anywhere — they are *computed* from other
facts (cloud growth = this year's cloud revenue over last year's, minus one;
margin change = this period's margin minus last period's, in basis points). A
stored-value tie-out can only check such a figure against a number someone typed;
this rule recomputes it from the underlying facts and compares.

That is exactly what catches the restatement at the math level: when the
prior-year base is restated ($467.0M → $474.3M), the recomputation moves from 31%
to 29% even if a stale 31% is still sitting in a source somewhere.

Supported formulas (``MetricSpec.derived_kind``):

* ``yoy_growth`` — percentage growth of ``derived_base`` vs the prior-year period.
* ``qoq_growth`` — percentage growth of ``derived_base`` vs the prior quarter.
* ``delta_bps``  — change in ``derived_base`` (a rate) vs the prior-year period,
  expressed in basis points.
* ``ttm_sum``    — base summed over the current and prior three quarters.
* ``sum``        — ``derived_components`` summed to the metric.
* ``ratio``      — ``derived_numerator`` / ``derived_denominator``.
* ``ratio_pct``  — the same ratio expressed as a percent (× 100).
"""

from __future__ import annotations

from decimal import Decimal

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry, MetricSpec
from attest.domain.money import (
    DEFAULT_POLICY,
    Quantity,
    QuantityParseError,
    Unit,
    parse_quantity,
)
from attest.domain.period import Period
from attest.domain.verdicts import FigureClaim, RuleFinding, RuleSeverity
from attest.factstore.repository import FactStore

_SUPPORTED = {"yoy_growth", "delta_bps", "qoq_growth"}


def _prior_year_period(period: str | None) -> str | None:
    """'FY2026-Q1' -> 'FY2025-Q1'. Returns None if the period isn't recognised."""
    p = Period.parse(period)
    return str(p.prior_year()) if p else None


def _prior_quarter_period(period: str | None) -> str | None:
    """'FY2026-Q1' -> 'FY2025-Q4'; 'FY2026-Q2' -> 'FY2026-Q1'. Quarterly only."""
    p = Period.parse(period)
    prior = p.prior_quarter() if p else None
    return str(prior) if prior else None


def _expected(kind: str, current: Decimal, prior: Decimal) -> tuple[Decimal, Unit, str] | None:
    """Return (value, unit, display suffix) for the recomputed figure, or None."""
    if kind in ("yoy_growth", "qoq_growth"):
        if prior == 0:
            return None
        return (current / prior - Decimal(1)) * Decimal(100), Unit.PERCENT, "%"
    if kind == "delta_bps":
        return (current - prior) * Decimal(100), Unit.BASIS_POINTS, " bps"
    return None


def check_derived_consistency(
    document: Document, registry: MetricRegistry, store: FactStore
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for claim in document.claims:
        spec = registry.get(claim.metric)
        if spec is None:
            continue

        # Ratio identities (e.g. EPS = net income / diluted shares) compare against
        # two same-period operands rather than a prior period.
        if spec.derived_kind in ("ratio", "ratio_pct"):
            findings.extend(_check_ratio(document, claim, spec, store))
            continue

        if spec.derived_kind == "sum":
            findings.extend(_check_sum(document, claim, spec, store))
            continue

        if spec.derived_kind == "ttm_sum":
            findings.extend(_check_ttm(document, claim, spec, store))
            continue

        if spec.derived_kind not in _SUPPORTED or spec.derived_base is None:
            continue

        if spec.derived_kind == "qoq_growth":
            prior_period = _prior_quarter_period(claim.period)
        else:
            prior_period = _prior_year_period(claim.period)
        if prior_period is None:
            continue

        current = store.latest(document.tenant_id, claim.entity, spec.derived_base, claim.period)
        prior = store.latest(document.tenant_id, claim.entity, spec.derived_base, prior_period)
        if current is None or prior is None:
            continue  # nothing to recompute against — never guess

        try:
            claimed = parse_quantity(claim.displayed_text)
        except QuantityParseError:
            continue

        computed = _expected(spec.derived_kind, current.value, prior.value)
        if computed is None:
            continue
        expected_value, expected_unit, suffix = computed
        if claimed.unit != expected_unit:
            continue

        expected = Quantity(value=expected_value, unit=expected_unit, quantum=claimed.quantum)
        if not claimed.matches(expected, DEFAULT_POLICY):
            rounded = DEFAULT_POLICY.round_to(expected_value, claimed.quantum)
            findings.append(
                RuleFinding(
                    rule="derived.recomputation_mismatch",
                    severity=RuleSeverity.BLOCK,
                    document_id=document.id,
                    metric=claim.metric,
                    message=f"'{spec.label}' is stated as {claim.displayed_text} but "
                    f"recomputes to {rounded}{suffix} from {spec.derived_base}.",
                    detail=f"Recomputed from {prior_period} {prior.quantity().display()} "
                    f"({prior.source_label or prior.source_type.value}) to {claim.period} "
                    f"{current.quantity().display()} "
                    f"({current.source_label or current.source_type.value}).",
                )
            )

    return findings


def _check_ttm(
    document: Document, claim: FigureClaim, spec: MetricSpec, store: FactStore
) -> list[RuleFinding]:
    """Verify a trailing-twelve-month sum: base over the current + prior 3 quarters."""
    if spec.derived_base is None:
        return []
    periods = [claim.period]
    p: str | None = claim.period
    for _ in range(3):
        p = _prior_quarter_period(p)
        if p is None:
            return []  # not a quarterly period — cannot build a TTM window
        periods.append(p)

    found = [
        store.latest(document.tenant_id, claim.entity, spec.derived_base, per)
        for per in periods
    ]
    facts = [f for f in found if f is not None]
    if len(facts) != len(periods):
        return []  # an interior quarter is missing — never guess

    try:
        claimed = parse_quantity(claim.displayed_text)
    except QuantityParseError:
        return []

    total = sum((f.value for f in facts), Decimal(0))
    expected = Quantity(value=total, unit=claimed.unit, quantum=claimed.quantum)
    if claimed.matches(expected, DEFAULT_POLICY):
        return []
    rounded = DEFAULT_POLICY.round_to(total, claimed.quantum or Decimal(1))
    return [
        RuleFinding(
            rule="derived.recomputation_mismatch",
            severity=RuleSeverity.BLOCK,
            document_id=document.id,
            metric=claim.metric,
            message=f"'{spec.label}' is stated as {claim.displayed_text} but the last "
            f"four quarters of {spec.derived_base} sum to {rounded}.",
            detail=f"TTM window: {', '.join(periods)}.",
        )
    ]


def _check_sum(
    document: Document, claim: FigureClaim, spec: MetricSpec, store: FactStore
) -> list[RuleFinding]:
    """Verify a sum identity: claimed total == sum of its components."""
    if not spec.derived_components:
        return []
    found = [
        store.latest(document.tenant_id, claim.entity, cid, claim.period)
        for cid in spec.derived_components
    ]
    parts = [p for p in found if p is not None]
    if len(parts) != len(spec.derived_components):
        return []  # a component is missing — never guess

    try:
        claimed = parse_quantity(claim.displayed_text)
    except QuantityParseError:
        return []

    total = sum((p.value for p in parts), Decimal(0))
    expected = Quantity(value=total, unit=claimed.unit, quantum=claimed.quantum)
    if claimed.matches(expected, DEFAULT_POLICY):
        return []
    rounded = DEFAULT_POLICY.round_to(total, claimed.quantum or Decimal(1))
    bridge = " + ".join(f"{p.value}" for p in parts)
    return [
        RuleFinding(
            rule="derived.sum_mismatch",
            severity=RuleSeverity.BLOCK,
            document_id=document.id,
            metric=claim.metric,
            message=f"'{spec.label}' is stated as {claim.displayed_text} but its "
            f"components sum to {rounded}.",
            detail=f"{bridge} = {total}.",
        )
    ]


def _check_ratio(
    document: Document, claim: FigureClaim, spec: MetricSpec, store: FactStore
) -> list[RuleFinding]:
    """Verify a ratio identity: claimed value == numerator / denominator."""
    num_id, den_id = spec.derived_numerator, spec.derived_denominator
    if num_id is None or den_id is None:
        return []
    num = store.latest(document.tenant_id, claim.entity, num_id, claim.period)
    den = store.latest(document.tenant_id, claim.entity, den_id, claim.period)
    if num is None or den is None or den.value == 0:
        return []  # nothing to recompute against — never guess

    try:
        claimed = parse_quantity(claim.displayed_text)
    except QuantityParseError:
        return []

    expected_value = num.value / den.value
    if spec.derived_kind == "ratio_pct":
        expected_value *= Decimal(100)
    expected = Quantity(value=expected_value, unit=claimed.unit, quantum=claimed.quantum)
    if claimed.matches(expected, DEFAULT_POLICY):
        return []
    rounded = DEFAULT_POLICY.round_to(expected_value, claimed.quantum or Decimal("0.01"))
    return [
        RuleFinding(
            rule="derived.ratio_mismatch",
            severity=RuleSeverity.BLOCK,
            document_id=document.id,
            metric=claim.metric,
            message=f"'{spec.label}' is stated as {claim.displayed_text} but "
            f"{num_id} / {den_id} = {rounded}.",
            detail=f"{num.quantity().display()} / {den.value} = {rounded}.",
        )
    ]
