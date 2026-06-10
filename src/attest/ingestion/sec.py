"""Shared SEC transport: fair-access fetching and ticker resolution.

Both EDGAR connectors (the 8-K release fetcher and the companyfacts ingester)
speak to ``sec.gov`` / ``data.sec.gov`` under the same fair-access policy: a
declared ``User-Agent`` ("name contact@email") and spaced requests. The
transport is a plain ``url -> bytes`` callable so tests inject their own and
run offline.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable

Fetcher = Callable[[str], bytes]

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

DEFAULT_USER_AGENT = "attest/0.1 (contact unset; pass user_agent or set ATTEST_SEC_USER_AGENT)"


class UrlFetcher:
    """SEC fair-access transport: declared User-Agent, spaced requests."""

    def __init__(self, user_agent: str, min_interval: float = 0.12) -> None:
        self.user_agent = user_agent
        self.min_interval = min_interval
        self._last = 0.0

    def __call__(self, url: str) -> bytes:
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        self._last = time.monotonic()
        return data


def resolve_cik(fetch: Fetcher, ticker: str) -> int:
    """Ticker -> CIK via SEC's own mapping file."""
    table = json.loads(fetch(TICKERS_URL))
    for row in table.values():
        if row.get("ticker", "").upper() == ticker.upper():
            return int(row["cik_str"])
    raise LookupError(f"Ticker {ticker!r} not found in SEC's company_tickers.json")
