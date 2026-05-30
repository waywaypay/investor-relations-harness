"""LLM claim proposer — prose in, :class:`FigureClaim` s out.

This is the production stand-in for :mod:`attest.verification.candidates`: instead
of a regex, an LLM reads the draft and proposes which canonical metric each figure
asserts. It emits the *same* :class:`FigureClaim` domain type the deterministic
engine already consumes, so swapping the detector is a constructor choice, not a
schema change. The proposer attaches a detection confidence and nothing more — it
cannot, and does not, assert a tie-out.
"""

from __future__ import annotations

from attest.domain.document import Document
from attest.domain.facts import Confidence
from attest.domain.metrics import MetricRegistry
from attest.domain.verdicts import FigureClaim
from attest.edge.client import LLMClient
from attest.edge.prompts import PROPOSER_SYSTEM, REPORT_FIGURES_TOOL

_CONFIDENCE = {
    "high": Confidence.HIGH,
    "medium": Confidence.MEDIUM,
    "low": Confidence.LOW,
}


class ClaimProposer:
    """Propose :class:`FigureClaim` s for a document's prose using an LLM."""

    def __init__(self, client: LLMClient, registry: MetricRegistry) -> None:
        self._client = client
        self._registry = registry

    def _vocabulary(self) -> str:
        lines = [
            f"  - {spec.id}: {spec.label} ({spec.unit.value})"
            for spec in self._registry.all()
        ]
        return "\n".join(lines)

    def _user_message(self, document: Document) -> str:
        return (
            "Known canonical metrics (map each figure to one of these ids when you "
            f"can; if none fits, use your best guess and set confidence='low'):\n"
            f"{self._vocabulary()}\n\n"
            f"Default entity: {document.tenant_id!r} issuer. Use segment-qualified "
            "entities (e.g. 'MRDN:Cloud') when the prose attributes a figure to a "
            "segment.\n\n"
            f"Draft document (id={document.id!r}, kind={document.kind.value}):\n"
            f"---\n{document.text}\n---"
        )

    def propose(self, document: Document) -> tuple[FigureClaim, ...]:
        """Return the figure claims the model proposes for ``document``.

        Returns an empty tuple when the document has no prose or the model finds
        nothing — never raises on a thin/garbled response (over-detect, never
        crash; a missing claim must surface as a gap, not an exception).
        """
        if not document.text.strip():
            return ()

        result = self._client.call_tool(
            system=PROPOSER_SYSTEM,
            messages=[{"role": "user", "content": self._user_message(document)}],
            tools=[REPORT_FIGURES_TOOL],
            tool_name=REPORT_FIGURES_TOOL["name"],
        )

        figures = result.first.get("figures", []) if result.tool_inputs else []
        claims: list[FigureClaim] = []
        for i, fig in enumerate(figures):
            text = (fig.get("displayed_text") or "").strip()
            metric = (fig.get("metric") or "").strip()
            entity = (fig.get("entity") or "").strip()
            period = (fig.get("period") or "").strip()
            if not (text and metric and entity and period):
                # Skip structurally incomplete proposals rather than fabricate scope.
                continue
            span = self._span(fig)
            claims.append(
                FigureClaim(
                    claim_id=f"{document.id}:llm:{i}",
                    document_id=document.id,
                    entity=entity,
                    metric=metric,
                    period=period,
                    displayed_text=text,
                    span=span,
                    detect_confidence=_CONFIDENCE.get(
                        str(fig.get("confidence", "")).lower(), Confidence.MEDIUM
                    ),
                )
            )
        return tuple(claims)

    @staticmethod
    def _span(fig: dict) -> tuple[int, int] | None:
        start, end = fig.get("span_start"), fig.get("span_end")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end:
            return (start, end)
        return None
