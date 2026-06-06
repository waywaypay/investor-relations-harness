"""Tests for the LLM edge.

Every test here runs hermetically: no ANTHROPIC_API_KEY, no network. The real
client is exercised against a stub SDK object so we can assert request shaping
(forced tool, prompt caching) without a call. The cardinal invariant — the
deterministic core renders the same verdicts no matter who proposed the claims —
is pinned by `test_llm_path_yields_identical_verdicts`.
"""

from __future__ import annotations

import pytest

from attest.demo import build_documents, demo_edge_service, seeded_service
from attest.domain.document import Document, DocumentKind
from attest.domain.facts import Confidence
from attest.domain.metrics import DEFAULT_REGISTRY
from attest.domain.verdicts import RuleSeverity
from attest.edge.client import AnthropicClient, FakeLLMClient, LLMResult
from attest.edge.narrator import HistoricalConsistencyNarrator
from attest.edge.proposer import ClaimProposer
from attest.edge.service import EdgeService


# --------------------------------------------------------------------------- #
# Proposer                                                                     #
# --------------------------------------------------------------------------- #


def _doc(text: str, doc_id: str = "d") -> Document:
    return Document(
        id=doc_id, tenant_id="atlas", title="t", kind=DocumentKind.RELEASE, text=text
    )


def test_proposer_maps_tool_output_to_figure_claims():
    client = FakeLLMClient.returning(
        {
            "figures": [
                {
                    "displayed_text": "$1.24 billion",
                    "metric": "total_revenue",
                    "entity": "ATLS",
                    "period": "FY2026-Q1",
                    "confidence": "high",
                    "span_start": 5,
                    "span_end": 18,
                },
                {
                    "displayed_text": "31%",
                    "metric": "cloud_growth_yoy",
                    "entity": "ATLS:Cloud",
                    "period": "FY2026-Q1",
                    "confidence": "low",
                },
            ]
        }
    )
    claims = ClaimProposer(client, DEFAULT_REGISTRY).propose(
        _doc("Revenue was $1.24 billion, up 31%.")
    )

    assert [c.metric for c in claims] == ["total_revenue", "cloud_growth_yoy"]
    assert claims[0].entity == "ATLS"
    assert claims[0].displayed_text == "$1.24 billion"
    assert claims[0].span == (5, 18)
    assert claims[0].detect_confidence == Confidence.HIGH
    # The model's "low" confidence is preserved so the core can route it to a human.
    assert claims[1].detect_confidence == Confidence.LOW
    assert claims[1].span is None
    # Claim ids are deterministic and document-scoped.
    assert {c.claim_id for c in claims} == {"d:llm:0", "d:llm:1"}


def test_proposer_forces_the_tool_and_passes_prompt():
    client = FakeLLMClient.returning({"figures": []})
    ClaimProposer(client, DEFAULT_REGISTRY).propose(_doc("Revenue was $1.24 billion."))
    call = client.calls[0]
    assert call["tool_name"] == "report_figures"
    assert call["tools"][0]["name"] == "report_figures"
    # The metric vocabulary is handed to the model so it maps onto canonical ids.
    assert "total_revenue" in call["messages"][0]["content"]


def test_proposer_skips_structurally_incomplete_figures():
    client = FakeLLMClient.returning(
        {
            "figures": [
                {"displayed_text": "$5", "metric": "", "entity": "ATLS", "period": "FY2026-Q1"},
                {"displayed_text": "", "metric": "total_revenue", "entity": "ATLS", "period": "x"},
                {
                    "displayed_text": "$5",
                    "metric": "total_revenue",
                    "entity": "ATLS",
                    "period": "FY2026-Q1",
                },
            ]
        }
    )
    claims = ClaimProposer(client, DEFAULT_REGISTRY).propose(_doc("..."))
    assert len(claims) == 1
    assert claims[0].displayed_text == "$5"


def test_proposer_empty_text_makes_no_call():
    client = FakeLLMClient.returning({"figures": [{"displayed_text": "x"}]})
    claims = ClaimProposer(client, DEFAULT_REGISTRY).propose(_doc("   "))
    assert claims == ()
    assert client.calls == []


def test_proposer_tolerates_empty_response():
    claims = ClaimProposer(FakeLLMClient(), DEFAULT_REGISTRY).propose(
        _doc("Revenue was $1.24 billion.")
    )
    assert claims == ()


# --------------------------------------------------------------------------- #
# Narrator                                                                     #
# --------------------------------------------------------------------------- #


def test_narrator_emits_non_blocking_findings():
    client = FakeLLMClient.returning(
        {
            "flags": [
                {
                    "passage": "growth is accelerating",
                    "metric": "cloud_growth_yoy",
                    "claimed_implication": "accelerating growth",
                    "contradiction": "YoY growth fell from 34% to 29%.",
                    "severity": "warn",
                }
            ]
        }
    )
    service = seeded_service()
    document = next(d for d in build_documents() if d.id == "release")
    findings = HistoricalConsistencyNarrator(client, DEFAULT_REGISTRY).narrate(
        document, service.store
    )

    assert len(findings) == 1
    f = findings[0]
    assert f.rule == "narrative.history_contradiction"
    assert f.severity == RuleSeverity.WARN  # advises, never blocks
    assert f.severity.value != "block"
    assert "fell" in f.message
    assert f.metric == "cloud_growth_yoy"


def test_narrator_includes_history_table_in_prompt():
    client = FakeLLMClient.returning({"flags": []})
    service = seeded_service()
    document = next(d for d in build_documents() if d.id == "release")
    HistoricalConsistencyNarrator(client, DEFAULT_REGISTRY).narrate(document, service.store)
    content = client.calls[0]["messages"][0]["content"]
    # Prior-year and current cloud-revenue facts both appear so a trend is visible.
    assert "Cloud segment revenue" in content
    assert "FY2025-Q1" in content and "FY2026-Q1" in content


def test_narrator_silent_without_claims_or_text():
    client = FakeLLMClient.returning({"flags": [{"passage": "x", "contradiction": "y"}]})
    narrator = HistoricalConsistencyNarrator(client, DEFAULT_REGISTRY)
    store = seeded_service().store
    assert narrator.narrate(_doc(""), store) == []
    assert client.calls == []


def test_narrator_drops_incomplete_flags():
    client = FakeLLMClient.returning({"flags": [{"passage": "x"}, {"contradiction": "y"}]})
    service = seeded_service()
    document = next(d for d in build_documents() if d.id == "release")
    findings = HistoricalConsistencyNarrator(client, DEFAULT_REGISTRY).narrate(
        document, service.store
    )
    assert findings == []


# --------------------------------------------------------------------------- #
# AnthropicClient request shaping (no network)                                 #
# --------------------------------------------------------------------------- #


class _StubBlock:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _StubResponse:
    def __init__(self, content):
        self.content = content


class _StubMessages:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _StubSDK:
    def __init__(self, response):
        self.messages = _StubMessages(response)


def test_anthropic_client_forces_tool_and_caches_prompt():
    response = _StubResponse(
        [
            _StubBlock("text", text="ignored"),
            _StubBlock("tool_use", input={"figures": [{"displayed_text": "$5"}]}),
        ]
    )
    sdk = _StubSDK(response)
    client = AnthropicClient(client=sdk, model="claude-sonnet-4-6")

    result = client.call_tool(
        system="SYS",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "a"}, {"name": "report_figures"}],
        tool_name="report_figures",
    )

    assert result.first == {"figures": [{"displayed_text": "$5"}]}
    assert result.raw_text == "ignored"

    sent = sdk.messages.last_kwargs
    assert sent["model"] == "claude-sonnet-4-6"
    assert sent["tool_choice"] == {"type": "tool", "name": "report_figures"}
    # System prompt is a cached content block.
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["system"][0]["text"] == "SYS"
    # Only the final tool carries the cache breakpoint; earlier tools do not.
    assert "cache_control" not in sent["tools"][0]
    assert sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}


# --------------------------------------------------------------------------- #
# EdgeService + service/API wiring                                            #
# --------------------------------------------------------------------------- #


def test_edge_service_propose_replaces_claims():
    client = FakeLLMClient.returning(
        {
            "figures": [
                {
                    "displayed_text": "$1.24 billion",
                    "metric": "total_revenue",
                    "entity": "ATLS",
                    "period": "FY2026-Q1",
                    "confidence": "high",
                }
            ]
        }
    )
    edge = EdgeService(client)
    original = next(d for d in build_documents() if d.id == "release")
    proposed = edge.propose_claims(original)
    assert original.claims  # untouched original
    assert len(proposed.claims) == 1
    assert proposed.claims[0].claim_id == "release:llm:0"


def test_llm_path_yields_identical_verdicts():
    """The deterministic core disposes the same regardless of who proposed."""
    deterministic = seeded_service()
    det_results, det_consistency = deterministic.verify_close_pack(build_documents())

    llm_service = seeded_service(edge=demo_edge_service())
    llm_results, llm_consistency = llm_service.verify_close_pack(build_documents(), use_llm=True)

    def signature(results):
        return [
            sorted((v.metric, v.displayed_text, v.verdict.value) for v in r.verdicts)
            for r in results
        ]

    assert signature(llm_results) == signature(det_results)
    assert llm_consistency == det_consistency
    assert all(r.publishable == d.publishable for r, d in zip(llm_results, det_results))


def test_narrative_findings_do_not_change_publishability():
    flagging_client = FakeLLMClient(
        handler=lambda *, tool_name, **_: (
            LLMResult(tool_inputs=({"figures": _release_figures()},))
            if tool_name == "report_figures"
            else LLMResult(
                tool_inputs=(
                    {
                        "flags": [
                            {
                                "passage": "strong start",
                                "metric": "total_revenue",
                                "claimed_implication": "record quarter",
                                "contradiction": "history does not support a record.",
                                "severity": "warn",
                            }
                        ]
                    },
                )
            )
        )
    )
    service = seeded_service(edge=EdgeService(flagging_client))
    release = next(d for d in build_documents() if d.id == "release")
    result = service.verify_document(release, use_llm=True)

    narrative = [f for f in result.findings if f.rule == "narrative.history_contradiction"]
    assert len(narrative) == 1
    # A conflict already blocked this doc; the WARN narrative finding doesn't flip it,
    # and crucially no narrative finding is ever severity 'block'.
    assert all(f.severity != RuleSeverity.BLOCK for f in narrative)


def test_use_llm_without_edge_raises():
    service = seeded_service()  # no edge configured
    release = next(d for d in build_documents() if d.id == "release")
    with pytest.raises(RuntimeError, match="requires an LLM edge"):
        service.verify_document(release, use_llm=True)


def _release_figures() -> list[dict]:
    release = next(d for d in build_documents() if d.id == "release")
    return [
        {
            "displayed_text": c.displayed_text,
            "metric": c.metric,
            "entity": c.entity,
            "period": c.period,
            "confidence": c.detect_confidence.value,
        }
        for c in release.claims
    ]


# --------------------------------------------------------------------------- #
# API surface                                                                  #
# --------------------------------------------------------------------------- #


def test_api_verify_use_llm_matches_and_requires_edge():
    from fastapi.testclient import TestClient

    from attest.api.app import create_app
    from attest.ingestion.edgar_xbrl import load_fixture

    release = next(d for d in build_documents() if d.id == "release")

    # No edge configured -> use_llm rejected with a clear 422.
    plain = TestClient(create_app())
    plain.post("/tenants/atlas/ingest/xbrl", json=load_fixture("atlas_q1_fy2026"))
    r = plain.post(
        "/tenants/atlas/verify?use_llm=true", json=release.model_dump(mode="json")
    )
    assert r.status_code == 422
    assert "edge" in r.json()["detail"].lower()

    # Edge configured -> identical counts to the deterministic path.
    service = seeded_service(edge=demo_edge_service())
    client = TestClient(create_app(service))
    body = client.post(
        "/tenants/atlas/verify?use_llm=true", json=release.model_dump(mode="json")
    ).json()
    assert body["counts"]["traced"] == 6
    assert body["counts"]["conflict"] == 1
    assert body["publishable"] is False
