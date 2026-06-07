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
    # (entity, metric, period) -> list of (document_id, displayed_text, quantity_or_None)
    groups: dict[tuple[str, str, str], list[tuple[str, str, Quantity | None]]] = defaultdict(
        list
    )

    for doc in documents:
        for claim in doc.claims:
            key = (claim.entity, claim.metric, claim.period)
            try:
                qty: Quantity | None = parse_quantity(claim.displayed_text)
            except QuantityParseError:
                qty = None
            groups[key].append((doc.id, claim.displayed_text, qty))

    findings: list[RuleFinding] = []
    for (entity, metric, period), uses in groups.items():
        if len(uses) < 2:
            continue
        # Two roundings of the same figure ("$1.24B" in the release, "$1,241.3M" in
        # the 10-Q) are the same number, not a mismatch — judge consistency under the
        # rounding policy, exactly as the intra-document check does. A flag only fires
        # when the parseable values genuinely disagree (e.g. $1.24B vs $1.25B).
        quantities = [q for _, _, q in uses if q is not None]
        if len(quantities) < 2 or _mutually_consistent(quantities):
            continue
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
