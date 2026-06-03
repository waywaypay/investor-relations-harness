"""The CI regression gate. A model / rule / prompt change must clear the golden sets.

Three surfaces are gated, each with the asymmetry that matters for a trust product:
figure tie-outs (a wrong number called traced is catastrophic), the extraction edge
(never under-detect; a mis-attribution must never be traced), and the rule engines
(never miss a compliance flag; never raise a spurious one).
"""

from attest.eval import run_eval, run_extraction_eval, run_gates, run_rules_eval
from attest.eval.harness import (
    MAX_FIGURE_FALSE_NEGATIVE_RATE,
    MAX_UNSAFE_TRACES,
    MIN_ATTRIBUTION_ACCURACY,
    MIN_DETECTION_RECALL,
    MIN_EXACT_ACCURACY,
    MIN_FLAG_PRECISION,
    MIN_GOLDEN_CASES,
    MIN_RULE_PRECISION,
    MIN_RULE_RECALL,
)


# -- figure tie-outs ---------------------------------------------------------

def test_figure_tieout_gate():
    report = run_eval("figure_tieouts")
    assert report.total >= MIN_GOLDEN_CASES, "golden set should be non-trivial"
    assert report.figure_false_negative_rate <= MAX_FIGURE_FALSE_NEGATIVE_RATE, report.as_dict()
    assert report.exact_accuracy >= MIN_EXACT_ACCURACY, report.as_dict()
    assert report.flag_precision >= MIN_FLAG_PRECISION, report.as_dict()


def test_no_mismatches_on_curated_set():
    report = run_eval("figure_tieouts")
    assert report.mismatches == [], report.mismatches


# -- extraction edge (the replaceable component's regression gate) -----------

def test_extraction_never_under_detects():
    # Under-detection is the failure mode; over-detection is allowed. Every stated
    # figure in the golden set must be surfaced.
    report = run_extraction_eval("extraction_attribution")
    assert report.expected_total >= MIN_GOLDEN_CASES, "extraction golden set should be non-trivial"
    assert report.detection_recall >= MIN_DETECTION_RECALL, report.as_dict()
    assert report.missed == [], report.missed


def test_extraction_misattribution_is_never_traced():
    # The load-bearing guarantee: the edge may err, but the deterministic core must
    # never assert a mis-attributed figure as traced.
    report = run_extraction_eval("extraction_attribution")
    assert len(report.unsafe_traces) <= MAX_UNSAFE_TRACES, report.unsafe_traces


def test_extraction_attribution_floor():
    # A regression floor, not a demand for perfection — the edge is allowed to be
    # imperfect precisely because the core is the safety net (asserted above).
    report = run_extraction_eval("extraction_attribution")
    assert report.attribution_accuracy >= MIN_ATTRIBUTION_ACCURACY, report.as_dict()


# -- deterministic rule engines ----------------------------------------------

def test_rules_never_miss_a_required_flag():
    # A missed Reg G / FLS / consistency / derived finding is a compliance miss.
    report = run_rules_eval("rule_findings")
    assert report.rule_recall >= MIN_RULE_RECALL, report.as_dict()
    assert report.missed == [], report.missed


def test_rules_raise_no_spurious_flags():
    # A spurious finding erodes trust; precision must be perfect on the curated set.
    report = run_rules_eval("rule_findings")
    assert report.rule_precision >= MIN_RULE_PRECISION, report.as_dict()
    assert report.spurious == [], report.spurious


# -- the single boolean the CLI and CI both decide on ------------------------

def test_all_gates_pass():
    failed = [gate.name for gate in run_gates() if not gate.passed]
    assert not failed, f"failing gates: {failed}"
