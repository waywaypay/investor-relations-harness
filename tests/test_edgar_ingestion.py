"""EDGAR ingestion — tie an uploaded draft out against real filed facts.

Hermetic: a :class:`StaticEdgarClient` stands in for SEC over the network, seeded
with payloads shaped exactly like SEC's ``companyconcept`` API (the real values
are Palo Alto Networks' FY2026 Q2 10-Q, accession 0001327567-26-000005). The live
``HttpEdgarClient`` is exercised separately and skipped without opt-in
(``tests/test_edgar_live.py``).
"""

from __future__ import annotations

import pytest

from attest.domain.document import DocumentKind
from attest.domain.facts import SourceType
from attest.ingestion.edgar import (
    EdgarConnector,
    EdgarUnavailable,
    StaticEdgarClient,
    fiscal_period,
)
from attest.service import AttestService

PANW_CIK = 1327567
ACCN = "0001327567-26-000005"


def _dur(start, end, val):
    return {"start": start, "end": end, "val": val, "accn": ACCN, "form": "10-Q",
            "filed": "2026-02-18", "fy": 2026, "fp": "Q2"}


def _inst(end, val, filed="2026-02-18"):
    return {"end": end, "val": val, "accn": ACCN, "form": "10-Q", "filed": filed,
            "fy": 2026, "fp": "Q2"}


def panw_client() -> StaticEdgarClient:
    """A static client seeded with PANW's real FY2026-Q2 filed values."""
    return StaticEdgarClient(
        tickers={"PANW": PANW_CIK},
        fiscal_year_ends={PANW_CIK: "0731"},  # PANW's fiscal year ends 31 July
        concepts={
            (PANW_CIK, "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"): {
                "units": {"USD": [
                    _dur("2025-11-01", "2026-01-31", 2594000000),  # Q2 FY2026 quarter
                    _dur("2024-11-01", "2025-01-31", 2257000000),  # prior-year comparative
                    _dur("2025-08-01", "2026-01-31", 5068000000),  # H1 YTD — must be skipped
                ]}
            },
            (PANW_CIK, "us-gaap:RevenueRemainingPerformanceObligation"): {
                "units": {"USD": [_inst("2026-01-31", 16000000000)]}
            },
            (PANW_CIK, "us-gaap:CashAndCashEquivalentsAtCarryingValue"): {
                "units": {"USD": [_inst("2026-01-31", 4158000000)]}
            },
        },
    )


# -- fiscal period mapping ----------------------------------------------------

@pytest.mark.parametrize(
    "end,fye,expected",
    [
        ("2026-01-31", "0731", "FY2026-Q2"),  # PANW Q2 (Aug-Jul fiscal year)
        ("2025-10-31", "0731", "FY2026-Q1"),
        ("2026-04-30", "0731", "FY2026-Q3"),
        ("2026-07-31", "0731", "FY2026-Q4"),
        ("2025-01-31", "0731", "FY2025-Q2"),  # the prior-year comparative's own period
        ("2026-03-31", "1231", "FY2026-Q1"),  # a calendar-year issuer
        ("2026-12-31", "1231", "FY2026-Q4"),
    ],
)
def test_fiscal_period_is_derived_from_the_datapoint_not_the_filing(end, fye, expected):
    assert fiscal_period(end, fye) == expected


def test_fiscal_period_returns_none_on_unparseable_date():
    assert fiscal_period("not-a-date", "0731") is None


# -- the connector ------------------------------------------------------------

def test_connector_lands_filed_facts_with_provenance():
    facts, report = EdgarConnector(client=panw_client()).fetch("PANW", "acme")
    by_scope = {(f.metric, f.period): f for f in facts}

    rev = by_scope[("total_revenue", "FY2026-Q2")]
    assert rev.value == 2594000000
    assert rev.entity == "PANW"  # scoped to the issuer so a draft binds to it
    assert rev.source_type is SourceType.EDGAR_XBRL and rev.is_filed
    assert rev.source_ref == f"{ACCN}#us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    assert rev.as_of == "2026-02-18"

    # RPO is a balance-sheet (instantaneous) fact — no duration, still landed.
    assert by_scope[("total_rpo", "FY2026-Q2")].value == 16000000000


def test_connector_maps_comparative_to_its_own_period_not_the_filings():
    # The $2,257M prior-year comparative is tagged with the *filing's* fy/fp (2026/Q2)
    # but reports the quarter ended 2025-01-31 — it must land in FY2025-Q2, never
    # double-booking FY2026-Q2 (which would invent a restatement conflict).
    facts, _ = EdgarConnector(client=panw_client()).fetch("PANW", "acme")
    rev_by_period = {f.period: f.value for f in facts if f.metric == "total_revenue"}
    assert rev_by_period["FY2026-Q2"] == 2594000000
    assert rev_by_period["FY2025-Q2"] == 2257000000


def test_connector_skips_ytd_durations():
    # Only the single-quarter (~91-day) revenue is kept; the 183-day H1 cumulative
    # is dropped so it can't bind as if it were the quarter.
    facts, _ = EdgarConnector(client=panw_client()).fetch("PANW", "acme")
    values = {f.value for f in facts if f.metric == "total_revenue"}
    assert 5068000000 not in values


def test_unknown_ticker_yields_no_facts_without_error():
    facts, report = EdgarConnector(client=panw_client()).fetch("NOPE", "acme")
    assert facts == [] and report.ingested == 0


# -- the service entry points -------------------------------------------------

def test_ingest_edgar_requires_a_client():
    with pytest.raises(RuntimeError):
        AttestService().ingest_edgar("PANW", "acme")


def test_analyze_ties_a_transcript_out_to_real_filed_values():
    svc = AttestService(edgar=panw_client())
    svc.ingest_edgar("PANW", "acme")
    text = (
        "Palo Alto Networks Fiscal Second Quarter 2026 Earnings Call. "
        "Total revenue was $2.59 billion and grew 15%. Our remaining performance "
        "obligation, or RPO, grew 23% to $16.0 billion. Cash and cash equivalents "
        "for the period was $7.9 billion."
    )
    _, result, entity, period = svc.analyze_text(
        tenant_id="acme", text=text, title="PANW Q2 FY2026", kind=DocumentKind.SCRIPT,
        entity="PANW",
    )
    assert entity == "PANW" and period == "FY2026-Q2"
    by_metric = {v.metric: v for v in result.verdicts}
    # The figures that ARE in the filing tie out to the as-filed value...
    assert by_metric["total_revenue"].verdict.value == "traced"
    assert by_metric["total_rpo"].verdict.value == "traced"
    # ...and the overstated cash figure is caught against the filed $4,158M.
    assert by_metric["cash_and_equivalents"].verdict.value == "conflict"


def test_ensure_issuer_facts_loads_then_is_idempotent():
    svc = AttestService(edgar=panw_client())
    first = svc.ensure_issuer_facts("acme", "PANW")
    assert any("PANW" in w for w in first)
    loaded = len(svc.store.all("acme"))
    # A second call is a no-op — it doesn't refetch or duplicate facts.
    assert svc.ensure_issuer_facts("acme", "PANW") == []
    assert len(svc.store.all("acme")) == loaded


def test_ensure_issuer_facts_is_a_noop_without_a_client():
    assert AttestService().ensure_issuer_facts("acme", "PANW") == []


def test_ensure_issuer_facts_degrades_honestly_on_outage():
    class _Down:
        def resolve_cik(self, ticker):
            raise EdgarUnavailable("connection refused")

        def fiscal_year_end(self, cik):  # pragma: no cover - not reached
            return None

        def company_concept(self, cik, taxonomy, tag):  # pragma: no cover
            return None

    svc = AttestService(edgar=_Down())
    warnings = svc.ensure_issuer_facts("acme", "PANW")
    assert warnings and "Could not reach SEC EDGAR" in warnings[0]
    # The outage is a warning, not a crash, and nothing was half-ingested.
    assert svc.store.all("acme") == []


def test_ensure_issuer_facts_warns_when_ticker_has_no_filings():
    warnings = AttestService(edgar=panw_client()).ensure_issuer_facts("acme", "ZZZZ")
    assert warnings and "No EDGAR filings found" in warnings[0]


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_http_client_wraps_non_404_errors_as_unavailable(monkeypatch, status):
    """SEC throttling (403/429) or an outage (5xx) must surface as EdgarUnavailable
    so the upload path degrades to an honest warning instead of crashing — a bare
    HTTPError would escape `except EdgarUnavailable` and 500 the whole request."""
    import urllib.error
    import urllib.request

    from attest.ingestion.edgar import HttpEdgarClient

    def _raise(*_a, **_k):
        raise urllib.error.HTTPError("https://sec.gov/x", status, "blocked", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    with pytest.raises(EdgarUnavailable):
        HttpEdgarClient(user_agent="test test@example.com").resolve_cik("PANW")


def test_http_client_treats_404_as_not_reported(monkeypatch):
    """A 404 is "this issuer doesn't report this tag", not an outage — it returns
    None so the connector simply skips that metric."""
    import urllib.error
    import urllib.request

    from attest.ingestion.edgar import HttpEdgarClient

    def _raise(*_a, **_k):
        raise urllib.error.HTTPError("https://sec.gov/x", 404, "missing", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    assert HttpEdgarClient(user_agent="t t@example.com").company_concept(1, "us-gaap", "X") is None


def test_analyze_with_ticker_survives_edgar_outage():
    """A draft tagged with a ticker must still analyze (figures honestly untraced)
    when EDGAR is unreachable — the outage is a warning, never a 500."""
    from attest.ingestion.edgar import EdgarUnavailable

    class _Down:
        def resolve_cik(self, ticker):
            raise EdgarUnavailable("HTTP 403 from sec.gov")

        def fiscal_year_end(self, cik):  # pragma: no cover - not reached
            return None

        def company_concept(self, cik, taxonomy, tag):  # pragma: no cover
            return None

    svc = AttestService(edgar=_Down())
    _doc, result, _e, _p = svc.analyze_text(
        tenant_id="acme", text="Total revenue was $2.59 billion.",
        kind=DocumentKind.RELEASE, entity="PANW", period="FY2026-Q2",
    )
    assert result.counts.get("untraced", 0) >= 1
