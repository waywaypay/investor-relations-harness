"""Live EDGAR smoke test — the real HttpEdgarClient against SEC.

Skipped unless ``ATTEST_TEST_EDGAR`` is set, mirroring how the storage integration
tests gate on a real database. This is the one test that touches the network; the
rest of the suite stays hermetic via :class:`StaticEdgarClient`.

    ATTEST_TEST_EDGAR=1 pytest tests/test_edgar_live.py
"""

from __future__ import annotations

import os

import pytest

from attest.domain.document import DocumentKind
from attest.ingestion.edgar import HttpEdgarClient
from attest.service import AttestService

pytestmark = pytest.mark.skipif(
    not os.environ.get("ATTEST_TEST_EDGAR"),
    reason="set ATTEST_TEST_EDGAR=1 to run the live SEC EDGAR smoke test",
)


def test_resolves_ticker_and_fiscal_year_end():
    client = HttpEdgarClient()
    cik = client.resolve_cik("PANW")
    assert cik == 1327567
    assert (client.fiscal_year_end(cik) or "").startswith("07")  # PANW: 31 July


def test_ingests_real_filed_revenue_and_a_transcript_ties_out():
    svc = AttestService(edgar=HttpEdgarClient())
    # A 10-year window so the now-permanent FY2026-Q2 fact stays in scope as later
    # fiscal years are filed.
    report = svc.ingest_edgar("PANW", "live", max_years=10)
    assert report.ingested > 0

    rev = svc.store.versions("live", "PANW", "total_revenue", "FY2026-Q2")
    assert rev and rev[-1].value == 2594000000  # as filed in the Q2 FY2026 10-Q

    _, result, _, period = svc.analyze_text(
        tenant_id="live",
        text="Fiscal Second Quarter 2026 — total revenue was $2.59 billion.",
        title="PANW", kind=DocumentKind.SCRIPT, entity="PANW",
    )
    assert period == "FY2026-Q2"
    assert {v.metric: v.verdict.value for v in result.verdicts}["total_revenue"] == "traced"
