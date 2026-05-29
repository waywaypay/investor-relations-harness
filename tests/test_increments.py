"""Increments extending the deterministic recomputation engine (derived.py).

Each test builds its own MetricRegistry and InMemoryFactStore so the red phase is
genuine: the test exists and fails before the capability is implemented.
"""

from decimal import Decimal

from attest.domain.document import Document, DocumentKind
from attest.domain.facts import Fact, SourceType
from attest.domain.metrics import MetricRegistry, MetricSpec
from attest.domain.money import Unit
from attest.domain.verdicts import FigureClaim
from attest.factstore.repository import InMemoryFactStore
from attest.verification.rules import (
    check_derived_consistency,
    check_directional_language,
)

T = "acme"


def _fact(metric, value, period, entity="ACME", unit=Unit.CURRENCY):
    return Fact(id=f"{metric}:{period}", tenant_id=T, entity=entity, metric=metric,
                period=period, value=Decimal(str(value)), unit=unit,
                source_type=SourceType.FILING_LINE, as_of="2026-04-28")


def _doc(claims):
    return Document(id="d", tenant_id=T, title="d", kind=DocumentKind.OTHER, text="",
                    claims=tuple(claims))


def _claim(metric, text, entity="ACME"):
    return FigureClaim(claim_id=metric, document_id="d", entity=entity, metric=metric,
                       period="FY2026-Q1", displayed_text=text)


def _rule(rule_id, findings):
    return any(f.rule == rule_id for f in findings)


# -- Increment 1: QoQ sequential growth ------------------------------------

def test_qoq_growth_flags_stale_sequential():
    reg = MetricRegistry([
        MetricSpec(id="total_revenue", label="Total revenue", unit=Unit.CURRENCY),
        MetricSpec(id="revenue_qoq_growth", label="Revenue growth, QoQ", unit=Unit.PERCENT,
                   derived_kind="qoq_growth", derived_base="total_revenue"),
    ])
    store = InMemoryFactStore()
    store.add(_fact("total_revenue", "1241300000", "FY2026-Q1"))
    store.add(_fact("total_revenue", "1190000000", "FY2025-Q4"))  # prior quarter
    # true QoQ = 1241.3/1190 - 1 = 4.3%, so a claim of 10% is wrong
    doc = _doc([_claim("revenue_qoq_growth", "10%")])
    assert _rule("derived.recomputation_mismatch", check_derived_consistency(doc, reg, store))


def test_qoq_growth_ok_when_correct():
    reg = MetricRegistry([
        MetricSpec(id="total_revenue", label="Total revenue", unit=Unit.CURRENCY),
        MetricSpec(id="revenue_qoq_growth", label="Revenue growth, QoQ", unit=Unit.PERCENT,
                   derived_kind="qoq_growth", derived_base="total_revenue"),
    ])
    store = InMemoryFactStore()
    store.add(_fact("total_revenue", "1241300000", "FY2026-Q1"))
    store.add(_fact("total_revenue", "1190000000", "FY2025-Q4"))
    doc = _doc([_claim("revenue_qoq_growth", "4%")])
    assert not _rule("derived.recomputation_mismatch", check_derived_consistency(doc, reg, store))


# -- Increment 2: EPS ratio identity (net income / diluted shares) ---------

def _eps_reg():
    return MetricRegistry([
        MetricSpec(id="net_income", label="Net income", unit=Unit.CURRENCY),
        MetricSpec(id="diluted_shares", label="Diluted shares", unit=Unit.SHARES),
        MetricSpec(id="gaap_diluted_eps", label="GAAP diluted EPS", unit=Unit.CURRENCY,
                   derived_kind="ratio", derived_numerator="net_income",
                   derived_denominator="diluted_shares"),
    ])


def test_eps_ratio_flags_inconsistent():
    store = InMemoryFactStore()
    store.add(_fact("net_income", "202000000", "FY2026-Q1"))
    store.add(_fact("diluted_shares", "232100000", "FY2026-Q1", unit=Unit.SHARES))
    # 202.0M / 232.1M = $0.87, so a claimed EPS of $1.05 is inconsistent
    doc = _doc([_claim("gaap_diluted_eps", "$1.05")])
    assert _rule("derived.ratio_mismatch", check_derived_consistency(doc, _eps_reg(), store))


def test_eps_ratio_ok_when_consistent():
    store = InMemoryFactStore()
    store.add(_fact("net_income", "202000000", "FY2026-Q1"))
    store.add(_fact("diluted_shares", "232100000", "FY2026-Q1", unit=Unit.SHARES))
    doc = _doc([_claim("gaap_diluted_eps", "$0.87")])
    assert not _rule("derived.ratio_mismatch", check_derived_consistency(doc, _eps_reg(), store))


# -- Increment 3: segment sum tie-out (components sum to total) -------------

def _sum_reg():
    return MetricRegistry([
        MetricSpec(id="cloud_revenue", label="Cloud revenue", unit=Unit.CURRENCY),
        MetricSpec(id="license_revenue", label="License & services revenue", unit=Unit.CURRENCY),
        MetricSpec(id="total_revenue", label="Total revenue", unit=Unit.CURRENCY,
                   derived_kind="sum", derived_components=("cloud_revenue", "license_revenue")),
    ])


def test_segment_sum_flags_when_total_off():
    store = InMemoryFactStore()
    store.add(_fact("cloud_revenue", "611800000", "FY2026-Q1"))
    store.add(_fact("license_revenue", "629500000", "FY2026-Q1"))
    # components sum to 1,241.3M; a claimed total of $1.30B doesn't tie
    doc = _doc([_claim("total_revenue", "$1.30 billion")])
    assert _rule("derived.sum_mismatch", check_derived_consistency(doc, _sum_reg(), store))


def test_segment_sum_ok_when_total_ties():
    store = InMemoryFactStore()
    store.add(_fact("cloud_revenue", "611800000", "FY2026-Q1"))
    store.add(_fact("license_revenue", "629500000", "FY2026-Q1"))
    doc = _doc([_claim("total_revenue", "$1.24 billion")])
    assert not _rule("derived.sum_mismatch", check_derived_consistency(doc, _sum_reg(), store))


# -- Increment 4: TTM period-sum (4 quarters sum to trailing twelve) -------

def _ttm_reg():
    return MetricRegistry([
        MetricSpec(id="total_revenue", label="Total revenue", unit=Unit.CURRENCY),
        MetricSpec(id="revenue_ttm", label="Revenue, TTM", unit=Unit.CURRENCY,
                   derived_kind="ttm_sum", derived_base="total_revenue"),
    ])


def _seed_ttm(store):
    store.add(_fact("total_revenue", "1241300000", "FY2026-Q1"))
    store.add(_fact("total_revenue", "1190000000", "FY2025-Q4"))
    store.add(_fact("total_revenue", "1131000000", "FY2025-Q3"))
    store.add(_fact("total_revenue", "1083000000", "FY2025-Q2"))
    # TTM = 4,645.3M


def test_ttm_flags_when_off():
    store = InMemoryFactStore()
    _seed_ttm(store)
    doc = _doc([_claim("revenue_ttm", "$5.0 billion")])
    assert _rule("derived.recomputation_mismatch", check_derived_consistency(doc, _ttm_reg(), store))


def test_ttm_ok_when_ties():
    store = InMemoryFactStore()
    _seed_ttm(store)
    doc = _doc([_claim("revenue_ttm", "$4.65 billion")])
    assert not _rule("derived.recomputation_mismatch", check_derived_consistency(doc, _ttm_reg(), store))


# -- Increment 5: directional language vs. sign of the change --------------

def _dir_reg():
    return MetricRegistry([
        MetricSpec(id="operating_margin", label="Operating margin", unit=Unit.PERCENT),
    ])


def _dir_store():
    store = InMemoryFactStore()
    store.add(_fact("operating_margin", "21.0", "FY2026-Q1", unit=Unit.PERCENT))
    store.add(_fact("operating_margin", "22.4", "FY2025-Q1", unit=Unit.PERCENT))  # was higher
    return store


def test_directional_flags_expanded_when_declined():
    # margin fell 22.4 -> 21.0 YoY, so "expanded" is the wrong direction
    doc = Document(id="d", tenant_id=T, title="d", kind=DocumentKind.OTHER,
                   text="Operating margin expanded year over year.",
                   claims=(_claim("operating_margin", "21.0%"),))
    assert _rule("directional.sign_mismatch", check_directional_language(doc, _dir_reg(), _dir_store()))


def test_directional_ok_when_language_matches_sign():
    doc = Document(id="d", tenant_id=T, title="d", kind=DocumentKind.OTHER,
                   text="Operating margin declined year over year.",
                   claims=(_claim("operating_margin", "21.0%"),))
    assert not _rule("directional.sign_mismatch", check_directional_language(doc, _dir_reg(), _dir_store()))
