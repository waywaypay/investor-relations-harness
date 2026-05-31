"""The LLM seam — a thin, model-replaceable boundary.

The whole architecture rests on keeping the probabilistic edge behind a narrow
interface so the deterministic core never depends on it. This module is that
boundary: a :class:`LLMClient` Protocol with exactly one operation (a structured
tool call), a real :class:`AnthropicClient` that speaks the Messages API with
prompt caching, and a :class:`FakeLLMClient` that lets every test run hermetically
with no key and no network.

Nothing here renders a verdict. The client returns the *raw structured proposal*
the model emitted; the proposer/narrator shape it into domain types, and the
deterministic engine disposes. The model is the replaceable component.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

# The latest Sonnet is the right cost/latency fit for span extraction and
# narrative review; callers can override per-environment.
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class LLMResult:
    """The structured output of one tool call.

    ``tool_inputs`` is the ``input`` payload of each ``tool_use`` block the model
    returned (already parsed JSON). Callers validate/shape it into domain types;
    this stays deliberately untyped so a malformed model response degrades to a
    handled empty proposal rather than a crash.
    """

    tool_inputs: tuple[dict, ...] = ()
    raw_text: str = ""

    @property
    def first(self) -> dict:
        return self.tool_inputs[0] if self.tool_inputs else {}


@runtime_checkable
class LLMClient(Protocol):
    """One structured call: system + messages + a forced tool -> tool inputs."""

    def call_tool(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_name: str,
        max_tokens: int = 2048,
    ) -> LLMResult: ...


def _cached_system(system: str) -> list[dict]:
    """Wrap the system prompt as a single cacheable content block.

    The system prompt is large and stable across a close pack; caching it (and the
    tool defs below) is the single biggest cost lever for this workload.
    """
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _cached_tools(tools: list[dict]) -> list[dict]:
    """Mark the final tool with a cache breakpoint so the tool defs are cached too."""
    if not tools:
        return tools
    cached = [dict(t) for t in tools]
    cached[-1] = {**cached[-1], "cache_control": {"type": "ephemeral"}}
    return cached


class AnthropicClient:
    """Real client over the Anthropic Messages API with prompt caching.

    The ``anthropic`` SDK is imported lazily so importing :mod:`attest.edge` never
    requires the optional dependency; only *constructing* this client does.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - exercised via message only
                raise RuntimeError(
                    "AnthropicClient requires the 'anthropic' package. "
                    "Install the LLM edge extra: pip install '.[llm]'"
                ) from exc
            client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self._client = client
        self._model = model

    def call_tool(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_name: str,
        max_tokens: int = 2048,
    ) -> LLMResult:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=_cached_system(system),
            messages=messages,
            tools=_cached_tools(tools),
            tool_choice={"type": "tool", "name": tool_name},
        )
        tool_inputs: list[dict] = []
        text_parts: list[str] = []
        for block in response.content:
            kind = getattr(block, "type", None)
            if kind == "tool_use":
                tool_inputs.append(dict(block.input))
            elif kind == "text":
                text_parts.append(block.text)
        return LLMResult(tool_inputs=tuple(tool_inputs), raw_text="".join(text_parts))


@dataclass
class FakeLLMClient:
    """A scripted client for hermetic tests and offline CLI runs.

    Supply either a list of canned :class:`LLMResult` responses (returned in order,
    the last one repeating once exhausted) or a ``handler`` callable for dynamic
    behaviour. Every call is recorded on :attr:`calls` for assertions.
    """

    responses: list[LLMResult] = field(default_factory=list)
    handler: Callable[..., LLMResult] | None = None
    calls: list[dict] = field(default_factory=list)

    def call_tool(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_name: str,
        max_tokens: int = 2048,
    ) -> LLMResult:
        self.calls.append(
            {"system": system, "messages": messages, "tools": tools, "tool_name": tool_name}
        )
        if self.handler is not None:
            return self.handler(
                system=system, messages=messages, tools=tools, tool_name=tool_name
            )
        if not self.responses:
            return LLMResult()
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)

    @classmethod
    def returning(cls, *tool_inputs: dict) -> "FakeLLMClient":
        """Build a client whose single response carries the given tool inputs."""
        return cls(responses=[LLMResult(tool_inputs=tuple(tool_inputs))])
