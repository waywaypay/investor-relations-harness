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

import re
from decimal import Decimal

from attest.domain.document import Document
from attest.domain.facts import Fact
from attest.domain.metrics import MetricRegistry, MetricSpec
from attest.domain.money import (
    DEFAULT_POLICY,
    Quantity,
    QuantityParseError,
    Unit,
    parse_quantity,
)
from attest.domain.verdicts import RuleFinding, RuleSeverity
from attest.factstore.repository import FactStore

_PERIOD_RE = re.compile(r"^FY(\d{4})(-.*)?$")
_QUARTER_RE = re.compile(r"^FY(\d{4})-Q([1-4])$")

_SUPPORTED = {"yoy_growth", "delta_bps", "qoq_growth"}


def _prior_year_period(period: str) -> str | None:
    """'FY2026-Q1' -> 'FY2025-Q1'. Returns None if the period isn't recognised."""
    m = _PERIOD_RE.match(period)
    if not m:
        return None
    year = int(m.group(1)) - 1
    return f"FY{year}{m.group(2) or ''}"


def _prior_quarter_period(period: str) -> str | None:
    """'FY2026-Q1' -> 'FY2025-Q4'; 'FY2026-Q2' -> 'FY2026-Q1'. Quarterly only."""
    m = _QUARTER_RE.match(period)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    if q == 1:
        return f"FY{year - 1}-Q4"
    return f"FY{year}-Q{q - 1}"


def _expected(kind: str, current: Decimal, prior: Decimal) -> tuple[Decimal, Unit, str] | None:
    """Return (value, unit, display suffix) for the recomputed figure, or None."""
    if kind in ("yoy_growth", "qoq_growth"):
        if prior == 0:
            return None
        return (current / prior - Decimal(1)) * Decimal(100), Unit.PERCENT, "%"
    if kind == "delta_bps":
        return (current - prior) * Decimal(100), Unit.BASIS_POINTS, " bps"
    return None


# Derived kinds that are an identity over *same-period* operands (no prior period):
# ratio/ratio_pct = a / b (×100), sum = Σ components. TTM sums stay rule-only: their
# four-quarter window makes a "which quarter is missing" verdict reason misleading.
CURRENT_PERIOD_KINDS = frozenset({"ratio", "ratio_pct", "sum"})

# Derived kinds computed against a *prior* period: YoY/QoQ growth of a base
# metric, and a rate's change in basis points.
PRIOR_PERIOD_KINDS = frozenset(_SUPPORTED)


def recompute_current_period(
    spec: MetricSpec,
    store: FactStore,
    tenant_id: str,
    entity: str,
    period: str,
    *,
    require_filed: bool,
) -> tuple[Quantity, list[Fact]] | None:
    """Recompute a same-period identity metric from its operand facts.

    Returns ``(expected_quantity, operand_facts)`` or ``None`` when any operand is
    missing — or, with ``require_filed=True``, present but not a filed source. This
    is the single place the identity math lives, shared by the consistency rule
    (which flags a *mis-stated* derived figure) and the engine (which renders a
    *verdict* for a derived metric that has no stored fact of its own, so a
    correctly stated medical care ratio recomputes to ``traced`` instead of
    falling to untraced).

    The returned quantity carries the metric's declared unit and an exact quantum;
    the caller rounds to the figure-as-written's precision when comparing.
    """

    def operand(metric_id: str | None) -> Fact | None:
        if not metric_id:
            return None
        fact = store.latest(tenant_id, entity, metric_id, period)
        if fact is None or (require_filed and not fact.is_filed):
            return None
        return fact

    kind = spec.derived_kind
    if kind in ("ratio", "ratio_pct"):
        num, den = operand(spec.derived_numerator), operand(spec.derived_denominator)
        if num is None or den is None or den.value == 0:
            return None
        value = num.value / den.value
        if kind == "ratio_pct":
            value *= Decimal(100)
        return Quantity(value=value, unit=spec.unit, quantum=Decimal(0)), [num, den]

    if kind == "sum":
        if not spec.derived_components:
            return None
        parts = [operand(c) for c in spec.derived_components]
        if any(p is None for p in parts):
            return None
        total = sum((p.value for p in parts), Decimal(0))  # type: ignore[union-attr]
        return Quantity(value=total, unit=spec.unit, quantum=Decimal(0)), parts  # type: ignore[return-value]

    return None


def recompute_prior_period(
    spec: MetricSpec,
    store: FactStore,
    tenant_id: str,
    entity: str,
    period: str,
    *,
    require_filed: bool,
) -> tuple[Quantity, list[Fact], str] | None:
    """Recompute a prior-period derived metric (YoY/QoQ growth, bps delta).

    The engine's path for a growth figure with no stored fact of its own — the
    normal case for an issuer ingested live from EDGAR, where only the *levels*
    are filed. "Revenue grew 9.8%" recomputes from the current and prior-year
    filed levels, so it renders ``traced``/``conflict`` instead of falling to
    untraced. The restatement story carries over for free: the base values come
    from ``store.latest``, so a restated prior-year base moves the recomputation
    exactly as it does for stored growth facts.

    Returns ``(expected, [current_fact, prior_fact], prior_period)`` or ``None``
    when either period's base can't be sourced — the caller falls through to
    untraced and never asserts a number it cannot source.
    """
    base_id = spec.derived_base
    if not base_id or spec.derived_kind not in PRIOR_PERIOD_KINDS:
        return None
    prior_period = (
        _prior_quarter_period(period)
        if spec.derived_kind == "qoq_growth"
        else _prior_year_period(period)
    )
    if prior_period is None:
        return None

    def base_fact(p: str) -> Fact | None:
        fact = store.latest(tenant_id, entity, base_id, p)
        if fact is None or (require_filed and not fact.is_filed):
            return None
        return fact

    current = base_fact(period)
    prior = base_fact(prior_period)
    if current is None or prior is None:
        return None
    expected = _expected(spec.derived_kind, current.value, prior.value)
    if expected is None:
        return None
    value, unit, _suffix = expected
    return Quantity(value=value, unit=unit, quantum=Decimal(0)), [current, prior], prior_period


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

        recomputed = recompute_prior_period(
            spec, store, document.tenant_id, claim.entity, claim.period, require_filed=False
        )
        if recomputed is None:
            continue  # nothing to recompute against — never guess
        expected_qty, (current, prior), prior_period = recomputed

        try:
            claimed = parse_quantity(claim.displayed_text)
        except QuantityParseError:
            continue
        if claimed.unit != expected_qty.unit:
            continue

        expected = Quantity(value=expected_qty.value, unit=expected_qty.unit, quantum=claimed.quantum)
        if not claimed.matches(expected, DEFAULT_POLICY):
            suffix = " bps" if expected_qty.unit is Unit.BASIS_POINTS else "%"
            rounded = DEFAULT_POLICY.round_to(expected_qty.value, claimed.quantum)
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


def _check_ttm(document, claim, spec, store) -> list[RuleFinding]:
    """Verify a trailing-twelve-month sum: base over the current + prior 3 quarters."""
    if spec.derived_base is None:
        return []
    periods = [claim.period]
    p = claim.period
    for _ in range(3):
        p = _prior_quarter_period(p)
        if p is None:
            return []  # not a quarterly period — cannot build a TTM window
        periods.append(p)

    facts = [
        store.latest(document.tenant_id, claim.entity, spec.derived_base, per)
        for per in periods
    ]
    if any(f is None for f in facts):
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


def _check_sum(document, claim, spec, store) -> list[RuleFinding]:
    """Verify a sum identity: claimed total == sum of its components."""
    recomputed = recompute_current_period(
        spec, store, document.tenant_id, claim.entity, claim.period, require_filed=False
    )
    if recomputed is None:
        return []  # a component is missing — never guess
    expected_qty, parts = recomputed

    try:
        claimed = parse_quantity(claim.displayed_text)
    except QuantityParseError:
        return []

    total = expected_qty.value
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


def _check_ratio(document, claim, spec, store) -> list[RuleFinding]:
    """Verify a ratio identity: claimed value == numerator / denominator."""
    num_id, den_id = spec.derived_numerator, spec.derived_denominator
    recomputed = recompute_current_period(
        spec, store, document.tenant_id, claim.entity, claim.period, require_filed=False
    )
    if recomputed is None:
        return []  # nothing to recompute against — never guess
    expected_value, (num, den) = recomputed[0].value, recomputed[1]

    try:
        claimed = parse_quantity(claim.displayed_text)
    except QuantityParseError:
        return []

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
