"""Cross-document consistency.

The same fact must be used identically across the release, the script, and the
Q&A. We group claims across documents by ``(entity, metric, period)`` and flag any
group whose normalized values disagree — the IRO's nightmare of a number that
reads one way in the press release and another on the call.
"""

from __future__ import annotations

from collections import defaultdict

from attest.domain.document import Document
from attest.domain.money import QuantityParseError, parse_quantity
from attest.domain.verdicts import RuleFinding, RuleSeverity


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
