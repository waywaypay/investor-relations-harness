"""Prior-period auto-fetch: unit and integration tests.

All EDGAR HTTP calls are intercepted by a stub client so no network access is
required. The stub returns canned responses that mirror the real EDGAR
submissions JSON and exhibit text shape.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from attest.api.app import create_app
from attest.ingestion.prior_period import (
    EdgarFetchError,
    FetchedExhibit,
    LiveEdgarClient,
    PriorPeriodFetcher,
    _filing_date_window,
    _strip_html,
    prior_period,
)
from attest.service import AttestService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TICKERS_JSON = {
    "0": {"cik_str": 1234567, "ticker": "MRDN", "title": "MERIDIAN SYSTEMS INC"},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}

_SUBMISSIONS_JSON = {
    "cik": "0001234567",
    "name": "MERIDIAN SYSTEMS INC",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0001234567-26-000100",
                "0001234567-26-000050",
                "0001234567-25-000400",
            ],
            "filingDate": ["2026-04-28", "2026-02-01", "2025-10-30"],
            "form": ["8-K", "8-K", "8-K"],
            "items": ["2.02,9.01", "5.02", "2.02,9.01"],
            "primaryDocument": ["ex99-1.htm", "ex99-1.htm", "ex99-1.htm"],
        }
    },
}

_PRESS_RELEASE_HTML = (
    "<html><body>"
    "<p>For the second quarter of fiscal 2026, the company expects total revenue "
    "in the range of $1.31 to $1.34 billion.</p>"
    "<p>First quarter revenue was $1.24 billion.</p>"
    "</body></html>"
)

_PRESS_RELEASE_TEXT = (
    "For the second quarter of fiscal 2026, the company expects total revenue "
    "in the range of $1.31 to $1.34 billion. "
    "First quarter revenue was $1.24 billion."
)

# Accession with dashes removed and the CIK integer
_ACC_NODASH = "000123456726000100"
_CIK_INT = "1234567"
_EXHIBIT_URL = (
    f"https://www.sec.gov/Archives/edgar/data/{_CIK_INT}/{_ACC_NODASH}/ex99-1.htm"
)


class _StubEdgarClient:
    """Deterministic stub — returns canned EDGAR responses, no network needed."""

    def __init__(
        self,
        *,
        tickers: dict | None = None,
        submissions: dict | None = None,
        exhibit_text: str | None = None,
        fail_tickers: bool = False,
        fail_submissions: bool = False,
        fail_exhibit: bool = False,
    ) -> None:
        self._tickers = tickers if tickers is not None else _TICKERS_JSON
        self._submissions = submissions if submissions is not None else _SUBMISSIONS_JSON
        self._exhibit = exhibit_text if exhibit_text is not None else _PRESS_RELEASE_HTML
        self._fail_tickers = fail_tickers
        self._fail_submissions = fail_submissions
        self._fail_exhibit = fail_exhibit

    def get_json(self, url: str) -> dict:
        if "company_tickers" in url:
            if self._fail_tickers:
                raise EdgarFetchError("tickers lookup failed")
            return self._tickers
        if "/submissions/" in url:
            if self._fail_submissions:
                raise EdgarFetchError("submissions lookup failed")
            return self._submissions
        return {}

    def get_text(self, url: str) -> str:
        if self._fail_exhibit:
            raise EdgarFetchError("exhibit fetch failed")
        return self._exhibit


# ---------------------------------------------------------------------------
# prior_period() helper
# ---------------------------------------------------------------------------

def test_prior_period_mid_year():
    assert prior_period("FY2026-Q2") == "FY2026-Q1"
    assert prior_period("FY2026-Q3") == "FY2026-Q2"
    assert prior_period("FY2026-Q4") == "FY2026-Q3"


def test_prior_period_crosses_year():
    assert prior_period("FY2026-Q1") == "FY2025-Q4"


def test_prior_period_invalid_returns_none():
    assert prior_period("FY2026-FY") is None
    assert prior_period("") is None
    assert prior_period("garbage") is None


# ---------------------------------------------------------------------------
# _filing_date_window()
# ---------------------------------------------------------------------------

def test_filing_window_q1():
    start, end = _filing_date_window("FY2026-Q1")
    assert start == "2026-04-01"
    assert end == "2026-06-15"


def test_filing_window_q4_crosses_year():
    start, end = _filing_date_window("FY2025-Q4")
    assert start == "2026-01-01"
    assert end == "2026-03-15"


def test_filing_window_invalid_returns_none():
    assert _filing_date_window("FY2026-FY") is None


# ---------------------------------------------------------------------------
# _strip_html()
# ---------------------------------------------------------------------------

def test_strip_html_removes_tags():
    result = _strip_html("<p>Revenue was <b>$1.24 billion</b>.</p>")
    assert "Revenue was" in result
    assert "$1.24 billion" in result
    assert "<" not in result


# ---------------------------------------------------------------------------
# PriorPeriodFetcher — ticker lookup
# ---------------------------------------------------------------------------

def test_fetcher_resolves_ticker_to_cik():
    stub = _StubEdgarClient()
    fetcher = PriorPeriodFetcher(client=stub)
    exhibits = fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2")
    assert len(exhibits) == 1
    assert exhibits[0].accession == "0001234567-26-000100"
    assert exhibits[0].filing_date == "2026-04-28"


def test_fetcher_ticker_lookup_is_case_insensitive():
    stub = _StubEdgarClient()
    fetcher = PriorPeriodFetcher(client=stub)
    exhibits = fetcher.fetch_exhibits(ticker="mrdn", period="FY2026-Q2")
    assert len(exhibits) == 1


def test_fetcher_accepts_explicit_cik():
    stub = _StubEdgarClient()
    fetcher = PriorPeriodFetcher(client=stub)
    exhibits = fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2", cik="0001234567")
    # CIK supplied: no ticker lookup needed — still finds the filing
    assert len(exhibits) == 1


def test_fetcher_unknown_ticker_returns_empty():
    stub = _StubEdgarClient(tickers={"0": {"cik_str": 999, "ticker": "ZZZZ", "title": "OTHER"}})
    fetcher = PriorPeriodFetcher(client=stub)
    assert fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2") == []


# ---------------------------------------------------------------------------
# PriorPeriodFetcher — filing selection
# ---------------------------------------------------------------------------

def test_fetcher_selects_earnings_8k_only():
    # Item 5.02 is a director change 8-K, not earnings — should not be returned.
    stub = _StubEdgarClient()
    fetcher = PriorPeriodFetcher(client=stub)
    exhibits = fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2")
    # Only the April 2026 8-K (item 2.02) should match for Q1 window (Apr–Jun 2026)
    assert all(e.accession == "0001234567-26-000100" for e in exhibits)


def test_fetcher_ignores_filings_outside_window():
    stub = _StubEdgarClient()
    fetcher = PriorPeriodFetcher(client=stub)
    # Q4 window is Jan–Mar 2026; the April 2026 filing is outside it
    exhibits = fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q1")
    # Q1 derives prior period FY2025-Q4; window = 2026-01-01 to 2026-03-15
    # The April 28 filing is outside that window
    assert exhibits == []


def test_fetcher_returns_q3_prior_period():
    # Confirm the October 2025 filing (Q3) is returned when fetching for FY2026-Q1
    # FY2026-Q1's prior = FY2025-Q4, window = Jan–Mar 2026.
    # But let's test FY2025-Q4 prior directly:
    # FY2025-Q4 → filing window Oct–Dec 2025, but our fixture has 2025-10-30 which IS in range.
    stub = _StubEdgarClient()
    fetcher = PriorPeriodFetcher(client=stub)
    # FY2026-Q1 prior = FY2025-Q4, window = 2026-01-01 to 2026-03-15: no match in fixture
    # FY2025-Q4 prior = FY2025-Q3, window = 2025-10-01 to 2025-12-15: the Oct 30 filing matches
    exhibits = fetcher.fetch_exhibits(ticker="MRDN", period="FY2025-Q4")
    assert len(exhibits) == 1
    assert exhibits[0].accession == "0001234567-25-000400"


# ---------------------------------------------------------------------------
# PriorPeriodFetcher — failure handling
# ---------------------------------------------------------------------------

def test_fetcher_graceful_on_ticker_lookup_failure():
    stub = _StubEdgarClient(fail_tickers=True)
    fetcher = PriorPeriodFetcher(client=stub)
    assert fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2") == []


def test_fetcher_graceful_on_submissions_failure():
    stub = _StubEdgarClient(fail_submissions=True)
    fetcher = PriorPeriodFetcher(client=stub)
    assert fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2") == []


def test_fetcher_graceful_on_exhibit_fetch_failure():
    stub = _StubEdgarClient(fail_exhibit=True)
    fetcher = PriorPeriodFetcher(client=stub)
    # Exhibit fetch fails but returns [] not raises
    assert fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2") == []


# ---------------------------------------------------------------------------
# Exhibit content
# ---------------------------------------------------------------------------

def test_fetcher_strips_html_from_exhibit():
    stub = _StubEdgarClient(exhibit_text=_PRESS_RELEASE_HTML)
    fetcher = PriorPeriodFetcher(client=stub)
    exhibits = fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2")
    assert len(exhibits) == 1
    assert "<" not in exhibits[0].text
    assert "$1.31 to $1.34 billion" in exhibits[0].text


def test_fetcher_preserves_plain_text_exhibit():
    stub = _StubEdgarClient(exhibit_text=_PRESS_RELEASE_TEXT)
    fetcher = PriorPeriodFetcher(client=stub)
    exhibits = fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2")
    assert exhibits[0].text == _PRESS_RELEASE_TEXT


def test_fetcher_exhibit_label_contains_date():
    stub = _StubEdgarClient()
    fetcher = PriorPeriodFetcher(client=stub)
    exhibits = fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2")
    assert "2026-04-28" in exhibits[0].label
    assert "8-K" in exhibits[0].label


# ---------------------------------------------------------------------------
# CIK cache
# ---------------------------------------------------------------------------

def test_fetcher_caches_cik_lookup():
    call_count = 0

    class _CountingClient(_StubEdgarClient):
        def get_json(self, url: str) -> dict:
            nonlocal call_count
            if "company_tickers" in url:
                call_count += 1
            return super().get_json(url)

    stub = _CountingClient()
    fetcher = PriorPeriodFetcher(client=stub)
    fetcher.fetch_exhibits(ticker="MRDN", period="FY2026-Q2")
    fetcher.fetch_exhibits(ticker="MRDN", period="FY2025-Q4")
    assert call_count == 1  # second call hits the cache


# ---------------------------------------------------------------------------
# Service integration
# ---------------------------------------------------------------------------

def test_service_fetch_prior_period_ingests_guidance_facts():
    svc = AttestService()
    stub = _StubEdgarClient(exhibit_text=_PRESS_RELEASE_TEXT)
    reports, prev, exhibits = svc.fetch_prior_period(
        tenant_id="meridian",
        entity="MRDN",
        period="FY2026-Q2",
        edgar_client=stub,
    )
    assert prev == "FY2026-Q1"
    assert len(exhibits) == 1
    assert len(reports) == 1
    assert reports[0].ingested >= 1
    # The guidance fact extracted from the press release must be in the store.
    facts = svc.store.all("meridian")
    assert any(f.metric == "revenue_guidance" for f in facts)


def test_service_fetch_prior_period_writes_audit_event():
    svc = AttestService()
    stub = _StubEdgarClient(exhibit_text=_PRESS_RELEASE_TEXT)
    before = len(svc.audit_export("meridian"))
    svc.fetch_prior_period(
        tenant_id="meridian",
        entity="MRDN",
        period="FY2026-Q2",
        edgar_client=stub,
    )
    after = len(svc.audit_export("meridian"))
    assert after == before + 1


def test_service_returns_prior_period_when_no_filing_found():
    stub = _StubEdgarClient(fail_submissions=True)
    svc = AttestService()
    reports, prev, exhibits = svc.fetch_prior_period(
        tenant_id="meridian",
        entity="MRDN",
        period="FY2026-Q2",
        edgar_client=stub,
    )
    assert prev == "FY2026-Q1"
    assert exhibits == []
    assert reports == []


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    return TestClient(create_app())


def test_prior_period_endpoint_returns_summary(client, monkeypatch):
    stub = _StubEdgarClient(exhibit_text=_PRESS_RELEASE_TEXT)
    monkeypatch.setattr("attest.ingestion.prior_period.LiveEdgarClient", lambda: stub)
    r = client.post(
        "/tenants/meridian/ingest/prior-period",
        json={"entity": "MRDN", "period": "FY2026-Q2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["prior_period"] == "FY2026-Q1"
    assert len(body["exhibits"]) == 1
    assert body["total_ingested"] >= 1
    assert body["exhibits"][0]["accession"] == "0001234567-26-000100"


def test_prior_period_endpoint_no_filing_found(client, monkeypatch):
    stub = _StubEdgarClient(fail_submissions=True)
    monkeypatch.setattr("attest.ingestion.prior_period.LiveEdgarClient", lambda: stub)
    r = client.post(
        "/tenants/meridian/ingest/prior-period",
        json={"entity": "MRDN", "period": "FY2026-Q2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["prior_period"] == "FY2026-Q1"
    assert body["exhibits"] == []
    assert body["total_ingested"] == 0


def test_prior_period_endpoint_accepts_explicit_cik(client, monkeypatch):
    stub = _StubEdgarClient(exhibit_text=_PRESS_RELEASE_TEXT)
    monkeypatch.setattr("attest.ingestion.prior_period.LiveEdgarClient", lambda: stub)
    r = client.post(
        "/tenants/meridian/ingest/prior-period",
        json={"entity": "MRDN", "period": "FY2026-Q2", "cik": "0001234567"},
    )
    assert r.status_code == 200
    assert r.json()["total_ingested"] >= 1
