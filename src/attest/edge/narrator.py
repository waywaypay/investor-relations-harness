"""Historical-consistency narrator — the narrative half of the edge.

Every deterministic rule catches a figure that is individually wrong. This catches
the subtler failure: every number is correct, yet the *story* told around them is
not — "accelerating growth" in a quarter that decelerated, a "record" that the
history refutes. That judgement is irreducibly linguistic, so it lives at the
edge. The narrator pulls the relevant verified facts from the store, asks the
model whether the prose's direction/superlatives survive them, and returns plain
:class:`RuleFinding` s the rest of the pipeline already knows how to render.

Findings are non-blocking by construction (``warn``/``info``): a narrative false
positive erodes trust, so the model can advise a human but never block a publish.
"""

from __future__ import annotations

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry
from attest.domain.verdicts import RuleFinding, RuleSeverity
from attest.edge.client import LLMClient
from attest.edge.prompts import NARRATOR_SYSTEM, REPORT_NARRATIVE_FLAGS_TOOL
from attest.factstore.repository import FactStore

# The narrator may only ever advise; it cannot block a disclosure.
_SEVERITY = {"warn": RuleSeverity.WARN, "info": RuleSeverity.INFO}


class HistoricalConsistencyNarrator:
    """Flag prose whose narrative implication contradicts the historical facts."""

    def __init__(self, client: LLMClient, registry: MetricRegistry) -> None:
        self._client = client
        self._registry = registry

    def _history_table(self, document: Document, store: FactStore) -> str:
        """Render the verified facts behind this document's claims, across periods.

        We include *every* version on record for each (entity, metric) the document
        touches so the model can see a trend, not just a point — that is what lets
        it judge "accelerating" vs "decelerating".
        """
        wanted = {(c.entity, c.metric) for c in document.claims}
        rows: list[tuple[str, str, str, str]] = []
        for fact in store.all(document.tenant_id):
            if (fact.entity, fact.metric) not in wanted:
                continue
            spec = self._registry.get(fact.metric)
            label = spec.label if spec else fact.metric
            rows.append((fact.entity, label, fact.period, fact.quantity().display()))

        if not rows:
            return "(no historical figures on record)"
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        return "\n".join(
            f"  - {entity} · {label} · {period}: {value}"
            for entity, label, period, value in rows
        )

    def narrate(self, document: Document, store: FactStore) -> list[RuleFinding]:
        if not document.text.strip() or not document.claims:
            return []

        result = self._client.call_tool(
            system=NARRATOR_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Verified historical figures:\n"
                        f"{self._history_table(document, store)}\n\n"
                        f"Draft prose (document id={document.id!r}):\n"
                        f"---\n{document.text}\n---"
                    ),
                }
            ],
            tools=[REPORT_NARRATIVE_FLAGS_TOOL],
            tool_name=REPORT_NARRATIVE_FLAGS_TOOL["name"],
        )

        flags = result.first.get("flags", []) if result.tool_inputs else []
        findings: list[RuleFinding] = []
        for flag in flags:
            passage = (flag.get("passage") or "").strip()
            implication = (flag.get("claimed_implication") or "").strip()
            contradiction = (flag.get("contradiction") or "").strip()
            if not (passage and contradiction):
                continue
            severity = _SEVERITY.get(str(flag.get("severity", "warn")).lower(), RuleSeverity.WARN)
            detail = passage if not implication else f"“{passage}” — implies {implication}."
            findings.append(
                RuleFinding(
                    rule="narrative.history_contradiction",
                    severity=severity,
                    document_id=document.id,
                    metric=(flag.get("metric") or "").strip() or None,
                    message=contradiction,
                    detail=detail,
                )
            )
        return findings
