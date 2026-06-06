"""Fetch a company's historical earnings releases and call transcripts via Exa.

SEC EDGAR throttles datacenter IPs, so live filing fetches are unreliable from a
hosted backend. Exa (https://exa.ai) is a neural search API that returns web
documents *with their published dates* — a robust path to a company's historical
earnings press releases (its IR site, the wires: Business Wire, GlobeNewswire,
PR Newswire) and call transcripts (the transcript publishers), reachable from any
cloud host.

The fetched prose flows into the same :class:`~attest.ingestion.guidance.DisclosureConnector`
path a manually uploaded transcript takes, so provenance is identical in shape:
each extracted figure cites the exact sentence it came from, attributed to the
source URL and its published date. These are web sources, not filings — they land
as ``PRIOR_DISCLOSURE`` reference facts (for consistency checks), never as a filed
source a draft can *trace* to.

Two-step by design, matching the review-then-load UI:

* :meth:`HistoricalFetcher.search` returns lightweight candidates (auto-titled
  with the publish date / quarter) the user reviews and selects — no full text.
* :meth:`HistoricalFetcher.fetch_contents` pulls the full text for the chosen
  URLs at ingest time.

HTTP is isolated behind :class:`ExaHttpClient` so tests inject a stub without
touching the network or needing an API key.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

_EXA_BASE = "https://api.exa.ai"

# The two document classes the IR workflow cares about, with the query suffix and
# the human label each gets in an auto-generated title.
_DOC_TYPES: dict[str, tuple[str, str]] = {
    "release": ("quarterly earnings results press release", "Earnings release"),
    "transcript": ("earnings call transcript prepared remarks", "Earnings call transcript"),
}


class ExaUnavailable(Exception):
    """Raised when an Exa request fails (network/HTTP)."""


class ExaNotConfigured(ExaUnavailable):
    """Raised when EXA_API_KEY is not set — the feature isn't configured."""


@dataclass
class ExaCandidate:
    """A search hit the user reviews before ingesting (no full text yet)."""

    url: str
    title: str           # auto-generated, e.g. "PANW Earnings call transcript · 2026-Q1 (pub 2026-01-28)"
    published_date: str  # ISO date "YYYY-MM-DD", or "" when Exa returns none
    source: str          # display domain, e.g. "fool.com"
    snippet: str         # short highlight for the review row
    doc_type: str        # "release" | "transcript"


@dataclass
class ExaDocument:
    """Full text of one selected document, fetched at ingest time."""

    url: str
    title: str
    published_date: str
    text: str


class ExaHttpClient(Protocol):
    """Minimal HTTP interface — inject a stub in tests instead of hitting Exa."""

    def post_json(self, path: str, payload: dict) -> dict: ...


class LiveExaClient:
    """Exa HTTP client backed by stdlib urllib (no extra dependencies).

    Reads the API key from ``EXA_API_KEY`` unless one is passed explicitly. A
    missing key raises :class:`ExaNotConfigured` so the API layer can return a
    clear "not configured" status rather than a generic failure.
    """

    def __init__(self, api_key: str | None = None, timeout: float = 20.0) -> None:
        self.api_key = api_key or os.environ.get("EXA_API_KEY")
        self.timeout = timeout

    def post_json(self, path: str, payload: dict) -> dict:
        if not self.api_key:
            raise ExaNotConfigured(
                "EXA_API_KEY is not set — set it to enable historical web search"
            )
        url = f"{_EXA_BASE}{path}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise ExaUnavailable(f"HTTP {exc.code} from Exa {path}") from exc
        except urllib.error.URLError as exc:
            raise ExaUnavailable(f"Network error reaching Exa: {exc.reason}") from exc


def _domain(url: str) -> str:
    """The bare host of a URL for display, e.g. ``https://www.fool.com/x`` -> ``fool.com``."""
    m = re.match(r"https?://([^/]+)", url)
    host = m.group(1) if m else url
    return host[4:] if host.startswith("www.") else host


def calendar_quarter(iso_date: str) -> str | None:
    """The calendar quarter a publish date falls in, e.g. ``2026-01-28`` -> ``2026-Q1``.

    This is the *publication* quarter (what the user asked to see in the title),
    not the issuer's fiscal reporting period — those can differ, and the figures
    themselves still resolve their own period from the prose.
    """
    m = re.match(r"(\d{4})-(\d{2})", iso_date)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    return f"{year}-Q{(month - 1) // 3 + 1}"


def _auto_title(entity: str, doc_type: str, published_date: str, fallback: str) -> str:
    """An auto-generated, human-readable title carrying the publish date / quarter."""
    label = _DOC_TYPES.get(doc_type, ("", "Disclosure"))[1]
    ent = entity.strip().upper()
    quarter = calendar_quarter(published_date)
    if published_date and quarter:
        return f"{ent} {label} · {quarter} (pub {published_date})"
    if published_date:
        return f"{ent} {label} (pub {published_date})"
    return fallback.strip() or f"{ent} {label}"


class HistoricalFetcher:
    """Searches Exa for a company's earnings docs, then fetches selected texts.

    Never raises on an *empty* result — only when Exa itself is unreachable or
    unconfigured (so the API layer can distinguish "nothing found" from "couldn't
    look").
    """

    def __init__(self, client: ExaHttpClient | None = None) -> None:
        self._http = client or LiveExaClient()

    def search(
        self, *, entity: str, doc_types: list[str], limit: int = 8
    ) -> list[ExaCandidate]:
        """Return auto-titled candidates for ``entity`` across the given doc types.

        Runs one Exa search per requested type, deduplicates by URL, and sorts
        newest-first. ``entity`` is a ticker or company name — both work as a
        neural-search query.
        """
        wanted = [d for d in doc_types if d in _DOC_TYPES] or list(_DOC_TYPES)
        per_type = max(1, limit // len(wanted))

        candidates: list[ExaCandidate] = []
        seen: set[str] = set()
        for doc_type in wanted:
            suffix = _DOC_TYPES[doc_type][0]
            data = self._http.post_json(
                "/search",
                {
                    "query": f"{entity} {suffix}",
                    "numResults": per_type,
                    "type": "auto",
                    "contents": {"highlights": {"numSentences": 2, "highlightsPerUrl": 1}},
                },
            )
            for r in data.get("results", []):
                url = r.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                published = (r.get("publishedDate") or "")[:10]
                highlights = r.get("highlights") or []
                snippet = (highlights[0] if highlights else r.get("title", "") or "")[:240]
                candidates.append(
                    ExaCandidate(
                        url=url,
                        title=_auto_title(entity, doc_type, published, r.get("title", "") or ""),
                        published_date=published,
                        source=_domain(url),
                        snippet=snippet,
                        doc_type=doc_type,
                    )
                )

        candidates.sort(key=lambda c: c.published_date, reverse=True)
        return candidates

    def fetch_contents(self, *, urls: list[str]) -> list[ExaDocument]:
        """Fetch the full text of the selected URLs. Skips any with empty text."""
        clean = [u for u in dict.fromkeys(urls) if u]  # dedupe, preserve order
        if not clean:
            return []
        data = self._http.post_json("/contents", {"urls": clean, "text": True})
        docs: list[ExaDocument] = []
        for r in data.get("results", []):
            text = (r.get("text") or "").strip()
            if not text:
                continue
            docs.append(
                ExaDocument(
                    url=r.get("url", ""),
                    title=r.get("title", "") or "",
                    published_date=(r.get("publishedDate") or "")[:10],
                    text=text,
                )
            )
        return docs
