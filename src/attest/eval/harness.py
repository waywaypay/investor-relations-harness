"""The eval harness.

For a trust product, the eval pipeline is the asset that lets you say
"trustworthy" without lying. This module scores the deterministic figure engine
against a labeled golden set and reports metrics with the *right* asymmetry:

* **Figure false negatives are catastrophic.** A wrong number we call ``traced``
  is the worst possible outcome, so ``figure_false_negative_rate`` is tracked
  separately and the CI gate requires it to be zero.
* **Narrative false positives kill trust.** (Tracked once the narrative service
  lands; the harness shape already supports a ``should_not_flag`` majority.)

A figure is treated as a positive ("flag") when its verdict is anything other
than ``traced`` — i.e. it needs a human's attention before publish.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from attest.domain.verdicts import FigureClaim, Verdict
from attest.ingestion.edgar_xbrl import load_fixture
from attest.service import AttestService

_GOLDEN_DIR = Path(__file__).parent / "golden"


@dataclass(frozen=True)
class EvalCase:
    id: str
    entity: str
    metric: str
    period: str
    text: str
    expected: Verdict


@dataclass
class EvalReport:
    total: int = 0
    correct: int = 0
    # confusion matrix for the binary "flag" decision (positive = needs attention)
    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0
    mismatches: list[dict] = field(default_factory=list)

    @property
    def exact_accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0

    @property
    def flag_precision(self) -> float:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else 1.0

    @property
    def flag_recall(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else 1.0

    @property
    def figure_false_negative_rate(self) -> float:
        """Share of figures that *should* have been flagged but were called traced."""
        denom = self.true_positive + self.false_negative
        return self.false_negative / denom if denom else 0.0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "correct": self.correct,
            "exact_accuracy": round(self.exact_accuracy, 4),
            "flag_precision": round(self.flag_precision, 4),
            "flag_recall": round(self.flag_recall, 4),
            "figure_false_negative_rate": round(self.figure_false_negative_rate, 4),
            "mismatches": self.mismatches,
        }


def load_golden(name: str = "figure_tieouts") -> tuple[dict, list[EvalCase]]:
    """Load a golden set: its filing fixture spec and labeled cases."""
    data = json.loads((_GOLDEN_DIR / f"{name}.json").read_text())
    cases = [
        EvalCase(
            id=c["id"],
            entity=c["entity"],
            metric=c["metric"],
            period=c["period"],
            text=c["text"],
            expected=Verdict(c["expected"]),
        )
        for c in data["cases"]
    ]
    return data, cases


def run_eval(name: str = "figure_tieouts") -> EvalReport:
    """Run the figure engine over a golden set and score it."""
    data, cases = load_golden(name)
    service = AttestService()
    service.ingest_xbrl(load_fixture(data["filing_fixture"]), tenant_id=data["tenant"])

    report = EvalReport()
    for case in cases:
        claim = FigureClaim(
            claim_id=case.id, document_id="eval", entity=case.entity,
            metric=case.metric, period=case.period, displayed_text=case.text,
        )
        verdict = service.engine.verify_claim(claim, data["tenant"]).verdict

        report.total += 1
        if verdict == case.expected:
            report.correct += 1
        else:
            report.mismatches.append(
                {"id": case.id, "expected": case.expected.value, "got": verdict.value}
            )

        expected_flag = case.expected != Verdict.TRACED
        predicted_flag = verdict != Verdict.TRACED
        if expected_flag and predicted_flag:
            report.true_positive += 1
        elif expected_flag and not predicted_flag:
            report.false_negative += 1
        elif not expected_flag and predicted_flag:
            report.false_positive += 1
        else:
            report.true_negative += 1

    return report
