"""Exa-backed historical document fetch (search-then-ingest).

The network is never touched: a stub :class:`ExaHttpClient` returns canned
``/search`` and ``/contents`` payloads, injected either directly into the service
or by monkeypatching ``LiveExaClient`` for the API-layer tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from attest.api.app import create_app
from attest.ingestion.exa import (
    ExaNotConfigured,
    HistoricalFetcher,
    LiveExaClient,
)
from attest.service import AttestService

# Two results, newest second, so sort-by-date is observable. One release URL and
# one transcript URL, both returned for every /search call so dedup is exercised.
SEARCH = {
    "results": [
        {
            "url": "https://www.businesswire.com/panw-q1",
            "title": "Palo Alto Networks Reports First Quarter Fiscal 2026 Results",
            "publishedDate": "2026-01-28T00:00:00.000Z",
            "highlights": ["Total revenue grew 14% year over year."],
        },
        {
            "url": "https://www.fool.com/panw-q1-transcript",
            "title": "Palo Alto Networks (PANW) Q1 2026 Earnings Call Transcript",
            "publishedDate": "2026-01-29T12:00:00Z",
            "highlights": ["Prepared remarks from the CEO."],
        },
    ]
}

# Full text for the selected release: an in-period figure the DisclosureConnector
# can extract once a period anchor is supplied.
CONTENTS = {
    "results": [
        {
            "url": "https://www.businesswire.com/panw-q1",
            "title": "Palo Alto Networks Reports Q1 Results",
            "publishedDate": "2026-01-28",
            "text": "Total revenue was $2.59 billion.",
        }
    ]
}


class _StubExa:
    """Canned Exa transport: returns the configured payload per endpoint."""

    def __init__(self, *, search: dict | None = None, contents: dict | None = None) -> None:
        self._search = search or {"results": []}
        self._contents = contents or {"results": []}
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, path: str, payload: dict) -> dict:
        self.calls.append((path, payload))
        if path == "/search":
            return self._search
        if path == "/contents":
            # Mirror the real endpoint: only return the requested URLs.
            wanted = set(payload.get("urls", []))
            return {
                "results": [r for r in self._contents["results"] if r.get("url") in wanted]
            }
        return {}


# -- LiveExaClient configuration --------------------------------------------


def test_live_client_raises_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(ExaNotConfigured):
        LiveExaClient().post_json("/search", {"query": "x"})


def test_live_client_accepts_explicit_key() -> None:
    # Constructing with a key must not raise (no request is made here).
    assert LiveExaClient(api_key="test-key").api_key == "test-key"


# -- HistoricalFetcher.search ------------------------------------------------


def test_search_dedupes_and_sorts_newest_first() -> None:
    fetcher = HistoricalFetcher(client=_StubExa(search=SEARCH))
    candidates = fetcher.search(entity="PANW", doc_types=["release", "transcript"])

    urls = [c.url for c in candidates]
    assert len(urls) == len(set(urls)) == 2  # same payload twice -> deduped
    # Newest-first: the 2026-01-29 transcript precedes the 2026-01-28 release.
    assert candidates[0].published_date >= candidates[1].published_date


def test_search_auto_titles_with_fiscal_period_and_pub_date() -> None:
    fetcher = HistoricalFetcher(client=_StubExa(search=SEARCH))
    candidates = fetcher.search(entity="panw", doc_types=["release"])

    candidate = candidates[-1]  # the 2026-01-28 release
    title = candidate.title
    assert "PANW" in title  # entity upper-cased
    assert "FY2026-Q1" in title  # fiscal period read from the doc title, not the date
    assert "pub 2026-01-28" in title
    assert candidate.period == "FY2026-Q1"  # surfaced for the ingest anchor
    assert candidate.source == "businesswire.com"  # www. stripped


def test_search_titles_with_reporting_quarter_not_publish_quarter() -> None:
    """A doc published in one calendar quarter that reports a *different* fiscal
    quarter (a July-fiscal-year issuer reporting Q3 in June) must be labeled by the
    quarter it reports — never the calendar quarter of its publish date."""
    search = {
        "results": [
            {
                "url": "https://www.prnewswire.com/panw-q3",
                "title": "Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results",
                "publishedDate": "2026-06-02T00:00:00Z",  # calendar Q2
                "highlights": ["Total revenue grew 15% year over year."],
            }
        ]
    }
    candidate = HistoricalFetcher(client=_StubExa(search=search)).search(
        entity="PANW", doc_types=["release"]
    )[0]
    assert candidate.period == "FY2026-Q3"
    assert "FY2026-Q3" in candidate.title
    assert "2026-Q2" not in candidate.title  # not the publish (calendar) quarter


def test_search_omits_period_when_unreadable() -> None:
    """No fabricated quarter when the doc states none — just the publish date."""
    search = {
        "results": [
            {
                "url": "https://example.com/panw-misc",
                "title": "Palo Alto Networks announces something",
                "publishedDate": "2026-06-02T00:00:00Z",
                "highlights": ["No period stated anywhere in here."],
            }
        ]
    }
    candidate = HistoricalFetcher(client=_StubExa(search=search)).search(
        entity="PANW", doc_types=["release"]
    )[0]
    assert candidate.period is None
    assert candidate.title == "PANW Earnings release (pub 2026-06-02)"


def test_search_unknown_doc_type_falls_back_to_all() -> None:
    stub = _StubExa(search=SEARCH)
    HistoricalFetcher(client=stub).search(entity="PANW", doc_types=["bogus"])
    # Falls back to both known types -> two /search calls.
    assert [c[0] for c in stub.calls] == ["/search", "/search"]


# -- HistoricalFetcher.fetch_contents ---------------------------------------


def test_fetch_contents_skips_empty_text() -> None:
    stub = _StubExa(
        contents={
            "results": [
                {"url": "u1", "title": "t1", "publishedDate": "2026-01-28", "text": "hello"},
                {"url": "u2", "title": "t2", "publishedDate": "2026-01-28", "text": "   "},
            ]
        }
    )
    docs = HistoricalFetcher(client=stub).fetch_contents(urls=["u1", "u2"])
    assert [d.url for d in docs] == ["u1"]


def test_fetch_contents_empty_urls_makes_no_request() -> None:
    stub = _StubExa()
    assert HistoricalFetcher(client=stub).fetch_contents(urls=[]) == []
    assert stub.calls == []


# -- service integration -----------------------------------------------------


def test_search_historical_returns_candidates() -> None:
    svc = AttestService()
    candidates = svc.search_historical(
        entity="PANW", doc_types=("release",), exa_client=_StubExa(search=SEARCH)
    )
    assert candidates and all(c.url for c in candidates)


def test_ingest_historical_files_reference_facts() -> None:
    svc = AttestService()
    results = svc.ingest_historical(
        tenant_id="acme",
        entity="PANW",
        items=[
            {
                "url": "https://www.businesswire.com/panw-q1",
                "title": "PANW Q1 release",
                "period": "FY2026-Q1",
            }
        ],
        exa_client=_StubExa(contents=CONTENTS),
    )

    assert len(results) == 1
    doc, report = results[0]
    assert doc.url == "https://www.businesswire.com/panw-q1"
    assert report.ingested >= 1

    facts = svc.store.all("acme")
    assert any(f.entity == "PANW" and f.metric == "total_revenue" for f in facts)
    # The disclosure cites its web source, not a filing.
    rev = next(f for f in facts if f.metric == "total_revenue")
    assert rev.source_ref == "https://www.businesswire.com/panw-q1"
    assert svc.audit_verify()  # ingestion wrote an intact audit event


def test_ingest_historical_infers_period_from_text_when_unspecified() -> None:
    # No period passed in the item: the connector must read the reporting quarter
    # from the document's own words and anchor figures to it (not skip them).
    contents = {
        "results": [
            {
                "url": "https://www.prnewswire.com/panw-q3",
                "title": "Palo Alto Networks Reports Fiscal Third Quarter 2026 Results",
                "publishedDate": "2026-06-02",
                "text": (
                    "Palo Alto Networks Reports Fiscal Third Quarter 2026 Results. "
                    "Total revenue was $2.59 billion."
                ),
            }
        ]
    }
    svc = AttestService()
    results = svc.ingest_historical(
        tenant_id="acme",
        entity="PANW",
        items=[{"url": "https://www.prnewswire.com/panw-q3"}],  # no period
        exa_client=_StubExa(contents=contents),
    )
    assert len(results) == 1 and results[0][1].ingested >= 1
    rev = next(f for f in svc.store.all("acme") if f.metric == "total_revenue")
    assert rev.period == "FY2026-Q3"  # read from the prose, not guessed from the date


def test_ingest_historical_drops_unfetchable_items() -> None:
    # The selected URL isn't among the fetched contents -> nothing ingested.
    svc = AttestService()
    results = svc.ingest_historical(
        tenant_id="acme",
        entity="PANW",
        items=[{"url": "https://example.com/missing", "period": "FY2026-Q1"}],
        exa_client=_StubExa(contents=CONTENTS),
    )
    assert results == []


# -- API endpoints -----------------------------------------------------------


def test_search_endpoint_returns_503_when_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    client = TestClient(create_app(AttestService()))
    r = client.post("/tenants/acme/historical/search", json={"entity": "PANW"})
    assert r.status_code == 503


def test_search_and_ingest_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubExa(search=SEARCH, contents=CONTENTS)
    monkeypatch.setattr("attest.ingestion.exa.LiveExaClient", lambda *a, **k: stub)

    client = TestClient(create_app(AttestService()))

    r = client.post(
        "/tenants/acme/historical/search",
        json={"entity": "PANW", "doc_types": ["release"]},
    )
    assert r.status_code == 200
    candidates = r.json()["candidates"]
    assert candidates and candidates[0]["url"]

    r2 = client.post(
        "/tenants/acme/historical/ingest",
        json={
            "entity": "PANW",
            "items": [
                {"url": "https://www.businesswire.com/panw-q1", "period": "FY2026-Q1"}
            ],
        },
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["total_ingested"] >= 1
    assert body["documents"][0]["url"] == "https://www.businesswire.com/panw-q1"
