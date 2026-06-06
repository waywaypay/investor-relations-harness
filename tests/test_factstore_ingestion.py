from decimal import Decimal

from attest.domain.facts import Fact, SourceType
from attest.domain.money import Unit
from attest.factstore.repository import InMemoryFactStore
from attest.ingestion.edgar_xbrl import XBRLConnector, load_fixture


def _fact(fact_id: str, value: str, as_of: str, source: SourceType) -> Fact:
    return Fact(
        id=fact_id, tenant_id="t", entity="MRDN", metric="total_revenue", period="FY2026-Q1",
        value=Decimal(value), unit=Unit.CURRENCY, source_type=source, as_of=as_of,
    )


def test_versions_sorted_and_latest_resolves_restatement():
    store = InMemoryFactStore()
    store.add(_fact("f2", "474", "2025-09-15", SourceType.EDGAR_XBRL))
    store.add(_fact("f1", "467", "2025-04-28", SourceType.FILING_LINE))
    versions = store.versions("t", "MRDN", "total_revenue", "FY2026-Q1")
    assert [v.as_of for v in versions] == ["2025-04-28", "2025-09-15"]
    assert store.latest("t", "MRDN", "total_revenue", "FY2026-Q1").value == Decimal("474")


def test_duplicate_id_rejected():
    store = InMemoryFactStore()
    store.add(_fact("f1", "1", "2025-01-01", SourceType.EDGAR_XBRL))
    try:
        store.add(_fact("f1", "2", "2025-01-02", SourceType.EDGAR_XBRL))
        assert False, "expected duplicate rejection"
    except ValueError:
        pass


def test_same_fact_id_isolated_across_tenants():
    # Fact ids are derived from the filing (no tenant), so the same filing
    # ingested by two tenants yields identical ids. Dedupe is per-tenant, so this
    # must not collide — and each tenant must see only its own fact.
    store = InMemoryFactStore()
    a = Fact(
        id="acc:total_revenue:FY2026-Q1:2026-04-28", tenant_id="alpha", entity="MRDN",
        metric="total_revenue", period="FY2026-Q1", value=Decimal("100"),
        unit=Unit.CURRENCY, source_type=SourceType.EDGAR_XBRL, as_of="2026-04-28",
    )
    b = a.model_copy(update={"tenant_id": "beta"})
    store.add(a)
    store.add(b)  # must not raise despite the identical id
    assert len(store.all("alpha")) == 1
    assert len(store.all("beta")) == 1
    assert store.get(a.id, tenant_id="alpha").tenant_id == "alpha"
    assert store.get(a.id, tenant_id="beta").tenant_id == "beta"


def test_demo_ingests_into_multiple_tenants():
    # The API seeds the demo into one tenant on startup; ingesting the same demo
    # filing into another tenant must succeed (the cross-tenant collision bug).
    store = InMemoryFactStore()
    for tenant in ("meridian", "acme", "brandnew"):
        facts, _ = XBRLConnector().fetch(load_fixture("meridian_q1_fy2026"), tenant_id=tenant)
        store.add_many(facts)
    assert len(store.all("meridian")) == len(store.all("acme")) > 0


def test_xbrl_ingestion_maps_tags_and_precision():
    store = InMemoryFactStore()
    facts, report = XBRLConnector().fetch(load_fixture("meridian_q1_fy2026"), tenant_id="meridian")
    store.add_many(facts)

    rev = store.latest("meridian", "MRDN", "total_revenue", "FY2026-Q1")
    assert rev.value == Decimal("1241300000")
    assert rev.quantum == Decimal("100000")  # decimals=-5
    assert rev.source_type == SourceType.EDGAR_XBRL

    # restatement-aware: two versions of cloud growth, latest is the restated 29%
    versions = store.versions("meridian", "MRDN:Cloud", "cloud_growth_yoy", "FY2026-Q1")
    assert [v.value for v in versions] == [Decimal("31"), Decimal("29")]
    assert report.ingested == 15
    assert report.skipped == 0


def test_unmapped_tags_are_skipped_not_guessed():
    instance = {"accession": "x", "entity": "MRDN", "facts": [
        {"tag": "us-gaap:SomethingUnmapped", "period": "FY2026-Q1", "value": "1", "unit": "USD"},
    ]}
    facts, report = XBRLConnector().fetch(instance, tenant_id="t")
    assert facts == []
    assert report.skipped == 1
    assert report.skipped_tags == ("us-gaap:SomethingUnmapped",)


def test_guidance_is_not_filed():
    facts, _ = XBRLConnector().fetch(load_fixture("meridian_q1_fy2026"), tenant_id="meridian")
    guidance = next(f for f in facts if f.metric == "q2_revenue_guidance")
    assert guidance.source_type == SourceType.MANAGEMENT_INPUT
    assert guidance.is_filed is False
