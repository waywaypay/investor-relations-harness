"""Structure-aware financial-table extraction.

The contract: an EDGAR-style statement table comes out as labelled prose lines
with the scale written per value, split-cell negatives merged into a leading
minus, the column period annotated next to each value, and section headers
chained into row labels — while layout tables fall through to the ordinary
flattening untouched. These are the exact failure modes that made correct
table figures bind as false conflicts (scale, period) or vanish (negatives).
"""

from __future__ import annotations

from attest.extraction.tables import render_financial_tables
from attest.extraction.text import extract_text
from attest.verification.candidates import detect_candidates

# EDGAR's actual rendering habits: the $ and the closing paren live in their
# own cells, the scale is declared once, the years sit under a duration banner.
STATEMENT_HTML = b"""
<html><body>
<p>UNITEDHEALTH GROUP<br/>CONSOLIDATED STATEMENTS OF OPERATIONS</p>
<table>
  <tr><td>(in millions, except per share data)</td>
      <td colspan="4">Three Months Ended March 31,</td></tr>
  <tr><td></td><td colspan="2">2026</td><td colspan="2">2025</td></tr>
  <tr><td>Revenues:</td><td></td><td></td><td></td><td></td></tr>
  <tr><td>Premiums</td><td>$</td><td>78,000</td><td>$</td><td>71,300</td></tr>
  <tr><td>Total revenues</td><td>$</td><td>109,605</td><td>$</td><td>99,797</td></tr>
  <tr><td>Earnings from operations</td><td></td><td>9,123</td><td></td><td>8,471</td></tr>
  <tr><td>Net earnings attributable to UnitedHealth Group</td>
      <td>$</td><td>6,291</td><td>$</td><td>(1,409</td><td>)</td></tr>
  <tr><td>Diluted earnings per share</td><td>$</td><td>6.85</td><td>$</td><td>(1.53</td><td>)</td></tr>
  <tr><td>Medical care ratio</td><td></td><td>84.8</td><td>%</td><td></td><td>85.1</td><td>%</td></tr>
  <tr><td>Weighted-average diluted shares</td><td></td><td>920</td><td></td><td>925</td></tr>
</table>
</body></html>
"""


def _rendered() -> str:
    return extract_text("release.htm", STATEMENT_HTML).text


def test_table_scale_is_written_per_value():
    text = _rendered()
    # "$ 109,605" under an "(in millions)" header must come out scale-explicit,
    # so parse_quantity reads the filed magnitude, not a 1e6-times-smaller one.
    assert "$109,605 million (FY2026-Q1)" in text
    assert "$99,797 million (FY2025-Q1)" in text


def test_column_periods_follow_the_duration_banner():
    text = _rendered()
    # The prior-year comparative carries its own period, not the document's.
    line = next(ln for ln in text.splitlines() if "Total revenues" in ln)
    assert "(FY2026-Q1)" in line and "(FY2025-Q1)" in line


def test_currency_columns_infer_the_omitted_dollar_sign():
    text = _rendered()
    # EDGAR puts "$" on the first row of a column only; rows below stay currency.
    assert "$9,123 million (FY2026-Q1)" in text


def test_split_cell_negatives_merge_into_a_leading_minus():
    text = _rendered()
    assert "-$1,409 million" in text
    assert "-$1.53" in text
    # ...and the detector + normalizer agree they are negative figures.
    cands = {c.text: c for c in detect_candidates(text)}
    assert any(t.startswith("-$1,409") for t in cands)


def test_per_share_rows_are_exempt_from_the_table_scale():
    text = _rendered()
    assert "$6.85 (FY2026-Q1)" in text
    assert "$6.85 million" not in text


def test_percent_marker_cells_merge_and_share_rows_stay_uncoerced():
    text = _rendered()
    assert "84.8% (FY2026-Q1)" in text
    # A share count is never coerced to currency by column inference.
    shares_line = next(ln for ln in text.splitlines() if "diluted shares" in ln)
    assert "$920" not in shares_line


def test_section_headers_chain_into_row_labels():
    text = _rendered()
    assert "Revenues — Premiums:" in text
    assert "Revenues — Total revenues:" in text


def test_balance_sheet_instants_key_to_their_closing_quarter():
    html = b"""
    <table>
      <tr><td>(in millions)</td><td>March 31, 2026</td><td>December 31, 2025</td></tr>
      <tr><td>Total assets</td><td>$</td><td>312,000</td><td>$</td><td>298,300</td></tr>
    </table>
    """
    text = extract_text("bs.htm", html).text
    assert "$312,000 million (FY2026-Q1)" in text
    assert "$298,300 million (FY2025-Q4)" in text


def test_two_duration_banners_split_the_year_columns_evenly():
    html = b"""
    <table>
      <tr><td>(in millions)</td><td colspan="2">Three Months Ended June 30,</td>
          <td colspan="2">Six Months Ended June 30,</td></tr>
      <tr><td></td><td>2026</td><td>2025</td><td>2026</td><td>2025</td></tr>
      <tr><td>Revenues</td><td>1,000</td><td>900</td><td>2,100</td><td>1,800</td></tr>
    </table>
    """
    text = extract_text("q2.htm", html).text
    line = next(ln for ln in text.splitlines() if ln.startswith("Revenues"))
    assert "(FY2026-Q2)" in line and "(FY2025-Q2)" in line
    assert "(FY2026-H1)" in line and "(FY2025-H1)" in line


def test_layout_tables_fall_through_to_plain_flattening():
    html = b"""
    <table><tr><td>
      <p>Contact: ir@example.com</p>
      <table><tr><td>About the company</td></tr>
        <tr><td>We make widgets and we are proud of it.</td></tr></table>
    </td></tr></table>
    """
    text = extract_text("page.htm", html).text
    assert "About the company" in text
    assert "—" not in text  # nothing got rendered as a financial row


def test_unreadable_headers_render_rows_without_period_guesses():
    html = b"""
    <table>
      <tr><td>(in millions)</td><td>Current</td><td>Prior</td></tr>
      <tr><td>Revenues</td><td>$</td><td>1,000</td><td>$</td><td>900</td></tr>
    </table>
    """
    text = extract_text("x.htm", html).text
    line = next(ln for ln in text.splitlines() if ln.startswith("Revenues"))
    assert "$1,000 million" in line and "(FY" not in line  # never guessed


def test_render_financial_tables_is_idempotent_on_plain_html():
    html = "<p>Revenue was $1.24 billion.</p>"
    assert render_financial_tables(html) == html
