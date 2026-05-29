"""Reg G / non-GAAP rule.

Reg G requires that any non-GAAP measure presented is reconciled to the most
directly comparable GAAP measure, and that the GAAP measure is given equal
prominence. Deterministically, for every non-GAAP metric a document claims, we
require: (1) its GAAP counterpart is also claimed in the same document, (2) a
reconciliation source exists in the fact store for both the measure and its
counterpart in the same period, and (3) the non-GAAP measure is not *presented
before* its GAAP counterpart (a position proxy for "equal or greater
prominence", judged from claim spans and skipped when spans are absent).
"""

from __future__ import annotations

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry
from attest.domain.verdicts import RuleFinding, RuleSeverity
from attest.factstore.repository import FactStore


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
