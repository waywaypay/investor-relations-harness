"""Candidate detection — the one place imperfection is allowed.

This is the probabilistic edge's job (regex here; an LLM in production). The
guidance from the architecture is explicit: over-detection is fine, *under*
detection is the failure mode to tune against. A missed number is a number that
silently ships unverified. So this detector is deliberately greedy; the
deterministic core downstream is what refuses to call anything "traced".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from attest.domain.money import Quantity, QuantityParseError, parse_quantity

# Greedy: currency with optional scale word, percentages, basis points, and
# bare numbers carrying a full scale word ("1,000 million" — how a rendered
# statement table states a $-less cell). Negatives are first-class: a leading
# minus ("-$1,409", "-12.4%") and the financial-statement convention of a
# dollar sign *outside* parentheses ("$ (1,409 )", "$(1.53)") both detect. A
# dollar sign *inside* parentheses ("($1.2 billion)") stays a positive figure
# — in prose that is an aside, not a negative.
_SCALE_WORDS = r"(?:billion|million|thousand|trillion|bn|mm|[bmkt])"
_FULL_SCALE_WORDS = r"(?:billion|million|thousand|trillion)"
_CANDIDATE_RE = re.compile(
    rf"""
    (?P<cur>(?<![\d.])-?\$\s?\(\s?\d[\d,]*(?:\.\d+)?\s*{_SCALE_WORDS}?\s?\)   # $ (1,409) — table negative
        | (?<![\d.])-?\$\s?-?\d[\d,]*(?:\.\d+)?\s*{_SCALE_WORDS}?\b
        | (?<![\d.$])-?\b\d[\d,]*(?:\.\d+)?\s{_FULL_SCALE_WORDS}\b)          # bare "1,000 million"
    | (?P<pct>(?<![\d.])-?\b\d{{1,3}}(?:\.\d+)?\s?%)
    | (?P<bps>\b\d{{1,4}}(?:\.\d+)?\s?(?:bps|basis\ points)\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class Candidate:
    """A raw numeric span found in prose, before any binding to a fact."""

    text: str
    span: tuple[int, int]
    quantity: Quantity | None  # None when the span could not be normalized


def detect_candidates(text: str) -> list[Candidate]:
    """Find every plausible figure in ``text``.

    Each candidate carries its character span and, when parseable, a normalized
    :class:`Quantity`. Spans that look numeric but resist normalization are still
    returned (with ``quantity=None``) so nothing is silently dropped.
    """
    candidates: list[Candidate] = []
    for m in _CANDIDATE_RE.finditer(text):
        raw = m.group(0).strip()
        try:
            qty: Quantity | None = parse_quantity(raw)
        except QuantityParseError:
            qty = None
        candidates.append(Candidate(text=raw, span=(m.start(), m.end()), quantity=qty))
    return candidates
