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

from attest.domain.money import NOUN_WORDS, Quantity, QuantityParseError, parse_quantity

# Greedy: currency with optional scale word, percentages, basis points.
#
# Two figure dialects show up in practice. Press releases write the symbols
# ("$1.24 billion", "31%"); earnings-call transcripts (and pasted prose) spell
# them ("1.24 billion dollars", "31 percent", bare "480 million"). Under-detection
# is the failure mode, so both are caught. The symbol-free currency branches
# *require* a scale word or the word "dollars" as an anchor — a bare integer like
# a year ("2026") must not read as money.
#
# The ``count`` branch comes before ``curscale`` and captures the trailing noun
# ("100 million shares", "480 million users") so the value layer can type it as a
# share/unit count, not money — otherwise the bare "100 million" reads as $100M
# and is tied out against a dollar metric. Order matters: a count noun wins over
# the symbol-free currency branch at the same position.
# A leading negative marker: a minus and/or an opening paren (accounting writes a
# loss/decline as "($0.12)", "$(0.12)", "(5)%"). Optionally an inner "(" after the
# currency symbol ("$(0.12)"). Each numeric branch also allows a ")" right after the
# digits — so an external scale word survives ("$(45) million") — and a ")" at the
# very end. ``parse_quantity`` resolves the sign from the parens it sees; the
# detector's job is only to not drop the span (a missed loss ships unverified, and a
# stripped-paren positive is a *wrong-signed* number — both are core failures).
_NEG = r"[-(]?"          # optional leading minus or open-paren
_CP = r"\)?"            # optional close-paren
_CANDIDATE_RE = re.compile(
    rf"""
    (?P<cur>{_NEG}\$\(?\s?\d[\d,]*(?:\.\d+)?{_CP}\s*(?:billion|million|thousand|trillion|bn|mm|[bmkt])?(?![A-Za-z]){_CP})
    | (?P<count>{_NEG}\b\d[\d,]*(?:\.\d+)?\s*(?:billion|million|thousand|trillion|bn|mm)?\s*(?:{NOUN_WORDS})\b{_CP})
    | (?P<curscale>{_NEG}\b\d[\d,]*(?:\.\d+)?{_CP}\s*(?:billion|million|thousand|trillion|bn|mm)\b{_CP})
    | (?P<curword>{_NEG}\b\d[\d,]*(?:\.\d+)?\s+(?:dollars|cents)\b{_CP})
    | (?P<pct>{_NEG}\b\d{{1,3}}(?:\.\d+)?{_CP}\s?(?:%|percent\b|pct\b){_CP})
    | (?P<bps>{_NEG}\b\d{{1,4}}(?:\.\d+)?{_CP}\s?(?:bps|basis\ points)\b{_CP})
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
        start, end = m.start(), m.end()
        raw = m.group(0)
        # A balanced "(...)" is accounting's negative; an *unbalanced* paren the
        # greedy branch swept up is just grammar (a parenthetical aside, "($1.12"
        # or "18%)"). Trim the lone paren — and move the span with it — so the
        # highlight is clean and the sign is read only from a real, balanced pair.
        if raw.startswith("(") and ")" not in raw:
            raw, start = raw[1:], start + 1
        if raw.endswith(")") and "(" not in raw:
            raw, end = raw[:-1], end - 1
        raw = raw.strip()
        try:
            qty: Quantity | None = parse_quantity(raw)
        except QuantityParseError:
            qty = None
        candidates.append(Candidate(text=raw, span=(start, end), quantity=qty))
    return candidates
