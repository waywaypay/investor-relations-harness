"""Search-assisted press-release retrieval via the Exa API — tightly constrained.

A single semantic query for "the past four quarters of <company> press
releases" fails structurally, not incidentally: embedding search returns the
top-k most *similar* pages, every quarter exists as half a dozen near-duplicate
mirrors so dedupe plus recency bias yields two distinct quarters out of ten
results, the advisory sibling ("<Company> to Announce ... Results") is nearly
identical in embedding space yet contains zero figures, and the IR landing
page Exa ranks is a JavaScript shell whose cached crawl has no numbers — the
tables live one hop away in the exhibit PDF or the EDGAR filing.

So this fetcher only lets Exa do what it is good at, and verifies everything:

- one **keyword** query per quarter, pinned to that quarter's *reporting
  window* (``startPublishedDate``/``endPublishedDate``: results for a quarter
  publish in the weeks after it closes) — enumeration is ours, not the
  ranker's, and the same-titled release from a year earlier can never satisfy
  this year's slot;
- domains pinned to where full text actually lives (EDGAR, the q4cdn exhibit
  CDN, the wire services) — pointedly *not* the IR landing page;
- contents requested as full ``text`` with a high character ceiling (never
  highlights, which return a few query-similar sentences and drop the tables)
  and ``livecrawl: preferred`` so a stale shell crawl is not trusted;
- advisory titles rejected; titles required to read like a quarterly results
  release — but *not* required to repeat the calendar slot's ordinal or year,
  because issuers title releases in their own fiscal terms ("Apple reports
  second quarter results" is the calendar-Q1 release, with no year in the
  title at all); the reporting window does the temporal verification;
- a candidate accepted only when its text demonstrably contains figures
  (:func:`detect_candidates`), ranked by source authority then figure count;
- the period labelled from the release's **own stated quarter** when it gives
  one (the issuer's fiscal labelling), falling back to the calendar slot with
  an explicit warning — never guessed silently.

Quarters that survive none of that land in ``ReleaseFetchReport.missing`` with
the reason — the honest answer, and the cue to fall back to
:class:`~attest.ingestion.edgar_releases.EdgarReleaseConnector`, which is the
authoritative path and needs none of this defence.

The transport is injectable (``post: (url, payload) -> response dict``) so
tests run offline; the default transport needs ``EXA_API_KEY``.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta

from attest.ingestion.releases import (
    ORDINALS,
    EarningsRelease,
    ReleaseFetchReport,
    infer_period,
    previous_calendar_quarter,
    walk_quarters,
)
from attest.verification.candidates import detect_candidates

Poster = Callable[[str, dict], dict]

SEARCH_URL = "https://api.exa.ai/search"

# Where the *full text* of an earnings release actually lives. The IR landing
# page domain is deliberately absent: it is a JS shell on the Q4 platform.
INCLUDE_DOMAINS = ("sec.gov", "q4cdn.com", "prnewswire.com", "businesswire.com")

# Source authority for tie-breaking: the filed exhibit outranks the issuer's
# CDN copy, which outranks the wire mirrors.
_DOMAIN_RANK = {"sec.gov": 0, "q4cdn.com": 1, "prnewswire.com": 2, "businesswire.com": 2}

# The advisory sibling and its cousins: announcements *about* the release.
_ADVISORY_RE = re.compile(r"\bto\s+(announce|report|release|host|webcast|discuss)\b", re.IGNORECASE)
# A quarterly results release, by its own title — in the issuer's words, which
# may be fiscal ("second quarter" for the calendar-Q1 release) and year-free.
_QUARTERISH_RE = re.compile(r"\bquarter|\bq[1-4]\b", re.IGNORECASE)
_RESULTSISH_RE = re.compile(r"\bresults?\b|\bearnings\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20\d\d)\b")


def reporting_window(year: int, quarter: int) -> tuple[date, date]:
    """When a calendar quarter's results release actually publishes.

    Releases land in the weeks after the quarter closes; off-calendar fiscal
    issuers (quarter ends within a month of the calendar boundary) stretch the
    tail. Five to ninety-five days past quarter end captures one reporting
    cycle per issuer and excludes both the prior and the following cycle.
    """
    if quarter == 4:
        quarter_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        quarter_end = date(year, quarter * 3 + 1, 1) - timedelta(days=1)
    return quarter_end + timedelta(days=5), quarter_end + timedelta(days=95)


class ExaReleaseFetcher:
    """Per-quarter, window-pinned, keyword-constrained, figure-verified retrieval."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        post: Poster | None = None,
        num_results: int = 5,
        max_characters: int = 200_000,
        min_figures: int = 8,
    ) -> None:
        self.api_key = api_key or os.environ.get("EXA_API_KEY")
        if post is None and not self.api_key:
            raise RuntimeError(
                "Exa transport needs an API key: pass api_key or set EXA_API_KEY "
                "(or inject a custom post callable)."
            )
        self._post = post or self._default_post
        self.num_results = num_results
        self.max_characters = max_characters
        self.min_figures = min_figures

    def fetch_quarterly(
        self,
        company: str,
        *,
        entity: str | None = None,
        latest: tuple[int, int] | None = None,
        quarters: int = 4,
    ) -> tuple[list[EarningsRelease], ReleaseFetchReport]:
        """The last ``quarters`` releases for ``company``, one query per quarter.

        ``latest`` is the most recent reported (year, quarter); it defaults to
        the last completed calendar quarter. ``entity`` labels the records
        (defaults to the company name).
        """
        latest = latest or previous_calendar_quarter(date.today())
        releases: list[EarningsRelease] = []
        missing: list[str] = []
        taken: set[str] = set()  # periods and URLs already satisfied

        for year, quarter in walk_quarters(*latest, quarters):
            slot = f"FY{year}-Q{quarter}"
            response = self._post(SEARCH_URL, self._payload(company, year, quarter))
            chosen, reason = self._choose(response.get("results", []), year)
            if chosen is None:
                missing.append(f"{slot}: {reason}")
                continue
            title = chosen.get("title") or ""
            text = chosen.get("text") or ""
            url = chosen.get("url") or ""
            period, warnings = self._label_period(title, text, slot)
            if period in taken or (url and url in taken):
                missing.append(
                    f"{slot}: best result resolves to {period} ({url}), already fetched"
                )
                continue
            taken.update({period, url} - {""})
            releases.append(
                EarningsRelease(
                    entity=entity or company,
                    title=title,
                    period=period,
                    url=url,
                    text=text,
                    filing_date=chosen.get("publishedDate"),
                    warnings=warnings,
                )
            )

        report = ReleaseFetchReport(
            source="exa_keyword_per_quarter",
            requested=quarters,
            fetched=len(releases),
            missing=tuple(missing),
        )
        return releases, report

    def _payload(self, company: str, year: int, quarter: int) -> dict:
        start, end = reporting_window(year, quarter)
        return {
            "query": f"{company} Reports {ORDINALS[quarter]} Quarter {year} Results",
            "type": "keyword",
            "numResults": self.num_results,
            "includeDomains": list(INCLUDE_DOMAINS),
            "startPublishedDate": f"{start.isoformat()}T00:00:00.000Z",
            "endPublishedDate": f"{end.isoformat()}T23:59:59.999Z",
            "contents": {
                "text": {"maxCharacters": self.max_characters},
                "livecrawl": "preferred",
            },
        }

    def _choose(self, results: list[dict], year: int) -> tuple[dict | None, str]:
        """Pick the one result that is verifiably the quarter's release.

        The title must read like a quarterly results release and not be the
        advisory; it need not repeat the slot's calendar ordinal or year (the
        issuer's fiscal labelling differs — the reporting window already pins
        the cycle), but a title whose every stated year is far from the slot
        is the wrong cycle's mirror. The text must contain figures. Among
        survivors: highest source authority, then most figures. Returns
        ``(None, reason)`` when nothing qualifies — reported upstream.
        """
        if not results:
            return None, "no results"

        advisories = 0
        titled: list[dict] = []
        for result in results:
            title = result.get("title") or ""
            if _ADVISORY_RE.search(title):
                advisories += 1
                continue
            if not (_QUARTERISH_RE.search(title) and _RESULTSISH_RE.search(title)):
                continue
            stated_years = [int(y) for y in _YEAR_RE.findall(title)]
            if stated_years and all(abs(y - year) > 1 for y in stated_years):
                continue  # a different year's release, not this slot's cycle
            titled.append(result)
        if not titled:
            return None, (
                f"no result titled as a quarterly results release "
                f"({len(results)} results, {advisories} advisories rejected)"
            )

        scored = [
            (_rank(result.get("url") or ""), -figures, result)
            for result in titled
            if (figures := len(detect_candidates(result.get("text") or ""))) >= self.min_figures
        ]
        if not scored:
            return None, (
                f"{len(titled)} title-matched result(s) but none contained figures — "
                "likely shell pages or truncated text"
            )
        best = min(scored, key=lambda item: (item[0], item[1]))
        return best[2], ""

    @staticmethod
    def _label_period(title: str, text: str, slot: str) -> tuple[str, tuple[str, ...]]:
        """The release's own stated quarter, or the calendar slot — warned."""
        stated = infer_period(f"{title}\n{text[:400]}")
        if stated is not None:
            return stated, ()
        return slot, (
            "period assumed from the calendar slot; the release does not state its quarter",
        )

    def _default_post(self, url: str, payload: dict) -> dict:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"x-api-key": self.api_key or "", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())


def _rank(url: str) -> int:
    for domain, rank in _DOMAIN_RANK.items():
        if domain in url:
            return rank
    return 9
