"""The edge composition seam.

Bundles the two probabilistic capabilities — claim proposal and historical-
consistency narration — behind one object the :class:`~attest.service.AttestService`
can hold (or not). When ``AttestService.edge is None`` the system runs exactly as
the deterministic-only v1 did; supplying an :class:`EdgeService` lights up the
``use_llm`` path without changing a single verdict's meaning.
"""

from __future__ import annotations

from attest.domain.document import Document
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.domain.verdicts import RuleFinding
from attest.edge.client import AnthropicClient, LLMClient
from attest.edge.narrator import HistoricalConsistencyNarrator
from attest.edge.proposer import ClaimProposer
from attest.factstore.repository import FactStore


class EdgeService:
    """Composition root for the LLM edge (proposer + narrator over one client)."""

    def __init__(
        self,
        client: LLMClient,
        registry: MetricRegistry | None = None,
    ) -> None:
        self.client = client
        self.registry = registry or DEFAULT_REGISTRY
        self.proposer = ClaimProposer(client, self.registry)
        self.narrator = HistoricalConsistencyNarrator(client, self.registry)

    def propose_claims(self, document: Document) -> Document:
        """Return ``document`` with its claims replaced by the model's proposals."""
        claims = self.proposer.propose(document)
        return document.model_copy(update={"claims": claims})

    def narrate(self, document: Document, store: FactStore) -> list[RuleFinding]:
        return self.narrator.narrate(document, store)

    @classmethod
    def anthropic(
        cls,
        *,
        model: str | None = None,
        api_key: str | None = None,
        registry: MetricRegistry | None = None,
    ) -> "EdgeService":
        """Convenience constructor wiring a real :class:`AnthropicClient`."""
        kwargs = {"api_key": api_key}
        if model is not None:
            kwargs["model"] = model
        return cls(AnthropicClient(**kwargs), registry=registry)
