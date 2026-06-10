from decimal import Decimal

from attest.demo import build_documents, seeded_service
from attest.domain.document import Document, DocumentKind
from attest.domain.facts import Fact, SourceType
from attest.domain.metrics import DEFAULT_REGISTRY
from attest.domain.money import Unit
from attest.domain.verdicts import FigureClaim
from attest.factstore.repository import InMemoryFactStore
from attest.verification.rules import (
    check_cross_document_consistency,
    check_derived_consistency,
    check_forward_looking,
    check_reg_g,
)


def _doc(doc_id, text, claims, tenant="meridian"):
    return Document(id=doc_id, tenant_id=tenant, title=doc_id, kind=DocumentKind.OTHER,
                    text=text, claims=tuple(claims))


def _claim(metric, text, entity="MRDN", period="FY2026-Q1", doc="d", span=None):
    return FigureClaim(claim_id=metric, document_id=doc, entity=entity, metric=metric,
                       period=period, displayed_text=text, span=span)


def test_reg_g_passes_with_gaap_and_recon():
    service = seeded_service()
    doc = _doc("ok", "non-GAAP $1.12 and GAAP $0.87", [
        _claim("non_gaap_diluted_eps", "$1.12"),
        _claim("gaap_diluted_eps", "$0.87"),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, service.store)
    assert findings == []


def test_reg_g_flags_missing_equal_prominence():
    service = seeded_service()
    doc = _doc("bad", "non-GAAP $1.12", [_claim("non_gaap_diluted_eps", "$1.12")])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, service.store)
    assert any(f.rule == "reg_g.equal_prominence" for f in findings)


def test_reg_g_flags_missing_reconciliation_source():
    # empty store: the GAAP counterpart fact does not exist
    store = InMemoryFactStore()
    doc = _doc("bad", "non-GAAP $1.12 and GAAP $0.87", [
        _claim("non_gaap_diluted_eps", "$1.12"),
        _claim("gaap_diluted_eps", "$0.87"),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, store)
    assert any(f.rule == "reg_g.reconciliation_required" for f in findings)


def test_forward_looking_requires_safe_harbor():
    service = seeded_service()
    doc = _doc("fls", "For the second quarter we expect revenue to grow.",
               [_claim("revenue_guidance", "$1.31 to $1.34 billion", period="FY2026-Q2")])
    findings = check_forward_looking(doc, service.store)
    assert any(f.rule == "forward_looking.safe_harbor_required" for f in findings)


def test_forward_looking_satisfied_with_safe_harbor():
    service = seeded_service()
    doc = _doc("fls", "We expect growth. Refer to our safe-harbor statement.",
               [_claim("revenue_guidance", "$1.31 to $1.34 billion", period="FY2026-Q2")])
    findings = check_forward_looking(doc, service.store)
    assert findings == []


def test_consistency_flags_divergent_values():
    d1 = _doc("release", "rev $1.24 billion", [_claim("total_revenue", "$1.24 billion", doc="release")])
    d2 = _doc("script", "rev $1.25 billion", [_claim("total_revenue", "$1.25 billion", doc="script")])
    findings = check_cross_document_consistency([d1, d2])
    assert any(f.rule == "consistency.cross_document_mismatch" for f in findings)


def test_consistency_clean_on_matching_values():
    docs = build_documents()
    assert check_cross_document_consistency(docs) == []


def test_reg_g_flags_non_gaap_presented_before_gaap():
    # Reg G requires the GAAP measure with equal-or-greater prominence. Using claim
    # spans as a position proxy: the non-GAAP figure must not appear before its GAAP
    # counterpart. Here non-GAAP ($1.12) sits at offset 0, GAAP ($0.87) far later.
    service = seeded_service()
    doc = _doc("order", "Non-GAAP diluted EPS of $1.12 ... GAAP diluted EPS of $0.87", [
        _claim("non_gaap_diluted_eps", "$1.12", span=(24, 29)),
        _claim("gaap_diluted_eps", "$0.87", span=(55, 60)),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, service.store)
    assert any(f.rule == "reg_g.equal_prominence_ordering" for f in findings)


def test_reg_g_ok_when_gaap_precedes_non_gaap():
    service = seeded_service()
    doc = _doc("order", "GAAP diluted EPS of $0.87 ... non-GAAP diluted EPS of $1.12", [
        _claim("gaap_diluted_eps", "$0.87", span=(20, 25)),
        _claim("non_gaap_diluted_eps", "$1.12", span=(53, 58)),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, service.store)
    assert not any(f.rule == "reg_g.equal_prominence_ordering" for f in findings)


def test_reg_g_ordering_skipped_without_spans():
    # When the edge didn't supply spans we cannot judge prominence — never guess.
    service = seeded_service()
    doc = _doc("nospan", "non-GAAP $1.12 and GAAP $0.87", [
        _claim("non_gaap_diluted_eps", "$1.12"),
        _claim("gaap_diluted_eps", "$0.87"),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, service.store)
    assert not any(f.rule == "reg_g.equal_prominence_ordering" for f in findings)


def test_derived_recompute_flags_stale_growth():
    # cloud_growth_yoy must equal YoY growth recomputed from cloud_revenue facts.
    # With the restated prior-year base ($474.3M), the true figure is 29%, so a
    # claim of 31% is a recomputation mismatch — caught at the math level.
    service = seeded_service()
    doc = _doc("g", "Cloud grew 31% year over year.",
               [_claim("cloud_growth_yoy", "31%", entity="MRDN:Cloud")])
    findings = check_derived_consistency(doc, DEFAULT_REGISTRY, service.store)
    assert any(f.rule == "derived.recomputation_mismatch" for f in findings)


def test_derived_recompute_ok_for_corrected_value():
    service = seeded_service()
    doc = _doc("g", "Cloud grew 29% year over year.",
               [_claim("cloud_growth_yoy", "29%", entity="MRDN:Cloud")])
    findings = check_derived_consistency(doc, DEFAULT_REGISTRY, service.store)
    assert not any(f.rule == "derived.recomputation_mismatch" for f in findings)


def test_derived_recompute_skips_when_base_facts_absent():
    # No underlying facts -> nothing to recompute against -> never guess.
    store = InMemoryFactStore()
    doc = _doc("g", "Cloud grew 31%.", [_claim("cloud_growth_yoy", "31%", entity="MRDN:Cloud")])
    assert check_derived_consistency(doc, DEFAULT_REGISTRY, store) == []


def test_derived_bps_delta_flags_overstated_margin():
    # operating_margin_change_bps must equal the recomputed period-over-period
    # change. Margin went 21.0% -> 22.4% (i.e. +140 bps), so a claim of 200 bps
    # is a recomputation mismatch.
    service = seeded_service()
    doc = _doc("m", "Operating margin expanded 200 bps.",
               [_claim("operating_margin_change_bps", "200 bps")])
    findings = check_derived_consistency(doc, DEFAULT_REGISTRY, service.store)
    assert any(f.rule == "derived.recomputation_mismatch" for f in findings)


def test_derived_bps_delta_ok_for_correct_margin():
    service = seeded_service()
    doc = _doc("m", "Operating margin expanded 140 bps.",
               [_claim("operating_margin_change_bps", "140 bps")])
    findings = check_derived_consistency(doc, DEFAULT_REGISTRY, service.store)
    assert not any(f.rule == "derived.recomputation_mismatch" for f in findings)


def test_reg_g_reconciliation_arithmetic_flags_broken_bridge():
    # GAAP 0.87 + SBC 0.18 + amortization 0.07 = 1.12. If the non-GAAP figure is
    # booked as 1.20, the reconciliation bridge does not add up.
    store = InMemoryFactStore()
    common = dict(tenant_id="meridian", entity="MRDN", period="FY2026-Q1",
                  unit=Unit.CURRENCY, source_type=SourceType.FILING_LINE, as_of="2026-04-28")
    store.add_many([
        Fact(id="g", metric="gaap_diluted_eps", value=Decimal("0.87"), **common),
        Fact(id="s", metric="sbc_eps_adjustment", value=Decimal("0.18"), **common),
        Fact(id="a", metric="intangibles_amort_eps_adjustment", value=Decimal("0.07"), **common),
        Fact(id="n", metric="non_gaap_diluted_eps", value=Decimal("1.20"), **common),
    ])
    doc = _doc("bridge", "non-GAAP $1.20 and GAAP $0.87", [
        _claim("non_gaap_diluted_eps", "$1.20"),
        _claim("gaap_diluted_eps", "$0.87"),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, store)
    assert any(f.rule == "reg_g.reconciliation_arithmetic" for f in findings)


def test_reg_g_reconciliation_arithmetic_ok_when_bridge_sums():
    service = seeded_service()  # 0.87 + 0.18 + 0.07 = 1.12, as booked
    doc = _doc("bridge", "non-GAAP $1.12 and GAAP $0.87", [
        _claim("non_gaap_diluted_eps", "$1.12"),
        _claim("gaap_diluted_eps", "$0.87"),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, service.store)
    assert not any(f.rule == "reg_g.reconciliation_arithmetic" for f in findings)


def test_demo_script_flags_reg_g_and_release_flags_fls():
    service = seeded_service()
    docs = build_documents()
    results, _ = service.engine.verify_close_pack(docs)
    script = next(r for r in results if r.document_id == "script")
    release = next(r for r in results if r.document_id == "release")
    assert any(f.rule == "reg_g.equal_prominence" for f in script.findings)
    assert any(f.rule == "forward_looking.safe_harbor_required" for f in release.findings)
