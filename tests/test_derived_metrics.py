"""Derived-metric tie-out: margins and free cash flow.

A metric that XBRL never tags (gross/operating margin, free cash flow) is still an
exact identity over figures that *are* filed. The engine recomputes the identity
from the filed operands so a correct figure traces and a wrong one conflicts —
while preserving the spine's invariant that a number is only ``traced`` when it
reconciles against *filed* sources (a non-filed operand can never make it traced).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from attest.domain.document import DocumentKind
from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.money import Unit
from attest.domain.verdicts import FigureClaim, Verdict
from attest.service import AttestService

T, E, P = "acme", "ACME", "FY2026-Q1"


def _svc_with_filed_operands() -> AttestService:
    svc = AttestService()

    def filed(metric: str, value: str, *, filed: bool = True) -> None:
        svc.store.add(
            Fact(
                id=f"{metric}-{P}",
                tenant_id=T,
                entity=E,
                metric=metric,
                period=P,
                value=Decimal(value),
                unit=Unit.CURRENCY,
                quantum=Decimal("1"),
                source_type=SourceType.EDGAR_XBRL if filed else SourceType.PRIOR_DISCLOSURE,
                source_ref=f"acc#{metric}",
                source_label="10-Q",
                as_of="2026-02-01",
                confidence=Confidence.HIGH,
            )
        )

    # revenue 1,000; gross profit 740 -> 74% ; operating income 280 -> 28%
    # operating cash flow 150; capex 30 -> free cash flow 120
    filed("total_revenue", "1000000000")
    filed("gross_profit", "740000000")
    filed("operating_income", "280000000")
    filed("operating_cash_flow", "150000000")
    filed("capex", "30000000")
    return svc


def _verify(svc: AttestService, metric: str, text: str) -> object:
    claim = FigureClaim(
        claim_id="c", document_id="d", entity=E, metric=metric, period=P, displayed_text=text
    )
    return svc.engine.verify_claim(claim, T)


def test_gross_margin_traces_by_recomputation():
    v = _verify(_svc_with_filed_operands(), "gross_margin", "74%")
    assert v.verdict == Verdict.TRACED
    assert v.provenance.source_type == SourceType.DERIVED
    assert "gross_profit / total_revenue" in v.provenance.ref


def test_operating_margin_traces_by_recomputation():
    v = _verify(_svc_with_filed_operands(), "operating_margin", "28%")
    assert v.verdict == Verdict.TRACED


def test_wrong_margin_conflicts_with_the_recomputed_value():
    v = _verify(_svc_with_filed_operands(), "gross_margin", "71%")
    assert v.verdict == Verdict.CONFLICT
    assert "74" in v.source_value  # the recomputed value is surfaced


def test_free_cash_flow_traces_as_ocf_minus_capex():
    v = _verify(_svc_with_filed_operands(), "free_cash_flow", "$120 million")
    assert v.verdict == Verdict.TRACED
    assert "operating_cash_flow - capex" in v.provenance.ref


def test_wrong_free_cash_flow_conflicts():
    v = _verify(_svc_with_filed_operands(), "free_cash_flow", "$200 million")
    assert v.verdict == Verdict.CONFLICT


def test_derived_metric_is_untraced_when_an_operand_is_missing():
    svc = AttestService()
    # operating cash flow is filed but capex is not — FCF can't be recomputed.
    svc.store.add(
        Fact(
            id="ocf", tenant_id=T, entity=E, metric="operating_cash_flow", period=P,
            value=Decimal("150000000"), unit=Unit.CURRENCY, quantum=Decimal("1"),
            source_type=SourceType.EDGAR_XBRL, source_ref="acc#ocf", source_label="10-Q",
            as_of="2026-02-01", confidence=Confidence.HIGH,
        )
    )
    v = _verify(svc, "free_cash_flow", "$120 million")
    assert v.verdict == Verdict.UNTRACED


def test_derived_metric_never_traces_from_a_non_filed_operand():
    # The invariant: a derived metric only traces when *every* operand is filed. A
    # gross profit that is only a prior disclosure (non-filed) must not make a margin
    # traceable — it falls to untraced, never asserted.
    svc = AttestService()
    svc.store.add(
        Fact(
            id="rev", tenant_id=T, entity=E, metric="total_revenue", period=P,
            value=Decimal("1000000000"), unit=Unit.CURRENCY, quantum=Decimal("1"),
            source_type=SourceType.EDGAR_XBRL, source_ref="acc#rev", source_label="10-Q",
            as_of="2026-02-01", confidence=Confidence.HIGH,
        )
    )
    svc.store.add(
        Fact(
            id="gp", tenant_id=T, entity=E, metric="gross_profit", period=P,
            value=Decimal("740000000"), unit=Unit.CURRENCY, quantum=Decimal("1"),
            source_type=SourceType.PRIOR_DISCLOSURE, source_ref="prior", source_label="release",
            as_of="2026-01-01", confidence=Confidence.HIGH,
        )
    )
    v = _verify(svc, "gross_margin", "74%")
    assert v.verdict == Verdict.UNTRACED


def test_recompute_mismatch_also_raises_a_blocking_finding():
    svc = _svc_with_filed_operands()
    _, result, _, _ = svc.analyze_text(
        tenant_id=T, text="Gross margin was 71%.", title="t",
        kind=DocumentKind.SCRIPT, entity=E, period=P,
    )
    assert any(f.rule == "derived.ratio_mismatch" for f in result.findings)


def test_free_cash_flow_mismatch_raises_a_difference_finding():
    svc = _svc_with_filed_operands()
    _, result, _, _ = svc.analyze_text(
        tenant_id=T, text="Free cash flow was $200 million.", title="t",
        kind=DocumentKind.SCRIPT, entity=E, period=P,
    )
    assert any(f.rule == "derived.difference_mismatch" for f in result.findings)


def test_billings_is_recognized_and_checked_for_consistency():
    svc = AttestService()
    # No filed source -> recognized as billings (not 'unidentified'), honestly untraced.
    _, result, _, _ = svc.analyze_text(
        tenant_id=T, text="Billings were $1.20 billion in the first quarter of fiscal 2026.",
        title="t", kind=DocumentKind.SCRIPT, entity=E, period=P,
    )
    billings = {v.metric: v for v in result.verdicts}.get("billings")
    assert billings is not None and billings.verdict == Verdict.UNTRACED

    # Filed as a prior disclosure, a later draft that changed it is flagged.
    svc.ingest_disclosure(
        text="Billings were $1.20 billion in the first quarter of fiscal 2026.",
        tenant_id=T, entity=E, period=P, label="Q1 release",
    )
    _, restated, _, _ = svc.analyze_text(
        tenant_id=T, text="In Q1 fiscal 2026, billings were $1.35 billion.",
        title="t", kind=DocumentKind.SCRIPT, entity=E, period=P,
    )
    v = {v.metric: v for v in restated.verdicts}["billings"]
    assert v.verdict == Verdict.CONFLICT
    assert "prior disclosure" in v.reason.lower()


@pytest.mark.parametrize(
    "prose, figure, expected_metric",
    [
        ("Gross margin was 74%.", "74%", "gross_margin"),
        ("Operating margin was 28%.", "28%", "operating_margin"),
        ("Non-GAAP gross margin was 78%.", "78%", "non_gaap_gross_margin"),
        ("Non-GAAP operating margin was 29%.", "29%", "non_gaap_operating_margin"),
        ("Free cash flow was $1.4 billion.", "$1.4 billion", "free_cash_flow"),
        ("Capital expenditures were $30 million.", "$30 million", "capex"),
        ("Billings were $2.8 billion.", "$2.8 billion", "billings"),
    ],
)
def test_extraction_maps_new_metric_phrasings(prose, figure, expected_metric):
    from attest.extraction.claims import ClaimExtractor

    svc = AttestService()
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        prose, document_id="d", tenant_id=T, entity=E, period=P
    )
    by_text = {c.displayed_text: c for c in claims}
    assert by_text[figure].metric == expected_metric
    # A margin level must not be demoted to low confidence for lacking a growth word.
    if expected_metric in ("gross_margin", "operating_margin"):
        assert by_text[figure].detect_confidence == Confidence.HIGH
