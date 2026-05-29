"""The synthetic eval must be scored in its own bucket, never summed with the
human-labeled golden set. These tests pin that separation in place.
"""

from attest.eval.synthetic_eval import run_synthetic_eval


def test_synthetic_eval_runs_and_reports():
    report = run_synthetic_eval()
    assert report.total > 0
    # The engine should handle by-construction cases well; this is a robustness
    # floor, deliberately looser than the human-labeled gate.
    assert report.figure_false_negative_rate == 0.0
    assert report.exact_accuracy >= 0.9


def test_synthetic_report_is_tagged_synthetic():
    report = run_synthetic_eval()
    d = report.as_dict()
    assert d["bucket"] == "synthetic_perturbation"
    # Must carry a guard note so nobody pastes this number next to the real gate.
    assert "not a reliability" in d["caveat"].lower() or "robustness" in d["caveat"].lower()
