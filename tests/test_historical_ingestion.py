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
from attest.ingestion.edgar import StaticEdgarClient
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


def _panw_q3_edgar(titles: dict[str, str] | None = None) -> StaticEdgarClient:
    cik = 1327567

    def dur(start: str, end: str, val: int | float, accn: str = "0001327567-26-000015") -> dict:
        return {"start": start, "end": end, "val": val, "accn": accn, "form": "10-Q", "filed": "2026-06-03"}

    def inst(end: str, val: int, accn: str = "0001327567-26-000015") -> dict:
        return {"end": end, "val": val, "accn": accn, "form": "10-Q", "filed": "2026-06-03"}

    return StaticEdgarClient(
        tickers={"PANW": cik},
        titles=titles,
        fiscal_year_ends={cik: "0731"},
        concepts={
            (cik, "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"): {
                "units": {"USD": [dur("2026-02-01", "2026-04-30", 3002000000)]}
            },
            (cik, "us-gaap:RevenueRemainingPerformanceObligation"): {
                "units": {"USD": [inst("2026-04-30", 18400000000)]}
            },
            (cik, "us-gaap:EarningsPerShareDiluted"): {
                "units": {"USD/shares": [
                    dur("2026-02-01", "2026-04-30", -0.22),
                    dur("2025-02-01", "2025-04-30", 0.37, "0001327567-25-000020"),
                ]}
            },
            (cik, "us-gaap:NetCashProvidedByUsedInOperatingActivities"): {
                "units": {"USD": [
                    dur("2025-08-01", "2026-01-31", 300000000),
                    dur("2025-08-01", "2026-04-30", 1171000000),
                ]}
            },
        },
    )


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


def test_search_excludes_sec_domains() -> None:
    stub = _StubExa(releases=RELEASES)
    HistoricalFetcher(client=stub).search(entity="PANW", doc_types=["release"])

    payload = stub.calls[0][1]
    assert "sec.gov" in payload["excludeDomains"]
    assert "-site:sec.gov" in payload["query"]


def test_fetch_contents_skips_sec_cover_page_without_release_exhibit() -> None:
    sec_cover = """UNITED STATES

SECURITIES AND EXCHANGE COMMISSION

Washington, D.C. 20549

FORM

8-K

CURRENT REPORT
"""
    stub = _StubExa(
        contents={
            "results": [
                {
                    "url": "https://www.sec.gov/Archives/x",
                    "title": "8-K",
                    "publishedDate": "2026-01-28",
                    "text": sec_cover,
                }
            ]
        }
    )

    assert (
        HistoricalFetcher(client=stub).fetch_contents(urls=["https://www.sec.gov/Archives/x"])
        == []
    )


def test_fetch_contents_extracts_press_release_exhibit_from_8k_blob() -> None:
    filing_blob = """UNITED STATES

SECURITIES AND EXCHANGE COMMISSION

Washington, D.C. 20549

FORM 8-K

EXHIBIT 99.1

Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results

Total revenue was $3.0 billion.

SIGNATURES

/s/ Officer
"""
    stub = _StubExa(
        contents={
            "results": [
                {
                    "url": "https://issuer.example/8k-q3",
                    "title": "8-K",
                    "publishedDate": "2026-06-02",
                    "text": filing_blob,
                }
            ]
        }
    )

    docs = HistoricalFetcher(client=stub).fetch_contents(urls=["https://issuer.example/8k-q3"])
    assert len(docs) == 1
    assert docs[0].text.startswith("Palo Alto Networks Reports Fiscal Third Quarter 2026")
    assert "UNITED STATES" not in docs[0].text
    assert "SIGNATURES" not in docs[0].text


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



def test_ingest_historical_analyzes_realistic_release_against_sec_without_false_conflicts() -> None:
    contents = {
        "results": [
            {
                "url": "https://www.paloaltonetworks.com/q3-fy2026",
                "title": "Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results",
                "publishedDate": "2026-06-02",
                "text": (
                    "Palo Alto Networks (NASDAQ: PANW) reported fiscal third quarter 2026 results. "
                    "Total revenue for the fiscal third quarter 2026 grew 31% year over year to $3.0 billion. "
                    "Remaining performance obligation grew 36% year over year to $18.4 billion. "
                    "GAAP net loss for the fiscal third quarter 2026 was $177 million, or ($0.22) per diluted share, "
                    "compared with GAAP net income of $262 million, or $0.37 per diluted share, for the fiscal third quarter 2025. "
                    "Net cash provided by operating activities for the fiscal third quarter 2026 was $871 million. "
                    "Product revenue was $594 million and services revenue was $2.4 billion. "
                    "Non-GAAP net income for the fiscal third quarter 2026 was $684 million, or $0.85 per diluted share. "
                    "For the fiscal fourth quarter 2026, we expect total revenue in the range of $3.345 billion to $3.355 billion."
                ),
            }
        ]
    }
    svc = AttestService(edgar=_panw_q3_edgar())
    results = svc.ingest_historical(
        tenant_id="acme",
        entity="PANW",
        items=[{
            "url": "https://www.paloaltonetworks.com/q3-fy2026",
            "title": "PANW Q3 release",
            "period": "FY2026-Q3",
            "doc_type": "release",
        }],
        exa_client=_StubExa(contents=contents),
    )

    verdicts = results[0].analysis.verdicts
    traced = {(v.metric, v.period, v.displayed_text) for v in verdicts if v.verdict.value == "traced"}
    assert ("total_revenue", "FY2026-Q3", "$3.0 billion") in traced
    assert ("total_rpo", "FY2026-Q3", "$18.4 billion") in traced
    assert ("gaap_diluted_eps", "FY2026-Q3", "($0.22)") in traced
    assert ("operating_cash_flow", "FY2026-Q3", "$871 million") in traced
    # Product/services revenue, non-GAAP income/EPS, and Q4 guidance are not SEC
    # totals for Q3; they must be honest untraced/review items, not false conflicts.
    assert [v for v in verdicts if v.verdict.value == "conflict"] == []

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


# -- dynamic entity resolution (company name -> ticker), titles, and linking --


def _named_panw_edgar() -> StaticEdgarClient:
    """The PANW Q3 client, with the registrant title so a name resolves."""
    return _panw_q3_edgar(titles={"PANW": "Palo Alto Networks Inc"})


def test_search_titles_use_resolved_ticker_when_company_name_typed() -> None:
    # The input invites "PANW or Palo Alto Networks". A typed company name must
    # produce ticker-labeled candidates ("PANW Earnings release · …"), not a
    # shouted upper-cased company name.
    svc = AttestService(edgar=_named_panw_edgar())
    candidates = svc.search_historical(
        entity="Palo Alto Networks",
        doc_types=("release",),
        exa_client=_StubExa(search=SEARCH),
    )
    assert candidates
    assert all(c.title.startswith("PANW ") for c in candidates)


def test_search_without_resolution_keeps_company_name_unshouted() -> None:
    # No EDGAR client: the name can't resolve, so the auto-title keeps the typed
    # casing rather than upper-casing a whole company name.
    svc = AttestService()
    candidates = svc.search_historical(
        entity="Palo Alto Networks",
        doc_types=("release",),
        exa_client=_StubExa(search=SEARCH),
    )
    assert candidates
    assert all(c.title.startswith("Palo Alto Networks ") for c in candidates)
    assert not any("PALO ALTO NETWORKS" in c.title for c in candidates)


def test_ingest_historical_resolves_company_name_and_traces_to_sec() -> None:
    # End-to-end regression: a user types the company name, not the ticker. The
    # run must still scope facts to the symbol (via the SEC company list, falling
    # back to the document's own "(NASDAQ: PANW)") so figures trace — previously
    # this path loaded no filed facts and every figure landed untraced.
    contents = {
        "results": [
            {
                "url": "https://www.paloaltonetworks.com/q3-fy2026",
                "title": "Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results",
                "publishedDate": "2026-06-02",
                "text": (
                    "Palo Alto Networks (NASDAQ: PANW) reported fiscal third quarter 2026 results. "
                    "Total revenue for the fiscal third quarter 2026 grew 15% year over year to $3.0 billion."
                ),
            }
        ]
    }
    svc = AttestService(edgar=_named_panw_edgar())
    results = svc.ingest_historical(
        tenant_id="acme",
        entity="Palo Alto Networks",
        items=[{
            "url": "https://www.paloaltonetworks.com/q3-fy2026",
            "title": "PANW Earnings release · FY2026-Q3",
            "period": "FY2026-Q3",
            "doc_type": "release",
        }],
        exa_client=_StubExa(contents=contents),
    )

    assert len(results) == 1
    r = results[0]
    assert r.entity == "PANW"  # the run resolved and reports the symbol
    assert r.title == "PANW Earnings release · FY2026-Q3"  # the reviewed title
    traced = [v for v in r.analysis.verdicts if v.verdict.value == "traced"]
    assert any(v.metric == "total_revenue" for v in traced)
    # Facts (filed and reference alike) are scoped under the resolved ticker.
    assert all(f.entity.split(":")[0] == "PANW" for f in svc.store.all("acme"))


def test_ingest_endpoint_returns_reviewed_title_and_resolved_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubExa(search=SEARCH, contents=CONTENTS)
    monkeypatch.setattr("attest.ingestion.exa.LiveExaClient", lambda *a, **k: stub)
    client = TestClient(create_app(AttestService()))

    r = client.post(
        "/tenants/acme/historical/ingest",
        json={
            "entity": "PANW",
            "items": [
                {
                    "url": "https://www.businesswire.com/panw-q1",
                    "title": "PANW Earnings release · FY2026-Q1 (pub 2026-01-28)",
                    "period": "FY2026-Q1",
                }
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["entity"] == "PANW"
    # The workspace must name the document as the user reviewed it — the
    # auto-title sent with the item — never the raw page <title>.
    assert body["documents"][0]["title"] == "PANW Earnings release · FY2026-Q1 (pub 2026-01-28)"
    # Bound verdicts carry the source pointer so the UI can cite the document.
    assert all("provenance" in v for v in body["documents"][0]["verdicts"])


def test_search_endpoint_returns_resolved_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubExa(search=SEARCH)
    monkeypatch.setattr("attest.ingestion.exa.LiveExaClient", lambda *a, **k: stub)
    client = TestClient(create_app(AttestService(edgar=_named_panw_edgar())))

    r = client.post(
        "/tenants/acme/historical/search",
        json={"entity": "Palo Alto Networks", "doc_types": ["release"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["entity"] == "PANW"
    assert body["candidates"]
