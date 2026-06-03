"""Forward-guidance ingestion from 8-K Exhibit 99.1 press-release prose.

XBRL is a gift for the *reported* numbers — they arrive machine-tagged. Forward
guidance is the exception: management states its next-period revenue / EPS /
operating-margin outlook in the press release's *prose*, and no XBRL fact captures
it. This connector is the prose analog of :class:`~attest.ingestion.edgar_xbrl.XBRLConnector`:
it consumes the EX-99.1 text (the bytes the SEC connector already fetches) and
turns each guidance statement into a citable :class:`Fact`.

The capability that matters is the citation: every emitted fact carries the *exact
sentence* it came from in ``source_excerpt`` and points back to the filed exhibit
in ``source_ref``, so the spine can answer "here is the number management gave,
and here is the line it came from" — the anti-"cited the wrong number" guarantee
applied to the one figure class that isn't otherwise verifiable.

Like every connector it is deterministic and model-free, and it *never guesses*:
it emits a fact only when it can both attribute a metric (by keyword + unit) and
parse a figure (with the same :func:`parse_quantity` the rest of the spine uses).
Guidance stated as a range is normalized to its arithmetic midpoint — the
canonical single value — while the excerpt preserves the range verbatim.
Sentences it recognizes as guidance but cannot resolve are reported as skipped.

This is one connector, not the core. Swapping the keyword heuristic for an LLM
changes nothing downstream: the deterministic spine still disposes of every fact
it emits.
"""

from __future__ import annotations

import re
from pathlib import Path

from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.domain.money import Quantity, QuantityParseError, Unit, parse_quantity
from attest.ingestion.base import IngestionReport
from attest.verification.rules.ranges import parse_range

_FIXTURE_DIR = Path(__file__).parent / "fixtures"

# Forward-looking verbs/nouns that mark a sentence as *guidance* rather than a
# report of an actual. Deliberately verb-driven: "for the second quarter" alone is
# a period cue that also appears in actuals ("for the first quarter revenue was"),
# so it is not a trigger on its own.
_GUIDANCE = re.compile(
    r"\b(expects?|anticipates?|outlook|guidance|forecasts?|projects?|we see|"
    r"guides?|reaffirms?|reiterates?|raises?|raising|now sees?)\b",
    re.IGNORECASE,
)

# Metric attribution within a guidance sentence, most specific first. Each entry is
# (metric_id, expected unit, keyword). EPS precedes revenue so "earnings per share"
# is never swallowed by a stray "sales"/"revenue" elsewhere in the same sentence.
_METRIC_KEYWORDS: tuple[tuple[str, Unit, re.Pattern[str]], ...] = (
    ("eps_guidance", Unit.CURRENCY, re.compile(r"\b(eps|earnings per share|per[- ]share)\b", re.IGNORECASE)),
    ("operating_margin_guidance", Unit.PERCENT, re.compile(r"\boperating margin|margin\b", re.IGNORECASE)),
    ("revenue_guidance", Unit.CURRENCY, re.compile(r"\b(total revenue|net revenue|revenue|net sales|sales|topline)\b", re.IGNORECASE)),
)

# Figure shapes, gated by unit so a percent is never read as currency or vice versa.
_CUR_TOKEN = r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:billion|million|thousand|trillion|bn|mm|[bmkt])?"
_CUR_RANGE = re.compile(
    rf"{_CUR_TOKEN}\s*(?:to|through|[-–—])\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:billion|million|thousand|trillion|bn|mm|[bmkt])?",
    re.IGNORECASE,
)
_CUR_SINGLE = re.compile(_CUR_TOKEN, re.IGNORECASE)
_PCT_RANGE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*%?\s*(?:to|through|[-–—])\s*\d[\d,]*(?:\.\d+)?\s*%",
    re.IGNORECASE,
)
_PCT_SINGLE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*%", re.IGNORECASE)

_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
_FULL_YEAR = re.compile(r"\b(full[- ]year|full fiscal year|fiscal year|for the year)\b", re.IGNORECASE)
_QUARTER = re.compile(r"\b(first|second|third|fourth)[\s-]+quarter\b", re.IGNORECASE)
_FY_IN_TEXT = re.compile(r"(?:fiscal(?:\s+year)?|fy)\s*(20\d\d)", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def load_press_release(name: str) -> str:
    """Load a bundled press-release text fixture by name (without extension)."""
    return (_FIXTURE_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _iter_sentences(text: str):
    """Yield each sentence as stripped prose, preserving its internal text."""
    pos = 0
    for m in _SENTENCE_BREAK.finditer(text):
        chunk = text[pos:m.start()].strip()
        if chunk:
            yield chunk
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        yield tail


def _base_year(base_period: str | None) -> int | None:
    m = re.match(r"FY(\d{4})", base_period or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


def _next_quarter_period(base_period: str | None) -> str | None:
    m = re.match(r"FY(\d{4})-Q([1-4])", base_period or "", re.IGNORECASE)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    return f"FY{year + 1}-Q1" if q == 4 else f"FY{year}-Q{q + 1}"


def _guidance_period(sentence: str, base_period: str | None) -> str | None:
    """Resolve the *target* period a guidance sentence is about.

    Prefers an explicit cue in the sentence — "full year fiscal 2026" -> ``FY2026-FY``,
    "second quarter" -> ``FY2026-Q2`` (year from the sentence, else the filing's
    base period). Falls back to the quarter immediately after the base period, which
    is what an undecorated "the company expects revenue of ..." refers to.
    """
    year_m = _FY_IN_TEXT.search(sentence)
    year = int(year_m.group(1)) if year_m else _base_year(base_period)

    if _FULL_YEAR.search(sentence):
        return f"FY{year}-FY" if year else None
    qm = _QUARTER.search(sentence)
    if qm and year:
        return f"FY{year}-Q{_QUARTER_WORDS[qm.group(1).lower()]}"
    return _next_quarter_period(base_period)


def _midpoint(low: Quantity, high: Quantity) -> Quantity:
    """The arithmetic midpoint of a range, at the precision of its low end."""
    return Quantity(value=(low.value + high.value) / 2, unit=low.unit, quantum=low.quantum)


def _parse_figure(region: str, unit: Unit) -> Quantity | None:
    """Pull a single guidance value from a sentence region for the given unit.

    A range collapses to its midpoint (the canonical single value); a lone figure
    parses directly. Returns ``None`` when nothing of the right unit is present, so
    the caller can skip rather than guess.
    """
    if unit is Unit.CURRENCY:
        rng = _CUR_RANGE.search(region)
        if rng and (parsed := parse_range(rng.group(0))) is not None:
            return _midpoint(*parsed)
        single = _CUR_SINGLE.search(region)
        candidate = single.group(0) if single else None
    elif unit is Unit.PERCENT:
        rng = _PCT_RANGE.search(region)
        if rng and (parsed := parse_range(rng.group(0))) is not None:
            return _midpoint(*parsed)
        single = _PCT_SINGLE.search(region)
        candidate = single.group(0) if single else None
    else:  # pragma: no cover - guidance metrics are only currency/percent today
        return None

    if not candidate:
        return None
    try:
        qty = parse_quantity(candidate)
    except QuantityParseError:
        return None
    return qty if qty.unit is unit else None


class GuidanceConnector:
    """Maps 8-K EX-99.1 press-release prose into citable guidance facts."""

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or DEFAULT_REGISTRY

    def fetch(
        self,
        *,
        text: str,
        tenant_id: str,
        entity: str,
        accession: str,
        base_period: str | None = None,
        as_of: str = "1970-01-01",
        label: str | None = None,
    ) -> tuple[list[Fact], IngestionReport]:
        """Extract forward guidance from a press release into facts-with-provenance.

        ``base_period`` is the fiscal period the filing reports (e.g. ``FY2026-Q1``);
        it anchors period inference for guidance sentences that only say "the company
        expects". ``accession`` and ``label`` describe the source for the citation.
        """
        source_ref = f"{accession}#exhibit-99.1"
        source_label = label or "Form 8-K · Exhibit 99.1 · forward guidance"

        facts: list[Fact] = []
        seen: set[tuple[str, str, str, str]] = set()
        skipped: list[str] = []

        for sentence in _iter_sentences(text):
            if not _GUIDANCE.search(sentence):
                continue

            period = _guidance_period(sentence, base_period)
            extracted_here = 0

            # Carve the sentence into regions, one per metric keyword present, so a
            # sentence naming two metrics attributes each figure to the right one.
            hits = [
                (km.start(), metric, unit)
                for metric, unit, pattern in _METRIC_KEYWORDS
                if (km := pattern.search(sentence)) is not None
            ]
            hits.sort()
            for i, (start, metric, unit) in enumerate(hits):
                end = hits[i + 1][0] if i + 1 < len(hits) else len(sentence)
                qty = _parse_figure(sentence[start:end], unit)
                if qty is None or period is None:
                    continue
                scope = (tenant_id, entity, metric, period)
                if scope in seen:  # first statement of a scope wins; reiterations dedupe
                    continue
                seen.add(scope)
                facts.append(
                    Fact(
                        id=f"{accession}:exhibit-99.1:{metric}:{period}",
                        tenant_id=tenant_id,
                        entity=entity,
                        metric=metric,
                        period=period,
                        value=qty.value,
                        unit=qty.unit,
                        quantum=qty.quantum,
                        source_type=SourceType.FILING_LINE,
                        source_ref=source_ref,
                        source_label=source_label,
                        source_excerpt=sentence,
                        as_of=as_of,
                        confidence=Confidence.HIGH,
                    )
                )
                extracted_here += 1

            if extracted_here == 0:
                # Recognized as guidance prose but nothing resolved — surface it.
                skipped.append(sentence[:60])

        report = IngestionReport(
            source=f"guidance_8k:{accession}",
            tenant_id=tenant_id,
            ingested=len(facts),
            skipped=len(skipped),
            skipped_tags=tuple(skipped),
        )
        return facts, report
