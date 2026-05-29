"""Guidance-range sanity and midpoint consistency.

Forward guidance is usually a range ("$1.31 to $1.34 billion"). Two deterministic
checks apply that a single-figure tie-out cannot, because the range never matches
a filed source:

* **Ordering** — the low end must not exceed the high end.
* **Midpoint** — when a separate "midpoint" figure is also stated, it must equal
  the arithmetic midpoint of the range.

The range parser is tolerant of a shared scale word at the end ("$1.31 to $1.34
billion" applies "billion" to both ends) and of an en-dash or hyphen separator.
"""

from __future__ import annotations

import re

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry
from attest.domain.money import (
    DEFAULT_POLICY,
    Quantity,
    QuantityParseError,
    parse_quantity,
)
from attest.domain.verdicts import RuleFinding, RuleSeverity

_SEP = re.compile(r"\s*(?:to|through|[‒–—-])\s*", re.IGNORECASE)
_SCALE_TAIL = re.compile(
    r"\b(billion|million|thousand|trillion|bn|mm|[bmkt])\b\s*$", re.IGNORECASE
)


def parse_range(text: str) -> tuple[Quantity, Quantity] | None:
    """Parse 'LOW to HIGH' into two normalized quantities, or None if not a range.

    Applies a trailing scale word to the low end when the low end omits its own
    (e.g. "$1.31 to $1.34 billion" -> low "$1.31 billion", high "$1.34 billion").
    """
    parts = _SEP.split(text.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    low_raw, high_raw = parts[0].strip(), parts[1].strip()

    tail = _SCALE_TAIL.search(high_raw)
    if tail and not _SCALE_TAIL.search(low_raw):
        low_raw = f"{low_raw} {tail.group(1)}"

    try:
        low, high = parse_quantity(low_raw), parse_quantity(high_raw)
    except QuantityParseError:
        return None
    if low.unit != high.unit:
        return None
    return low, high


def check_range_sanity(document: Document, registry: MetricRegistry) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for claim in document.claims:
        spec = registry.get(claim.metric)
        parsed = parse_range(claim.displayed_text)
        if parsed is None:
            continue
        low, high = parsed
        if low.value > high.value:
            label = spec.label if spec else claim.metric
            findings.append(
                RuleFinding(
                    rule="ranges.inverted_range",
                    severity=RuleSeverity.BLOCK,
                    document_id=document.id,
                    metric=claim.metric,
                    message=f"'{label}' range is inverted: low {low.display()} exceeds "
                    f"high {high.display()}.",
                    detail=f"Stated as '{claim.displayed_text}'.",
                )
            )
    return findings


_MIDPOINT_RE = re.compile(
    r"midpoint[^$%\d]{0,20}?"
    r"(\$?\s?\d[\d,]*(?:\.\d+)?\s*(?:billion|million|thousand|trillion|bn|mm|[bmkt])?|"
    r"\d{1,3}(?:\.\d+)?\s?%)",
    re.IGNORECASE,
)


def extract_stated_midpoints(document: Document) -> dict[str, str]:
    """Pull a 'midpoint of <figure>' phrase from the prose, mapped to range metrics.

    A document typically states at most one guidance midpoint; we attach it to any
    range-valued claim in the document. Returns {} when no midpoint phrase exists.
    """
    m = _MIDPOINT_RE.search(document.text or "")
    if not m:
        return {}
    midpoint_text = m.group(1).strip()
    mapping: dict[str, str] = {}
    for claim in document.claims:
        if parse_range(claim.displayed_text) is not None:
            mapping[claim.metric] = midpoint_text
    return mapping


def check_range_midpoint(
    document: Document, registry: MetricRegistry, midpoints: dict[str, str] | None = None
) -> list[RuleFinding]:
    """Verify a separately-stated midpoint equals the arithmetic midpoint of a range.

    ``midpoints`` maps a claim metric to the midpoint figure text stated for it.
    When omitted, the midpoint is extracted from the document prose.
    """
    if midpoints is None:
        midpoints = extract_stated_midpoints(document)
    findings: list[RuleFinding] = []
    for claim in document.claims:
        parsed = parse_range(claim.displayed_text)
        if parsed is None or claim.metric not in midpoints:
            continue
        low, high = parsed
        try:
            stated = parse_quantity(midpoints[claim.metric])
        except QuantityParseError:
            continue
        if stated.unit != low.unit:
            continue
        expected_value = (low.value + high.value) / 2
        expected = Quantity(value=expected_value, unit=low.unit, quantum=stated.quantum)
        if stated.matches(expected, DEFAULT_POLICY):
            continue
        rounded = DEFAULT_POLICY.round_to(expected_value, stated.quantum)
        spec = registry.get(claim.metric)
        label = spec.label if spec else claim.metric
        findings.append(
            RuleFinding(
                rule="ranges.midpoint_mismatch",
                severity=RuleSeverity.BLOCK,
                document_id=document.id,
                metric=claim.metric,
                message=f"Stated midpoint {midpoints[claim.metric]} for '{label}' does not "
                f"match the range midpoint ({rounded}).",
                detail=f"Range '{claim.displayed_text}' has midpoint {rounded}.",
            )
        )
    return findings
