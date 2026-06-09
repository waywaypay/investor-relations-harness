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

- one **keyword** query per quarter, built from the release-title convention
  ("<Company> Reports Third Quarter 2025 Results") — enumeration is ours, not
  the ranker's;
- domains pinned to where full text actually lives (EDGAR, the q4cdn exhibit
  CDN, the wire services) — pointedly *not* the IR landing page;
- contents requested as full ``text`` with a high character ceiling (never
  highlights, which return a few query-similar sentences and drop the tables)
  and ``livecrawl: preferred`` so a stale shell crawl is not trusted;
- advisory titles rejected, titles required to actually name the quarter; and
- a candidate accepted only when its text demonstrably contains figures
  (:func:`detect_candidates`), ranked by source authority then figure count.

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
import urllib.request
from collections.abc import Callable
from datetime import date

from attest.ingestion.releases import (
    ORDINALS,
    EarningsRelease,
    ReleaseFetchReport,
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


class ExaReleaseFetcher:
    """Per-quarter, keyword-constrained, figure-verified Exa retrieval."""

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

        for year, quarter in walk_quarters(*latest, quarters):
            response = self._post(SEARCH_URL, self._payload(company, year, quarter))
            chosen, reason = self._choose(response.get("results", []), year, quarter)
            if chosen is None:
                missing.append(f"FY{year}-Q{quarter}: {reason}")
                continue
            releases.append(
                EarningsRelease(
                    entity=entity or company,
                    title=chosen.get("title") or "",
                    period=f"FY{year}-Q{quarter}",
                    url=chosen.get("url") or "",
                    text=chosen.get("text") or "",
                    filing_date=chosen.get("publishedDate"),
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
        return {
            "query": f"{company} Reports {ORDINALS[quarter]} Quarter {year} Results",
            "type": "keyword",
            "numResults": self.num_results,
            "includeDomains": list(INCLUDE_DOMAINS),
            "contents": {
                "text": {"maxCharacters": self.max_characters},
                "livecrawl": "preferred",
            },
        }

    def _choose(self, results: list[dict], year: int, quarter: int) -> tuple[dict | None, str]:
        """Pick the one result that is verifiably the quarter's release.

        Title must name the quarter and not be the advisory; text must contain
        figures. Among survivors: highest source authority, then most figures.
        Returns ``(None, reason)`` when nothing qualifies — reported upstream.
        """
        if not results:
            return None, "no results"

        advisories = 0
        titled: list[dict] = []
        for result in results:
            title = (result.get("title") or "").lower()
            if "to announce" in title:
                advisories += 1
                continue
            wanted = f"{ORDINALS[quarter].lower()} quarter"
            if wanted in title and str(year) in title and "results" in title:
                titled.append(result)
        if not titled:
            return None, (
                f"no result titled as the release "
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
