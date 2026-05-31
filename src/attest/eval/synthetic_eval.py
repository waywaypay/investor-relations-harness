"""Score the engine against synthetic perturbation cases — in its own bucket.

This is deliberately a *separate entry point* from :func:`attest.eval.run_eval`.
Synthetic cases measure **robustness coverage** (does the deterministic engine
catch a digit transposition, a scale error?). They must never be summed into the
human-labeled reliability number, because their labels come from a known mutation
rather than human disclosure judgment — a generator that's easy for the engine to
pass would otherwise inflate the headline metric.

The report carries a ``bucket`` tag and a ``caveat`` string so the number can't be
quietly pasted next to the real gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from attest.domain.verdicts import FigureClaim, Verdict
from attest.eval.perturbation import perturb_facts
from attest.ingestion.edgar_xbrl import load_fixture
from attest.service import AttestService

_CAVEAT = (
    "Synthetic robustness coverage from known mutations of real filed values. "
    "This is NOT a reliability metric — do not report it alongside the "
    "human-labeled golden-set gate."
)


@dataclass
class SyntheticReport:
    bucket: str = "synthetic_perturbation"
    total: int = 0
    correct: int = 0
    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0
    mismatches: list[dict] = field(default_factory=list)
    by_operation: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def exact_accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0

    @property
    def figure_false_negative_rate(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.false_negative / denom if denom else 0.0

    def as_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "caveat": _CAVEAT,
            "total": self.total,
            "correct": self.correct,
            "exact_accuracy": round(self.exact_accuracy, 4),
            "figure_false_negative_rate": round(self.figure_false_negative_rate, 4),
            "by_operation": self.by_operation,
            "mismatches": self.mismatches,
        }


def run_synthetic_eval(fixture: str = "meridian_q1_fy2026", tenant: str = "meridian") -> SyntheticReport:
    """Generate perturbations from a real fixture and score the engine on them."""
    service = AttestService()
    service.ingest_xbrl(load_fixture(fixture), tenant_id=tenant)
    facts = service.store.all(tenant)
    cases = perturb_facts(facts)

    report = SyntheticReport()
    for case in cases:
        claim = FigureClaim(
            claim_id=case.id, document_id="synthetic", entity=case.entity,
            metric=case.metric, period=case.period, displayed_text=case.text,
        )
        verdict = service.engine.verify_claim(claim, tenant).verdict

        report.total += 1
        op = report.by_operation.setdefault(case.operation, {"total": 0, "correct": 0})
        op["total"] += 1
        if verdict == case.expected:
            report.correct += 1
            op["correct"] += 1
        else:
            report.mismatches.append(
                {"id": case.id, "operation": case.operation,
                 "expected": case.expected.value, "got": verdict.value}
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
