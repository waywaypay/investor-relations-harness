"""The eval harness: golden datasets + per-surface metrics that gate deploys."""

from attest.eval.harness import (
    EvalCase,
    EvalReport,
    ExtractionCase,
    ExtractionReport,
    GateResult,
    RuleCase,
    RulesReport,
    load_golden,
    run_eval,
    run_extraction_eval,
    run_gates,
    run_rules_eval,
)

__all__ = [
    "EvalCase",
    "EvalReport",
    "ExtractionCase",
    "ExtractionReport",
    "RuleCase",
    "RulesReport",
    "GateResult",
    "load_golden",
    "run_eval",
    "run_extraction_eval",
    "run_rules_eval",
    "run_gates",
]
