"""Forward-looking-statement (FLS) detection -> safe-harbor requirement.

A document that makes forward-looking statements must carry safe-harbor language
before it can publish. We detect FLS via trigger phrases and, separately, via any
claim bound to a ``MANAGEMENT_INPUT`` (guidance) source, then require a
safe-harbor marker somewhere in the prose.
"""

from __future__ import annotations

import re

from attest.domain.document import Document
from attest.domain.facts import SourceType
from attest.domain.verdicts import RuleFinding, RuleSeverity
from attest.factstore.repository import FactStore

_FLS_TRIGGERS = re.compile(
    r"\b(we expect|expects?|we anticipate|anticipates?|outlook|guidance|"
    r"looking ahead|for the (?:second|third|fourth|first) quarter|"
    r"full[- ]year|we believe we will|projected?|forecast)\b",
    re.IGNORECASE,
)

_SAFE_HARBOR = re.compile(
    r"\b(safe[- ]harbor|forward[- ]looking statements?|"
    r"Private Securities Litigation Reform Act|Section 27A|Section 21E)\b",
    re.IGNORECASE,
)


def check_forward_looking(document: Document, store: FactStore) -> list[RuleFinding]:
    has_trigger = bool(_FLS_TRIGGERS.search(document.text))

    guidance_metrics = [
        c.metric
        for c in document.claims
        if (
            (fact := store.latest(document.tenant_id, c.entity, c.metric, c.period))
            is not None
            and fact.source_type == SourceType.MANAGEMENT_INPUT
        )
    ]

    if not has_trigger and not guidance_metrics:
        return []

    if _SAFE_HARBOR.search(document.text):
        return []  # FLS present, and safe-harbor language is attached — satisfied.

    reason_bits = []
    if has_trigger:
        reason_bits.append("forward-looking language")
    if guidance_metrics:
        reason_bits.append(f"guidance figures ({', '.join(sorted(set(guidance_metrics)))})")

    return [
        RuleFinding(
            rule="forward_looking.safe_harbor_required",
            severity=RuleSeverity.BLOCK,
            document_id=document.id,
            message="Forward-looking statements present without safe-harbor language.",
            detail="Detected " + " and ".join(reason_bits)
            + ". Attach safe-harbor language before this document can publish.",
        )
    ]
