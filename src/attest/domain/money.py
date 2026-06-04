"""Value normalization — units, scale, and a deterministic rounding policy.

This module is the heart of "deterministic core, probabilistic edge". An LLM may
*locate* a numeric span in prose; this module decides, with no model in the loop,
whether the span the drafter wrote is the same value as the one in the filing —
including the rounding the IR team applied on the way (``$1,241.3M`` -> ``$1.24B``).

Everything is computed with :class:`decimal.Decimal` so that rounding is exact and
reproducible: a tie-out must give the same answer on every machine, every time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum


class Unit(str, Enum):
    """The dimension a quantity is measured in.

    Two quantities can only be compared when their units match. ``CURRENCY`` is
    always normalized to base units (e.g. dollars, not millions of dollars) so
    that scale words ("million", "billion") never affect equality.
    """

    CURRENCY = "currency"
    PERCENT = "percent"
    BASIS_POINTS = "basis_points"
    SHARES = "shares"
    RATIO = "ratio"
    COUNT = "count"


class QuantityParseError(ValueError):
    """Raised when a string cannot be parsed into a :class:`Quantity`."""


# Scale words -> multiplier applied to reach base units.
_SCALES: dict[str, Decimal] = {
    "thousand": Decimal(1_000),
    "thousands": Decimal(1_000),
    "k": Decimal(1_000),
    "million": Decimal(1_000_000),
    "millions": Decimal(1_000_000),
    "m": Decimal(1_000_000),
    "mm": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
    "billions": Decimal(1_000_000_000),
    "b": Decimal(1_000_000_000),
    "bn": Decimal(1_000_000_000),
    "trillion": Decimal(1_000_000_000_000),
    "t": Decimal(1_000_000_000_000),
}

_NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?"
_SCALE_WORDS = "|".join(sorted(_SCALES, key=len, reverse=True))

_RE_PERCENT = re.compile(rf"^\(?\s*(?P<num>{_NUMBER})\s*(?:%|percent|pct)\s*\)?$", re.IGNORECASE)
_RE_BPS = re.compile(rf"^\(?\s*(?P<num>{_NUMBER})\s*(?:bps|basis points)\s*\)?$", re.IGNORECASE)
# Currency may be symbol-led ("$1.24 billion") or spelled ("1.24 billion dollars",
# "87 cents"); the trailing money word is optional and ignored once matched.
_RE_CURRENCY = re.compile(
    rf"^\(?\s*(?:US)?\$?\s*(?P<num>{_NUMBER})\s*(?P<scale>{_SCALE_WORDS})?\s*(?P<money>dollars|cents)?\s*\)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Quantity:
    """A normalized numeric value with a unit and the precision it was stated at.

    ``value`` is always in base units (dollars, percent, shares, ...). ``quantum``
    is the place value of the least-significant digit *as written* — e.g. a draft
    that says ``$1.24 billion`` carries ``value=1_240_000_000`` and
    ``quantum=10_000_000`` (two decimals at billions scale). The quantum is what
    lets us round a filed value to the precision the drafter chose.
    """

    value: Decimal
    unit: Unit
    quantum: Decimal

    def matches(self, source: Quantity, policy: RoundingPolicy) -> bool:
        """Return True iff ``source`` reduces to this quantity under ``policy``.

        ``self`` is the value *as written in the draft*; ``source`` is the value
        *as filed/booked*. The match is exact-with-tolerance, never fuzzy: the
        source is rounded to the draft's precision (and, if configured, allowed a
        relative tolerance) and compared for exact equality.
        """
        if self.unit != source.unit:
            return False
        rounded = policy.round_to(source.value, self.quantum)
        if rounded == self.value:
            return True
        if policy.relative_tolerance > 0 and source.value != 0:
            rel = abs(self.value - source.value) / abs(source.value)
            return rel <= policy.relative_tolerance
        return False

    def display(self) -> str:
        """A canonical, human-readable rendering (mostly for diagnostics)."""
        if self.unit == Unit.PERCENT:
            return f"{_strip(self.value)}%"
        if self.unit == Unit.BASIS_POINTS:
            return f"{_strip(self.value)} bps"
        if self.unit == Unit.CURRENCY:
            return f"${_strip(self.value)}"
        return _strip(self.value)


@dataclass(frozen=True)
class RoundingPolicy:
    """A tenant's tolerance for rounding between a filing and a release.

    The primary rule is precision-based ("round the source to the draft's
    quantum"). ``relative_tolerance`` is an optional secondary allowance (e.g.
    ``0.0005`` for 5bps) for buyers whose policy permits it; it defaults to zero,
    keeping tie-outs strict unless a tenant opts in.
    """

    relative_tolerance: Decimal = Decimal(0)
    rounding = ROUND_HALF_UP

    @staticmethod
    def round_to(value: Decimal, quantum: Decimal) -> Decimal:
        """Round ``value`` to the given ``quantum`` using half-up (disclosure norm)."""
        if quantum <= 0:
            return value
        return (value / quantum).quantize(Decimal(1), rounding=ROUND_HALF_UP) * quantum


# A sensible default: strict, precision-only matching.
DEFAULT_POLICY = RoundingPolicy()


def _strip(value: Decimal) -> str:
    """Render a Decimal without scientific notation or trailing zeros."""
    text = format(value.normalize(), "f")
    return text


def _to_decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise QuantityParseError(f"not a number: {raw!r}") from exc


def _quantum_for(num_text: str, multiplier: Decimal) -> Decimal:
    """Determine the place value of the least-significant written digit.

    ``"1.24"`` at billions (multiplier 1e9) -> 1e9 * 1e-2 = 1e7.
    ``"31"`` (percent, multiplier 1) -> 1.
    """
    cleaned = num_text.replace(",", "").strip()
    if "." in cleaned:
        decimals = len(cleaned.split(".", 1)[1])
    else:
        decimals = 0
    return multiplier * (Decimal(10) ** (-decimals))


def parse_quantity(text: str) -> Quantity:
    """Parse a disclosure figure string into a normalized :class:`Quantity`.

    Handles currency with scale words (``$1.24 billion``, ``$1,241.3 million``,
    ``$0.87``), percentages (``31%``), basis points (``50 bps``) and parenthesised
    negatives (``(250.0)`` -> ``-250``). Raises :class:`QuantityParseError` on
    anything it cannot confidently normalize — we never guess.
    """
    if text is None:
        raise QuantityParseError("cannot parse None")
    s = text.strip()
    if not s:
        raise QuantityParseError("cannot parse empty string")

    negative_paren = s.startswith("(") and s.endswith(")")

    m = _RE_PERCENT.match(s)
    if m:
        num = m.group("num")
        value = _to_decimal(num)
        if negative_paren:
            value = -value
        return Quantity(value=value, unit=Unit.PERCENT, quantum=_quantum_for(num, Decimal(1)))

    m = _RE_BPS.match(s)
    if m:
        num = m.group("num")
        value = _to_decimal(num)
        if negative_paren:
            value = -value
        return Quantity(value=value, unit=Unit.BASIS_POINTS, quantum=_quantum_for(num, Decimal(1)))

    m = _RE_CURRENCY.match(s)
    if m:
        num = m.group("num")
        scale = (m.group("scale") or "").lower()
        multiplier = _SCALES.get(scale, Decimal(1))
        # "87 cents" is $0.87 — a hundredth of a dollar, not 87 of them.
        if (m.group("money") or "").lower() == "cents":
            multiplier = multiplier / Decimal(100)
        value = _to_decimal(num) * multiplier
        if negative_paren:
            value = -value
        return Quantity(value=value, unit=Unit.CURRENCY, quantum=_quantum_for(num, multiplier))

    raise QuantityParseError(f"unrecognized figure format: {text!r}")
