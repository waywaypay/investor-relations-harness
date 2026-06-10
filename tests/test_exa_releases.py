"""Constrained Exa retrieval: the guard-rails that make semantic search usable.

The naive failure mode this fetcher exists to prevent: one embedding query for
"the past four quarters of press releases" returns two distinct quarters out
of a soup of near-duplicate mirrors, picks the figure-free advisory sibling
("... to Announce ... Results"), or hands back a JS shell page whose cached
crawl contains no numbers. The contract under test is each guard-rail in
turn: one keyword query per quarter pinned to that quarter's reporting
window, pinned domains, full-text livecrawl contents, advisory titles
rejected, fiscal-labelled titles accepted (issuers do not title releases in
calendar terms), candidates without detectable figures rejected, source
authority winning ties, periods taken from the release's own stated quarter,
and unresolvable quarters reported with the reason rather than silently
absent.
"""

from __future__ import annotations

import pytest

from attest.ingestion.exa_releases import (
    INCLUDE_DOMAINS,
    SEARCH_URL,
    ExaReleaseFetcher,
    reporting_window,
)

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


def test_each_quarter_query_is_pinned_to_its_reporting_window():
    # Without a window, the same-titled release from a year earlier can
    # satisfy this year's slot; with one, each query covers exactly one
    # reporting cycle (5–95 days past calendar quarter end).
    post = FakePost({})
    ExaReleaseFetcher(post=post).fetch_quarterly("Meta", latest=(2026, 1), quarters=2)

    q1_payload = post.calls[0][1]
    assert q1_payload["startPublishedDate"] == "2026-04-05T00:00:00.000Z"
    assert q1_payload["endPublishedDate"] == "2026-07-04T23:59:59.999Z"
    q4_payload = post.calls[1][1]
    assert q4_payload["startPublishedDate"] == "2026-01-05T00:00:00.000Z"
    assert q4_payload["endPublishedDate"] == "2026-04-05T23:59:59.999Z"

    start, end = reporting_window(2025, 2)  # quarter ends 2025-06-30
    assert (start.isoformat(), end.isoformat()) == ("2025-07-05", "2025-10-03")


def test_fiscal_titles_without_the_calendar_ordinal_or_year_are_accepted():
    # Apple's calendar-Q1 release is titled "second quarter" (its fiscal
    # label) with no year at all. The window pins the cycle; the title gate
    # must not demand the calendar ordinal — and the period must come from
    # the release's own stated quarter, not the calendar slot.
    apple = {
        "title": "Apple reports second quarter results",
        "url": "https://www.businesswire.com/news/home/apple-q2-fy26",
        "text": (
            "CUPERTINO — Apple today announced financial results for its fiscal "
            "2026 second quarter ended March 28, 2026. " + FIGURE_RICH
        ),
        "publishedDate": "2026-05-01T20:30:00.000Z",
    }
    post = FakePost({"Apple Reports First Quarter 2026 Results": [apple]})
    releases, report = ExaReleaseFetcher(post=post).fetch_quarterly(
        "Apple", latest=(2026, 1), quarters=1
    )

    assert report.fetched == 1
    assert releases[0].period == "FY2026-Q2"  # the issuer's fiscal labelling
    assert releases[0].warnings == ()


def test_titles_stating_only_a_distant_year_are_rejected():
    stale_mirror = {
        "title": "Meta Reports First Quarter 2024 Results",
        "url": "https://www.prnewswire.com/news-releases/meta-q1-2024",
        "text": FIGURE_RICH,
    }
    post = FakePost({"Meta Reports First Quarter 2026 Results": [stale_mirror]})
    releases, report = ExaReleaseFetcher(post=post).fetch_quarterly(
        "Meta", latest=(2026, 1), quarters=1
    )

    assert releases == []
    assert "FY2026-Q1" in report.missing[0]


def test_unstated_period_falls_back_to_the_calendar_slot_with_a_warning():
    vague = {
        "title": "Acme Posts Strong Quarterly Earnings",
        "url": "https://www.businesswire.com/news/home/acme-q",
        "text": FIGURE_RICH,  # figures, but no stated quarter anywhere
    }
    post = FakePost({"Acme Reports First Quarter 2026 Results": [vague]})
    releases, _ = ExaReleaseFetcher(post=post).fetch_quarterly(
        "Acme", latest=(2026, 1), quarters=1
    )

    assert releases[0].period == "FY2026-Q1"
    assert any("calendar slot" in w for w in releases[0].warnings)


def test_one_release_cannot_satisfy_two_slots():
    apple = {
        "title": "Apple reports second quarter results",
        "url": "https://www.businesswire.com/news/home/apple-q2-fy26",
        "text": "Results for the second quarter ended March 28, 2026. " + FIGURE_RICH,
    }
    post = FakePost(
        {
            "Apple Reports First Quarter 2026 Results": [apple],
            "Apple Reports Fourth Quarter 2025 Results": [apple],  # ranker echo
        }
    )
    releases, report = ExaReleaseFetcher(post=post).fetch_quarterly(
        "Apple", latest=(2026, 1), quarters=2
    )

    assert report.fetched == 1
    assert releases[0].period == "FY2026-Q2"
    assert "FY2025-Q4" in report.missing[0]
    assert "already fetched" in report.missing[0]


def test_default_transport_requires_an_api_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="EXA_API_KEY"):
        ExaReleaseFetcher()
