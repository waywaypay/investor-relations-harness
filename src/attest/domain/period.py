"""Fiscal-period value object — the one place period arithmetic lives.

A period like ``FY2026-Q1`` (or ``FY2026-FY`` for a full year, or a bare
``FY2026``) was, until this module, parsed by ad-hoc regex in four places, with
three separate "prior-year" implementations and two "next-quarter" ones. That is
exactly the kind of duplication that drifts: the connectors had just grown a
full-year period the quarter-only helpers silently didn't understand.

:class:`Period` centralises parsing, formatting, and the year/quarter
arithmetic, so every consumer (the derived-figure rules, the directional rule,
the extraction edge, the guidance connector) shares one definition. It is a
frozen value object; the arithmetic methods return new periods and never mutate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Strict, anchored parse for a clean period string. The optional part is a
# quarter (``Q1``..``Q4``) or the full-year marker (``FY``); absent means a bare
# fiscal year.
_PERIOD_RE = re.compile(r"^FY(\d{4})(?:-(Q[1-4]|FY))?$", re.IGNORECASE)
# A loose search for the first period token embedded in free prose.
_PERIOD_IN_TEXT_RE = re.compile(r"FY\d{4}(?:-(?:Q[1-4]|FY))?", re.IGNORECASE)


@dataclass(frozen=True)
class Period:
    """A fiscal period: a year plus an optional within-year part.

    ``part`` is ``""`` for a bare fiscal year, ``"Q1"``..``"Q4"`` for a quarter,
    or ``"FY"`` for an explicit full-year period. Quarter arithmetic is only
    defined for quarterly periods and returns ``None`` otherwise — the caller
    then stays silent rather than guessing, consistent with the rest of the spine.
    """

    year: int
    part: str = ""

    @classmethod
    def parse(cls, text: str | None) -> "Period | None":
        """Parse a clean period string, or ``None`` if it isn't one."""
        if not text:
            return None
        m = _PERIOD_RE.match(text.strip())
        if not m:
            return None
        return cls(year=int(m.group(1)), part=(m.group(2) or "").upper())

    @classmethod
    def find(cls, text: str | None) -> "Period | None":
        """Find the first period token embedded anywhere in free prose."""
        if not text:
            return None
        m = _PERIOD_IN_TEXT_RE.search(text)
        return cls.parse(m.group(0)) if m else None

    def __str__(self) -> str:
        return f"FY{self.year}" if not self.part else f"FY{self.year}-{self.part}"

    @property
    def quarter(self) -> int | None:
        """The quarter number (1-4) for a quarterly period, else ``None``."""
        return int(self.part[1]) if self.part.startswith("Q") else None

    @property
    def is_full_year(self) -> bool:
        return self.part == "FY"

    def prior_year(self) -> "Period":
        """The same within-year part one fiscal year earlier."""
        return Period(year=self.year - 1, part=self.part)

    def prior_quarter(self) -> "Period | None":
        """The immediately preceding quarter, or ``None`` if not quarterly."""
        q = self.quarter
        if q is None:
            return None
        if q == 1:
            return Period(year=self.year - 1, part="Q4")
        return Period(year=self.year, part=f"Q{q - 1}")

    def next_quarter(self) -> "Period | None":
        """The immediately following quarter, or ``None`` if not quarterly."""
        q = self.quarter
        if q is None:
            return None
        if q == 4:
            return Period(year=self.year + 1, part="Q1")
        return Period(year=self.year, part=f"Q{q + 1}")
