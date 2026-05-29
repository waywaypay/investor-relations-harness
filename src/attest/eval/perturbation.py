"""Synthetic perturbation generator.

Mints figure-tie-out cases by applying *known mutations* to real filed values. The
label of each case is determined by the operation, not by running the verification
engine — so these cases can legitimately test the engine without the circularity
of grading its own homework.

Two hard guardrails encode the conversation that produced this module:

1. **Every case is tagged ``label_source="synthetic_perturbation"``** so the eval
   harness can bucket it separately from human-/EDGAR-labeled cases. Synthetic
   cases measure *robustness coverage* (does the engine catch a digit
   transposition?), never the headline *reliability* number shown to diligence.
2. **The generator only perturbs filed currency facts with a known quantum.** It
   skips percent/bps (whose correct label is rounding-policy dependent — the very
   thing under test) and non-filed sources like guidance (which are
   ``needs_review`` by nature). Those belong to the human-labeled core.

This generates *coverage, not truth*: it cannot invent the realistic distribution
of how disclosures actually go wrong (that comes from real 8-K Item 4.02
restatements), and it never produces the judgment-call cases that are the corpus's
actual moat.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from attest.domain.facts import Fact
from attest.domain.money import Unit, parse_quantity
from attest.domain.verdicts import Verdict

LABEL_SOURCE = "synthetic_perturbation"


@dataclass(frozen=True)
class SyntheticCase:
    """A generated figure case with a by-construction label."""

    id: str
    filing_ref: str
    entity: str
    metric: str
    period: str
    text: str
    expected: Verdict
    operation: str
    rationale: str
    label_source: str = LABEL_SOURCE

    def as_golden_row(self) -> dict:
        """Render in the figure_tieouts.json case schema (plus provenance fields)."""
        return {
            "id": self.id,
            "entity": self.entity,
            "metric": self.metric,
            "period": self.period,
            "text": self.text,
            "expected": self.expected.value,
            "operation": self.operation,
            "rationale": self.rationale,
            "label_source": self.label_source,
        }


def _fmt_millions(value: Decimal, decimals: int = 1) -> str:
    """Render a base-unit currency value as '$X.Y million'."""
    millions = value / Decimal(1_000_000)
    return f"${millions:.{decimals}f} million"


def _fmt_billions(value: Decimal, decimals: int = 2) -> str:
    billions = value / Decimal(1_000_000_000)
    return f"${billions:.{decimals}f} billion"


def _transpose_first_two_significant(value: Decimal) -> Decimal | None:
    """Swap the first two significant digits (e.g. 1241.3M-ish -> 2141...). Returns
    None if the swap doesn't change the value (e.g. leading digits equal)."""
    digits = list(str(int(value)))
    if len(digits) < 2 or digits[0] == digits[1]:
        return None
    digits[0], digits[1] = digits[1], digits[0]
    return Decimal("".join(digits))


def perturb_fact(fact: Fact) -> list[SyntheticCase]:
    """Generate synthetic figure cases from a single filed currency fact.

    Returns [] for facts that aren't clean perturbation targets (non-filed sources,
    non-currency units, or values too small to mutate meaningfully).
    """
    if not fact.is_filed:
        return []
    if fact.unit != Unit.CURRENCY:
        return []
    base = fact.value
    if base <= 0 or base < Decimal(1_000_000):
        # Below ~$1M the scale-word renderings below aren't meaningful; skip.
        return []

    prefix = f"syn_{fact.metric}_{fact.period}"
    cases: list[SyntheticCase] = []

    def add(op: str, text: str, verdict: Verdict, why: str) -> None:
        # Self-check: the rendered text must parse. If a mutation can't be rendered
        # cleanly we drop it rather than emit an unparseable (and thus mislabeled) case.
        try:
            parse_quantity(text)
        except Exception:
            return
        cases.append(
            SyntheticCase(
                id=f"{prefix}_{op}", filing_ref=fact.source_ref, entity=fact.entity,
                metric=fact.metric, period=fact.period, text=text,
                expected=verdict, operation=op, rationale=why,
            )
        )

    # -- identity (TRACED by construction): same value, different scale word -----
    add(
        "identity_reformat",
        _fmt_millions(base, decimals=1),
        Verdict.TRACED,
        "Same filed value re-expressed in millions; must tie out within policy.",
    )

    # -- scale errors (CONFLICT): decimal shifted by 1000 in each direction ------
    add(
        "scale_error_div1000",
        _fmt_millions(base / Decimal(1000), decimals=2),
        Verdict.CONFLICT,
        "Value divided by 1000 (thousands/millions confusion).",
    )
    add(
        "scale_error_x1000",
        _fmt_billions(base * Decimal(1000), decimals=2),
        Verdict.CONFLICT,
        "Value multiplied by 1000 (millions/billions confusion).",
    )

    # -- digit transposition (CONFLICT) -----------------------------------------
    transposed = _transpose_first_two_significant(base)
    if transposed is not None:
        add(
            "digit_transpose",
            _fmt_millions(transposed, decimals=1),
            Verdict.CONFLICT,
            "First two significant digits transposed.",
        )

    # -- magnitude typo (CONFLICT): ~15% off, beyond any rounding tolerance ------
    add(
        "magnitude_typo",
        _fmt_millions(base * Decimal("1.15"), decimals=1),
        Verdict.CONFLICT,
        "Value off by ~15%, well outside rounding tolerance.",
    )

    return cases


def perturb_facts(facts: list[Fact]) -> list[SyntheticCase]:
    """Generate synthetic cases across many facts.

    Restatement-aware: when a scope ``(tenant, entity, metric, period)`` has
    multiple versions, only the latest (by ``as_of``) is perturbed. Perturbing a
    superseded value would mislabel — the engine binds against the latest, so an
    "identity" reformat of an old value is correctly a conflict, not traced.
    """
    latest_by_scope: dict[tuple, Fact] = {}
    for f in facts:
        key = f.scope_key()
        current = latest_by_scope.get(key)
        if current is None or f.as_of > current.as_of:
            latest_by_scope[key] = f

    out: list[SyntheticCase] = []
    for f in latest_by_scope.values():
        out.extend(perturb_fact(f))
    return out
