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
from attest.verification.rules import check_derived_consistency

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
