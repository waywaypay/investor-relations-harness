"""Deterministic press-release retrieval from EDGAR: 8-K Item 2.02 -> EX-99.1.

Semantic web search is structurally the wrong tool for "the past four quarters
of press releases": an embedding query returns the top-k most *similar* pages,
and every quarter exists as half a dozen near-duplicate mirrors plus an
advisory sibling with no numbers in it, while the issuer's IR landing page is
a JavaScript shell whose crawl contains no figures at all. EDGAR is the
authoritative enumeration instead: every US issuer files its earnings release
as a Form 8-K announcing **Item 2.02** with the release attached verbatim as
**Exhibit 99.1** — static HTML, no JavaScript, no bot wall, complete tables.

This connector walks ``data.sec.gov``'s submissions index (paging into the
older archive files when a lookback outruns the recent window), locates each
filing's EX-99.1 via the filing index, and recovers the prose with the same
:func:`~attest.extraction.text.extract_text` the upload path uses. It emits
:class:`~attest.ingestion.releases.EarningsRelease` records ready for the
analyze pipeline or :class:`~attest.ingestion.guidance.GuidanceConnector` —
this *is* the "SEC connector that already fetches the EX-99.1 text" the
guidance adapter assumes.

One filing per quarter is **not** true in the wild, and assuming it loses
quarters: issuers furnish guidance updates and outlook suspensions under Item
2.02, lenders furnish *monthly* credit metrics under it, and preliminary or
re-furnished results double a quarter up. Counting filings would let that
noise crowd the lookback window — ask for four quarters, get four filings
spanning two. So the walk enumerates **distinct fiscal quarters**: filings
that resolve to an already-covered quarter are compared and the best artifact
kept (a release that states its quarter in its own words beats one whose
period was inferred from the 8-K's period-of-report date; more detected
figures breaks ties), and a quarter occupied only by a date-inferred filing is
evicted when an older filing turns out to be a stated quarterly release the
window should have covered. The walk stops once the requested quarters are
covered and a new, older quarter appears that cannot improve them.

Like every connector it never guesses and reports what it could not do: an
earnings 8-K without a locatable exhibit, or a lookback the index cannot
satisfy, lands in ``ReleaseFetchReport.missing`` rather than vanishing.

The transport is injectable (``fetch: url -> bytes``) so tests run offline;
the default transport honors SEC fair-access policy (declared ``User-Agent``,
request spacing). Set ``ATTEST_SEC_USER_AGENT`` to "name contact@email".
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, replace

from attest.extraction.text import extract_text
from attest.ingestion.releases import EarningsRelease, ReleaseFetchReport, infer_period
from attest.ingestion.sec import (  # noqa: F401  (TICKERS_URL re-exported for callers)
    DEFAULT_USER_AGENT,
    TICKERS_URL,
    Fetcher,
    UrlFetcher,
    resolve_cik,
)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://data.sec.gov/submissions/{name}"
FILING_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}"

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
_EXHIBIT_NAME_RE = re.compile(r"ex.{0,3}99|press", re.IGNORECASE)


@dataclass(frozen=True)
class _Filing:
    accession: str
    filing_date: str
    report_date: str


def _filing_rows(block: dict) -> Iterator[dict]:
    """Yield per-filing dicts from EDGAR's parallel-array filing block.

    Both shapes are handled: ``filings.recent`` in the main submissions JSON
    and the bare top-level arrays of the older archive files.
    """
    block = block.get("filings", {}).get("recent", block)
    forms = block.get("form", [])
    keys = ("accessionNumber", "filingDate", "reportDate", "items", "form")
    for i in range(len(forms)):
        yield {key: (block.get(key) or [""] * len(forms))[i] for key in keys}


def _exhibit_from_index_html(index_html: str) -> str | None:
    """Pull the EX-99.1 document href out of a filing's ``-index.htm`` table.

    The Type column is authoritative. An exact ``EX-99.1`` row wins; any other
    ``EX-99.*`` row is the fallback (some filers label the release ``EX-99``).
    """
    fallback: str | None = None
    for row in _ROW_RE.finditer(index_html):
        body = row.group(1)
        href = _HREF_RE.search(body)
        if not href:
            continue
        if re.search(r">\s*EX-99\.1\s*<", body, re.IGNORECASE):
            return href.group(1)
        if fallback is None and re.search(r">\s*EX-99(\.\d+)?\s*<", body, re.IGNORECASE):
            fallback = href.group(1)
    return fallback


def _exhibit_from_listing(listing: dict) -> str | None:
    """Filename-heuristic fallback over the filing directory's ``index.json``.

    PDFs are eligible — some filers attach the release as PDF only, and the
    extraction edge has a best-effort PDF path — but an HTML sibling wins.
    """
    names = [
        item.get("name", "")
        for item in listing.get("directory", {}).get("item", [])
        if item.get("name", "").lower().endswith((".htm", ".html", ".pdf"))
    ]
    matches = [name for name in names if _EXHIBIT_NAME_RE.search(name)]
    # Exhibit-numbered names first, then HTML over PDF.
    matches.sort(key=lambda name: ("99" not in name, name.lower().endswith(".pdf")))
    return matches[0] if matches else None


class EdgarReleaseConnector:
    """Enumerates and fetches quarterly earnings releases from EDGAR."""

    def __init__(self, *, user_agent: str | None = None, fetch: Fetcher | None = None) -> None:
        agent = user_agent or os.environ.get("ATTEST_SEC_USER_AGENT") or DEFAULT_USER_AGENT
        self._fetch = fetch or UrlFetcher(agent)

    def resolve_cik(self, ticker: str) -> int:
        """Ticker -> CIK via SEC's own mapping file."""
        return resolve_cik(self._fetch, ticker)

    def fetch_quarterly(
        self, issuer: str, quarters: int = 4
    ) -> tuple[list[EarningsRelease], ReleaseFetchReport]:
        """The last ``quarters`` *distinct* reported quarters for ``issuer``.

        Enumeration is exact — 8-Ks announcing Item 2.02, newest first — and
        counted in **quarters covered, not filings seen**: a quarter that
        carries several Item 2.02 filings (guidance updates, monthly metrics,
        preliminary results) yields its single best release instead of
        crowding older quarters out of the window. Shortfalls are reported,
        never papered over.
        """
        cik = int(issuer) if issuer.isdigit() else self.resolve_cik(issuer)
        submissions = json.loads(self._fetch(SUBMISSIONS_URL.format(cik=cik)))
        entity = issuer.upper() if not issuer.isdigit() else self._entity_label(submissions)

        # quarter key -> (best release so far, period stated in its own text?)
        chosen: dict[str, tuple[EarningsRelease, bool]] = {}
        missing: list[str] = []
        for filing in self._earnings_filings(submissions):
            release = self._fetch_release(cik, entity, filing, missing)
            if release is None:
                continue
            key, stated = _quarter_claim(release)
            if key in chosen:
                chosen[key] = _resolve_duplicate(chosen[key], (release, stated), key)
                continue
            if len(chosen) < quarters:
                chosen[key] = (release, stated)
                continue
            # Window full and an older quarter surfaced. A stated quarterly
            # release reclaims a slot held by date-inferred noise (a lender's
            # monthly 8-K claiming the in-progress quarter); otherwise the
            # requested quarters are covered and the walk is done.
            evictable = next((k for k, (_, s) in chosen.items() if not s), None)
            if not stated or evictable is None:
                break
            del chosen[evictable]
            chosen[key] = (release, stated)

        releases = sorted(
            (release for release, _ in chosen.values()),
            key=lambda r: (r.filing_date or "", r.accession or ""),
            reverse=True,
        )
        if len(releases) < quarters:
            missing.append(
                f"index satisfied only {len(releases)} of {quarters} requested quarters"
            )
        report = ReleaseFetchReport(
            source="edgar_8k_ex99",
            requested=quarters,
            fetched=len(releases),
            missing=tuple(missing),
        )
        return releases, report

    @staticmethod
    def _entity_label(submissions: dict) -> str:
        tickers = submissions.get("tickers") or []
        return tickers[0] if tickers else submissions.get("name", "unknown")

    def _earnings_filings(self, submissions: dict) -> Iterator[_Filing]:
        """All earnings 8-Ks, newest first, paging into archive files on demand."""
        yield from self._earnings_in(submissions)
        archives = submissions.get("filings", {}).get("files", [])
        # The archive list's order is not contractual; keep the walk newest-first.
        for archive in sorted(archives, key=lambda a: a.get("filingTo", ""), reverse=True):
            older = json.loads(self._fetch(ARCHIVE_URL.format(name=archive["name"])))
            yield from self._earnings_in(older)

    @staticmethod
    def _earnings_in(block: dict) -> Iterator[_Filing]:
        for row in _filing_rows(block):
            items = {item.strip() for item in row["items"].split(",")}
            if row["form"] == "8-K" and "2.02" in items:
                yield _Filing(
                    accession=row["accessionNumber"],
                    filing_date=row["filingDate"],
                    report_date=row["reportDate"],
                )

    def _fetch_release(
        self, cik: int, entity: str, filing: _Filing, missing: list[str]
    ) -> EarningsRelease | None:
        exhibit_url = self._locate_exhibit(cik, filing.accession)
        if exhibit_url is None:
            missing.append(f"{filing.accession}: no EX-99.1 located in the filing index")
            return None
        extracted = extract_text(exhibit_url.rsplit("/", 1)[-1], self._fetch(exhibit_url))
        warnings = list(extracted.warnings)
        release = EarningsRelease(
            entity=entity,
            title=_title_from_text(extracted.text),
            period=infer_period(extracted.text[:400], filing.report_date),
            url=exhibit_url,
            text=extracted.text,
            accession=filing.accession,
            filing_date=filing.filing_date,
            report_date=filing.report_date,
            warnings=tuple(warnings),
        )
        if release.figure_count == 0:
            release = EarningsRelease(
                **{**release.__dict__, "warnings": (*warnings, "no figures detected in text")}
            )
        return release

    def _locate_exhibit(self, cik: int, accession: str) -> str | None:
        """Find the EX-99.1 document URL for a filing.

        The ``-index.htm`` Type column is authoritative; the directory
        listing's filename heuristic is the fallback. ``None`` means the
        exhibit genuinely could not be located — reported, never guessed.
        """
        base = FILING_BASE.format(cik=cik, acc_nodash=accession.replace("-", ""))
        index_html = self._fetch(f"{base}/{accession}-index.htm").decode("utf-8", "replace")
        href = _exhibit_from_index_html(index_html)
        if href is None:
            listing = json.loads(self._fetch(f"{base}/index.json"))
            href = _exhibit_from_listing(listing)
        if href is None:
            return None
        return _absolute(href, base)


def _quarter_claim(release: EarningsRelease) -> tuple[str, bool]:
    """Which fiscal quarter a release claims, and on what authority.

    A quarter the release states in its own opening text is authoritative
    (``True``); the 8-K period-of-report calendar fallback is provisional
    (``False``) — it is how a guidance update or a monthly-metrics filing
    masquerades as a quarter it does not actually report.
    """
    stated = infer_period(release.text[:400])
    if stated is not None:
        return stated, True
    if release.period:
        return release.period, False
    return f"unstated:{release.accession}", False


def _resolve_duplicate(
    current: tuple[EarningsRelease, bool],
    candidate: tuple[EarningsRelease, bool],
    key: str,
) -> tuple[EarningsRelease, bool]:
    """Pick the better of two Item 2.02 filings claiming the same quarter.

    A stated-quarter release beats a date-inferred one; more detected figures
    breaks ties (the earnings release dwarfs the guidance update it shares a
    quarter with); a dead tie keeps the newer filing. The runner-up is noted
    on the kept release — visible, never silently dropped.
    """
    cur_release, cur_stated = current
    new_release, new_stated = candidate
    if (new_stated, new_release.figure_count) > (cur_stated, cur_release.figure_count):
        winner, winner_stated, loser = new_release, new_stated, cur_release
    else:
        winner, winner_stated, loser = cur_release, cur_stated, new_release
    note = (
        f"{key} also claimed by {loser.accession} (filed {loser.filing_date}, "
        f"{loser.figure_count} figures) — kept this release"
    )
    return replace(winner, warnings=(*winner.warnings, note)), winner_stated


def _absolute(href: str, base: str) -> str:
    """Resolve an index href to a fetchable URL, unwrapping the iXBRL viewer."""
    if href.startswith("/ix?doc="):
        href = href[len("/ix?doc=") :]
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://www.sec.gov{href}"
    return f"{base}/{href}"


def _title_from_text(text: str) -> str:
    """A display title: the '<Issuer> Reports ... Results' line when present."""
    m = re.search(r"^.{0,100}\breports\b.{0,100}$", text, re.IGNORECASE | re.MULTILINE)
    if m:
        return m.group(0).strip()
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return "(empty document)"
