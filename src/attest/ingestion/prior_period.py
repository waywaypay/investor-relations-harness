"""Auto-fetch prior period 8-K press releases from EDGAR.

Companies file quarterly earnings results as an 8-K with Exhibit 99.1 (the press
release). This connector locates the prior quarter's filing for a given entity and
fetches its exhibit text, so the verification spine can ingest prior-period source
facts without requiring the user to manually upload them.

Fetched text is ingested via :class:`~attest.ingestion.guidance.GuidanceConnector` —
the same path a manually uploaded press release takes — so provenance is identical:
each extracted figure carries the exact sentence it came from, cited to the filed
8-K accession number.

HTTP calls are isolated behind :class:`EdgarHttpClient` so tests inject a stub
without touching the network.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol

_EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions"
_EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_USER_AGENT = "Attest/0.1 (investor-relations-harness; contact@attest.io)"

# 8-K item 2.02 = Results of Operations (the earnings 8-K)
_EARNINGS_ITEMS = frozenset({"2.02", "2.01"})


class EdgarFetchError(Exception):
    """Raised when an EDGAR HTTP request fails."""


@dataclass
class FetchedExhibit:
    """Plain text of one EDGAR 8-K exhibit with its filing provenance."""

    accession: str    # e.g. "0001047469-26-001200"
    filing_date: str  # ISO date, e.g. "2026-04-28"
    label: str        # human-readable citation, e.g. "Form 8-K · Exhibit 99.1 · 2026-04-28"
    text: str         # plain text of the exhibit


class EdgarHttpClient(Protocol):
    """Minimal HTTP interface — inject a stub in tests instead of hitting the network."""

    def get_json(self, url: str) -> dict: ...
    def get_text(self, url: str) -> str: ...


class LiveEdgarClient:
    """EDGAR HTTP client backed by stdlib urllib (no extra dependencies)."""

    def _fetch(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise EdgarFetchError(f"HTTP {exc.code}: {url}") from exc
        except urllib.error.URLError as exc:
            raise EdgarFetchError(f"Network error fetching {url}: {exc.reason}") from exc

    def get_json(self, url: str) -> dict:
        return json.loads(self._fetch(url))

    def get_text(self, url: str) -> str:
        raw = self._fetch(url)
        for enc in ("utf-8", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")


class _HtmlStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped)


def _strip_html(html: str) -> str:
    p = _HtmlStripper()
    p.feed(html)
    return " ".join(p._parts)


def prior_period(period: str) -> str | None:
    """Return the fiscal quarter preceding ``period``.

    >>> prior_period("FY2026-Q2")
    'FY2026-Q1'
    >>> prior_period("FY2026-Q1")
    'FY2025-Q4'
    """
    m = re.match(r"FY(\d{4})-Q([1-4])", period, re.IGNORECASE)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    return f"FY{year - 1}-Q4" if q == 1 else f"FY{year}-Q{q - 1}"


def _filing_date_window(period: str) -> tuple[str, str] | None:
    """Return ``(start_date, end_date)`` for when an 8-K for this period is typically filed.

    Assumes a calendar-year fiscal year. An 8-K for Q1 (Jan–Mar) is filed
    April–June; Q4 (Oct–Dec) straddles the year and is filed January–March of
    the following year.
    """
    m = re.match(r"FY(\d{4})-Q([1-4])", period, re.IGNORECASE)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    windows: dict[int, tuple[str, str]] = {
        1: (f"{year}-04-01", f"{year}-06-15"),
        2: (f"{year}-07-01", f"{year}-09-15"),
        3: (f"{year}-10-01", f"{year}-12-15"),
        4: (f"{year + 1}-01-01", f"{year + 1}-03-15"),
    }
    return windows[q]


class PriorPeriodFetcher:
    """Locates and fetches prior-period 8-K earnings press releases from EDGAR.

    Given a ticker and the *current* period, derives the prior fiscal quarter,
    estimates the filing-date window, and returns exhibit texts with provenance.
    Returns ``[]`` when nothing is found — never raises on lookup failures.
    """

    def __init__(self, client: EdgarHttpClient | None = None) -> None:
        self._http = client or LiveEdgarClient()
        self._cik_cache: dict[str, str | None] = {}

    def fetch_exhibits(
        self,
        *,
        ticker: str,
        period: str,
        cik: str | None = None,
    ) -> list[FetchedExhibit]:
        """Return exhibit texts for 8-K filings matching the prior quarter of ``period``.

        Derives the prior fiscal quarter internally, so the caller passes the
        *current* period (e.g. ``"FY2026-Q2"``) and this method fetches the
        prior quarter's (``"FY2026-Q1"``) press release.

        If ``cik`` is not supplied it is resolved via the SEC company-tickers
        list. Returns ``[]`` when the ticker cannot be resolved, when no matching
        8-K exists, or when any network request fails.
        """
        resolved = cik or self._lookup_cik(ticker)
        if not resolved:
            return []
        prev = prior_period(period)
        if not prev:
            return []
        window = _filing_date_window(prev)
        if not window:
            return []
        return self._search_8k_exhibits(cik=resolved, start=window[0], end=window[1])

    def _lookup_cik(self, ticker: str) -> str | None:
        key = ticker.upper()
        if key in self._cik_cache:
            return self._cik_cache[key]
        try:
            data = self._http.get_json(_TICKERS_URL)
        except EdgarFetchError:
            self._cik_cache[key] = None
            return None
        for entry in data.values():
            if entry.get("ticker", "").upper() == key:
                cik = str(entry["cik_str"]).zfill(10)
                self._cik_cache[key] = cik
                return cik
        self._cik_cache[key] = None
        return None

    def _search_8k_exhibits(
        self, *, cik: str, start: str, end: str
    ) -> list[FetchedExhibit]:
        url = f"{_EDGAR_SUBMISSIONS}/CIK{cik}.json"
        try:
            data = self._http.get_json(url)
        except EdgarFetchError:
            return []

        recent = data.get("filings", {}).get("recent", {})
        exhibits: list[FetchedExhibit] = []

        rows = zip(
            recent.get("accessionNumber", []),
            recent.get("filingDate", []),
            recent.get("form", []),
            recent.get("items", []),
            recent.get("primaryDocument", []),
        )
        for acc, date, form, items_str, primary_doc in rows:
            if form != "8-K":
                continue
            if not (start <= date <= end):
                continue
            items = {i.strip() for i in items_str.split(",")}
            if not items & _EARNINGS_ITEMS:
                continue
            text = self._fetch_exhibit_text(cik=cik, accession=acc, document=primary_doc)
            if text:
                exhibits.append(FetchedExhibit(
                    accession=acc,
                    filing_date=date,
                    label=f"Form 8-K · Exhibit 99.1 · {date}",
                    text=text,
                ))

        return exhibits

    def _fetch_exhibit_text(
        self, *, cik: str, accession: str, document: str
    ) -> str | None:
        # EDGAR archive path: /data/{cik_int}/{acc_nodash}/{document}
        cik_int = str(int(cik))
        acc_nodash = accession.replace("-", "")
        url = f"{_EDGAR_ARCHIVES}/{cik_int}/{acc_nodash}/{document}"
        try:
            raw = self._http.get_text(url)
        except EdgarFetchError:
            return None
        return _strip_html(raw) if raw.lstrip().startswith("<") else raw
