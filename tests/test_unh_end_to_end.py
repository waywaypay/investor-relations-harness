"""End to end on a UNH-shaped quarter: EX-99.1 HTML + companyfacts -> verdicts.

This pins the fix for the original failure mode — "8 of 242 figures traced" —
which decomposed into: table figures off by the header scale (false conflicts
on correct numbers), prior-year columns binding to the current period (more
false conflicts), split-cell negatives invisible to the detector, a registry
that knew none of the issuer's vocabulary, and no real fact supply.

The bar here is deliberately the inverse of that report: most figures trace,
segment rows without facts are *untraced* (honest), and — the trust-critical
property — a correct number is never reported as a conflict.
"""

from __future__ import annotations

import json

from attest.domain.document import DocumentKind
from attest.domain.verdicts import Verdict
from attest.extraction.text import extract_text
from attest.ingestion.edgar_companyfacts import COMPANYFACTS_URL, CompanyFactsConnector
from attest.ingestion.sec import TICKERS_URL
from attest.service import AttestService

CIK = 731766

# A faithful miniature of a UNH first-quarter EX-99.1: headline prose, the
# full-year EPS outlook, a statement-of-operations table and a segment table,
# rendered the way EDGAR actually renders them ($ and parens in their own
# cells, scale and periods declared once in the header).
RELEASE_HTML = b"""
<html><body>
<p><b>UnitedHealth Group Reports First Quarter 2026 Results</b></p>
<p>Revenues of $109.6 billion grew 9.8% year-over-year. Earnings from operations
were $9.1 billion. Net earnings of $6.85 per share and adjusted net earnings of
$7.20 per share. Cash flows from operations were $5.5 billion. The first quarter
medical care ratio was 84.8% compared to 85.1% last year. Return on equity was
26.8% in the quarter.</p>
<p>The company maintains its full year 2026 outlook of net earnings of $24.65 to
$25.15 per share and adjusted net earnings of $26.00 to $26.50 per share. These
statements are forward-looking; refer to our safe harbor statement.</p>

<table>
  <tr><td>(in millions, except per share data)</td>
      <td colspan="4">Three Months Ended March 31,</td></tr>
  <tr><td></td><td colspan="2">2026</td><td colspan="2">2025</td></tr>
  <tr><td>Revenues:</td><td></td><td></td><td></td><td></td></tr>
  <tr><td>Premiums</td><td>$</td><td>78,000</td><td>$</td><td>71,300</td></tr>
  <tr><td>Total revenues</td><td>$</td><td>109,605</td><td>$</td><td>99,797</td></tr>
  <tr><td>Medical costs</td><td>$</td><td>66,144</td><td>$</td><td>60,676</td></tr>
  <tr><td>Earnings from operations</td><td></td><td>9,123</td><td></td><td>8,471</td></tr>
  <tr><td>Net earnings attributable to UnitedHealth Group</td>
      <td>$</td><td>6,291</td><td>$</td><td>(1,409</td><td>)</td></tr>
  <tr><td>Diluted earnings per share</td><td>$</td><td>6.85</td><td>$</td><td>(1.53</td><td>)</td></tr>
</table>

<table>
  <tr><td>(in millions)</td><td colspan="4">Three Months Ended March 31,</td></tr>
  <tr><td></td><td colspan="2">2026</td><td colspan="2">2025</td></tr>
  <tr><td>Revenues:</td><td></td><td></td><td></td><td></td></tr>
  <tr><td>UnitedHealthcare</td><td>$</td><td>81,602</td><td>$</td><td>75,414</td></tr>
  <tr><td>Optum Health</td><td>$</td><td>26,659</td><td>$</td><td>26,725</td></tr>
  <tr><td>Optum Insight</td><td>$</td><td>4,633</td><td>$</td><td>4,388</td></tr>
  <tr><td>Optum Rx</td><td>$</td><td>35,123</td><td>$</td><td>30,754</td></tr>
</table>
</body></html>
"""

# The fact supply, shaped exactly like SEC's companyfacts payload. Values are
# consistent with the release (66,144 / 78,000 = 84.8%; 60,676 / 71,300 = 85.1%).
_USD = "USD"


def _occ(start, end, val, filed, frame=None):
    out = {"start": start, "end": end, "val": val, "accn": "26-q1", "form": "10-Q", "filed": filed}
    if frame:
        out["frame"] = frame
    return out


PAYLOAD = {
    "cik": CIK,
    "entityName": "UNITEDHEALTH GROUP INC",
    "facts": {"us-gaap": {
        "Revenues": {"label": "Revenues", "units": {_USD: [
            _occ("2026-01-01", "2026-03-31", 109_605_000_000, "2026-04-15", "CY2026Q1"),
            _occ("2025-01-01", "2025-03-31", 99_797_000_000, "2025-04-15", "CY2025Q1"),
        ]}},
        "PremiumsEarnedNet": {"label": "Premiums", "units": {_USD: [
            _occ("2026-01-01", "2026-03-31", 78_000_000_000, "2026-04-15", "CY2026Q1"),
            _occ("2025-01-01", "2025-03-31", 71_300_000_000, "2025-04-15", "CY2025Q1"),
        ]}},
        "PolicyholderBenefitsAndClaimsIncurredNet": {"label": "Medical costs", "units": {_USD: [
            _occ("2026-01-01", "2026-03-31", 66_144_000_000, "2026-04-15", "CY2026Q1"),
            _occ("2025-01-01", "2025-03-31", 60_676_000_000, "2025-04-15", "CY2025Q1"),
        ]}},
        "OperatingIncomeLoss": {"label": "Earnings from operations", "units": {_USD: [
            _occ("2026-01-01", "2026-03-31", 9_123_000_000, "2026-04-15", "CY2026Q1"),
            _occ("2025-01-01", "2025-03-31", 8_471_000_000, "2025-04-15", "CY2025Q1"),
        ]}},
        "NetIncomeLoss": {"label": "Net earnings", "units": {_USD: [
            _occ("2026-01-01", "2026-03-31", 6_291_000_000, "2026-04-15", "CY2026Q1"),
            _occ("2025-01-01", "2025-03-31", -1_409_000_000, "2025-04-15", "CY2025Q1"),
        ]}},
        "EarningsPerShareDiluted": {"label": "EPS, diluted", "units": {"USD/shares": [
            _occ("2026-01-01", "2026-03-31", 6.85, "2026-04-15", "CY2026Q1"),
            _occ("2025-01-01", "2025-03-31", -1.53, "2025-04-15", "CY2025Q1"),
        ]}},
        "NetCashProvidedByUsedInOperatingActivities": {"label": "OCF", "units": {_USD: [
            _occ("2026-01-01", "2026-03-31", 5_500_000_000, "2026-04-15", "CY2026Q1"),
        ]}},
    }},
}

TICKERS = {"0": {"cik_str": CIK, "ticker": "UNH", "title": "UNITEDHEALTH GROUP INC"}}


def _fake_fetch(url: str) -> bytes:
    if url == TICKERS_URL:
        return json.dumps(TICKERS).encode()
    if url == COMPANYFACTS_URL.format(cik=CIK):
        return json.dumps(PAYLOAD).encode()
    raise AssertionError(f"unexpected URL: {url}")


def _analyzed():
    service = AttestService()
    service.ingest_companyfacts(
        "UNH", tenant_id="unh", connector=CompanyFactsConnector(fetch=_fake_fetch)
    )
    text = extract_text("unh_ex99_1.htm", RELEASE_HTML).text
    document, result, entity, period = service.analyze_text(
        tenant_id="unh",
        text=text,
        title="UnitedHealth Group Reports First Quarter 2026 Results",
        kind=DocumentKind.RELEASE,
    )
    return document, result, entity, period


def test_resolves_issuer_and_period_from_the_release_itself():
    _, _, entity, period = _analyzed()
    assert entity == "UNH" and period == "FY2026-Q1"


def test_correct_numbers_are_never_reported_as_conflicts():
    # The trust-critical property. Before structure-aware tables, every correct
    # table figure conflicted (scale lost) and every prior-year column conflicted
    # (period lost). All figures in this release agree with the filed facts.
    _, result, _, _ = _analyzed()
    assert result.counts["conflict"] == 0


def test_most_figures_trace_instead_of_eight_in_two_hundred():
    _, result, _, _ = _analyzed()
    counts = result.counts
    total = sum(counts.values())
    # Prose headlines, both statement-table columns, both EPS columns, and the
    # recomputed derived figures (YoY growth, both medical care ratios) trace.
    assert counts["traced"] >= 17
    assert counts["traced"] / total >= 0.6  # vs ~3% in the reported failure


def test_derived_figures_trace_by_recomputation_from_filed_levels():
    # No growth or ratio fact is ever filed in XBRL — only the levels are. The
    # engine recomputes them, so the most quoted figures in the release link too.
    _, result, _, _ = _analyzed()
    by_text = {v.displayed_text: v for v in result.verdicts}
    growth = by_text["9.8%"]
    assert growth.metric == "revenue_growth_yoy"
    assert growth.verdict == Verdict.TRACED
    assert "recomputed" in growth.reason.lower()
    assert by_text["84.8%"].verdict == Verdict.TRACED   # 66,144 / 78,000
    assert by_text["85.1%"].verdict == Verdict.TRACED   # 60,676 / 71,300, FY2025-Q1


def test_table_figures_trace_with_scale_and_column_period():
    _, result, _, _ = _analyzed()
    by_text = {v.displayed_text: v for v in result.verdicts}
    # Current column, scale applied.
    assert by_text["$109,605 million"].verdict == Verdict.TRACED
    # Prior-year column binds to its own period and traces there.
    prior = by_text["$99,797 million"]
    assert prior.period == "FY2025-Q1" and prior.verdict == Verdict.TRACED
    # The $-less row inherits column currency and traces.
    assert by_text["$9,123 million"].verdict == Verdict.TRACED


def test_split_cell_negatives_detect_and_trace():
    _, result, _, _ = _analyzed()
    by_text = {v.displayed_text: v for v in result.verdicts}
    loss = by_text["-$1,409 million"]
    assert loss.period == "FY2025-Q1" and loss.verdict == Verdict.TRACED
    assert by_text["-$1.53"].verdict == Verdict.TRACED


def test_prose_headlines_trace_including_per_share_disambiguation():
    _, result, _, _ = _analyzed()
    by_text = {v.displayed_text: v for v in result.verdicts}
    assert by_text["$109.6 billion"].verdict == Verdict.TRACED
    assert by_text["$9.1 billion"].verdict == Verdict.TRACED   # "earnings from operations"
    assert by_text["$5.5 billion"].verdict == Verdict.TRACED   # "cash flows from operations"
    # "net earnings of $6.85 per share" pins to EPS, not the $-aggregate...
    eps = by_text["$6.85"]
    assert eps.metric == "gaap_diluted_eps" and eps.verdict == Verdict.TRACED
    # ...and "adjusted net earnings of $7.20 per share" to the non-GAAP EPS,
    # which has no filed source — untraced, honestly, instead of a false bind.
    adjusted = by_text["$7.20"]
    assert adjusted.metric == "non_gaap_diluted_eps"
    assert adjusted.verdict == Verdict.UNTRACED


def test_segment_rows_attribute_to_segment_metrics_and_stay_honest():
    # companyfacts carries no dimensioned facts, so segment rows must come out
    # *untraced* (no source) — never conflicts against consolidated revenue,
    # which is what label leakage used to produce.
    document, result, _, _ = _analyzed()
    by_claim = {c.claim_id: c for c in document.claims}
    segment_verdicts = [
        (by_claim[v.claim_id].metric, v.verdict)
        for v in result.verdicts
        if by_claim[v.claim_id].metric.startswith(("unitedhealthcare_", "optum_"))
    ]
    assert len(segment_verdicts) == 8  # four segments x two columns
    assert all(verdict == Verdict.UNTRACED for _, verdict in segment_verdicts)
    metrics = {metric for metric, _ in segment_verdicts}
    assert metrics == {
        "unitedhealthcare_revenue", "optum_health_revenue",
        "optum_insight_revenue", "optum_rx_revenue",
    }


def test_prior_year_prose_comparative_shifts_period():
    document, _, _, _ = _analyzed()
    ratios = [c for c in document.claims if c.metric == "medical_care_ratio"]
    by_text = {c.displayed_text: c for c in ratios}
    assert by_text["84.8%"].period == "FY2026-Q1"
    assert by_text["85.1%"].period == "FY2025-Q1"  # "compared to 85.1% last year"


def test_medical_care_ratio_recomputes_clean_from_filed_components():
    # MCR is derived (medical costs / premiums); the stated 84.8% and 85.1%
    # both recompute exactly from filed facts — no derived findings.
    _, result, _, _ = _analyzed()
    assert not any(f.rule.startswith("derived.") for f in result.findings)


def test_guidance_ranges_attribute_to_eps_metrics_for_the_full_year():
    document, result, _, _ = _analyzed()
    guidance = {c.metric: c for c in document.claims if "guidance" in c.metric}
    assert set(guidance) == {"eps_guidance", "adjusted_eps_guidance"}
    assert guidance["eps_guidance"].displayed_text.startswith("$24.65")
    assert guidance["adjusted_eps_guidance"].displayed_text.startswith("$26.00")
    assert all(c.period == "FY2026" for c in guidance.values())
    # Two distinct EPS ranges in one sentence: no intra-document false conflict.
    assert not any(f.rule == "consistency.intra_document_mismatch" for f in result.findings)
