"""Tests for the hardening pass: the period value object, the injectable edge
Protocol, the honoured rounding mode, generic guidance attribution, the upload
cap, tenant authorization, and concurrency-safety of the in-memory stores.

These guard the seams the architecture promises (a swappable edge, a configurable
tie-out policy, real multi-tenancy) so they can't silently regress.
"""

from __future__ import annotations

import threading
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from fastapi.testclient import TestClient

from attest.api.app import create_app
from attest.api.auth import AllowAllAuthorizer, Authorizer, StaticTokenAuthorizer
from attest.audit.events import EventType
from attest.audit.log import InMemoryAuditLog
from attest.domain.facts import Fact, SourceType
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry, MetricSpec
from attest.domain.money import RoundingPolicy, Unit
from attest.domain.period import Period
from attest.domain.verdicts import FigureClaim, Verdict
from attest.extraction.claims import DEFAULT_ALIASES, ClaimExtractor, ClaimProposer
from attest.factstore.repository import InMemoryFactStore
from attest.ingestion.edgar_xbrl import load_fixture
from attest.service import AttestService


# -- Period value object ------------------------------------------------------

def test_period_parse_format_roundtrip_and_rejects_junk():
    for s in ("FY2026-Q1", "FY2026-FY", "FY2026"):
        assert str(Period.parse(s)) == s
    assert Period.parse("fy2026-q3").part == "Q3"  # case-insensitive, normalised
    assert Period.parse("not a period") is None
    assert Period.parse(None) is None


def test_period_arithmetic_is_centralised_and_correct():
    assert str(Period.parse("FY2026-Q1").prior_year()) == "FY2025-Q1"
    assert str(Period.parse("FY2026-Q1").prior_quarter()) == "FY2025-Q4"  # wraps the year
    assert str(Period.parse("FY2026-Q2").prior_quarter()) == "FY2026-Q1"
    assert str(Period.parse("FY2026-Q4").next_quarter()) == "FY2027-Q1"  # wraps the year
    assert str(Period.parse("FY2026-Q1").next_quarter()) == "FY2026-Q2"
    # prior_year preserves the within-year part, including the full-year marker.
    assert str(Period.parse("FY2026-FY").prior_year()) == "FY2025-FY"
    # quarter arithmetic is undefined for non-quarterly periods — never guessed.
    assert Period.parse("FY2026-FY").next_quarter() is None
    assert Period.parse("FY2026").prior_quarter() is None


def test_period_find_in_prose():
    assert str(Period.find("see the FY2025-Q3 numbers")) == "FY2025-Q3"
    assert Period.find("no period token here") is None


# -- injectable probabilistic edge (ClaimProposer) ----------------------------

def test_claim_extractor_satisfies_the_proposer_protocol():
    assert isinstance(ClaimExtractor(DEFAULT_REGISTRY, InMemoryFactStore()), ClaimProposer)


def test_service_uses_an_injected_proposer_and_core_disposes_unchanged():
    """The whole thesis: swap the edge, and the deterministic core still disposes."""

    class FixedProposer:  # a stand-in for an LLM edge — no regex, just a fixed proposal
        def __init__(self, registry, store, aliases) -> None:  # factory shape
            pass

        def extract(self, text, *, document_id, tenant_id, entity, period):
            return (
                FigureClaim(
                    claim_id="x", document_id=document_id, entity=entity,
                    metric="total_revenue", period=period or "FY2026-Q1",
                    displayed_text="$1.24 billion",
                ),
            )

    svc = AttestService(proposer_factory=FixedProposer)
    svc.ingest_xbrl(load_fixture("meridian_q1_fy2026"), tenant_id="meridian")
    doc, result, _, _ = svc.analyze_text(
        tenant_id="meridian", text="(ignored by the fake edge)",
        entity="MRDN", period="FY2026-Q1",
    )
    assert [c.metric for c in doc.claims] == ["total_revenue"]
    # The injected proposal flows through the unchanged core and ties out.
    assert result.verdicts[0].verdict == Verdict.TRACED


# -- honoured rounding mode ---------------------------------------------------

def test_rounding_policy_mode_is_honoured():
    half_up = RoundingPolicy()  # the default
    down = RoundingPolicy(rounding=ROUND_DOWN)
    assert half_up.rounding == ROUND_HALF_UP
    # 1.25 rounded to a 0.1 quantum: half-up -> 1.3, truncating -> 1.2.
    assert half_up.round_to(Decimal("1.25"), Decimal("0.1")) == Decimal("1.3")
    assert down.round_to(Decimal("1.25"), Decimal("0.1")) == Decimal("1.2")


# -- generic guidance attribution (no hard-coded demo metric) -----------------

def test_guidance_range_routes_to_a_guidance_metric_never_an_actual():
    reg = MetricRegistry([
        MetricSpec(id="total_revenue", label="Total revenue", unit=Unit.CURRENCY),
        MetricSpec(id="revenue_guidance", label="Revenue guidance", unit=Unit.CURRENCY),
    ])
    text = "The company expects revenue in the range of $1.31 to $1.34 billion."
    claims = ClaimExtractor(reg, InMemoryFactStore(), DEFAULT_ALIASES).extract(
        text, document_id="d", tenant_id="t", entity="E", period="FY2026-Q1",
    )
    rng = next(c for c in claims if " to " in c.displayed_text)
    # The nearest alias is the 'revenue' actual, but a guidance range must never be
    # asserted as a same-period actual — it resolves to the guidance metric.
    assert rng.metric == "revenue_guidance"


# -- API: upload size cap -----------------------------------------------------

def test_analyze_rejects_oversized_upload(monkeypatch):
    monkeypatch.setattr("attest.api.app._MAX_UPLOAD_BYTES", 1024)
    client = TestClient(create_app())
    client.post("/tenants/meridian/ingest/demo")
    r = client.post(
        "/tenants/meridian/analyze",
        data={"kind": "release", "entity": "MRDN", "period": "FY2026-Q1"},
        files={"file": ("big.txt", b"x" * 4096, "text/plain")},
    )
    assert r.status_code == 413


# -- API: tenant authorization ------------------------------------------------

def test_static_token_authorizer_scopes_and_wildcard():
    az: Authorizer = StaticTokenAuthorizer({"tokA": {"meridian"}, "admin": {"*"}})
    assert az.authorize("meridian", "tokA")
    assert not az.authorize("acme", "tokA")        # token not scoped to this tenant
    assert az.authorize("acme", "admin")           # wildcard grants any tenant
    assert not az.authorize("meridian", None)      # missing token fails closed
    assert not az.authorize("meridian", "bogus")   # unknown token fails closed


def test_static_token_authorizer_from_env_parsing():
    az = StaticTokenAuthorizer.from_env("tokA=meridian|acme, admin=*, junk_without_eq")
    assert az.authorize("acme", "tokA")
    assert az.authorize("whatever", "admin")
    assert not az.authorize("x", "junk_without_eq")  # malformed entry skipped


def test_open_access_is_the_default():
    client = TestClient(create_app())  # AllowAllAuthorizer by default
    assert isinstance(create_app().state.authorizer, AllowAllAuthorizer)
    assert client.get("/tenants/meridian/extraction/aliases").status_code == 200


def test_api_gates_tenant_routes_when_an_authorizer_is_set():
    client = TestClient(create_app(authorizer=StaticTokenAuthorizer({"tok": {"meridian"}})))
    auth = {"Authorization": "Bearer tok"}

    assert client.get("/tenants/meridian/extraction/aliases").status_code == 403  # no token
    assert client.get("/tenants/meridian/extraction/aliases", headers=auth).status_code == 200
    # A valid token for another tenant cannot reach this one.
    assert client.get("/tenants/acme/extraction/aliases", headers=auth).status_code == 403
    # Non-tenant routes stay open (health/integrity checks need no tenant scope).
    assert client.get("/health").status_code == 200
    assert client.get("/audit/verify").status_code == 200


# -- concurrency safety of the in-memory stores -------------------------------

def _fact(fid: str) -> Fact:
    return Fact(
        id=fid, tenant_id="t", entity="E", metric="m", period="FY2026-Q1",
        value=Decimal("1"), unit=Unit.CURRENCY, source_type=SourceType.FILING_LINE,
        as_of="2026-01-01",
    )


def _run_concurrently(target, n_threads: int = 8) -> None:
    threads = [threading.Thread(target=target, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_audit_chain_survives_concurrent_appends():
    log = InMemoryAuditLog()

    def worker(_):
        for _ in range(100):
            log.append(actor="t", type=EventType.VERDICT, tenant_id="x")

    _run_concurrently(worker)
    events = log.events()
    assert len(events) == 800
    # Sequence numbers are dense and unique — no two appends forked the tail.
    assert [e.seq for e in events] == list(range(800))
    assert log.verify() is True  # the hash chain is intact


def test_factstore_keeps_every_fact_under_concurrent_adds():
    store = InMemoryFactStore()

    def worker(base):
        for i in range(100):
            store.add(_fact(f"{base}:{i}"))

    _run_concurrently(worker)
    assert len(store.all()) == 800
