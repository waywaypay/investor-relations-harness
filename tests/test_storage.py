"""Integration tests for the Postgres + Redis storage backends.

These run against *real* servers, not fakes — the whole point is to prove the
durable backends honour the same contracts as the in-memory references. They are
gated on env vars so a machine without databases skips them cleanly:

    ATTEST_TEST_DATABASE_URL   e.g. "host=/tmp port=5433 dbname=attest_test user=postgres"
    ATTEST_TEST_REDIS_URL      e.g. "redis://localhost:6399/0"

`docker compose up -d` (see docker-compose.yml) provides both locally.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

psycopg = pytest.importorskip("psycopg")

from attest.audit.events import EventType  # noqa: E402
from attest.audit.log import ChainIntegrityError, InMemoryAuditLog  # noqa: E402
from attest.demo import build_documents  # noqa: E402
from attest.domain.facts import Confidence, Fact, SourceType  # noqa: E402
from attest.domain.money import Unit  # noqa: E402
from attest.factstore.repository import InMemoryFactStore  # noqa: E402
from attest.ingestion.edgar_xbrl import load_fixture  # noqa: E402
from attest.service import AttestService  # noqa: E402
from attest.storage.postgres import PostgresAuditLog, PostgresFactStore, ensure_schema  # noqa: E402

DB_URL = os.environ.get("ATTEST_TEST_DATABASE_URL")
REDIS_URL = os.environ.get("ATTEST_TEST_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not DB_URL, reason="set ATTEST_TEST_DATABASE_URL to run storage integration tests"
)


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def conn():
    c = psycopg.connect(DB_URL, autocommit=True)
    ensure_schema(c)
    with c.cursor() as cur:
        cur.execute("TRUNCATE facts, audit_events RESTART IDENTITY")
    yield c
    c.close()


def _fact(fid: str, *, metric="cloud_revenue", period="FY2026-Q1", value="612000000",
          as_of="2026-04-28", entity="ATLS:Cloud", source=SourceType.EDGAR_XBRL) -> Fact:
    return Fact(
        id=fid, tenant_id="atlas", entity=entity, metric=metric, period=period,
        value=Decimal(value), unit=Unit.CURRENCY, quantum=Decimal("1000000"),
        source_type=source, source_ref="acc#tag", as_of=as_of, confidence=Confidence.HIGH,
    )


# --------------------------------------------------------------------------- #
# Postgres fact store                                                          #
# --------------------------------------------------------------------------- #


def test_fact_roundtrip_is_lossless(conn):
    store = PostgresFactStore(conn)
    original = _fact("f1", value="612300000")
    store.add(original)
    got = store.get("f1")
    assert got == original  # full Pydantic equality incl. Decimal value/quantum


def test_versions_ordered_and_latest_is_newest(conn):
    store = PostgresFactStore(conn)
    # Insert out of as_of order to prove ordering is by as_of, not insertion.
    store.add(_fact("f_new", value="474300000", as_of="2025-09-15"))
    store.add(_fact("f_old", value="467000000", as_of="2025-04-28"))
    versions = store.versions("atlas", "ATLS:Cloud", "cloud_revenue", "FY2026-Q1")
    assert [v.as_of for v in versions] == ["2025-04-28", "2025-09-15"]
    latest = store.latest("atlas", "ATLS:Cloud", "cloud_revenue", "FY2026-Q1")
    assert latest.value == Decimal("474300000")


def test_duplicate_id_raises(conn):
    store = PostgresFactStore(conn)
    store.add(_fact("dup"))
    with pytest.raises(ValueError, match="duplicate fact id"):
        store.add(_fact("dup", value="999"))


def test_same_fact_id_isolated_across_tenants(conn):
    # A fact id is derived from the filing (no tenant), so the same filing
    # ingested by two tenants yields identical ids. Dedupe is per-tenant
    # (UNIQUE (tenant_id, id)), so this must not collide.
    store = PostgresFactStore(conn)
    alpha = _fact("shared").model_copy(update={"tenant_id": "alpha"})
    beta = _fact("shared", value="999").model_copy(update={"tenant_id": "beta"})
    store.add(alpha)
    store.add(beta)  # identical id, different tenant — must succeed
    assert store.get("shared", tenant_id="alpha").value == alpha.value
    assert store.get("shared", tenant_id="beta").value == beta.value
    assert len(store.all("alpha")) == 1 and len(store.all("beta")) == 1


def test_matches_inmemory_contract(conn):
    """The Postgres store and the in-memory store answer identically."""
    facts = [
        _fact("a", value="467000000", as_of="2025-04-28"),
        _fact("b", value="474300000", as_of="2025-09-15"),
        _fact("c", metric="total_revenue", entity="ATLS", value="1241300000"),
    ]
    pg = PostgresFactStore(conn)
    mem = InMemoryFactStore()
    pg.add_many(facts)
    mem.add_many(facts)

    scope = ("atlas", "ATLS:Cloud", "cloud_revenue", "FY2026-Q1")
    assert pg.versions(*scope) == mem.versions(*scope)
    assert pg.latest(*scope) == mem.latest(*scope)
    assert sorted(f.id for f in pg.all("atlas")) == sorted(f.id for f in mem.all("atlas"))


# --------------------------------------------------------------------------- #
# Postgres audit log                                                           #
# --------------------------------------------------------------------------- #


def test_audit_chain_verifies_and_is_contiguous(conn):
    log = PostgresAuditLog(conn)
    log.append(actor="sys", type=EventType.INGEST, tenant_id="atlas", payload={"n": 1})
    log.append(actor="cfo", type=EventType.SIGN_OFF, tenant_id="atlas", payload={"doc": "r"})
    events = log.events()
    assert [e.seq for e in events] == [0, 1]
    assert events[1].prev_hash == events[0].hash
    assert log.verify() is True


def test_audit_hashes_match_inmemory_backend(conn):
    """Swapping to Postgres must not change the chain — same inputs, same hashes."""
    pg = PostgresAuditLog(conn)
    mem = InMemoryAuditLog()
    events = [
        ("sys", EventType.INGEST, "atlas", {"ingested": 15}, "2026-05-30T00:00:00+00:00"),
        ("eng", EventType.VERDICT, "atlas", {"verdict": "traced"}, "2026-05-30T00:01:00+00:00"),
    ]
    for actor, type_, tid, payload, ts in events:
        p = pg.append(actor=actor, type=type_, tenant_id=tid, payload=payload, timestamp=ts)
        m = mem.append(actor=actor, type=type_, tenant_id=tid, payload=payload, timestamp=ts)
        assert p.hash == m.hash
        assert p.prev_hash == m.prev_hash


def test_audit_tamper_is_detected(conn):
    log = PostgresAuditLog(conn)
    log.append(actor="sys", type=EventType.INGEST, tenant_id="atlas", payload={"n": 1})
    log.append(actor="x", type=EventType.OVERRIDE, tenant_id="atlas", payload={"n": 2})
    # Retroactively edit a persisted payload — the chain must catch it.
    with conn.cursor() as cur:
        cur.execute("""UPDATE audit_events SET payload = '{"n": 999}' WHERE seq = 0""")
    with pytest.raises(ChainIntegrityError):
        log.verify()


def test_audit_tenant_filter(conn):
    log = PostgresAuditLog(conn)
    log.append(actor="sys", type=EventType.INGEST, tenant_id="atlas", payload={})
    log.append(actor="sys", type=EventType.INGEST, tenant_id="acme", payload={})
    assert [e.tenant_id for e in log.events("atlas")] == ["atlas"]
    assert len(log.events()) == 2


# --------------------------------------------------------------------------- #
# Redis caching decorator                                                      #
# --------------------------------------------------------------------------- #

redis_required = pytest.mark.skipif(
    not REDIS_URL, reason="set ATTEST_TEST_REDIS_URL to run cache tests"
)


@pytest.fixture
def redis_client():
    redis = pytest.importorskip("redis")
    client = redis.from_url(REDIS_URL)
    client.flushdb()
    yield client
    client.flushdb()


@redis_required
def test_cache_serves_reads_and_invalidates_on_write(conn, redis_client):
    from attest.storage.redis_cache import CachingFactStore

    inner = PostgresFactStore(conn)
    cached = CachingFactStore(inner, redis_client)
    scope = ("atlas", "ATLS:Cloud", "cloud_revenue", "FY2026-Q1")

    cached.add(_fact("a", value="467000000", as_of="2025-04-28"))
    assert len(cached.versions(*scope)) == 1  # populates the cache

    # Write straight to the inner store, bypassing the cache: the cache should
    # still serve the stale single-version list (proving it really cached).
    inner.add(_fact("b", value="474300000", as_of="2025-09-15"))
    assert len(cached.versions(*scope)) == 1

    # A write *through* the caching store invalidates the scope, so the next read
    # reflects every version and latest() resolves to the newest.
    cached.add(_fact("c", value="480000000", as_of="2025-10-01"))
    versions = cached.versions(*scope)
    assert {v.id for v in versions} == {"a", "b", "c"}
    assert cached.latest(*scope).id == "c"


# --------------------------------------------------------------------------- #
# End-to-end: a Postgres-backed service matches the in-memory one              #
# --------------------------------------------------------------------------- #


def test_postgres_backed_service_matches_inmemory(conn):
    pg_service = AttestService(
        store=PostgresFactStore(conn), audit_log=PostgresAuditLog(conn)
    )
    mem_service = AttestService()
    for svc in (pg_service, mem_service):
        svc.ingest_xbrl(load_fixture("atlas_q1_fy2026"), tenant_id="atlas")

    docs = build_documents()
    pg_results, pg_consistency = pg_service.verify_close_pack(docs)
    mem_results, mem_consistency = mem_service.verify_close_pack(docs)

    def signature(results):
        return [
            sorted((v.metric, v.displayed_text, v.verdict.value) for v in r.verdicts)
            for r in results
        ]

    assert signature(pg_results) == signature(mem_results)
    assert pg_consistency == mem_consistency
    # The durable audit chain is intact and recorded the same number of events.
    assert pg_service.audit_verify() is True
    assert len(pg_service.audit_log.events()) == len(mem_service.audit_log.events())
