"""Engine recompute fallback — derived metrics verified from filed operands.

A growth percent or a ratio has no XBRL fact of its own for a live-ingested
issuer; only the *levels* are filed. The engine's `_bind_derived` recomputes the
metric's identity from the filed operands and renders traced/conflict, instead
of leaving the most quoted figure in a release untraced. The invariant stays:
operands must be filed sources, and nothing un-sourceable is ever asserted.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.money import Unit
from attest.domain.verdicts import FigureClaim, Verdict
from attest.service import AttestService

TENANT = "acme"
ENTITY = "ACME"
URL = "https://www.sec.gov/Archives/edgar/data/123/000012326000010/0000123-26-000010-index.htm"


def _fact(
    metric: str,
    period: str,
    value: str,
    *,
    unit: Unit = Unit.CURRENCY,
    source_type: SourceType = SourceType.EDGAR_XBRL,
    as_of: str = "2026-04-15",
    url: str | None = None,
) -> Fact:
    return Fact(
        id=f"{metric}:{period}:{as_of}",
        tenant_id=TENANT,
        entity=ENTITY,
        metric=metric,
        period=period,
        value=Decimal(value),
        unit=unit,
        quantum=Decimal(0),
        source_type=source_type,
        source_ref=f"0000123-26-000010#{metric}",
        source_url=url,
        as_of=as_of,
        confidence=Confidence.HIGH,
    )


def _claim(metric: str, text: str, *, period: str = "FY2026-Q1", confidence=Confidence.HIGH):
    return FigureClaim(
        claim_id="c", document_id="d", entity=ENTITY, metric=metric,
        period=period, displayed_text=text, detect_confidence=confidence,
    )


@pytest.fixture
def service() -> AttestService:
    svc = AttestService()
    svc.store.add_many(
        [
            _fact("total_revenue", "FY2026-Q1", "109605000000", url=URL),
            _fact("total_revenue", "FY2025-Q1", "99797000000", as_of="2025-04-15"),
            _fact("medical_costs", "FY2026-Q1", "66144000000"),
            _fact("premium_revenue", "FY2026-Q1", "78000000000"),
        ]
    )
    return svc


def test_growth_percent_recomputes_to_traced_from_filed_levels(service):
    v = service.engine.verify_claim(_claim("revenue_growth_yoy", "9.8%"), TENANT)
    assert v.verdict == Verdict.TRACED
    assert "recomputed from filed sources" in v.reason.lower()
    assert v.provenance.source_type == SourceType.DERIVED
    # The synthetic provenance is readable, dated by its newest operand, and
    # inherits a resolvable filing link from the operands.
    assert v.source_value == "9.828%"
    assert v.as_of == "2026-04-15"
    assert v.provenance.url == URL


def test_falsified_growth_percent_conflicts_with_the_recomputation(service):
    v = service.engine.verify_claim(_claim("revenue_growth_yoy", "12%"), TENANT)
    assert v.verdict == Verdict.CONFLICT
    assert "9.828%" in v.reason


def test_growth_stays_untraced_when_the_prior_level_is_missing(service):
    v = service.engine.verify_claim(
        _claim("revenue_growth_yoy", "9.8%", period="FY2025-Q1"), TENANT
    )
    assert v.verdict == Verdict.UNTRACED  # FY2024-Q1 level was never ingested


def test_growth_never_recomputes_from_non_filed_operands():
    svc = AttestService()
    svc.store.add_many(
        [
            _fact("total_revenue", "FY2026-Q1", "109605000000"),
            _fact(
                "total_revenue", "FY2025-Q1", "99797000000",
                source_type=SourceType.MANAGEMENT_INPUT, as_of="2025-04-15",
            ),
        ]
    )
    v = svc.engine.verify_claim(_claim("revenue_growth_yoy", "9.8%"), TENANT)
    assert v.verdict == Verdict.UNTRACED


def test_ratio_identity_recomputes_to_traced(service):
    v = service.engine.verify_claim(_claim("medical_care_ratio", "84.8%"), TENANT)
    assert v.verdict == Verdict.TRACED
    assert v.provenance.ref == "derived:medical_costs / premium_revenue"


def test_low_confidence_derived_claim_routes_to_review_not_traced(service):
    v = service.engine.verify_claim(
        _claim("revenue_growth_yoy", "9.8%", confidence=Confidence.LOW), TENANT
    )
    assert v.verdict == Verdict.NEEDS_REVIEW


def test_decline_recomputes_against_signed_values():
    svc = AttestService()
    svc.store.add_many(
        [
            _fact("total_revenue", "FY2026-Q1", "95000000000"),
            _fact("total_revenue", "FY2025-Q1", "100000000000", as_of="2025-04-15"),
        ]
    )
    v = svc.engine.verify_claim(_claim("revenue_growth_yoy", "-5%"), TENANT)
    assert v.verdict == Verdict.TRACED


def test_non_derived_metric_without_facts_still_falls_to_untraced(service):
    v = service.engine.verify_claim(_claim("non_gaap_diluted_eps", "$7.20"), TENANT)
    assert v.verdict == Verdict.UNTRACED
