"""Claim-unit vs. metric-declared-unit consistency.

Every canonical metric declares the dimension it is measured in (currency,
percent, basis points, shares...). If a claim's figure parses to a *different*
dimension than the metric it asserts — a percentage where a dollar amount belongs
— that is a structural error the value-matching path can't see, because it only
ever compares same-unit quantities. This rule catches the mismatch up front.

A few currency metrics are legitimately *also* discussed as rates (a growth line
expressed in percent). Those metrics carry their own ``unit`` and would not be
claimed against here unless mis-tagged; the check compares strictly against the
declared unit and stays silent when the figure can't be parsed.
"""

from __future__ import annotations

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry
from attest.domain.money import QuantityParseError, parse_quantity
from attest.domain.verdicts import RuleFinding, RuleSeverity
from attest.factstore.repository import FactStore


def check_unit_consistency(
    document: Document, registry: MetricRegistry, store: FactStore
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []

    for claim in document.claims:
        spec = registry.get(claim.metric)
        if spec is None:
            continue
        try:
            claimed = parse_quantity(claim.displayed_text)
        except QuantityParseError:
            continue  # ranges / unparseable text are handled elsewhere — never guess
        if claimed.unit != spec.unit:
            findings.append(
                RuleFinding(
                    rule="units.unit_mismatch",
                    severity=RuleSeverity.BLOCK,
                    document_id=document.id,
                    metric=claim.metric,
                    message=f"'{spec.label}' is declared in {spec.unit.value} but the "
                    f"figure '{claim.displayed_text}' parses as {claimed.unit.value}.",
                    detail="The figure's unit does not match the metric's declared unit.",
                )
            )

    return findings
