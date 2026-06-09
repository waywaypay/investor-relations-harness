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


@dataclass(frozen=True)
class _DocTypeSpec:
    """How one document class is searched: the query suffix, the human label its
    auto-title gets, and the Exa content ``category`` that biases the search."""

    query_suffix: str
    label: str
    category: str


# The two document classes the IR workflow cares about. The release query targets
# the issuer's *own* earnings press release (its investor-relations site and the
# newswires it distributes through — Business Wire / GlobeNewswire / PR Newswire),
# not third-party recaps or analysis; ``category="news"`` keeps Exa on published
# press/news pages rather than blogs or filings. The transcript query targets the
# call's prepared remarks.

# Search and ingest should return the issuer's press-release prose, not SEC
# filing cover pages. Exa supports domain exclusions for
# normal/news search, but we also keep local guardrails because search providers can
# still surface mirrored filings from an issuer's own site.
_SEC_EXCLUDED_DOMAINS = ["sec.gov", "www.sec.gov"]

_SEC_COVER_RE = re.compile(
    r"\bUNITED\s+STATES\b.{0,400}?\bSECURITIES\s+AND\s+EXCHANGE\s+COMMISSION\b.{0,600}?\bFORM\s+8-K\b",
    re.IGNORECASE | re.DOTALL,
)
_EXHIBIT_99_RE = re.compile(
    r"(?:^|\n)\s*(?:EX-99\.1|EXHIBIT\s+99\.1|EXHIBIT\s+99(?:\s|$))\s*(?:\n|$|[:.-])",
    re.IGNORECASE,
)
_NEXT_EXHIBIT_RE = re.compile(r"(?:^|\n)\s*(?:EX-\d+|EXHIBIT\s+\d+)\b", re.IGNORECASE)


def _looks_like_sec_filing(text: str) -> bool:
    """True when Exa returned an SEC filing/8-K cover instead of article prose."""
    return bool(_SEC_COVER_RE.search(text[:4000]))


def _extract_exhibit_99(text: str) -> str | None:
    """Return the press-release exhibit from an 8-K text blob, when present.

    Some company IR sites expose a single filing text/PDF whose first page is the
    SEC cover sheet and whose actual earnings release is attached as Exhibit 99.1.
    Rendering the whole blob creates the vertical "UNITED / STATES / SECURITIES"
    page users reported.  If the exhibit is present, keep only that article-like
    section; otherwise the caller should skip the document rather than load a
    filing cover as a historical press release.
    """
    m = _EXHIBIT_99_RE.search(text)
    if not m:
        return None
    start = m.end()
    tail = text[start:]
    nxt = _NEXT_EXHIBIT_RE.search(tail)
    if nxt and nxt.start() > 200:
        tail = tail[: nxt.start()]
    # Filing text often appends signature boilerplate after the exhibit; drop it
    # when it appears well after the release body has started.
    sig = re.search(r"(?:^|\n)\s*SIGNATURES?\s*(?:\n|$)", tail, re.IGNORECASE)
    if sig and sig.start() > 50:
        tail = tail[: sig.start()]
    return tail.strip() or None


def _clean_historical_text(text: str) -> str:
    """Normalize fetched historical prose and discard SEC filing cover pages."""
    cleaned = re.sub(r"\r\n?", "\n", text).strip()
    if not cleaned:
        return ""
    if _looks_like_sec_filing(cleaned):
        exhibit = _extract_exhibit_99(cleaned)
        if not exhibit:
            return ""
        cleaned = exhibit
    # Exa markdown occasionally preserves very tall filing/PDF spacing. Keep real
    # paragraph breaks, but remove runs of whitespace-only lines that make the UI
    # look like a cover-page facsimile rather than prose.
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{4,}", "\n\n", cleaned)
    return cleaned.strip()

_DOC_TYPES: dict[str, _DocTypeSpec] = {
    "release": _DocTypeSpec(
        "investor relations earnings press release announces financial results",
        "Earnings release",
        "news",
    ),
    "transcript": _DocTypeSpec(
        "earnings call transcript prepared remarks",
        "Earnings call transcript",
        "news",
    ),
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


# What a bare ticker symbol looks like ("PANW", "BRK.B"). Auto-titles upper-case
# only these; a typed company name keeps its own casing instead of being shouted.
_TICKER_SHAPE = re.compile(r"[A-Za-z]{1,5}(?:[.\-][A-Za-z0-9]{1,2})?")


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
    spec = _DOC_TYPES.get(doc_type)
    label = spec.label if spec else "Disclosure"
    ent = entity.strip()
    if _TICKER_SHAPE.fullmatch(ent):
        ent = ent.upper()
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
        self,
        *,
        entity: str,
        doc_types: list[str],
        quarters: int = 4,
        label_entity: str | None = None,
    ) -> list[ExaCandidate]:
        """One candidate per fiscal period, for the most recent ``quarters`` periods.

        Runs one Exa search per requested type over a generous result pool, reads
        each hit's reporting period, then collapses the pool to a single best
        candidate per period — several outlets cover the same quarter, and the
        reviewer wants one release (and one transcript) per quarter, not the same
        period repeated. Returns the newest ``quarters`` periods per type, newest
        first overall. ``entity`` is a ticker or company name and drives the search
        query; ``label_entity`` (e.g. the resolved ticker) drives the auto-titles.
        """
        title_entity = (label_entity or entity).strip()
        wanted = [d for d in doc_types if d in _DOC_TYPES] or list(_DOC_TYPES)
        # Pull a wider pool than we keep: multiple outlets cover each quarter, so we
        # need headroom to still cover `quarters` distinct periods after collapsing.
        pool = min(30, max(10, quarters * 3))

        candidates: list[ExaCandidate] = []
        seen: set[str] = set()
        for doc_type in wanted:
            spec = _DOC_TYPES[doc_type]
            data = self._http.post_json(
                "/search",
                {
                    "query": f"{entity} {spec.query_suffix} -site:sec.gov -site:www.sec.gov",
                    "numResults": pool,
                    "type": "auto",
                    "category": spec.category,
                    "excludeDomains": _SEC_EXCLUDED_DOMAINS,
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
                        title=_auto_title(title_entity, doc_type, published, period, result_title),
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
            text = _clean_historical_text(r.get("text") or "")
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
