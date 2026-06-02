"""Reg G / non-GAAP rule.

Reg G requires that any non-GAAP measure presented is reconciled to the most
directly comparable GAAP measure, and that the GAAP measure is given equal
prominence. Deterministically, for every non-GAAP metric a document claims, we
require: (1) its GAAP counterpart is also claimed in the same document, (2) a
reconciliation source exists in the fact store for both the measure and its
counterpart in the same period, (2b) the disclosed bridge actually adds up —
GAAP + sum(adjustments) == the non-GAAP measure — and (3) the non-GAAP measure
is not *presented before* its GAAP counterpart (a position proxy for "equal or
greater prominence", judged from claim spans and skipped when spans are absent).
"""

from __future__ import annotations

from decimal import Decimal

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry
from attest.domain.money import DEFAULT_POLICY
from attest.domain.verdicts import RuleFinding, RuleSeverity
from attest.factstore.repository import FactStore


def _metric_label(registry: MetricRegistry, metric_id: str) -> str:
    spec = registry.get(metric_id)
    return spec.label if spec else metric_id


def check_reg_g(
    document: Document, registry: MetricRegistry, store: FactStore
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    claimed_metrics = {c.metric for c in document.claims}

    for claim in document.claims:
        spec = registry.get(claim.metric)
        if spec is None or not spec.is_non_gaap:
            continue
        counterpart = spec.gaap_counterpart

        # (1) equal prominence: the GAAP counterpart must appear in the same document.
        if counterpart is None or counterpart not in claimed_metrics:
            findings.append(
                RuleFinding(
                    rule="reg_g.equal_prominence",
                    severity=RuleSeverity.BLOCK,
                    document_id=document.id,
                    metric=claim.metric,
                    message=f"Non-GAAP measure '{spec.label}' lacks an equal-prominence "
                    f"GAAP figure in this document.",
                    detail="Reg G requires the comparable GAAP measure to be presented "
                    "with equal prominence.",
                )
            )
            continue

        # (2) reconciliation: both legs must exist as facts for the claimed period.
        non_gaap_fact = store.latest(
            document.tenant_id, claim.entity, claim.metric, claim.period
        )
        gaap_fact = store.latest(document.tenant_id, claim.entity, counterpart, claim.period)
        if non_gaap_fact is None or gaap_fact is None:
            findings.append(
                RuleFinding(
                    rule="reg_g.reconciliation_required",
                    severity=RuleSeverity.BLOCK,
                    document_id=document.id,
                    metric=claim.metric,
                    message=f"No reconciliation source found bridging '{spec.label}' to "
                    f"its GAAP counterpart for {claim.period}.",
                    detail="A GAAP-to-non-GAAP reconciliation must be disclosed.",
                )
            )
            continue

        # (2b) reconciliation arithmetic: GAAP + sum(disclosed adjustments) must
        # equal the non-GAAP measure. A bridge that doesn't add up is caught here,
        # not by eyeballing the exhibit. Skipped when no adjustments are declared
        # or any adjustment fact is missing — we never guess at the bridge.
        adjustment_ids = spec.reconciliation_adjustments
        if adjustment_ids:
            found = [
                store.latest(document.tenant_id, claim.entity, adj_id, claim.period)
                for adj_id in adjustment_ids
            ]
            adj_facts = [f for f in found if f is not None]
            if len(adj_facts) == len(adjustment_ids):
                bridged = gaap_fact.value + sum((f.value for f in adj_facts), Decimal(0))
                quantum = non_gaap_fact.quantum or Decimal("0.01")
                expected = DEFAULT_POLICY.round_to(bridged, quantum)
                if expected != non_gaap_fact.value:
                    parts = " + ".join(
                        f"{f.value} ({_metric_label(registry, aid)})"
                        for aid, f in zip(adjustment_ids, adj_facts)
                    )
                    findings.append(
                        RuleFinding(
                            rule="reg_g.reconciliation_arithmetic",
                            severity=RuleSeverity.BLOCK,
                            document_id=document.id,
                            metric=claim.metric,
                            message=f"Reconciliation for '{spec.label}' does not add up: "
                            f"GAAP {gaap_fact.value} + adjustments = {expected}, but the "
                            f"non-GAAP measure is booked as {non_gaap_fact.value}.",
                            detail=f"Bridge: {gaap_fact.value} (GAAP) + {parts} = {expected}.",
                        )
                    )

    # (3) equal-prominence ordering: a non-GAAP measure must not be presented
    # before its GAAP counterpart. Spans are the position proxy; if either side
    # lacks a span we cannot judge prominence and do not guess.
    first_start: dict[str, int] = {}
    for claim in document.claims:
        if claim.span is not None:
            start = claim.span[0]
            if claim.metric not in first_start or start < first_start[claim.metric]:
                first_start[claim.metric] = start

    for metric_id in {c.metric for c in document.claims}:
        spec = registry.get(metric_id)
        if spec is None or not spec.is_non_gaap or spec.gaap_counterpart is None:
            continue
        counterpart = spec.gaap_counterpart
        if metric_id not in first_start or counterpart not in first_start:
            continue
        if first_start[metric_id] < first_start[counterpart]:
            findings.append(
                RuleFinding(
                    rule="reg_g.equal_prominence_ordering",
                    severity=RuleSeverity.WARN,
                    document_id=document.id,
                    metric=metric_id,
                    message=f"Non-GAAP measure '{spec.label}' is presented before its "
                    f"GAAP counterpart.",
                    detail="Reg G requires the comparable GAAP measure to be presented "
                    "with equal or greater prominence; here the non-GAAP figure appears "
                    "first.",
                )
            )

    return findings
