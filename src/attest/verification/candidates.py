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

# Greedy: currency with optional scale word, percentages, basis points.
_CANDIDATE_RE = re.compile(
    r"""
    (?P<cur>\$\s?\d[\d,]*(?:\.\d+)?\s*(?:billion|million|thousand|trillion|bn|mm|[bmkt])?\b)
    | (?P<pct>\b\d{1,3}(?:\.\d+)?\s?%)
    | (?P<bps>\b\d{1,4}(?:\.\d+)?\s?(?:bps|basis\ points)\b)
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
