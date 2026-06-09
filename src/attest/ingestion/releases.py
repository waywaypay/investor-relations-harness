"""Quarterly earnings press-release retrieval — the shared record and helpers.

The spine consumes *documents* (the analyze pipeline) and *facts* (the
connectors). Quarterly earnings press releases arrive one step upstream of
both: something has to enumerate which releases exist and recover their full
text before the edge can propose a single figure. The two fetchers —
:mod:`attest.ingestion.edgar_releases` (deterministic, authoritative) and
:mod:`attest.ingestion.exa_releases` (search-assisted, tightly constrained) —
both land on the :class:`EarningsRelease` record defined here, so everything
downstream is indifferent to which road the text arrived by.

One hard-won lesson is encoded in :attr:`EarningsRelease.figure_count`: a
"press release" whose text contains no detectable figures is almost never a
quiet quarter — it is the wrong artifact. Either the advisory sibling
("Company *to Announce* ... Results", which never carries a number) or a
JavaScript shell page whose cached crawl is navigation chrome. Fetchers
surface the count instead of letting an empty extraction pass silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from attest.verification.candidates import detect_candidates

ORDINALS = {1: "First", 2: "Second", 3: "Third", 4: "Fourth"}
_QUARTER_WORDS = {word.lower(): n for n, word in ORDINALS.items()}

# "Third Quarter 2025" / "Fourth Quarter and Full Year 2025" as stated in the
# release's own title — the issuer's fiscal labelling, which always wins over
# any calendar-date fallback.
_TITLE_QUARTER = re.compile(
    r"\b(first|second|third|fourth)\s+quarter\b[^.\n]{0,60}?\b(20\d\d)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EarningsRelease:
    """One quarter's earnings press release, with full recovered text."""

    entity: str
    title: str
    period: str | None  # "FY2025-Q3" when inferable, else None — never guessed
    url: str
    text: str
    accession: str | None = None
    filing_date: str | None = None
    report_date: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def figure_count(self) -> int:
        """How many numeric spans the edge's detector finds in the text.

        Zero means the artifact is wrong (advisory, shell page, failed
        extraction) — an earnings release without figures does not exist.
        """
        return len(detect_candidates(self.text))


class ReleaseFetchReport(BaseModel):
    """Honest account of a retrieval run: what was asked, found, and not."""

    source: str
    requested: int
    fetched: int
    missing: tuple[str, ...] = ()


def infer_period(head: str, report_date: str | None = None) -> str | None:
    """Resolve a release's fiscal period label, never guessing.

    The quarter the release *states in its own title* ("Meta Reports Third
    Quarter 2025 Results" -> ``FY2025-Q3``) is authoritative — it is the
    issuer's fiscal labelling. The 8-K's period-of-report date is the fallback,
    mapped onto calendar quarters (right for calendar-fiscal issuers; off-cycle
    issuers resolve via the stated-title path). Returns ``None`` when neither
    is available.
    """
    m = _TITLE_QUARTER.search(head)
    if m:
        return f"FY{m.group(2)}-Q{_QUARTER_WORDS[m.group(1).lower()]}"
    if report_date:
        m = re.match(r"(\d{4})-(\d{2})-\d{2}", report_date)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            return f"FY{year}-Q{(month - 1) // 3 + 1}"
    return None


def walk_quarters(year: int, quarter: int, count: int) -> list[tuple[int, int]]:
    """The ``count`` quarters ending at (``year``, ``quarter``), newest first."""
    out: list[tuple[int, int]] = []
    for _ in range(count):
        out.append((year, quarter))
        year, quarter = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
    return out


def previous_calendar_quarter(today: date) -> tuple[int, int]:
    """The most recent *completed* calendar quarter — the latest one an issuer
    could plausibly have reported."""
    quarter = (today.month - 1) // 3 + 1
    return (today.year - 1, 4) if quarter == 1 else (today.year, quarter - 1)
