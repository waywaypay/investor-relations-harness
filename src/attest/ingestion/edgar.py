"""EDGAR company-facts ingestion — tie out a draft against *real* filed numbers.

:mod:`attest.ingestion.edgar_xbrl` ingests a hand-shaped XBRL instance (the demo
fixture). This connector is its live sibling: given a ticker, it pulls the
issuer's machine-tagged facts straight from SEC EDGAR's public ``companyconcept``
API and lands them in the fact store, so an uploaded transcript / release ties out
against the company's *as-filed* values rather than a bundled sample.

Two things make this honest rather than a scrape:

* **Provenance survives.** Every landed fact is ``EDGAR_XBRL`` with the
  originating accession + tag in ``source_ref`` and the filing date as ``as_of`` —
  the same shape the demo fixture produces, so the engine treats it identically
  and can still call it ``traced``.
* **The fiscal period is derived from the datapoint, not the filing.** SEC's
  ``fy``/``fp`` describe the *filing's* focus, so a prior-year comparative inside a
  10-Q carries the current filing's ``fy``/``fp`` even though it reports an earlier
  quarter. Binding on that would invent a restatement. We instead compute the
  fiscal period from each datapoint's own ``end`` date against the issuer's
  fiscal-year-end (``submissions.fiscalYearEnd``), so comparatives land in the
  period they actually report.

The transport is a swappable :class:`EdgarClient` Protocol. :class:`HttpEdgarClient`
is the real, stdlib-only (``urllib``) implementation; :class:`StaticEdgarClient`
backs the hermetic tests and offline demos with in-memory fixtures. The connector
itself never touches the network — it only maps client output onto :class:`Fact`.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Protocol

from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.domain.money import Unit
from attest.ingestion.base import IngestionReport

# SEC asks every automated client to identify itself with a descriptive
# User-Agent that includes a contact. Override in any real deployment.
_DEFAULT_USER_AGENT = "Attest disclosure-verification (contact: ir-ops@attest.example)"

# SEC unit string -> the dimension the registry compares in. EPS arrives as
# "USD/shares" but the registry models it as CURRENCY (a per-share dollar amount),
# matching how the demo fixture and the engine already treat it.
_UNIT_MAP: dict[str, Unit] = {
    "USD": Unit.CURRENCY,
    "USD/shares": Unit.CURRENCY,
    "USD/share": Unit.CURRENCY,
    "shares": Unit.SHARES,
    "pure": Unit.RATIO,
}

# Duration (in days) that counts as a single fiscal quarter. YTD and annual
# durations are skipped so a 6-month cumulative figure never masquerades as the
# quarter's value; balance-sheet (instantaneous) facts have no duration at all.
_QUARTER_DAYS = range(80, 101)

# Two reportings of the same (metric, period) within this relative distance are
# treated as the same fact (a later comparative rounded to a coarser unit), not a
# restatement. A genuine restatement moves a value far more than this.
_RESTATEMENT_TOLERANCE = Decimal("0.002")


def _close(a: Decimal, b: Decimal) -> bool:
    """True when two values match within the rounding-noise tolerance."""
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= _RESTATEMENT_TOLERANCE


def fiscal_period(end_iso: str, fiscal_year_end: str) -> str | None:
    """Map a period-end date to a fiscal period label like ``FY2026-Q2``.

    ``fiscal_year_end`` is the issuer's ``MMDD`` (e.g. ``"0731"`` for a 31-July
    year end, as SEC's submissions feed reports it). The label uses the convention
    that a fiscal year is named for the calendar year in which it *ends*: PANW's
    year ending 31 Jul 2026 is FY2026, so its quarter ending 31 Jan 2026 is
    ``FY2026-Q2``. Returns ``None`` if the date can't be parsed.
    """
    try:
        end = date.fromisoformat(end_iso)
    except (TypeError, ValueError):
        return None
    try:
        fye_month = int(fiscal_year_end[:2])
    except (TypeError, ValueError, IndexError):
        fye_month = 12
    if not 1 <= fye_month <= 12:
        fye_month = 12

    fiscal_year = end.year if end.month <= fye_month else end.year + 1
    # Months elapsed since the fiscal year started (the month after the FYE month).
    fiscal_month = ((end.month - fye_month - 1) % 12) + 1
    quarter = (fiscal_month + 2) // 3
    return f"FY{fiscal_year}-Q{quarter}"


class EdgarClient(Protocol):
    """The EDGAR transport the connector depends on (real HTTP or a test double)."""

    def resolve_cik(self, ticker: str) -> int | None:
        """Resolve a ticker to its SEC CIK, or ``None`` if unknown."""
        ...

    def resolve_ticker(self, query: str) -> str | None:
        """Resolve a typed ticker *or company name* to the issuer's ticker symbol."""
        ...

    def fiscal_year_end(self, cik: int) -> str | None:
        """The issuer's fiscal-year-end as ``MMDD`` (e.g. ``"0731"``), or ``None``."""
        ...

    def company_concept(self, cik: int, taxonomy: str, tag: str) -> dict | None:
        """Raw ``companyconcept`` JSON for one tag, or ``None`` if not reported."""
        ...


# Legal suffixes that carry no identity when matching a typed company name against
# SEC's registrant titles ("Palo Alto Networks" must match "Palo Alto Networks Inc").
_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies",
    "ltd", "limited", "plc", "lp", "llp", "llc", "sa", "nv", "ag", "se",
    "holdings", "holding", "group", "trust", "fund", "international",
})


def _normalize_company(name: str) -> str:
    """Lowercase, strip punctuation, and drop trailing legal suffixes."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    words = cleaned.split()
    while words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def match_company_ticker(query: str, rows: Iterable[tuple[str, str]]) -> str | None:
    """Resolve a typed ticker or company name against SEC ``(ticker, title)`` rows.

    An exact ticker match always wins (a valid symbol is never re-mapped by name).
    Otherwise the first row whose normalized registrant title equals — or extends —
    the normalized query wins; SEC orders the company list by market cap, so "apple"
    resolves to Apple Inc., not a smaller registrant that happens to share the word.
    Deliberately conservative: a query that is neither a known symbol nor a title
    prefix resolves to ``None`` rather than guessing.
    """
    typed = query.strip()
    if not typed:
        return None
    upper = typed.upper()
    rows = list(rows)
    for ticker, _ in rows:
        if ticker.upper() == upper:
            return ticker.upper()
    normalized = _normalize_company(typed)
    if len(normalized) < 3:
        return None
    for ticker, title in rows:
        candidate = _normalize_company(title)
        if candidate == normalized or candidate.startswith(normalized + " "):
            return ticker.upper()
    return None


class HttpEdgarClient:
    """The real client: SEC EDGAR over ``urllib`` (stdlib only).

    Lightweight in-process caching keeps a single ingest from refetching the
    ticker map and the issuer's submissions for every metric. Construction does no
    network I/O — fetches happen lazily on first use.
    """

    _TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    _SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
    _CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{taxonomy}/{tag}.json"

    def __init__(self, *, user_agent: str | None = None, timeout: float = 20.0) -> None:
        self.user_agent = user_agent or os.environ.get(
            "ATTEST_EDGAR_USER_AGENT", _DEFAULT_USER_AGENT
        )
        self.timeout = timeout
        # (ticker, cik, registrant title) rows in SEC's market-cap order.
        self._rows: list[tuple[str, int, str]] | None = None
        self._fye: dict[int, str | None] = {}

    def _get_json(self, url: str) -> dict | None:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # tag not reported by this issuer — not an error
                return None
            # Any other HTTP status is an availability failure, not a clean result:
            # SEC throttling (403/429) or an outage (5xx) must degrade to an honest
            # "couldn't reach EDGAR" warning, never crash the upload that triggered
            # the fetch. Conflating these with a hard error breaks the documented
            # guarantee that an EDGAR outage adds a warning, not a failure.
            raise EdgarUnavailable(f"HTTP {exc.code} from {url}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise EdgarUnavailable(str(exc)) from exc

    def _company_rows(self) -> list[tuple[str, int, str]]:
        if self._rows is None:
            data = self._get_json(self._TICKERS_URL) or {}
            self._rows = [
                (str(row["ticker"]).upper(), int(row["cik_str"]), str(row.get("title") or ""))
                for row in data.values()
                if row.get("ticker") and row.get("cik_str") is not None
            ]
        return self._rows

    def resolve_cik(self, ticker: str) -> int | None:
        wanted = ticker.strip().upper()
        for symbol, cik, _ in self._company_rows():
            if symbol == wanted:
                return cik
        return None

    def resolve_ticker(self, query: str) -> str | None:
        return match_company_ticker(
            query, ((symbol, title) for symbol, _, title in self._company_rows())
        )

    def fiscal_year_end(self, cik: int) -> str | None:
        if cik not in self._fye:
            data = self._get_json(self._SUBMISSIONS_URL.format(cik=cik)) or {}
            self._fye[cik] = data.get("fiscalYearEnd")
        return self._fye[cik]

    def company_concept(self, cik: int, taxonomy: str, tag: str) -> dict | None:
        return self._get_json(self._CONCEPT_URL.format(cik=cik, taxonomy=taxonomy, tag=tag))


class StaticEdgarClient:
    """An in-memory :class:`EdgarClient` for tests and offline demos.

    Seed it with a ticker->CIK map, per-CIK fiscal-year-end, and per-(cik, tag)
    ``companyconcept`` payloads. It performs no I/O, so the suite stays hermetic.
    """

    def __init__(
        self,
        *,
        tickers: dict[str, int],
        fiscal_year_ends: dict[int, str],
        concepts: dict[tuple[int, str], dict],
        titles: dict[str, str] | None = None,
    ) -> None:
        self._tickers = {k.upper(): v for k, v in tickers.items()}
        # ticker -> registrant title, for company-name resolution (optional).
        self._titles = {k.upper(): v for k, v in (titles or {}).items()}
        self._fye = fiscal_year_ends
        # keyed by (cik, "taxonomy:tag")
        self._concepts = concepts

    def resolve_cik(self, ticker: str) -> int | None:
        return self._tickers.get(ticker.strip().upper())

    def resolve_ticker(self, query: str) -> str | None:
        return match_company_ticker(
            query, ((t, self._titles.get(t, "")) for t in self._tickers)
        )

    def fiscal_year_end(self, cik: int) -> str | None:
        return self._fye.get(cik)

    def company_concept(self, cik: int, taxonomy: str, tag: str) -> dict | None:
        return self._concepts.get((cik, f"{taxonomy}:{tag}"))


class EdgarUnavailable(RuntimeError):
    """Raised when EDGAR can't be reached (network/transport failure).

    Distinct from "the issuer doesn't report this tag" (which is a ``None``
    concept, not an error), so callers can degrade honestly on an outage rather
    than conflating it with a clean "nothing to ingest" result.
    """


class EdgarConnector:
    """Maps an issuer's EDGAR ``companyconcept`` facts into the fact store.

    Only ``us-gaap`` tags on registered metrics are fetched (issuer-extension and
    segment tags vary too much to bind blindly); each quarterly or balance-sheet
    datapoint within ``max_years`` becomes a filed :class:`Fact`. Like every
    connector it maps and reports — it contains no verification logic.
    """

    def __init__(self, registry: MetricRegistry | None = None, client: EdgarClient | None = None) -> None:
        self.registry = registry or DEFAULT_REGISTRY
        self.client = client or HttpEdgarClient()

    def fetch(
        self, ticker: str, tenant_id: str, *, max_years: int = 3
    ) -> tuple[list[Fact], IngestionReport]:
        """Pull ``ticker``'s filed facts for the metrics the registry maps.

        Facts are scoped to ``entity=<TICKER>`` so a draft analyzed under that
        issuer binds to them. A value re-reported across filings (often at a coarser
        rounding in a later comparative) lands once; only a *materially* different
        value for the same period lands as a later version, so the engine's
        restatement detection still fires without rounding noise inventing one.
        """
        ticker = ticker.strip().upper()
        cik = self.client.resolve_cik(ticker)
        source = f"edgar:{ticker}"
        if cik is None:
            return [], IngestionReport(source=source, tenant_id=tenant_id, ingested=0, skipped=0)

        fye = self.client.fiscal_year_end(cik) or "1231"
        facts: list[Fact] = []
        skipped_tags: list[str] = []

        for spec in self.registry.metrics():
            us_gaap_tags = [t for t in spec.xbrl_tags if t.startswith("us-gaap:")]
            if not us_gaap_tags:
                continue
            # period -> the datapoints reporting it, and the tag that first claimed
            # it (so a legacy alias-tag can't double-book a period a primary tag owns).
            by_period: dict[str, list[dict]] = {}
            claimed_by: dict[str, str] = {}
            got_any = False
            for tag in us_gaap_tags:
                taxonomy, _, name = tag.partition(":")
                concept = self.client.company_concept(cik, taxonomy, name)
                if not concept:
                    continue
                got_any = True
                for datum in _quarterly_data(concept, fye):
                    period = fiscal_period(datum["end"], fye)
                    if period is None or claimed_by.setdefault(period, tag) != tag:
                        continue
                    by_period.setdefault(period, []).append({**datum, "tag": tag})
            if not got_any:
                skipped_tags.append(spec.id)
            for period, data in by_period.items():
                facts.extend(self._facts_for_scope(spec, period, data, tenant_id, ticker))

        facts = _within_recent_years(facts, max_years)
        report = IngestionReport(
            source=source,
            tenant_id=tenant_id,
            ingested=len(facts),
            skipped=len(skipped_tags),
            skipped_tags=tuple(skipped_tags),
        )
        return facts, report

    def _facts_for_scope(
        self, spec, period: str, data: list[dict], tenant_id: str, entity: str
    ) -> list[Fact]:
        """Build the fact version(s) for one (metric, period) scope.

        Datapoints are ordered by filing date; a later value within
        ``_RESTATEMENT_TOLERANCE`` of one already kept is dropped as a rounding-level
        re-report, while a materially different value is kept as a new version
        (``as_of`` = its filing date) so a genuine restatement is still detectable.
        """
        kept: list[Decimal] = []
        out: list[Fact] = []
        for datum in sorted(data, key=lambda d: d.get("filed", "")):
            value = Decimal(str(datum["val"]))
            if any(_close(value, k) for k in kept):
                continue
            kept.append(value)
            tag = datum["tag"]
            out.append(
                Fact(
                    # The version index keeps the id unique even in the unlikely
                    # event two kept values share an accession + filing date.
                    id=f"{datum['accn']}:{spec.id}:{period}:{datum['filed']}:v{len(kept)}",
                    tenant_id=tenant_id,
                    entity=entity,
                    metric=spec.id,
                    period=period,
                    value=value,
                    unit=spec.unit,
                    quantum=Decimal(0),  # treat the filed value as exact
                    source_type=SourceType.EDGAR_XBRL,
                    source_ref=f"{datum['accn']}#{tag}",
                    source_label=f"Form {datum.get('form', '')} · {tag}".strip(" ·"),
                    source_excerpt="",
                    as_of=datum.get("filed", "1970-01-01"),
                    confidence=Confidence.HIGH,
                )
            )
        return out


def _quarterly_data(concept: dict, fiscal_year_end: str) -> list[dict]:
    """Yield quarterly / balance-sheet datapoints from a ``companyconcept``.

    Keeps native single-quarter durations and instantaneous balance-sheet facts.
    For cash-flow-style tags that SEC often reports only as year-to-date durations,
    derive the quarter by subtracting the prior YTD fact in the same fiscal year.
    That lets an earnings release's current-quarter operating cash flow tie to the
    filed 10-Q instead of staying untraced, while still never treating a cumulative
    six/nine-month value as a quarter by itself.
    """
    out: list[dict] = []
    ytd: dict[str, dict[str, dict]] = {}
    direct_periods: set[tuple[str, str]] = set()

    for unit_key, rows in concept.get("units", {}).items():
        for row in rows:
            end = row.get("end")
            if not end or row.get("val") is None or not row.get("accn"):
                continue
            period = fiscal_period(end, fiscal_year_end)
            if period is None:
                continue
            start = row.get("start")
            if not start:  # instantaneous balance-sheet fact
                out.append({**row, "unit_key": unit_key})
                direct_periods.add((unit_key, period))
                continue
            try:
                days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except ValueError:
                continue
            if days in _QUARTER_DAYS:
                out.append({**row, "unit_key": unit_key})
                direct_periods.add((unit_key, period))
                continue
            # Longer duration: keep as YTD input only. It becomes a quarter if the
            # previous YTD exists below; otherwise it is skipped, preserving the old
            # no-guess behaviour for annual/cumulative-only facts.
            ytd.setdefault(unit_key, {})[period] = {**row, "unit_key": unit_key}

    for unit_key, by_period in ytd.items():
        for period, row in sorted(by_period.items()):
            if (unit_key, period) in direct_periods:
                continue
            prev = _previous_fiscal_quarter(period)
            if prev is None or prev not in by_period:
                continue
            value = Decimal(str(row["val"])) - Decimal(str(by_period[prev]["val"]))
            out.append({**row, "val": value, "unit_key": unit_key, "derived_from_ytd": True})
    return out


def _previous_fiscal_quarter(period: str) -> str | None:
    m = re.match(r"FY(\d{4})-Q([1-4])", period)
    if not m:
        return None
    year, q = int(m.group(1)), int(m.group(2))
    if q <= 1:
        return None
    return f"FY{year}-Q{q - 1}"


def _within_recent_years(facts: list[Fact], max_years: int) -> list[Fact]:
    """Keep only facts in the most recent ``max_years`` fiscal years present.

    Bounds the ingest to the window an earnings draft actually cites, instead of a
    decade of comparatives, without needing to know "today" — it's relative to the
    most recent fiscal year the issuer has filed.
    """
    if not facts or max_years <= 0:
        return facts
    years = [_fiscal_year(f.period) for f in facts]
    years = [y for y in years if y is not None]
    if not years:
        return facts
    cutoff = max(years) - max_years + 1
    return [f for f in facts if (_fiscal_year(f.period) or 0) >= cutoff]


def _fiscal_year(period: str) -> int | None:
    if period.startswith("FY") and len(period) >= 6:
        try:
            return int(period[2:6])
        except ValueError:
            return None
    return None
