"""EDGAR companyfacts ingestion — every reported, tagged fact, fetched for real.

The simplified-instance :class:`~attest.ingestion.edgar_xbrl.XBRLConnector`
consumes a hand-built dict; this connector removes that manual step. SEC's
companyfacts API (``data.sec.gov/api/xbrl/companyfacts/CIK##########.json``)
exposes every ``us-gaap``-tagged value an issuer has ever filed, keyed by
concept and period, with the accession and filing date of each occurrence.
That is the fact store's natural diet:

* concepts map onto canonical metrics through the registry's ``xbrl_tags``
  (unmapped concepts are *skipped, not guessed* — the count is reported so
  coverage is observable);
* each occurrence's own ``start``/``end`` dates become the period key the
  rest of the spine uses (``FY2026-Q1``, ``FY2026-H1``, ``FY2026-9M``,
  ``FY2026``), preferring SEC's calendar ``frame`` label when present;
* the same period re-reported in a later filing with a *different* value is a
  restatement: it lands as a new :class:`Fact` **version** (ordered by filing
  date), which is exactly what the engine's superseded-value conflict needs.

Calendar-quarter convention: periods are keyed by the calendar quarter the
end date closes (the same mapping the release fetcher's ``report_date``
fallback uses), so calendar-fiscal issuers line up exactly. Off-cycle fiscal
issuers (an AAPL-style September year-end) will key Q1 as the calendar
quarter, not the issuer's fiscal label — a known v1 limitation, reported
here rather than hidden.

Scope note: companyfacts carries **consolidated** values only — dimensioned
(segment-axis) facts never appear in this API. Segment metrics therefore
still ingest via the simplified-instance connector until a dimension-aware
instance parser lands.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from decimal import Decimal

from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.ingestion.base import IngestionReport
from attest.ingestion.sec import DEFAULT_USER_AGENT, Fetcher, UrlFetcher, resolve_cik

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{accession}-index.htm"

# companyfacts unit keys this connector understands; anything else is skipped.
_KNOWN_UNITS = {"USD", "USD/shares", "USD/share", "shares", "pure"}

_FRAME_RE = re.compile(r"^CY(\d{4})(?:Q([1-4]))?I?$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


def filing_index_url(cik: int, accession: str) -> str | None:
    """The sec.gov filing-index page for an accession, or ``None``.

    Only a canonically shaped accession number resolves on EDGAR; anything else
    (a missing or mangled ``accn``) gets no URL rather than a fabricated link
    that 404s.
    """
    if not _ACCESSION_RE.match(accession or ""):
        return None
    return FILING_INDEX_URL.format(
        cik=cik, accession_nodash=accession.replace("-", ""), accession=accession
    )


def _parse_date(text: str | None) -> date | None:
    m = _DATE_RE.match(text or "")
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _quarter_of(month: int) -> int:
    return (month - 1) // 3 + 1


def _period_key(entry: dict) -> str | None:
    """The fact-store period key for one companyfacts occurrence.

    SEC's calendar ``frame`` ("CY2026Q1", "CY2025", instants suffixed "I") is
    authoritative when present. Otherwise the occurrence's own dates decide:
    an instant keys to the quarter its date closes; a duration keys by its
    month span — 3 months a quarter, 6 a half, 9 a nine-month YTD, 12 a year.
    Anything else (a 4-month stub, say) returns ``None`` and is skipped.
    """
    frame = entry.get("frame") or ""
    m = _FRAME_RE.match(frame)
    if m:
        year, quarter = m.group(1), m.group(2)
        return f"FY{year}-Q{quarter}" if quarter else f"FY{year}"

    end = _parse_date(entry.get("end"))
    if end is None:
        return None
    start = _parse_date(entry.get("start"))
    if start is None:  # an instant (balance-sheet date)
        return f"FY{end.year}-Q{_quarter_of(end.month)}"

    months = round((end - start).days / 30.44)
    if months == 3:
        return f"FY{end.year}-Q{_quarter_of(end.month)}"
    if months == 6:
        return f"FY{end.year}-H{1 if end.month <= 6 else 2}"
    if months == 9:
        return f"FY{end.year}-9M"
    if months == 12:
        return f"FY{end.year}"
    return None


def _period_rank(period: str) -> int:
    """Order periods on a quarter axis so a lookback can be applied uniformly."""
    m = re.match(r"FY(\d{4})(?:-(?:Q([1-4])|H([12])|9M))?$", period)
    if not m:
        return 0
    year = int(m.group(1))
    if m.group(2):
        quarter = int(m.group(2))
    elif m.group(3):
        quarter = int(m.group(3)) * 2
    elif period.endswith("9M"):
        quarter = 3
    else:
        quarter = 4  # a full-year fact ranks at its closing quarter
    return year * 4 + quarter


class CompanyFactsConnector:
    """Fetches an issuer's companyfacts and lands them as facts-with-provenance."""

    def __init__(
        self,
        registry: MetricRegistry | None = None,
        *,
        user_agent: str | None = None,
        fetch: Fetcher | None = None,
    ) -> None:
        self.registry = registry or DEFAULT_REGISTRY
        agent = user_agent or os.environ.get("ATTEST_SEC_USER_AGENT") or DEFAULT_USER_AGENT
        self._fetch = fetch or UrlFetcher(agent)

    def fetch_company(
        self, issuer: str, tenant_id: str, *, quarters: int = 12
    ) -> tuple[list[Fact], IngestionReport]:
        """All registry-mapped facts for ``issuer`` (ticker or CIK), last ``quarters``.

        Returns the facts plus an honest report: how many occurrences landed and
        which concepts were skipped because no canonical metric maps them.
        """
        cik = int(issuer) if issuer.isdigit() else resolve_cik(self._fetch, issuer)
        payload = json.loads(self._fetch(COMPANYFACTS_URL.format(cik=cik)))
        entity = issuer.upper() if not issuer.isdigit() else payload.get("entityName", str(cik))

        gaap = payload.get("facts", {}).get("us-gaap", {})
        skipped_tags: set[str] = set()
        # (metric, period) -> filed-ordered occurrences, for restatement versioning.
        groups: dict[tuple[str, str], list[dict]] = {}

        for concept, body in gaap.items():
            tag = f"us-gaap:{concept}"
            spec = self.registry.by_xbrl_tag(tag)
            if spec is None:
                skipped_tags.add(concept)
                continue
            for unit_key, entries in body.get("units", {}).items():
                if unit_key not in _KNOWN_UNITS:
                    skipped_tags.add(f"{concept}[{unit_key}]")
                    continue
                for entry in entries:
                    period = _period_key(entry)
                    if period is None or entry.get("val") is None:
                        continue
                    groups.setdefault((spec.id, period), []).append(
                        {**entry, "_tag": tag, "_label": body.get("label") or spec.label}
                    )

        facts = self._versioned_facts(groups, tenant_id, entity, quarters, cik)
        report = IngestionReport(
            source=f"edgar_companyfacts:CIK{cik:010d}",
            tenant_id=tenant_id,
            ingested=len(facts),
            skipped=len(skipped_tags),
            skipped_tags=tuple(sorted(skipped_tags)),
        )
        return facts, report

    def _versioned_facts(
        self,
        groups: dict[tuple[str, str], list[dict]],
        tenant_id: str,
        entity: str,
        quarters: int,
        cik: int,
    ) -> list[Fact]:
        """Collapse occurrences into value *versions* per (metric, period).

        The same value re-reported in a later filing (the comparative column of
        the next 10-Q) is not a restatement and is dropped; a different value
        is, and becomes a new version with the later filing's date as ``as_of``.
        """
        if not groups:
            return []
        latest_rank = max(_period_rank(period) for _, period in groups)
        cutoff = latest_rank - quarters

        facts: list[Fact] = []
        counter = 0
        for (metric, period), entries in sorted(groups.items()):
            if _period_rank(period) <= cutoff:
                continue
            entries.sort(key=lambda e: (e.get("filed") or "", e.get("accn") or ""))
            seen_values: list[Decimal] = []
            for entry in entries:
                value = Decimal(str(entry["val"]))
                if seen_values and value == seen_values[-1]:
                    continue  # re-reported, not restated
                seen_values.append(value)
                counter += 1
                accession = entry.get("accn", "unknown")
                facts.append(
                    Fact(
                        id=f"{accession}:{entity}:{metric}:{period}:v{counter}",
                        tenant_id=tenant_id,
                        entity=entity,
                        metric=metric,
                        period=period,
                        value=value,
                        unit=self.registry.get(metric).unit,  # the metric's declared unit wins
                        quantum=Decimal(0),
                        source_type=SourceType.EDGAR_XBRL,
                        source_ref=f"{accession}#{entry['_tag']}",
                        source_label=entry.get("_label", ""),
                        source_excerpt="",
                        source_url=filing_index_url(cik, accession),
                        as_of=entry.get("filed", "1970-01-01"),
                        confidence=Confidence.HIGH,
                    )
                )
        return facts
