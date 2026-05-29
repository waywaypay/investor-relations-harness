"""Tests for the 8-K Item 4.02 restatement harvester.

These produce *real* (not synthetic) conflict labels: when a prior value was
restated, a draft still citing the original value must be flagged `conflict`, and
the corrected value must be `traced`. Both labels come from the adjudicated
restatement event itself, so they are real-but-automatable.
"""

from attest.eval.restatement import (
    RestatementCase,
    cases_from_restatement,
    load_restatement_fixture,
)
from attest.domain.verdicts import Verdict


def test_fixture_yields_original_conflict_and_restated_traced():
    rec = load_restatement_fixture("meridian_cloud_4_02")
    cases = cases_from_restatement(rec)
    by_verdict = {c.expected: c for c in cases}
    assert Verdict.CONFLICT in by_verdict
    assert Verdict.TRACED in by_verdict
    # original value -> conflict
    conflict = by_verdict[Verdict.CONFLICT]
    assert conflict.text == "$467.0 million"
    # restated value -> traced
    traced = by_verdict[Verdict.TRACED]
    assert traced.text == "$474.3 million"


def test_cases_tagged_edgar_restatement():
    cases = cases_from_restatement(load_restatement_fixture("meridian_cloud_4_02"))
    assert cases
    assert all(isinstance(c, RestatementCase) for c in cases)
    assert all(c.label_source == "edgar_restatement" for c in cases)
    assert all(c.accession for c in cases)  # provenance: the 8-K that adjudicated it


def test_engine_agrees_with_harvested_labels():
    # The harvested labels must agree with the real engine when the restated fact
    # store is loaded — proving these are gold, not guesses.
    from attest.eval.restatement import build_store_from_restatement
    from attest.service import AttestService
    from attest.domain.verdicts import FigureClaim

    rec = load_restatement_fixture("meridian_cloud_4_02")
    cases = cases_from_restatement(rec)
    svc = AttestService()
    build_store_from_restatement(rec, svc)
    for case in cases:
        claim = FigureClaim(
            claim_id=case.id, document_id="eval", entity=case.entity,
            metric=case.metric, period=case.period, displayed_text=case.text,
        )
        got = svc.engine.verify_claim(claim, rec["tenant"]).verdict
        assert got == case.expected, f"{case.id}: expected {case.expected}, got {got}"
