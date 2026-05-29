"""The synthetic eval must be scored in its own bucket, never summed with the
human-labeled golden set. These tests pin that separation in place.
"""

from attest.eval.synthetic_eval import run_synthetic_eval


def test_synthetic_eval_runs_and_reports():
    report = run_synthetic_eval()
    assert report.total > 0
    # Labels are sound by construction (latest-version only), so the engine should
    # clear all of them. A mismatch here means either a generator-label bug or a
    # real engine regression — both worth a failure.
    assert report.figure_false_negative_rate == 0.0
    assert report.mismatches == [], report.mismatches
    assert report.exact_accuracy == 1.0


def test_synthetic_report_is_tagged_synthetic():
    report = run_synthetic_eval()
    d = report.as_dict()
    assert d["bucket"] == "synthetic_perturbation"
    # Must carry a guard note so nobody pastes this number next to the real gate.
    assert "not a reliability" in d["caveat"].lower() or "robustness" in d["caveat"].lower()
