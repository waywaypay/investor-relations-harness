"""Tests for the synthetic perturbation generator.

The contract: every synthetic case's label is determined by the *operation applied
to a real filed value*, never by asking the engine. These tests assert the
by-construction labels are correct, and that the generator refuses to emit cases
whose correct label would depend on the rounding policy (the thing under test) —
those belong to the human-labeled core, not the synthetic bucket.
"""

from decimal import Decimal

from attest.domain.facts import Fact, SourceType
from attest.domain.money import Unit
from attest.domain.verdicts import Verdict
from attest.eval.perturbation import SyntheticCase, perturb_fact

REV = Fact(
    id="f1", tenant_id="t", entity="MRDN", metric="total_revenue", period="FY2026-Q1",
    value=Decimal("1241300000"), unit=Unit.CURRENCY, quantum=Decimal("100000"),
    source_type=SourceType.EDGAR_XBRL, source_ref="acc#rev", as_of="2026-04-28",
)


def _ops(cases):
    return {c.operation for c in cases}


def test_emits_synthetic_label_source_only():
    cases = perturb_fact(REV)
    assert cases, "should emit at least one case for a filed currency fact"
    assert all(c.label_source == "synthetic_perturbation" for c in cases)
    assert all(isinstance(c, SyntheticCase) for c in cases)


def test_identity_reformat_is_traced_by_construction():
    cases = perturb_fact(REV)
    identity = [c for c in cases if c.operation == "identity_reformat"]
    assert identity
    assert all(c.expected == Verdict.TRACED for c in identity)


def test_scale_and_transpose_are_conflict_by_construction():
    cases = perturb_fact(REV)
    conflicts = [c for c in cases if c.operation in
                 ("scale_error_div1000", "scale_error_x1000", "digit_transpose", "magnitude_typo")]
    assert len(conflicts) >= 3
    assert all(c.expected == Verdict.CONFLICT for c in conflicts)


def test_case_ids_are_unique_and_traceable():
    cases = perturb_fact(REV)
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))
    assert all(c.id.startswith("syn_") and "total_revenue" in c.id for c in cases)


def test_non_currency_metrics_are_skipped():
    # Percent/bps correct labels are policy/judgment dependent -> human-labeled core.
    pct = REV.model_copy(update={"metric": "cloud_growth_yoy", "unit": Unit.PERCENT,
                                 "value": Decimal("29"), "quantum": Decimal("1")})
    assert perturb_fact(pct) == []


def test_non_filed_sources_are_skipped():
    # Guidance is needs_review, not a clean perturbation target.
    guidance = REV.model_copy(update={"source_type": SourceType.MANAGEMENT_INPUT,
                                      "source_ref": "none"})
    assert perturb_fact(guidance) == []


def test_text_is_parseable_and_matches_expected_verdict_independently():
    # Independent check: the by-construction label must agree with parse_quantity math,
    # NOT with the engine. (We re-derive value relationships here, no engine involved.)
    from attest.domain.money import DEFAULT_POLICY, parse_quantity
    for c in perturb_fact(REV):
        q = parse_quantity(c.text)  # must parse
        if c.operation == "identity_reformat":
            assert q.matches(REV.quantity(), DEFAULT_POLICY)
        elif c.operation in ("scale_error_div1000", "scale_error_x1000",
                             "digit_transpose", "magnitude_typo"):
            assert not q.matches(REV.quantity(), DEFAULT_POLICY)
