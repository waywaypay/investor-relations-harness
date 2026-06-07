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

from attest.extraction.claims import infer_period

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
    title: str           # auto-generated, e.g. "PANW Earnings call transcript · FY2026-Q1 (pub 2026-01-28)"
    published_date: str  # ISO date "YYYY-MM-DD", or "" when Exa returns none
    source: str          # display domain, e.g. "fool.com"
    snippet: str         # short highlight for the review row
    doc_type: str        # "release" | "transcript"
    period: str | None = None  # fiscal period read from the doc itself, e.g. "FY2026-Q3"


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


def _auto_title(
    entity: str, doc_type: str, published_date: str, period: str | None, fallback: str
) -> str:
    """An auto-generated, human-readable title carrying the *fiscal* period it reports.

    The period is read from the document's own words (title / highlight), not guessed
    from the publish date — an issuer's reporting quarter and the calendar quarter it
    publishes in routinely differ (a July-fiscal-year company reports its Q3 in June),
    so a date-derived label would contradict the document beside it. When no period can
    be read, the title falls back to just the publish date rather than inventing one.
    """
    label = _DOC_TYPES.get(doc_type, ("", "Disclosure"))[1]
    ent = entity.strip().upper()
    if period and published_date:
        return f"{ent} {label} · {period} (pub {published_date})"
    if period:
        return f"{ent} {label} · {period}"
    if published_date:
        return f"{ent} {label} (pub {published_date})"
    return fallback.strip() or f"{ent} {label}"


def _one_per_period(cands: list[ExaCandidate], quarters: int) -> list[ExaCandidate]:
    """Collapse a result pool to one candidate per fiscal period, newest first.

    Several outlets report the same quarter; keep the single best hit per period
    (the most recently published) and return the most recent ``quarters`` periods.
    Hits whose period can't be read are kept only to backfill when there aren't
    enough dated quarters — so the reviewer never sees the same quarter twice, but
    isn't left empty-handed when periods are unreadable.
    """
    best_by_period: dict[str, ExaCandidate] = {}
    undated: list[ExaCandidate] = []
    for c in cands:
        if not c.period:
            undated.append(c)
            continue
        cur = best_by_period.get(c.period)
        if cur is None or c.published_date > cur.published_date:
            best_by_period[c.period] = c
    perioded = sorted(best_by_period.values(), key=lambda c: c.period or "", reverse=True)
    kept = perioded[:quarters]
    if len(kept) < quarters:
        kept += undated[: quarters - len(kept)]
    return kept


class HistoricalFetcher:
    """Searches Exa for a company's earnings docs, then fetches selected texts.

    Never raises on an *empty* result — only when Exa itself is unreachable or
    unconfigured (so the API layer can distinguish "nothing found" from "couldn't
    look").
    """

    def __init__(self, client: ExaHttpClient | None = None) -> None:
        self._http = client or LiveExaClient()

    def search(
        self, *, entity: str, doc_types: list[str], quarters: int = 4
    ) -> list[ExaCandidate]:
        """One candidate per fiscal period, for the most recent ``quarters`` periods.

        Runs one Exa search per requested type over a generous result pool, reads
        each hit's reporting period, then collapses the pool to a single best
        candidate per period — several outlets cover the same quarter, and the
        reviewer wants one release (and one transcript) per quarter, not the same
        period repeated. Returns the newest ``quarters`` periods per type, newest
        first overall. ``entity`` is a ticker or company name.
        """
        wanted = [d for d in doc_types if d in _DOC_TYPES] or list(_DOC_TYPES)
        # Pull a wider pool than we keep: multiple outlets cover each quarter, so we
        # need headroom to still cover `quarters` distinct periods after collapsing.
        pool = min(30, max(10, quarters * 3))

        candidates: list[ExaCandidate] = []
        seen: set[str] = set()
        for doc_type in wanted:
            suffix = _DOC_TYPES[doc_type][0]
            data = self._http.post_json(
                "/search",
                {
                    "query": f"{entity} {suffix}",
                    "numResults": pool,
                    "type": "auto",
                    "contents": {"highlights": {"numSentences": 2, "highlightsPerUrl": 1}},
                },
            )
            pool_candidates: list[ExaCandidate] = []
            for r in data.get("results", []):
                url = r.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                published = (r.get("publishedDate") or "")[:10]
                result_title = r.get("title", "") or ""
                highlights = r.get("highlights") or []
                snippet = (highlights[0] if highlights else result_title)[:240]
                # Read the fiscal period from the document's own title / highlight,
                # so the label matches the prose (and anchors ingest to the right quarter).
                period = infer_period(result_title, snippet)
                pool_candidates.append(
                    ExaCandidate(
                        url=url,
                        title=_auto_title(entity, doc_type, published, period, result_title),
                        published_date=published,
                        source=_domain(url),
                        snippet=snippet,
                        doc_type=doc_type,
                        period=period,
                    )
                )
            candidates.extend(_one_per_period(pool_candidates, quarters))

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
