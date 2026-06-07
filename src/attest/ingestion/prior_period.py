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

# Filename signals for the earnings press release exhibit within a filing. The
# release is Exhibit 99.1, but filers name the file a dozen ways ("ex99-1.htm",
# "ex991q1press.htm", "exhibit991.htm", "...earningsrelease.htm"); these match the
# common shapes, separators and all, against the lowercased filename.
_EX99_1_NAME = re.compile(r"ex(?:hibit)?[^a-z0-9]*99[^a-z0-9]*1")
_EX99_NAME = re.compile(r"ex(?:hibit)?[^a-z0-9]*99")
_EARNINGS_RELEASE_NAME = re.compile(r"earnings[^a-z0-9]*release")
_PRESS_RELEASE_NAME = re.compile(r"press[^a-z0-9]*release")


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


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    """``(year, month)`` shifted by ``delta`` months, wrapping the year."""
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _filing_date_window(period: str, fiscal_year_end: str = "1231") -> tuple[str, str] | None:
    """Return ``(start_date, end_date)`` for when an 8-K for this period is typically filed.

    The window is derived from the *issuer's* fiscal-year-end (``fiscal_year_end``
    as the ``MMDD`` SEC's submissions feed reports, e.g. ``"0731"`` for a 31-July
    year end), not a fixed calendar assumption. The earnings 8-K lands in the ~2.5
    months after a quarter closes, so the window runs from the first day of the
    month after quarter-end to the 15th of the month two later.

    For a calendar-year issuer (the default ``"1231"``) this reproduces the prior
    behaviour exactly — Q1 (ends 31 Mar) is filed Apr 1–Jun 15 — while a July-FYE
    issuer like PANW has its Q1 (ends 31 Oct) filed Nov 1–Jan 15, so the right
    quarter's release is found instead of a calendar-shifted (wrong) one.
    """
    m = re.match(r"FY(\d{4})-Q([1-4])", period, re.IGNORECASE)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    try:
        fye_month = int(fiscal_year_end[:2])
    except (TypeError, ValueError, IndexError):
        fye_month = 12
    if not 1 <= fye_month <= 12:
        fye_month = 12

    # The quarter-end month/year: Q4 ends at the fiscal-year-end; each earlier
    # quarter is three months before the next.
    end_year, end_month = _add_months(year, fye_month, -3 * (4 - q))
    start_year, start_month = _add_months(end_year, end_month, 1)
    stop_year, stop_month = _add_months(end_year, end_month, 3)
    return (f"{start_year:04d}-{start_month:02d}-01", f"{stop_year:04d}-{stop_month:02d}-15")


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
        return self._search_8k_exhibits(cik=resolved, period=prev)

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
        self, *, cik: str, period: str
    ) -> list[FetchedExhibit]:
        url = f"{_EDGAR_SUBMISSIONS}/CIK{cik}.json"
        try:
            data = self._http.get_json(url)
        except EdgarFetchError:
            return []

        # The same submissions feed carries the issuer's fiscal-year-end, so the
        # filing window is anchored to *its* calendar, not a fixed Dec-31 assumption.
        window = _filing_date_window(period, data.get("fiscalYearEnd") or "1231")
        if not window:
            return []
        start, end = window

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
        # `document` (the filing's primaryDocument) is the 8-K *cover page*, not the
        # press release — it carries no results/guidance prose. The earnings release
        # is filed as a separate Exhibit 99.1 document, so resolve and fetch that.
        target = self._resolve_exhibit_document(
            cik_int=cik_int, acc_nodash=acc_nodash, fallback=document
        )
        url = f"{_EDGAR_ARCHIVES}/{cik_int}/{acc_nodash}/{target}"
        try:
            raw = self._http.get_text(url)
        except EdgarFetchError:
            return None
        return _strip_html(raw) if raw.lstrip().startswith("<") else raw

    def _resolve_exhibit_document(
        self, *, cik_int: str, acc_nodash: str, fallback: str
    ) -> str:
        """The filename of the earnings press release (Exhibit 99.1) in a filing.

        Reads the filing's ``index.json`` document listing and picks the EX-99.1
        exhibit — recognised by its filename (``ex99-1``/``ex991``/``exhibit99-1``)
        or an ``earnings release`` name. Falls back to ``fallback`` (the filing's
        primary document) when the index can't be read or names no exhibit, so the
        fetch degrades to the prior behaviour rather than failing.
        """
        idx_url = f"{_EDGAR_ARCHIVES}/{cik_int}/{acc_nodash}/index.json"
        try:
            index = self._http.get_json(idx_url)
        except EdgarFetchError:
            return fallback

        best_priority, best_size, best_name = 0, -1, fallback
        for item in index.get("directory", {}).get("item", []):
            name = item.get("name", "")
            low = name.lower()
            if not low.endswith((".htm", ".html", ".txt")):
                continue
            if low.endswith(("-index.html", "-index-headers.html")) or low == fallback.lower():
                continue
            if re.fullmatch(r"r\d+\.htm", low):  # XBRL viewer fragments
                continue
            if _EX99_1_NAME.search(low) or _EARNINGS_RELEASE_NAME.search(low):
                priority = 3
            elif _EX99_NAME.search(low) or _PRESS_RELEASE_NAME.search(low):
                priority = 2
            else:
                continue  # not an exhibit press release — leave the fallback in place
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            # Strongest filename signal wins; among equals, the largest document —
            # the substantive release, not a one-paragraph supplemental exhibit.
            if (priority, size) > (best_priority, best_size):
                best_priority, best_size, best_name = priority, size, name
        return best_name
