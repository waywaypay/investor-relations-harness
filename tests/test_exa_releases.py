"""Constrained Exa retrieval: the guard-rails that make semantic search usable.

The naive failure mode this fetcher exists to prevent: one embedding query for
"the past four quarters of press releases" returns two distinct quarters out
of a soup of near-duplicate mirrors, picks the figure-free advisory sibling
("... to Announce ... Results"), or hands back a JS shell page whose cached
crawl contains no numbers. The contract under test is each guard-rail in
turn: one keyword query per quarter, pinned domains, full-text livecrawl
contents, advisory titles rejected, candidates without detectable figures
rejected, source authority winning ties, and unresolvable quarters reported
with the reason rather than silently absent.
"""

from __future__ import annotations

import pytest

from attest.ingestion.exa_releases import INCLUDE_DOMAINS, SEARCH_URL, ExaReleaseFetcher

FIGURE_RICH = (
    "Revenue was $56,311 million, an increase of 33%. Net income was $18,447 "
    "million, up 35%. Diluted EPS was $7.25 compared with $5.34, up 36%. "
    "Operating margin was 43% versus 41%. Capital expenditures were $30.5 billion."
)

ADVISORY = {
    "title": "Meta to Announce First Quarter 2026 Results",
    "url": "https://www.prnewswire.com/news-releases/meta-to-announce",
    "text": "Meta will report its results after market close and host a conference call.",
}
SHELL = {
    "title": "Meta Reports First Quarter 2026 Results",
    "url": "https://s21.q4cdn.com/399680738/landing",
    "text": "Investor Relations. Menu. Cookie Settings. Download the release.",
}
WIRE = {
    "title": "Meta Reports First Quarter 2026 Results",
    "url": "https://www.prnewswire.com/news-releases/meta-reports-q1-2026",
    "text": FIGURE_RICH + " Even more detail and one extra figure: $1.23 billion.",
}
FILED = {
    "title": "Meta Reports First Quarter 2026 Results",
    "url": "https://www.sec.gov/Archives/edgar/data/1326801/000162828026000101/ex991.htm",
    "text": FIGURE_RICH,
}


class FakePost:
    def __init__(self, responses: dict[str, list[dict]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, payload: dict) -> dict:
        self.calls.append((url, payload))
        return {"results": self.responses.get(payload["query"], [])}


def test_one_constrained_keyword_query_per_quarter():
    post = FakePost(
        {
            "Meta Reports First Quarter 2026 Results": [FILED],
            "Meta Reports Fourth Quarter 2025 Results": [],
            "Meta Reports Third Quarter 2025 Results": [],
            "Meta Reports Second Quarter 2025 Results": [],
        }
    )
    fetcher = ExaReleaseFetcher(post=post)
    fetcher.fetch_quarterly("Meta", latest=(2026, 1), quarters=4)

    assert [payload["query"] for _, payload in post.calls] == [
        "Meta Reports First Quarter 2026 Results",
        "Meta Reports Fourth Quarter 2025 Results",
        "Meta Reports Third Quarter 2025 Results",
        "Meta Reports Second Quarter 2025 Results",
    ]
    for url, payload in post.calls:
        assert url == SEARCH_URL
        assert payload["type"] == "keyword"  # enumeration is ours, not the ranker's
        assert payload["includeDomains"] == list(INCLUDE_DOMAINS)
        assert payload["contents"]["livecrawl"] == "preferred"
        assert payload["contents"]["text"]["maxCharacters"] >= 100_000  # never highlights


def test_advisories_and_shells_lose_to_the_filed_copy():
    post = FakePost({"Meta Reports First Quarter 2026 Results": [ADVISORY, SHELL, WIRE, FILED]})
    fetcher = ExaReleaseFetcher(post=post)
    releases, report = fetcher.fetch_quarterly("Meta", latest=(2026, 1), quarters=1)

    assert report.fetched == 1
    # The wire copy has more figures, but the filed exhibit outranks it.
    assert releases[0].url == FILED["url"]
    assert releases[0].period == "FY2026-Q1"
    assert releases[0].figure_count >= 8


def test_quarter_with_only_advisory_is_reported_missing_with_reason():
    post = FakePost(
        {
            "Meta Reports First Quarter 2026 Results": [FILED],
            "Meta Reports Fourth Quarter 2025 Results": [ADVISORY | {
                "title": "Meta to Announce Fourth Quarter 2025 Results"
            }],
        }
    )
    fetcher = ExaReleaseFetcher(post=post)
    releases, report = fetcher.fetch_quarterly("Meta", latest=(2026, 1), quarters=2)

    assert report.fetched == 1
    assert len(report.missing) == 1
    assert "FY2025-Q4" in report.missing[0]
    assert "advisories" in report.missing[0]


def test_title_matched_but_figure_free_results_are_rejected_with_reason():
    post = FakePost({"Meta Reports First Quarter 2026 Results": [SHELL]})
    fetcher = ExaReleaseFetcher(post=post)
    releases, report = fetcher.fetch_quarterly("Meta", latest=(2026, 1), quarters=1)

    assert releases == []
    assert "none contained figures" in report.missing[0]


def test_no_results_is_reported():
    post = FakePost({})
    fetcher = ExaReleaseFetcher(post=post)
    _, report = fetcher.fetch_quarterly("Meta", latest=(2026, 1), quarters=1)
    assert report.missing == ("FY2026-Q1: no results",)


def test_default_transport_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="EXA_API_KEY"):
        ExaReleaseFetcher()
