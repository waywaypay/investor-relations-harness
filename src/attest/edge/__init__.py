"""The LLM edge — the *replaceable* probabilistic layer above the deterministic spine.

The edge proposes; the core disposes. Everything in this package locates candidate
figures and reads narrative direction; none of it decides a tie-out. It is wired in
behind an optional :class:`EdgeService`, so the deterministic v1 remains the default
and the model stays swappable.
"""

from attest.edge.client import (
    DEFAULT_MODEL,
    AnthropicClient,
    FakeLLMClient,
    LLMClient,
    LLMResult,
)
from attest.edge.narrator import HistoricalConsistencyNarrator
from attest.edge.proposer import ClaimProposer
from attest.edge.service import EdgeService

__all__ = [
    "DEFAULT_MODEL",
    "AnthropicClient",
    "FakeLLMClient",
    "LLMClient",
    "LLMResult",
    "ClaimProposer",
    "HistoricalConsistencyNarrator",
    "EdgeService",
]
