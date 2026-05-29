"""Derived-figure recomputation.

Some headline figures are not booked anywhere — they are *computed* from other
facts (cloud growth = this year's cloud revenue over last year's, minus one). A
stored-value tie-out can only check such a figure against a number someone typed;
this rule recomputes it from the underlying facts and compares.

That is exactly what catches the restatement at the math level: when the
prior-year base is restated ($467.0M → $474.3M), the recomputation moves from 31%
to 29% even if a stale 31% is still sitting in a source somewhere.
"""

from __future__ import annotations

import re
from decimal import Decimal

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry
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


def _prior_year_period(period: str) -> str | None:
    """'FY2026-Q1' -> 'FY2025-Q1'. Returns None if the period isn't recognised."""
    m = _PERIOD_RE.match(period)
    if not m:
        return None
    year = int(m.group(1)) - 1
    return f"FY{year}{m.group(2) or ''}"


def check_derived_consistency(
    document: Document, registry: MetricRegistry, store: FactStore
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for claim in document.claims:
        spec = registry.get(claim.metric)
        if spec is None or spec.derived_kind != "yoy_growth" or spec.derived_base is None:
            continue

        prior_period = _prior_year_period(claim.period)
        if prior_period is None:
            continue

        current = store.latest(document.tenant_id, claim.entity, spec.derived_base, claim.period)
        prior = store.latest(document.tenant_id, claim.entity, spec.derived_base, prior_period)
        if current is None or prior is None or prior.value == 0:
            continue  # nothing to recompute against — never guess

        try:
            claimed = parse_quantity(claim.displayed_text)
        except QuantityParseError:
            continue
        if claimed.unit != Unit.PERCENT:
            continue

        expected_value = (current.value / prior.value - Decimal(1)) * Decimal(100)
        expected = Quantity(value=expected_value, unit=Unit.PERCENT, quantum=claimed.quantum)

        if not claimed.matches(expected, DEFAULT_POLICY):
            rounded = DEFAULT_POLICY.round_to(expected_value, claimed.quantum)
            findings.append(
                RuleFinding(
                    rule="derived.recomputation_mismatch",
                    severity=RuleSeverity.BLOCK,
                    document_id=document.id,
                    metric=claim.metric,
                    message=f"'{spec.label}' is stated as {claim.displayed_text} but "
                    f"recomputes to {rounded}% from {spec.derived_base}.",
                    detail=f"Recomputed from {prior_period} {prior.quantity().display()} "
                    f"({prior.source_label or prior.source_type.value}) to {claim.period} "
                    f"{current.quantity().display()} "
                    f"({current.source_label or current.source_type.value}).",
                )
            )

    return findings
