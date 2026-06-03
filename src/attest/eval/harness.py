"""The eval harness.

For a trust product, the eval pipeline is the asset that lets you say
"trustworthy" without lying. This module scores the spine against labeled golden
sets and reports metrics with the *right* asymmetry. It covers three surfaces:

* **Figure tie-outs** (:func:`run_eval`). A wrong number we call ``traced`` is the
  worst possible outcome, so ``figure_false_negative_rate`` is tracked separately
  and the gate requires it to be zero. A figure is a positive ("flag") when its
  verdict is anything other than ``traced`` — i.e. it needs a human before publish.

* **The extraction edge** (:func:`run_extraction_eval`). The edge is the explicitly
  *replaceable* (eventually-LLM) component, so it needs a regression gate that lets
  you swap a model in safely. *Under-detection is the failure mode* (the edge is
  allowed to over-detect), so detection recall must be perfect. Attribution may be
  imperfect on hard prose — but the architecture's load-bearing guarantee is that a
  mis-attributed figure is *never* asserted ``traced`` (the deterministic core
  disposes), and that invariant is gated at zero.

* **The deterministic rule engines** (:func:`run_rules_eval`). Reg G / FLS /
  consistency / derived findings are scored for *recall* (a missed compliance flag
  is catastrophic) and *precision* (a spurious flag kills trust) against a
  labeled ``should_flag`` / ``should_not_flag`` set — the asymmetry this harness
  was always shaped for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from attest.domain.document import Document, DocumentKind
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


# ---------------------------------------------------------------------------
# Extraction-edge eval — the regression gate for the replaceable component.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedFigure:
    """The *correct* attribution for one stated figure.

    ``metric`` is the canonical metric id, or ``"unidentified"`` when the right
    behavior is for the edge to decline to attribute (over-detect, never assert).
    """

    text: str
    metric: str
    entity: str


@dataclass(frozen=True)
class ExtractionCase:
    id: str
    period: str
    text: str
    expected: tuple[ExpectedFigure, ...]


@dataclass
class ExtractionReport:
    expected_total: int = 0
    detected: int = 0
    attributed_correct: int = 0
    missed: list[dict] = field(default_factory=list)            # under-detection (the failure mode)
    misattributions: list[dict] = field(default_factory=list)   # wrong (metric, entity)
    unsafe_traces: list[dict] = field(default_factory=list)     # mis-attributed yet 'traced'

    @property
    def detection_recall(self) -> float:
        """Share of stated figures the edge surfaced at all. Under-detection is fatal."""
        return self.detected / self.expected_total if self.expected_total else 1.0

    @property
    def attribution_accuracy(self) -> float:
        """Share of *detected* figures mapped to the correct (metric, entity)."""
        return self.attributed_correct / self.detected if self.detected else 1.0

    def as_dict(self) -> dict:
        return {
            "expected_total": self.expected_total,
            "detected": self.detected,
            "detection_recall": round(self.detection_recall, 4),
            "attribution_accuracy": round(self.attribution_accuracy, 4),
            "unsafe_traces": self.unsafe_traces,
            "missed": self.missed,
            "misattributions": self.misattributions,
        }


def load_extraction_golden(name: str = "extraction_attribution") -> tuple[dict, list[ExtractionCase]]:
    data = json.loads((_GOLDEN_DIR / f"{name}.json").read_text())
    cases = [
        ExtractionCase(
            id=c["id"],
            period=c["period"],
            text=c["text"],
            expected=tuple(
                ExpectedFigure(text=e["text"], metric=e["metric"], entity=e["entity"])
                for e in c["expected"]
            ),
        )
        for c in data["cases"]
    ]
    return data, cases


def run_extraction_eval(name: str = "extraction_attribution") -> ExtractionReport:
    """Score the model-free extraction edge against labeled prose.

    Runs each case end to end (edge proposes claims -> core disposes) so we can
    measure both the edge's quality *and* the guarantee that the core catches the
    edge's mistakes: every mis-attributed figure is checked to confirm it did not
    come back ``traced``.
    """
    data, cases = load_extraction_golden(name)
    service = AttestService()
    service.ingest_xbrl(load_fixture(data["filing_fixture"]), tenant_id=data["tenant"])

    report = ExtractionReport()
    for case in cases:
        document, result, _, _ = service.analyze_text(
            tenant_id=data["tenant"],
            text=case.text,
            title=case.id,
            kind=DocumentKind.OTHER,
            entity=data["entity"],
            period=case.period,
            document_id=case.id,
        )
        verdict_by_claim = {v.claim_id: v.verdict for v in result.verdicts}
        remaining = list(document.claims)  # consumed as matched, to handle repeats

        for exp in case.expected:
            report.expected_total += 1
            match = next((c for c in remaining if c.displayed_text == exp.text), None)
            if match is None:
                report.missed.append({"case": case.id, "text": exp.text})
                continue
            remaining.remove(match)
            report.detected += 1

            if match.metric == exp.metric and match.entity == exp.entity:
                report.attributed_correct += 1
                continue

            report.misattributions.append({
                "case": case.id,
                "text": exp.text,
                "expected": f"{exp.metric}@{exp.entity}",
                "got": f"{match.metric}@{match.entity}",
            })
            # The load-bearing guarantee: a mis-attributed figure is never traced.
            if verdict_by_claim.get(match.claim_id) == Verdict.TRACED:
                report.unsafe_traces.append({
                    "case": case.id, "text": exp.text, "got": f"{match.metric}@{match.entity}",
                })

    return report


# ---------------------------------------------------------------------------
# Rule-engine eval — recall (never miss a compliance flag) + precision (no spurious flags).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleCase:
    id: str
    documents: tuple[Document, ...]
    should_flag: tuple[str, ...]
    should_not_flag: tuple[str, ...]


@dataclass
class RulesReport:
    true_positive: int = 0
    false_positive: int = 0
    true_negative: int = 0
    false_negative: int = 0
    missed: list[dict] = field(default_factory=list)    # required rule that did not fire
    spurious: list[dict] = field(default_factory=list)  # forbidden rule that fired

    @property
    def rule_recall(self) -> float:
        denom = self.true_positive + self.false_negative
        return self.true_positive / denom if denom else 1.0

    @property
    def rule_precision(self) -> float:
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom else 1.0

    def as_dict(self) -> dict:
        return {
            "checked": self.true_positive + self.false_positive
            + self.true_negative + self.false_negative,
            "rule_recall": round(self.rule_recall, 4),
            "rule_precision": round(self.rule_precision, 4),
            "missed": self.missed,
            "spurious": self.spurious,
        }


def _build_rule_documents(tenant: str, case_data: dict) -> tuple[Document, ...]:
    docs: list[Document] = []
    for d in case_data["documents"]:
        claims = tuple(
            FigureClaim(
                claim_id=f"{d['id']}-c{i}",
                document_id=d["id"],
                entity=c.get("entity", d["entity"]),
                metric=c["metric"],
                period=c.get("period", d["period"]),
                displayed_text=c["text"],
                span=tuple(c["span"]) if c.get("span") else None,
            )
            for i, c in enumerate(d["claims"])
        )
        docs.append(
            Document(
                id=d["id"],
                tenant_id=tenant,
                title=d["id"],
                kind=DocumentKind(d.get("kind", "other")),
                text=d.get("text", ""),
                claims=claims,
            )
        )
    return tuple(docs)


def load_rules_golden(name: str = "rule_findings") -> tuple[dict, list[RuleCase]]:
    data = json.loads((_GOLDEN_DIR / f"{name}.json").read_text())
    cases = [
        RuleCase(
            id=c["id"],
            documents=_build_rule_documents(data["tenant"], c),
            should_flag=tuple(c.get("should_flag", ())),
            should_not_flag=tuple(c.get("should_not_flag", ())),
        )
        for c in data["cases"]
    ]
    return data, cases


def run_rules_eval(name: str = "rule_findings") -> RulesReport:
    """Score the deterministic rule engines against labeled documents.

    Each case's documents go through ``verify_close_pack`` so both per-document and
    cross-document rules fire; the set of raised rule ids is then scored against the
    case's ``should_flag`` (recall) and ``should_not_flag`` (precision) labels.
    """
    data, cases = load_rules_golden(name)
    service = AttestService()
    service.ingest_xbrl(load_fixture(data["filing_fixture"]), tenant_id=data["tenant"])

    report = RulesReport()
    for case in cases:
        results, cross_findings = service.engine.verify_close_pack(list(case.documents))
        fired = {f.rule for r in results for f in r.findings}
        fired |= {f.rule for f in cross_findings}

        for rule in case.should_flag:
            if rule in fired:
                report.true_positive += 1
            else:
                report.false_negative += 1
                report.missed.append({"case": case.id, "rule": rule})
        for rule in case.should_not_flag:
            if rule in fired:
                report.false_positive += 1
                report.spurious.append({"case": case.id, "rule": rule})
            else:
                report.true_negative += 1

    return report


# ---------------------------------------------------------------------------
# The gates — one source of truth shared by the CI test and the `attest eval` CLI.
# ---------------------------------------------------------------------------

# Figure tie-outs: a wrong number called traced is catastrophic.
MAX_FIGURE_FALSE_NEGATIVE_RATE = 0.0
MIN_EXACT_ACCURACY = 0.95
MIN_FLAG_PRECISION = 0.90
MIN_GOLDEN_CASES = 15

# Extraction edge: never under-detect, never let a mis-attribution be traced. The
# edge is *allowed* to be imperfect — attribution accuracy is a regression floor,
# not a demand for perfection, because the core is the safety net (gated separately).
MIN_DETECTION_RECALL = 1.0
MAX_UNSAFE_TRACES = 0
MIN_ATTRIBUTION_ACCURACY = 0.80

# Rule engines: a missed compliance flag is catastrophic; a spurious flag kills trust.
MIN_RULE_RECALL = 1.0
MIN_RULE_PRECISION = 1.0


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    metrics: dict


def run_gates() -> list[GateResult]:
    """Run every golden set and evaluate it against its deploy gate.

    Returns one :class:`GateResult` per surface. ``all(g.passed for g in run_gates())``
    is the single boolean the CI test and the CLI both decide on.
    """
    figures = run_eval("figure_tieouts")
    extraction = run_extraction_eval("extraction_attribution")
    rules = run_rules_eval("rule_findings")

    return [
        GateResult(
            name="figure_tieouts",
            passed=(
                figures.total >= MIN_GOLDEN_CASES
                and figures.figure_false_negative_rate <= MAX_FIGURE_FALSE_NEGATIVE_RATE
                and figures.exact_accuracy >= MIN_EXACT_ACCURACY
                and figures.flag_precision >= MIN_FLAG_PRECISION
            ),
            metrics=figures.as_dict(),
        ),
        GateResult(
            name="extraction_attribution",
            passed=(
                extraction.detection_recall >= MIN_DETECTION_RECALL
                and len(extraction.unsafe_traces) <= MAX_UNSAFE_TRACES
                and extraction.attribution_accuracy >= MIN_ATTRIBUTION_ACCURACY
            ),
            metrics=extraction.as_dict(),
        ),
        GateResult(
            name="rule_findings",
            passed=(
                rules.rule_recall >= MIN_RULE_RECALL
                and rules.rule_precision >= MIN_RULE_PRECISION
            ),
            metrics=rules.as_dict(),
        ),
    ]
