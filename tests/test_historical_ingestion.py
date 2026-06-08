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

# A realistic release pool: two outlets cover the SAME quarter (FY2026-Q1) — these
# must collapse to one — plus a distinct earlier quarter (FY2025-Q4).
RELEASES = {
    "results": [
        {
            "url": "https://www.businesswire.com/panw-q1",
            "title": "Palo Alto Networks Reports First Quarter Fiscal 2026 Results",
            "publishedDate": "2026-01-28T00:00:00.000Z",
            "highlights": ["Total revenue grew 14% year over year."],
        },
        {
            "url": "https://www.investing.com/panw-q1-recap",
            "title": "Palo Alto Networks Q1 2026 results: revenue tops estimates",
            "publishedDate": "2026-01-29T00:00:00Z",  # same period, newer -> wins the collapse
            "highlights": ["Q1 fiscal 2026 revenue and guidance recap."],
        },
        {
            "url": "https://www.businesswire.com/panw-q4",
            "title": "Palo Alto Networks Reports Fourth Quarter Fiscal 2025 Results",
            "publishedDate": "2025-08-18T00:00:00Z",
            "highlights": ["Fiscal 2025 fourth quarter revenue grew 15%."],
        },
    ]
}

# A transcript pool spanning the same two quarters.
TRANSCRIPTS = {
    "results": [
        {
            "url": "https://www.fool.com/panw-q1-transcript",
            "title": "Palo Alto Networks (PANW) Q1 2026 Earnings Call Transcript",
            "publishedDate": "2026-01-30T12:00:00Z",
            "highlights": ["Prepared remarks from the CEO."],
        },
        {
            "url": "https://www.fool.com/panw-q4-transcript",
            "title": "Palo Alto Networks (PANW) Q4 2025 Earnings Call Transcript",
            "publishedDate": "2025-08-20T12:00:00Z",
            "highlights": ["Prepared remarks for the fourth quarter."],
        },
    ]
}

# Back-compat single-payload fixture for tests that don't care about per-type results.
SEARCH = RELEASES

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
    """Canned Exa transport: returns the configured payload per endpoint.

    ``search`` returns one payload for any query; ``releases`` / ``transcripts``
    let a test return type-specific results (the real API runs one query per type).
    """

    def __init__(
        self,
        *,
        search: dict | None = None,
        releases: dict | None = None,
        transcripts: dict | None = None,
        contents: dict | None = None,
    ) -> None:
        self._releases = releases or search or {"results": []}
        self._transcripts = transcripts or search or {"results": []}
        self._contents = contents or {"results": []}
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, path: str, payload: dict) -> dict:
        self.calls.append((path, payload))
        if path == "/search":
            query = payload.get("query", "")
            return self._transcripts if "transcript" in query else self._releases
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


def test_search_returns_one_result_per_period() -> None:
    # The release pool has two outlets for FY2026-Q1 and one for FY2025-Q4; the
    # transcript pool spans the same two quarters. The result must carry exactly one
    # candidate per (type, period) — no quarter repeated — newest first overall.
    fetcher = HistoricalFetcher(client=_StubExa(releases=RELEASES, transcripts=TRANSCRIPTS))
    candidates = fetcher.search(entity="PANW", doc_types=["release", "transcript"])

    scopes = [(c.doc_type, c.period) for c in candidates]
    assert len(scopes) == len(set(scopes))  # no (type, period) repeated
    assert sorted(scopes) == sorted(
        [("release", "FY2026-Q1"), ("release", "FY2025-Q4"),
         ("transcript", "FY2026-Q1"), ("transcript", "FY2025-Q4")]
    )
    # Sorted newest-first by publish date.
    dates = [c.published_date for c in candidates]
    assert dates == sorted(dates, reverse=True)
    # Among the two FY2026-Q1 releases, the most recently published outlet wins.
    q1_release = next(c for c in candidates if c.doc_type == "release" and c.period == "FY2026-Q1")
    assert q1_release.source == "investing.com"  # pub 2026-01-29 beat businesswire's 2026-01-28


def test_search_limits_to_requested_quarters() -> None:
    # quarters=1 -> only the single most recent period per type.
    fetcher = HistoricalFetcher(client=_StubExa(releases=RELEASES, transcripts=TRANSCRIPTS))
    candidates = fetcher.search(entity="PANW", doc_types=["release", "transcript"], quarters=1)

    assert {(c.doc_type, c.period) for c in candidates} == {
        ("release", "FY2026-Q1"),
        ("transcript", "FY2026-Q1"),
    }


def test_search_auto_titles_with_fiscal_period_and_pub_date() -> None:
    fetcher = HistoricalFetcher(client=_StubExa(releases=RELEASES))
    candidates = fetcher.search(entity="panw", doc_types=["release"])

    q4 = next(c for c in candidates if c.period == "FY2025-Q4")
    title = q4.title
    assert "PANW" in title  # entity upper-cased
    assert "FY2025-Q4" in title  # fiscal period read from the doc title, not the date
    assert "pub 2025-08-18" in title
    assert q4.source == "businesswire.com"  # www. stripped


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


def test_release_search_targets_investor_press_release() -> None:
    # The release search must look for the issuer's *own* earnings press release
    # (its investor-relations site / the newswires), biased to published news —
    # not third-party recaps — so it actually pulls the press release.
    stub = _StubExa(releases=RELEASES)
    HistoricalFetcher(client=stub).search(entity="PANW", doc_types=["release"])
    path, payload = stub.calls[0]
    assert path == "/search"
    query = payload["query"].lower()
    assert "press release" in query
    assert "investor relations" in query
    assert payload.get("category") == "news"  # keep Exa on published press/news pages


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
    r = results[0]
    assert r.document.url == "https://www.businesswire.com/panw-q1"
    assert r.report.ingested >= 1
    assert r.period == "FY2026-Q1"  # the resolved reporting period travels with the result

    # The document is analyzed against filed sources, so its figures come back as
    # real metric claims/verdicts the UI can link — not unattributed numbers.
    assert any(c.metric == "total_revenue" for c in r.analyzed.claims)
    assert any(v.metric == "total_revenue" for v in r.analysis.verdicts)

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
    assert len(results) == 1 and results[0].report.ingested >= 1
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
    doc = body["documents"][0]
    assert doc["url"] == "https://www.businesswire.com/panw-q1"
    # The fetched prose and resolved period come back so the client can render the
    # loaded document, not just report a figure count.
    assert doc["period"] == "FY2026-Q1"
    assert "Total revenue was $2.59 billion." in doc["text"]
    # The document's own figures come back analyzed (real metric attribution), so
    # the UI can show each figure's link to its source.
    assert any(v["metric"] == "total_revenue" for v in doc["verdicts"])
    assert any(c["metric"] == "total_revenue" for c in doc["claims"])
