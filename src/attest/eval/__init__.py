"""The eval harness: golden datasets + per-check metrics that gate deploys."""

from attest.eval.harness import EvalCase, EvalReport, load_golden, run_eval

__all__ = ["EvalCase", "EvalReport", "load_golden", "run_eval"]
