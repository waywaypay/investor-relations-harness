"""EDGAR companyfacts ingestion — offline, against SEC's actual payload shape.

The contract: registry-mapped concepts land as facts keyed by the spine's
period convention, a restated value becomes a second *version* (not a
mutation), re-reported identical values are deduped, unmapped concepts are
skipped-and-reported, and the lookback window prunes ancient periods.
"""

from __future__ import annotations

import json
from decimal import Decimal


from attest.ingestion.edgar_companyfacts import (
    COMPANYFACTS_URL,
    CompanyFactsConnector,
    _period_key,
)
from attest.ingestion.sec import TICKERS_URL
from attest.service import AttestService

CIK = 731766  # UNH

PAYLOAD = {
    "cik": CIK,
    "entityName": "UNITEDHEALTH GROUP INC",
    "facts": {
        "us-gaap": {
            "Revenues": {
                "label": "Revenues",
                "units": {
                    "USD": [
                        # FY2025-Q1 as originally reported...
                        {"start": "2025-01-01", "end": "2025-03-31", "val": 99_797_000_000,
                         "accn": "25-q1", "form": "10-Q", "filed": "2025-05-07", "frame": "CY2025Q1"},
                        # ...re-reported unchanged in the next year's comparative...
                        {"start": "2025-01-01", "end": "2025-03-31", "val": 99_797_000_000,
                         "accn": "26-q1", "form": "10-Q", "filed": "2026-05-06"},
                        # ...and the current quarter.
                        {"start": "2026-01-01", "end": "2026-03-31", "val": 109_605_000_000,
                         "accn": "26-q1", "form": "10-Q", "filed": "2026-05-06", "frame": "CY2026Q1"},
                        # An ancient quarter outside the lookback window.
                        {"start": "2018-01-01", "end": "2018-03-31", "val": 55_100_000_000,
                         "accn": "18-q1", "form": "10-Q", "filed": "2018-05-08", "frame": "CY2018Q1"},
                    ]
                },
            },
            "EarningsPerShareDiluted": {
                "label": "EPS, diluted",
                "units": {
                    "USD/shares": [
                        # Originally reported, then restated in a later filing.
                        {"start": "2026-01-01", "end": "2026-03-31", "val": 6.85,
                         "accn": "26-8k", "form": "8-K", "filed": "2026-04-15"},
                        {"start": "2026-01-01", "end": "2026-03-31", "val": 6.91,
                         "accn": "26-q1", "form": "10-Q", "filed": "2026-05-06"},
                    ]
                },
            },
            "Assets": {
                "label": "Total assets",
                "units": {
                    "USD": [
                        # An instant (balance-sheet date), no "start".
                        {"end": "2026-03-31", "val": 312_000_000_000,
                         "accn": "26-q1", "form": "10-Q", "filed": "2026-05-06", "frame": "CY2026Q1I"},
                    ]
                },
            },
            "NetIncomeLoss": {
                "label": "Net income",
                "units": {
                    "USD": [
                        # A full-year duration -> the annual period key.
                        {"start": "2025-01-01", "end": "2025-12-31", "val": 23_100_000_000,
                         "accn": "25-10k", "form": "10-K", "filed": "2026-02-20", "frame": "CY2025"},
                    ]
                },
            },
            # No canonical metric maps this concept: skipped, not guessed.
            "AccountsPayableCurrent": {
                "label": "Accounts payable",
                "units": {"USD": [
                    {"end": "2026-03-31", "val": 1, "accn": "x", "filed": "2026-05-06"},
                ]},
            },
        }
    },
}

TICKERS = {"0": {"cik_str": CIK, "ticker": "UNH", "title": "UNITEDHEALTH GROUP INC"}}


def _fake_fetch(url: str) -> bytes:
    if url == TICKERS_URL:
        return json.dumps(TICKERS).encode()
    if url == COMPANYFACTS_URL.format(cik=CIK):
        return json.dumps(PAYLOAD).encode()
    raise AssertionError(f"unexpected URL: {url}")


def _connector() -> CompanyFactsConnector:
    return CompanyFactsConnector(fetch=_fake_fetch)


def test_ticker_resolves_and_mapped_concepts_land_as_facts():
    facts, report = _connector().fetch_company("UNH", tenant_id="unh")
    assert report.source == f"edgar_companyfacts:CIK{CIK:010d}"
    by_key = {(f.metric, f.period) for f in facts}
    assert ("total_revenue", "FY2026-Q1") in by_key
    assert ("total_revenue", "FY2025-Q1") in by_key
    assert ("gaap_diluted_eps", "FY2026-Q1") in by_key
    assert ("total_assets", "FY2026-Q1") in by_key   # instant frame "CY2026Q1I"
    assert ("net_income", "FY2025") in by_key        # annual frame "CY2025"
    assert all(f.entity == "UNH" for f in facts)


def test_restated_value_becomes_a_second_version_not_a_mutation():
    facts, _ = _connector().fetch_company("UNH", tenant_id="unh")
    eps = sorted(
        (f for f in facts if f.metric == "gaap_diluted_eps"), key=lambda f: f.as_of
    )
    assert [f.value for f in eps] == [Decimal("6.85"), Decimal("6.91")]
    assert eps[0].as_of == "2026-04-15" and eps[1].as_of == "2026-05-06"


def test_rereported_identical_value_is_deduped():
    facts, _ = _connector().fetch_company("UNH", tenant_id="unh")
    q1_25 = [f for f in facts if f.metric == "total_revenue" and f.period == "FY2025-Q1"]
    assert len(q1_25) == 1  # the 2026 comparative restatement of the same value drops


def test_unmapped_concepts_are_skipped_and_reported():
    _, report = _connector().fetch_company("UNH", tenant_id="unh")
    assert "AccountsPayableCurrent" in report.skipped_tags
    assert report.skipped >= 1


def test_lookback_prunes_ancient_periods():
    facts, _ = _connector().fetch_company("UNH", tenant_id="unh", quarters=12)
    assert not any(f.period == "FY2018-Q1" for f in facts)


def test_period_key_handles_duration_shapes_without_frames():
    assert _period_key({"start": "2026-01-01", "end": "2026-06-30"}) == "FY2026-H1"
    assert _period_key({"start": "2025-10-01", "end": "2026-06-30"}) == "FY2026-9M"
    assert _period_key({"start": "2025-07-01", "end": "2026-06-30"}) == "FY2026"
    assert _period_key({"end": "2025-12-31"}) == "FY2025-Q4"
    assert _period_key({"start": "2026-01-01", "end": "2026-05-15"}) is None  # a stub period


def test_service_ingest_companyfacts_lands_in_store_with_audit_event():
    service = AttestService()
    report = service.ingest_companyfacts(
        "UNH", tenant_id="unh", connector=_connector()
    )
    assert report.ingested == len(service.store.all("unh")) > 0
    events = service.audit_export("unh")
    assert events and events[-1]["type"] == "ingest"
    # The engine can now bind a draft figure against a fetched fact.
    latest = service.store.latest("unh", "UNH", "total_revenue", "FY2026-Q1")
    assert latest is not None and latest.value == Decimal(109_605_000_000)


def test_verification_uses_latest_version_and_flags_the_superseded_one():
    service = AttestService()
    service.ingest_companyfacts("UNH", tenant_id="unh", connector=_connector())
    from attest.domain.verdicts import FigureClaim, Verdict

    def verdict_for(text: str):
        claim = FigureClaim(
            claim_id="c1", document_id="d", entity="UNH",
            metric="gaap_diluted_eps", period="FY2026-Q1", displayed_text=text,
        )
        return service.engine.verify_claim(claim, "unh").verdict

    assert verdict_for("$6.91") == Verdict.TRACED
    assert verdict_for("$6.85") == Verdict.CONFLICT  # the superseded original
