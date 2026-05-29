"""The CI regression gate. Prompt/model/rule changes must clear the golden set."""

from attest.eval import run_eval

# Gate thresholds. Figure false negatives are catastrophic -> must be zero.
MAX_FIGURE_FALSE_NEGATIVE_RATE = 0.0
MIN_EXACT_ACCURACY = 0.95
MIN_FLAG_PRECISION = 0.90


def test_figure_tieout_gate():
    report = run_eval("figure_tieouts")
    assert report.total >= 15, "golden set should be non-trivial"
    assert report.figure_false_negative_rate <= MAX_FIGURE_FALSE_NEGATIVE_RATE, report.as_dict()
    assert report.exact_accuracy >= MIN_EXACT_ACCURACY, report.as_dict()
    assert report.flag_precision >= MIN_FLAG_PRECISION, report.as_dict()


def test_no_mismatches_on_curated_set():
    report = run_eval("figure_tieouts")
    assert report.mismatches == [], report.mismatches
