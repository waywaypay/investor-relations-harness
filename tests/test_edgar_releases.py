"""EDGAR press-release retrieval: 8-K Item 2.02 -> EX-99.1, deterministically.

The contract under test is the fix for the semantic-search failure mode:
enumeration must be *exact* and counted in **distinct reported quarters**
(newest first, paging into the archive index when the lookback outruns the
recent window) — issuers furnish guidance updates, monthly metrics, and
preliminary results under Item 2.02 too, and that noise must not crowd real
quarters out of the lookback. The exhibit must be located from the filing
index rather than guessed, the recovered text must actually contain the
figures, and anything the index cannot satisfy must be reported as missing —
never papered over.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from attest.ingestion.edgar_releases import (
    SUBMISSIONS_URL,
    TICKERS_URL,
    EdgarReleaseConnector,
    _exhibit_from_index_html,
    _exhibit_from_listing,
)
from attest.ingestion.releases import (
    infer_period,
    previous_calendar_quarter,
    walk_quarters,
)

CIK = 1326801

A1 = "0001628280-26-000101"  # Q1 2026 earnings 8-K
A2 = "0001326801-26-000014"  # Q4 2025 earnings 8-K (exhibit via index.json fallback)
A3 = "0001628280-25-047114"  # Q3 2025 earnings 8-K with no locatable exhibit
A4 = "0001628280-25-033001"  # Q2 2025 earnings 8-K (iXBRL-viewer href), in archive file
A5 = "0001628280-25-020002"  # Q1 2025 earnings 8-K (typed EX-99, not EX-99.1), in archive

ARCHIVE_NAME = "CIK0001326801-submissions-001.json"

SUBMISSIONS = {
    "name": "Meta Platforms, Inc.",
    "tickers": ["META"],
    "filings": {
        "recent": {
            "accessionNumber": [A1, "0001326801-26-000900", A2, "0001326801-26-000800", A3],
            "filingDate": ["2026-04-29", "2026-03-02", "2026-01-28", "2025-11-15", "2025-10-29"],
            "reportDate": ["2026-03-31", "2026-03-02", "2025-12-31", "2025-11-15", "2025-09-30"],
            # An advisory 8-K (7.01) and a personnel 8-K (5.02) must be filtered out.
            "items": ["2.02,9.01", "7.01", "2.02,9.01", "5.02", "2.02"],
            "form": ["8-K", "8-K", "8-K", "8-K", "8-K"],
        },
        "files": [{"name": ARCHIVE_NAME}],
    },
}

ARCHIVE = {
    "accessionNumber": [A4, "0001628280-25-025000", A5],
    "filingDate": ["2025-07-30", "2025-07-01", "2025-04-30"],
    "reportDate": ["2025-06-30", "2025-07-01", "2025-03-31"],
    "items": ["2.02,9.01", "8.01", "2.02,9.01"],
    "form": ["8-K", "10-Q", "8-K"],
}


def _base(accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{CIK}/{accession.replace('-', '')}"


def _index_html(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<tr><td>{i}</td><td>DOC</td><td><a href="{href}">{href.rsplit("/", 1)[-1]}</a></td>'
        f"<td>{doc_type}</td><td>1234</td></tr>"
        for i, (href, doc_type) in enumerate(rows, 1)
    )
    return f'<html><body><table class="tableFile">{body}</table></body></html>'


def _exhibit_html(quarter_phrase: str, year: int) -> str:
    return f"""<html><body>
    <p>NEWS RELEASE</p>
    <p>Meta Reports {quarter_phrase} {year} Results</p>
    <p>MENLO PARK, Calif. -- Meta Platforms, Inc. (Nasdaq: META) today reported results.</p>
    <table>
      <tr><td>Revenue</td><td>$</td><td>56,311</td></tr>
      <tr><td>Net income</td><td>$</td><td>18,447</td></tr>
    </table>
    <p>Diluted EPS was $7.25, an increase of 36%. Operating margin was 43%.</p>
    </body></html>"""


def _urls() -> dict[str, object]:
    a1_exhibit = f"{_base(A1)}/meta-03312026xexhibit991.htm"
    a4_exhibit = f"{_base(A4)}/meta-06302025xexhibit991.htm"
    a5_exhibit = f"{_base(A5)}/meta-03312025xexhibit991.htm"
    return {
        TICKERS_URL: {"0": {"cik_str": CIK, "ticker": "META", "title": "Meta Platforms, Inc."}},
        SUBMISSIONS_URL.format(cik=CIK): SUBMISSIONS,
        f"https://data.sec.gov/submissions/{ARCHIVE_NAME}": ARCHIVE,
        # A1: exhibit typed EX-99.1 in the -index.htm table.
        f"{_base(A1)}/{A1}-index.htm": _index_html(
            [(f"{_base(A1)}/meta-8k.htm", "8-K"), (a1_exhibit, "EX-99.1")]
        ),
        a1_exhibit: _exhibit_html("First Quarter", 2026),
        # A2: no EX row in -index.htm -> located via the index.json filename fallback.
        f"{_base(A2)}/{A2}-index.htm": _index_html([(f"{_base(A2)}/form8k.htm", "8-K")]),
        f"{_base(A2)}/index.json": {
            "directory": {
                "item": [
                    {"name": "form8k.htm"},
                    {"name": "meta-12312025xex991.htm"},
                    {"name": "styles.css"},
                ]
            }
        },
        f"{_base(A2)}/meta-12312025xex991.htm": _exhibit_html(
            "Fourth Quarter and Full Year", 2025
        ),
        # A3: exhibit genuinely unlocatable -> must be reported missing.
        f"{_base(A3)}/{A3}-index.htm": _index_html([(f"{_base(A3)}/form8k.htm", "8-K")]),
        f"{_base(A3)}/index.json": {"directory": {"item": [{"name": "form8k.htm"}]}},
        # A4: href wrapped in the iXBRL viewer prefix -> must be unwrapped.
        f"{_base(A4)}/{A4}-index.htm": _index_html(
            [(f"/ix?doc={a4_exhibit.removeprefix('https://www.sec.gov')}", "EX-99.1")]
        ),
        a4_exhibit: _exhibit_html("Second Quarter", 2025),
        # A5: filer typed the release EX-99 (no .1) -> fallback type match.
        f"{_base(A5)}/{A5}-index.htm": _index_html([(a5_exhibit, "EX-99")]),
        a5_exhibit: _exhibit_html("First Quarter", 2025),
    }


def _connector(urls: dict[str, object]) -> tuple[EdgarReleaseConnector, list[str]]:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        payload = urls[url]
        if isinstance(payload, (dict, list)):
            return json.dumps(payload).encode("utf-8")
        return str(payload).encode("utf-8")

    return EdgarReleaseConnector(fetch=fetch), calls


def test_enumerates_one_release_per_quarter_paging_into_archive():
    connector, _ = _connector(_urls())
    releases, report = connector.fetch_quarterly("META", quarters=4)

    assert report.fetched == 4
    assert [r.period for r in releases] == ["FY2026-Q1", "FY2025-Q4", "FY2025-Q2", "FY2025-Q1"]
    assert [r.accession for r in releases] == [A1, A2, A4, A5]
    # The unlocatable exhibit is reported, not silently skipped.
    assert any(A3 in note for note in report.missing)
    # Every recovered text demonstrably contains the figures.
    assert all(r.figure_count >= 5 for r in releases)
    assert all("EX-99" not in r.text for r in releases)  # text is prose, not the index page


def test_advisory_and_unrelated_filings_are_never_fetched():
    connector, calls = _connector(_urls())
    connector.fetch_quarterly("META", quarters=4)
    # The 7.01 advisory, 5.02 personnel 8-K, and the 10-Q never get index lookups.
    assert not any("000132680126000900" in url for url in calls)
    assert not any("000132680126000800" in url for url in calls)
    assert not any("000162828025025000" in url for url in calls)


def test_ixbrl_viewer_href_is_unwrapped():
    connector, _ = _connector(_urls())
    releases, _ = connector.fetch_quarterly("META", quarters=4)
    q2 = next(r for r in releases if r.period == "FY2025-Q2")
    assert q2.url == f"{_base(A4)}/meta-06302025xexhibit991.htm"


def test_shortfall_is_reported_honestly():
    connector, _ = _connector(_urls())
    releases, report = connector.fetch_quarterly("META", quarters=6)
    assert report.fetched == len(releases) == 4
    assert any("only 4 of 6" in note for note in report.missing)


def test_resolve_cik_and_cik_issuer_labeling():
    connector, _ = _connector(_urls())
    assert connector.resolve_cik("meta") == CIK
    with pytest.raises(LookupError):
        connector.resolve_cik("ZZZZ")
    releases, _ = connector.fetch_quarterly(str(CIK), quarters=1)
    assert releases[0].entity == "META"  # labeled from the submissions index


def test_exhibit_index_html_prefers_ex_99_1_over_other_exhibits():
    html = _index_html(
        [
            ("/a/press-deck.htm", "EX-99.2"),
            ("/a/release.htm", "EX-99.1"),
            ("/a/form8k.htm", "8-K"),
        ]
    )
    assert _exhibit_from_index_html(html) == "/a/release.htm"
    # Without an exact EX-99.1, any EX-99.* row is the fallback.
    html = _index_html([("/a/form8k.htm", "8-K"), ("/a/release.htm", "EX-99")])
    assert _exhibit_from_index_html(html) == "/a/release.htm"
    assert _exhibit_from_index_html(_index_html([("/a/form8k.htm", "8-K")])) is None


def test_exhibit_listing_fallback_prefers_exhibit_numbered_names():
    listing = {
        "directory": {
            "item": [
                {"name": "pressrelease.htm"},
                {"name": "meta-ex991.htm"},
                {"name": "logo.jpg"},
            ]
        }
    }
    assert _exhibit_from_listing(listing) == "meta-ex991.htm"
    assert _exhibit_from_listing({"directory": {"item": [{"name": "form8k.htm"}]}}) is None


def test_exhibit_listing_accepts_pdf_only_filings_but_prefers_html():
    # Some filers attach the release as PDF only; the extraction edge has a
    # best-effort PDF path, so the exhibit must be located, not reported missing.
    pdf_only = {"directory": {"item": [{"name": "form8k.htm"}, {"name": "ex991.pdf"}]}}
    assert _exhibit_from_listing(pdf_only) == "ex991.pdf"
    both = {
        "directory": {
            "item": [{"name": "ex991.pdf"}, {"name": "ex991.htm"}, {"name": "form8k.htm"}]
        }
    }
    assert _exhibit_from_listing(both) == "ex991.htm"


# --- distinct-quarter enumeration: Item 2.02 noise must not crowd quarters ---
#
# The observed live failure: "fetch the past four quarters" returned four
# *filings* spanning two quarters, because guidance updates / monthly metrics
# are furnished under Item 2.02 alongside the earnings releases.

ACME_CIK = 7777


def _acme_urls(filings: list[tuple[str, str, str, str]]) -> dict[str, object]:
    """Build a submissions index + per-filing exhibits for issuer ACME.

    Each filing is (accession, filing_date, report_date, exhibit_html).
    """
    urls: dict[str, object] = {
        TICKERS_URL: {"0": {"cik_str": ACME_CIK, "ticker": "ACME", "title": "Acme Corp"}},
        SUBMISSIONS_URL.format(cik=ACME_CIK): {
            "name": "Acme Corp",
            "tickers": ["ACME"],
            "filings": {
                "recent": {
                    "accessionNumber": [f[0] for f in filings],
                    "filingDate": [f[1] for f in filings],
                    "reportDate": [f[2] for f in filings],
                    "items": ["2.02,9.01"] * len(filings),
                    "form": ["8-K"] * len(filings),
                },
                "files": [],
            },
        },
    }
    for accession, _, _, exhibit_html in filings:
        base = f"https://www.sec.gov/Archives/edgar/data/{ACME_CIK}/{accession.replace('-', '')}"
        exhibit = f"{base}/ex991.htm"
        urls[f"{base}/{accession}-index.htm"] = _index_html([(exhibit, "EX-99.1")])
        urls[exhibit] = exhibit_html
    return urls


def _guidance_html(sentence: str) -> str:
    """An Item 2.02 exhibit that is *not* a quarterly release: no stated quarter."""
    return f"<html><body><p>NEWS RELEASE</p><p>{sentence}</p></body></html>"


def test_guidance_updates_do_not_crowd_quarters_out_of_the_lookback():
    # Two guidance 8-Ks (Item 2.02, no stated quarter) interleave with four
    # real releases. Counting filings would return two distinct quarters;
    # counting quarters must return all four.
    filings = [
        ("0000007777-26-000005", "2026-04-28", "2026-03-31",
         _exhibit_html("First Quarter", 2026).replace("Meta", "Acme")),
        ("0000007777-26-000004", "2026-03-05", "2026-03-05",
         _guidance_html("Acme raises its full year outlook to $4.2 billion.")),
        ("0000007777-26-000003", "2026-01-27", "2025-12-31",
         _exhibit_html("Fourth Quarter and Full Year", 2025).replace("Meta", "Acme")),
        ("0000007777-25-000099", "2025-11-10", "2025-11-10",
         _guidance_html("Acme updates its outlook following the divestiture.")),
        ("0000007777-25-000080", "2025-10-28", "2025-09-30",
         _exhibit_html("Third Quarter", 2025).replace("Meta", "Acme")),
        ("0000007777-25-000060", "2025-07-29", "2025-06-30",
         _exhibit_html("Second Quarter", 2025).replace("Meta", "Acme")),
    ]
    connector, _ = _connector(_acme_urls(filings))
    releases, report = connector.fetch_quarterly("ACME", quarters=4)

    assert report.fetched == 4
    assert report.missing == ()
    assert [r.period for r in releases] == [
        "FY2026-Q1", "FY2025-Q4", "FY2025-Q3", "FY2025-Q2"
    ]
    # The crowded-out filings are noted on the kept releases, not silently dropped.
    q1 = next(r for r in releases if r.period == "FY2026-Q1")
    assert any("0000007777-26-000004" in w for w in q1.warnings)
    q4 = next(r for r in releases if r.period == "FY2025-Q4")
    assert any("0000007777-25-000099" in w for w in q4.warnings)


def test_monthly_metric_filings_are_evicted_by_the_real_quarterly_release():
    # A lender's newest Item 2.02 is monthly credit data for the *in-progress*
    # quarter. It may claim a slot at first, but the oldest requested quarter's
    # stated release must reclaim it: the user asked for reported quarters.
    monthly = _guidance_html(
        "Acme reported net charge-offs of 4.5% and delinquencies of 3.1% "
        "for the month ended May 31, 2026. Loan receivables were $42.0 billion."
    )
    filings = [
        ("0000007777-26-000010", "2026-06-05", "2026-05-31", monthly),
        ("0000007777-26-000005", "2026-04-28", "2026-03-31",
         _exhibit_html("First Quarter", 2026).replace("Meta", "Acme")),
        ("0000007777-26-000003", "2026-01-27", "2025-12-31",
         _exhibit_html("Fourth Quarter and Full Year", 2025).replace("Meta", "Acme")),
        ("0000007777-25-000080", "2025-10-28", "2025-09-30",
         _exhibit_html("Third Quarter", 2025).replace("Meta", "Acme")),
        ("0000007777-25-000060", "2025-07-29", "2025-06-30",
         _exhibit_html("Second Quarter", 2025).replace("Meta", "Acme")),
        ("0000007777-25-000040", "2025-04-29", "2025-03-31",
         _exhibit_html("First Quarter", 2025).replace("Meta", "Acme")),
    ]
    connector, _ = _connector(_acme_urls(filings))
    releases, report = connector.fetch_quarterly("ACME", quarters=4)

    assert report.fetched == 4
    assert [r.period for r in releases] == [
        "FY2026-Q1", "FY2025-Q4", "FY2025-Q3", "FY2025-Q2"
    ]
    assert not any(r.accession == "0000007777-26-000010" for r in releases)
    # FY2025-Q1 exists in the index but the four requested quarters were
    # already covered — the walk stopped without including it.
    assert not any(r.period == "FY2025-Q1" for r in releases)


def test_preliminary_results_lose_to_the_figure_richer_final_release():
    # Both filings *state* the same quarter; the release with more detectable
    # figures (the final one, with its tables) wins the slot.
    preliminary = (
        "<html><body><p>Acme Reports Preliminary Fourth Quarter 2025 Results</p>"
        "<p>Acme expects revenue of approximately $13.1 billion and diluted EPS "
        "of approximately $2.05.</p></body></html>"
    )
    filings = [
        ("0000007777-26-000003", "2026-01-28", "2025-12-31",
         _exhibit_html("Fourth Quarter and Full Year", 2025).replace("Meta", "Acme")),
        ("0000007777-26-000001", "2026-01-10", "2026-01-08", preliminary),
    ]
    connector, _ = _connector(_acme_urls(filings))
    releases, report = connector.fetch_quarterly("ACME", quarters=1)

    assert report.fetched == 1
    assert releases[0].accession == "0000007777-26-000003"
    assert any("0000007777-26-000001" in w for w in releases[0].warnings)


def test_period_inference_prefers_stated_title_over_report_date():
    # The issuer's own fiscal labelling wins...
    assert infer_period("Meta Reports Third Quarter 2025 Results") == "FY2025-Q3"
    assert (
        infer_period("Meta Reports Fourth Quarter and Full Year 2025 Results") == "FY2025-Q4"
    )
    # ...the 8-K period-of-report is the calendar fallback...
    assert infer_period("no quarter stated here", "2025-06-30") == "FY2025-Q2"
    # ...and absent both, the period is None — never guessed.
    assert infer_period("no quarter stated here") is None


def test_quarter_walking():
    assert walk_quarters(2026, 1, 4) == [(2026, 1), (2025, 4), (2025, 3), (2025, 2)]
    assert previous_calendar_quarter(date(2026, 6, 9)) == (2026, 1)
    assert previous_calendar_quarter(date(2026, 2, 1)) == (2025, 4)
