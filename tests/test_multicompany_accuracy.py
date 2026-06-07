"""Accuracy regression suite — twelve issuers, one obsession: correctness.

Each test models a different company's real disclosure shape and pins a specific
accuracy guarantee. The split is deliberate:

* **False-positive guards** assert the engine does *not* block a disclosure that is
  actually correct (a number rounded two legitimate ways, prose about another
  metric, a multi-segment filing). A spurious BLOCK erodes trust as surely as a
  miss, and an IR team that gets cried wolf at will start ignoring the tool.
* **True-positive guards** assert the engine *still* catches the genuine errors
  (a divergent figure, a wrong-direction verb, a reconciliation that doesn't add
  up, a stale restated growth rate). Fixing false positives must never blunt these.

These encode the bugs surfaced while stress-testing the spine from many issuers'
perspectives, so a regression on any of them fails CI.
"""

from __future__ import annotations

from decimal import Decimal

from attest.domain.document import Document, DocumentKind
from attest.domain.facts import Fact, SourceType
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry, MetricSpec
from attest.domain.money import Unit
from attest.domain.verdicts import FigureClaim
from attest.factstore.repository import InMemoryFactStore
from attest.ingestion.edgar_xbrl import XBRLConnector
from attest.ingestion.guidance import GuidanceConnector
from attest.verification.engine import VerificationEngine
from attest.verification.rules import (
    check_cross_document_consistency,
    check_derived_consistency,
    check_directional_language,
    check_range_midpoint,
    check_reg_g,
)


def _claim(cid, doc, metric, period, text, entity, span=None):
    return FigureClaim(
        claim_id=cid, document_id=doc, entity=entity, metric=metric,
        period=period, displayed_text=text, span=span,
    )


def _has(rule_id, findings):
    return any(f.rule == rule_id for f in findings)


# === 1. Aperture Materials — same number, two legitimate roundings ===========
# The release rounds to "$1.24 billion"; the 10-Q states "$1,241.3 million".
# These are the SAME figure. Cross-document consistency must not block it (it
# previously did, while intra-document correctly passed it).
def test_aperture_cross_document_rounding_is_not_a_conflict():
    rel = Document(
        id="release", tenant_id="aperture", title="r", kind=DocumentKind.RELEASE, text="x",
        claims=(_claim("a", "release", "total_revenue", "FY2026-Q1", "$1.24 billion", "APRT"),),
    )
    tenq = Document(
        id="10q", tenant_id="aperture", title="q", kind=DocumentKind.OTHER, text="x",
        claims=(_claim("b", "10q", "total_revenue", "FY2026-Q1", "$1,241.3 million", "APRT"),),
    )
    findings = check_cross_document_consistency([rel, tenq])
    assert not _has("consistency.cross_document_mismatch", findings)


# === 2. Borealis Energy — a multi-segment filing must ingest ================
# Three reporting segments report the same metric for the same period in one
# filing with one as_of. The fact id must stay unique per entity, or ingestion
# crashes with "duplicate fact id".
def test_borealis_multi_segment_ingestion_does_not_collide():
    instance = {
        "accession": "0000000-26-000001",
        "entity": "BRLS",
        "facts": [
            {"tag": "us-gaap:Revenues", "entity": "BRLS:NorthAmerica", "period": "FY2026-Q1",
             "value": "100000000", "unit": "USD", "decimals": -5, "as_of": "2026-04-28"},
            {"tag": "us-gaap:Revenues", "entity": "BRLS:EMEA", "period": "FY2026-Q1",
             "value": "200000000", "unit": "USD", "decimals": -5, "as_of": "2026-04-28"},
            {"tag": "us-gaap:Revenues", "entity": "BRLS:APAC", "period": "FY2026-Q1",
             "value": "150000000", "unit": "USD", "decimals": -5, "as_of": "2026-04-28"},
        ],
    }
    store = InMemoryFactStore()
    facts, report = XBRLConnector().fetch(instance, "borealis")
    store.add_many(facts)  # must not raise
    assert report.ingested == 3
    assert {f.entity for f in facts} == {"BRLS:NorthAmerica", "BRLS:EMEA", "BRLS:APAC"}
    assert len({f.id for f in facts}) == 3


# === 3. Cirrus Cloud — compact guidance range "$1.31–1.34B" =================
# Guidance written compactly with a glued scale letter must land at its true
# midpoint ($1.325B), not parse the low end as $1.31 (a ~1000x error).
def test_cirrus_compact_range_guidance_midpoint():
    text = "For the second quarter, the company expects revenue of $1.31–1.34B."
    facts, _ = GuidanceConnector().fetch(
        text=text, tenant_id="cirrus", entity="CRRS",
        accession="acc", base_period="FY2026-Q1",
    )
    revenue = [f for f in facts if f.metric == "revenue_guidance"]
    assert len(revenue) == 1
    assert revenue[0].value == Decimal("1325000000.00")


# === 4. Delphi Retail — prose about revenue must not indict the margin =======
# The release says "revenue grew"; operating margin (never named in the prose)
# happened to fall. The directional rule must NOT attribute the foreign verb
# "grew" to operating margin and block the release.
def test_delphi_directional_does_not_borrow_foreign_verb():
    store = InMemoryFactStore()
    store.add(Fact(id="f1", tenant_id="delphi", entity="DLPH", metric="operating_margin",
                   period="FY2026-Q1", value=Decimal("19.0"), unit=Unit.PERCENT,
                   quantum=Decimal("0.1"), source_type=SourceType.FILING_LINE,
                   source_ref="r", as_of="2026-04-28"))
    store.add(Fact(id="f2", tenant_id="delphi", entity="DLPH", metric="operating_margin",
                   period="FY2025-Q1", value=Decimal("21.0"), unit=Unit.PERCENT,
                   quantum=Decimal("0.1"), source_type=SourceType.FILING_LINE,
                   source_ref="r", as_of="2025-04-28"))
    doc = Document(
        id="d", tenant_id="delphi", title="d", kind=DocumentKind.RELEASE,
        text="Total revenue grew as same-store demand increased across the quarter.",
        claims=(_claim("c", "d", "operating_margin", "FY2026-Q1", "19.0%", "DLPH"),),
    )
    assert not _has("directional.sign_mismatch",
                    check_directional_language(doc, DEFAULT_REGISTRY, store))


# === 5. Equinox Pharma — a genuine wrong-direction claim still blocks ========
# When the metric IS named with a contradicting verb ("operating margin
# expanded" while it fell), the rule must still fire. Fixing #4 must not blunt it.
def test_equinox_directional_true_positive_still_blocks():
    store = InMemoryFactStore()
    store.add(Fact(id="f1", tenant_id="equinox", entity="EQNX", metric="operating_margin",
                   period="FY2026-Q1", value=Decimal("19.0"), unit=Unit.PERCENT,
                   quantum=Decimal("0.1"), source_type=SourceType.FILING_LINE,
                   source_ref="r", as_of="2026-04-28"))
    store.add(Fact(id="f2", tenant_id="equinox", entity="EQNX", metric="operating_margin",
                   period="FY2025-Q1", value=Decimal("21.0"), unit=Unit.PERCENT,
                   quantum=Decimal("0.1"), source_type=SourceType.FILING_LINE,
                   source_ref="r", as_of="2025-04-28"))
    doc = Document(
        id="d", tenant_id="equinox", title="d", kind=DocumentKind.RELEASE,
        text="Operating margin expanded year over year on disciplined spending.",
        claims=(_claim("c", "d", "operating_margin", "FY2026-Q1", "19.0%", "EQNX"),),
    )
    assert _has("directional.sign_mismatch",
                check_directional_language(doc, DEFAULT_REGISTRY, store))


# === 6. Fjord Logistics — a real cross-document divergence still blocks ======
# $1.24B in the release vs $1.25B on the call are genuinely different numbers;
# the rounding-aware fix must still catch this.
def test_fjord_cross_document_true_divergence_still_blocks():
    rel = Document(
        id="release", tenant_id="fjord", title="r", kind=DocumentKind.RELEASE, text="x",
        claims=(_claim("a", "release", "total_revenue", "FY2026-Q1", "$1.24 billion", "FJRD"),),
    )
    scr = Document(
        id="script", tenant_id="fjord", title="s", kind=DocumentKind.SCRIPT, text="x",
        claims=(_claim("b", "script", "total_revenue", "FY2026-Q1", "$1.25 billion", "FJRD"),),
    )
    assert _has("consistency.cross_document_mismatch",
                check_cross_document_consistency([rel, scr]))


# === 7. Granite Financial — a non-GAAP bridge that doesn't add up ===========
# GAAP $0.87 + $0.18 + $0.07 = $1.12, but the release books non-GAAP EPS at
# $1.20. Reg G reconciliation arithmetic must block it.
def test_granite_reg_g_bridge_must_add_up():
    store = InMemoryFactStore()

    def f(metric, val):
        return Fact(id=f"{metric}", tenant_id="granite", entity="GRNT", metric=metric,
                    period="FY2026-Q1", value=Decimal(val), unit=Unit.CURRENCY,
                    quantum=Decimal("0.01"), source_type=SourceType.FILING_LINE,
                    source_ref="r", as_of="2026-04-28")

    store.add(f("non_gaap_diluted_eps", "1.20"))
    store.add(f("gaap_diluted_eps", "0.87"))
    store.add(f("sbc_eps_adjustment", "0.18"))
    store.add(f("intangibles_amort_eps_adjustment", "0.07"))
    doc = Document(
        id="d", tenant_id="granite", title="d", kind=DocumentKind.RELEASE, text="x",
        claims=(
            _claim("c1", "d", "gaap_diluted_eps", "FY2026-Q1", "$0.87", "GRNT", span=(0, 5)),
            _claim("c2", "d", "non_gaap_diluted_eps", "FY2026-Q1", "$1.20", "GRNT", span=(10, 15)),
        ),
    )
    assert _has("reg_g.reconciliation_arithmetic", check_reg_g(doc, DEFAULT_REGISTRY, store))


# === 8. Helios Semiconductors — a stale growth rate over a restated base =====
# Prior-year cloud revenue was restated $467.0M -> $474.3M, so YoY growth is no
# longer 31%. The derived recomputation must catch a draft that still says 31%.
def test_helios_restated_base_flags_stale_growth():
    store = InMemoryFactStore()

    def cloud(period, val, as_of, st=SourceType.EDGAR_XBRL):
        return Fact(id=f"cloud:{period}:{as_of}", tenant_id="helios", entity="HLOS:Cloud",
                    metric="cloud_revenue", period=period, value=Decimal(val),
                    unit=Unit.CURRENCY, quantum=Decimal("100000"), source_type=st,
                    source_ref="r", as_of=as_of)

    store.add(cloud("FY2026-Q1", "611800000", "2026-04-28"))
    store.add(cloud("FY2025-Q1", "467000000", "2025-04-28", SourceType.FILING_LINE))
    store.add(cloud("FY2025-Q1", "474300000", "2025-09-15"))  # restated base, supersedes
    doc = Document(
        id="d", tenant_id="helios", title="d", kind=DocumentKind.RELEASE,
        text="Cloud grew 31% year over year.",
        claims=(_claim("c", "d", "cloud_growth_yoy", "FY2026-Q1", "31%", "HLOS:Cloud"),),
    )
    findings = check_derived_consistency(doc, DEFAULT_REGISTRY, store)
    assert _has("derived.recomputation_mismatch", findings)
    # And the corrected 29% must clear.
    doc_ok = Document(
        id="d", tenant_id="helios", title="d", kind=DocumentKind.RELEASE,
        text="Cloud grew 29% year over year.",
        claims=(_claim("c", "d", "cloud_growth_yoy", "FY2026-Q1", "29%", "HLOS:Cloud"),),
    )
    assert not _has("derived.recomputation_mismatch",
                    check_derived_consistency(doc_ok, DEFAULT_REGISTRY, store))


# === 9. Ionix Biotech — segment-level guidance for two entities =============
# A press release that guides both the parent and a segment in the same exhibit
# must land two distinct facts, not collide on a shared id.
def test_ionix_segment_guidance_does_not_collide():
    store = InMemoryFactStore()
    parent, _ = GuidanceConnector().fetch(
        text="For the second quarter, the company expects revenue of $1.31 to $1.34 billion.",
        tenant_id="ionix", entity="INXB", accession="acc", base_period="FY2026-Q1",
    )
    segment, _ = GuidanceConnector().fetch(
        text="For the second quarter, the company expects revenue of $0.50 to $0.60 billion.",
        tenant_id="ionix", entity="INXB:Therapeutics", accession="acc", base_period="FY2026-Q1",
    )
    store.add_many(parent)
    store.add_many(segment)  # must not raise
    assert store.latest("ionix", "INXB", "revenue_guidance", "FY2026-Q2").value == Decimal("1325000000.00")
    assert store.latest("ionix", "INXB:Therapeutics", "revenue_guidance", "FY2026-Q2").value == Decimal("550000000.00")


# === 10. Juniper Foods — an "and"-joined guidance range is still verified ====
# "$5.10 and $5.30" is a range; a stated midpoint that disagrees with it must be
# caught, not silently skipped because the separator wasn't "to".
def test_juniper_and_separated_range_midpoint_checked():
    reg = MetricRegistry([
        MetricSpec(id="eps_guidance", label="EPS guidance", unit=Unit.CURRENCY),
    ])
    doc = Document(
        id="d", tenant_id="juniper", title="d", kind=DocumentKind.RELEASE,
        text="We guide full-year EPS of $5.10 and $5.30, a midpoint of $5.40.",
        claims=(_claim("c", "d", "eps_guidance", "FY2026-FY", "$5.10 and $5.30", "JNPR"),),
    )
    # Arithmetic midpoint is $5.20; the stated $5.40 is wrong and must be flagged.
    assert _has("ranges.midpoint_mismatch",
                check_range_midpoint(doc, reg, {"eps_guidance": "$5.40"}))
    # A correct stated midpoint clears.
    assert not _has("ranges.midpoint_mismatch",
                    check_range_midpoint(doc, reg, {"eps_guidance": "$5.20"}))


# === 11. Kestrel Software — a fully correct close pack is publishable ========
# The end-to-end happy path: every figure traces and no rule blocks.
def test_kestrel_clean_pack_is_publishable():
    store = InMemoryFactStore()

    def f(metric, val, q, entity="KSTL", st=SourceType.EDGAR_XBRL):
        return Fact(id=f"{entity}:{metric}", tenant_id="kestrel", entity=entity, metric=metric,
                    period="FY2026-Q1", value=Decimal(val), unit=Unit.CURRENCY, quantum=Decimal(q),
                    source_type=st, source_ref="r", as_of="2026-04-28")

    store.add(f("total_revenue", "1241300000", "100000"))
    store.add(f("operating_cash_flow", "338200000", "100000"))
    engine = VerificationEngine(store, DEFAULT_REGISTRY)
    doc = Document(
        id="d", tenant_id="kestrel", title="d", kind=DocumentKind.RELEASE,
        text="Total revenue was $1.24 billion. Operating cash flow was $338 million.",
        claims=(
            _claim("c1", "d", "total_revenue", "FY2026-Q1", "$1.24 billion", "KSTL", span=(0, 13)),
            _claim("c2", "d", "operating_cash_flow", "FY2026-Q1", "$338 million", "KSTL", span=(20, 33)),
        ),
    )
    result = engine.verify_document(doc)
    assert result.counts["traced"] == 2
    assert result.publishable


# === 12. Lumen Devices — a trailing-twelve-month sum must reconcile ==========
# A TTM figure that doesn't equal the sum of its four quarters is an error a
# single-period tie-out cannot see.
def test_lumen_ttm_sum_recomputation():
    reg = MetricRegistry([
        MetricSpec(id="quarterly_revenue", label="Quarterly revenue", unit=Unit.CURRENCY),
        MetricSpec(id="ttm_revenue", label="TTM revenue", unit=Unit.CURRENCY,
                   derived_kind="ttm_sum", derived_base="quarterly_revenue"),
    ])
    store = InMemoryFactStore()
    quarters = {"FY2026-Q1": "300", "FY2025-Q4": "280", "FY2025-Q3": "260", "FY2025-Q2": "240"}
    for period, val in quarters.items():
        store.add(Fact(id=f"q:{period}", tenant_id="lumen", entity="LMND",
                       metric="quarterly_revenue", period=period, value=Decimal(val),
                       unit=Unit.CURRENCY, quantum=Decimal("1"),
                       source_type=SourceType.FILING_LINE, source_ref="r", as_of="2026-04-28"))
    # True TTM = 300+280+260+240 = 1080. A claim of 1100 is wrong.
    bad = Document(id="d", tenant_id="lumen", title="d", kind=DocumentKind.RELEASE, text="x",
                   claims=(_claim("c", "d", "ttm_revenue", "FY2026-Q1", "$1100", "LMND"),))
    assert _has("derived.recomputation_mismatch", check_derived_consistency(bad, reg, store))
    good = Document(id="d", tenant_id="lumen", title="d", kind=DocumentKind.RELEASE, text="x",
                    claims=(_claim("c", "d", "ttm_revenue", "FY2026-Q1", "$1080", "LMND"),))
    assert not _has("derived.recomputation_mismatch", check_derived_consistency(good, reg, store))
