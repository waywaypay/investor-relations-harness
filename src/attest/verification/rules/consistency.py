"""Cross-document and intra-document figure consistency.

The same fact must be used identically across the release, the script, and the
Q&A — and within any single document. We group claims by
``(entity, metric, period)`` and flag any group whose values disagree — the IRO's
nightmare of a number that reads one way in the press release and another on the
call (or twice in the same script).
"""

from __future__ import annotations

from collections import defaultdict

from attest.domain.document import Document
from attest.domain.money import (
    DEFAULT_POLICY,
    Quantity,
    QuantityParseError,
    parse_quantity,
)
from attest.domain.verdicts import RuleFinding, RuleSeverity


def _mutually_consistent(quantities: list[Quantity]) -> bool:
    """True if every pair agrees under the rounding policy (one rounds to the other)."""
    for i in range(len(quantities)):
        for j in range(i + 1, len(quantities)):
            a, b = quantities[i], quantities[j]
            if not (a.matches(b, DEFAULT_POLICY) or b.matches(a, DEFAULT_POLICY)):
                return False
    return True


def check_intra_document_consistency(document: Document) -> list[RuleFinding]:
    """Flag a metric/period claimed with incompatible values within one document."""
    groups: dict[tuple[str, str, str], list[tuple[str, Quantity]]] = defaultdict(list)
    for claim in document.claims:
        try:
            qty = parse_quantity(claim.displayed_text)
        except QuantityParseError:
            continue
        groups[(claim.entity, claim.metric, claim.period)].append((claim.displayed_text, qty))

    findings: list[RuleFinding] = []
    for (entity, metric, period), uses in groups.items():
        if len(uses) < 2:
            continue
        if not _mutually_consistent([q for _, q in uses]):
            rendered = "; ".join(f"'{text}'" for text, _ in uses)
            findings.append(
                RuleFinding(
                    rule="consistency.intra_document_mismatch",
                    severity=RuleSeverity.BLOCK,
                    document_id=document.id,
                    metric=metric,
                    message=f"'{metric}' for {period} ({entity}) is stated with incompatible "
                    f"values within this document.",
                    detail=rendered,
                )
            )
    return findings


def check_cross_document_consistency(documents: list[Document]) -> list[RuleFinding]:
    # (entity, metric, period) -> list of (document_id, displayed_text, normalized_or_None)
    groups: dict[tuple[str, str, str], list[tuple[str, str, object]]] = defaultdict(list)

    for doc in documents:
        for claim in doc.claims:
            key = (claim.entity, claim.metric, claim.period)
            try:
                norm = parse_quantity(claim.displayed_text)
                normalized: object = (norm.unit, norm.value)
            except QuantityParseError:
                normalized = None
            groups[key].append((doc.id, claim.displayed_text, normalized))

    findings: list[RuleFinding] = []
    for (entity, metric, period), uses in groups.items():
        if len(uses) < 2:
            continue
        distinct = {u[2] for u in uses if u[2] is not None}
        if len(distinct) > 1:
            rendered = "; ".join(f"{doc_id}: '{text}'" for doc_id, text, _ in uses)
            findings.append(
                RuleFinding(
                    rule="consistency.cross_document_mismatch",
                    severity=RuleSeverity.BLOCK,
                    metric=metric,
                    message=f"'{metric}' for {period} ({entity}) is stated inconsistently "
                    f"across documents.",
                    detail=rendered,
                )
            )
    return findings
