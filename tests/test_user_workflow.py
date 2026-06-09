"""The user's walkthrough, graded figure by figure.

This suite drives the exact workflow a user drives — type a company name,
search, load the found release, read it — through the real HTTP API, and then
grades every number in the document against an explicit contract:

* **every figure is linked**: each number the detector finds becomes a claim
  with a character span, so the UI renders it as a clickable chip;
* **no figure is falsely linked**: each figure's verdict class matches the
  expected one for what the figure *is* — filed GAAP levels, growth percents,
  margins, and derived figures trace to (or recompute from) the SEC source;
  non-GAAP / operational / forward figures land honestly untraced or in
  review — and **zero** conflicts fire on a release whose numbers are right.

The transports are hermetic stubs carrying realistic content; the engine,
extraction, service and API layers in between are the real ones.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from attest.api.app import create_app
from attest.ingestion.edgar import StaticEdgarClient
from attest.service import AttestService
from attest.verification.candidates import detect_candidates

# ---------------------------------------------------------------------------
# A realistic earnings release: every numeric shape a real one carries —
# headline level + growth, comparative prior-year figures, EPS pair, margins
# and a bps change, cash flow + derived FCF, a count, and a guidance range.
# ---------------------------------------------------------------------------

RELEASE_URL = "https://www.paloaltonetworks.com/company/press/2026/q3-results"

RELEASE_TEXT = """Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results

SANTA CLARA, Calif., May 28, 2026 -- Palo Alto Networks (NASDAQ: PANW), the global cybersecurity leader, today announced financial results for its fiscal third quarter 2026, ended April 30, 2026.

Total revenue for the fiscal third quarter 2026 grew 20% year over year to $3.0 billion, compared with total revenue of $2.5 billion for the fiscal third quarter 2025. Remaining performance obligation grew 25% year over year to $18.4 billion.

GAAP net income for the fiscal third quarter 2026 was $310 million, or $0.45 per diluted share, compared with $0.37 per diluted share for the fiscal third quarter 2025. Non-GAAP net income per diluted share was $0.85.

Operating margin expanded 120 basis points year over year to 13.0%. Net cash provided by operating activities was $870 million, and free cash flow was $800 million. Cash and cash equivalents were $2.2 billion at quarter end.

The company ended the quarter serving more than 4,000 customers across its platforms.

For the fiscal fourth quarter 2026, the company expects total revenue in the range of $3.30 billion to $3.40 billion."""


# The grading contract: every figure in the prose, and the only verdict class
# each may render. A figure missing from the document's claims, or rendering
# any other class, is the "0" the user grades us by. GAAP levels and the
# derived/growth figures must be exactly `traced`; figures with no possible
# filed source must be the honest classes, never a false conflict or trace.
EXPECTED_VERDICTS: dict[str, set[str]] = {
    "20%": {"traced"},                          # revenue growth, recomputed YoY
    "$3.0 billion": {"traced"},                 # total revenue, filed
    "$2.5 billion": {"traced"},                 # prior-year revenue comparative, filed
    "25%": {"traced"},                          # RPO growth, recomputed YoY
    "$18.4 billion": {"traced"},                # RPO, filed
    "$310 million": {"traced"},                 # net income, filed
    "$0.45": {"traced"},                        # GAAP diluted EPS, filed
    "$0.37": {"traced"},                        # prior-year EPS comparative, filed
    "$0.85": {"untraced", "needs_review"},      # non-GAAP EPS — no filed source exists
    "120 basis points": {"traced"},             # margin change, recomputed from margins
    "13.0%": {"traced"},                        # operating margin, recomputed ratio
    "$870 million": {"traced"},                 # operating cash flow, filed
    "$800 million": {"traced"},                 # free cash flow, recomputed (OCF − capex)
    "$2.2 billion": {"traced"},                 # cash and equivalents, filed
    "4,000 customers": {"untraced", "needs_review"},  # operational count — not filed
    "$3.30 billion to $3.40 billion": {"untraced", "needs_review"},  # forward guidance
}

# The figures that must cite a real source pointer when traced.
MUST_CITE = {"$3.0 billion", "$18.4 billion", "$310 million", "$0.45", "$870 million", "$2.2 billion"}

CIK = 1327567
ACCN = "0001327567-26-000015"


def _dur(val: float, start: str, end: str, filed: str = "2026-06-03") -> dict:
    return {"val": val, "start": start, "end": end, "accn": ACCN, "form": "10-Q", "filed": filed}


def _inst(val: float, end: str, filed: str = "2026-06-03") -> dict:
    return {"val": val, "end": end, "accn": ACCN, "form": "10-Q", "filed": filed}


def panw_two_year_edgar() -> StaticEdgarClient:
    """PANW (July fiscal year end) with FY2026-Q3 and the FY2025-Q3 comparatives —
    what a real `ingest_edgar` pulls — so levels trace and growth recomputes."""
    q3_26 = ("2026-02-01", "2026-04-30")
    q3_25 = ("2025-02-01", "2025-04-30")
    return StaticEdgarClient(
        tickers={"PANW": CIK},
        titles={"PANW": "Palo Alto Networks Inc"},
        fiscal_year_ends={CIK: "0731"},
        concepts={
            (CIK, "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"): {
                "units": {"USD": [_dur(3_000_000_000, *q3_26), _dur(2_500_000_000, *q3_25)]}
            },
            (CIK, "us-gaap:RevenueRemainingPerformanceObligation"): {
                "units": {"USD": [_inst(18_400_000_000, q3_26[1]), _inst(14_720_000_000, q3_25[1])]}
            },
            (CIK, "us-gaap:NetIncomeLoss"): {
                "units": {"USD": [_dur(310_000_000, *q3_26)]}
            },
            (CIK, "us-gaap:EarningsPerShareDiluted"): {
                "units": {"USD/shares": [_dur(0.45, *q3_26), _dur(0.37, *q3_25)]}
            },
            (CIK, "us-gaap:OperatingIncomeLoss"): {
                # 390/3000 = 13.0% margin; prior 295/2500 = 11.8% -> +120 bps
                "units": {"USD": [_dur(390_000_000, *q3_26), _dur(295_000_000, *q3_25)]}
            },
            (CIK, "us-gaap:NetCashProvidedByUsedInOperatingActivities"): {
                "units": {"USD": [_dur(870_000_000, *q3_26)]}
            },
            (CIK, "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"): {
                "units": {"USD": [_dur(70_000_000, *q3_26)]}
            },
            (CIK, "us-gaap:CashAndCashEquivalentsAtCarryingValue"): {
                "units": {"USD": [_inst(2_200_000_000, q3_26[1])]}
            },
        },
    )


class _StubExa:
    """Hermetic Exa transport serving the realistic release."""

    def post_json(self, path: str, payload: dict) -> dict:
        if path == "/search":
            return {
                "results": [
                    {
                        "url": RELEASE_URL,
                        "title": "Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results",
                        "publishedDate": "2026-05-28T00:00:00.000Z",
                        "highlights": [
                            "Total revenue for the fiscal third quarter 2026 grew 20% year over year"
                        ],
                    }
                ]
            }
        if path == "/contents":
            return {
                "results": [
                    {
                        "url": RELEASE_URL,
                        "title": "Palo Alto Networks Reports Fiscal Third Quarter 2026 Financial Results",
                        "publishedDate": "2026-05-28",
                        "text": RELEASE_TEXT,
                    }
                ]
            }
        raise AssertionError(f"unexpected Exa path {path}")


@pytest.fixture
def workflow(monkeypatch: pytest.MonkeyPatch):
    """The search→review→load walkthrough a user performs, via the real API."""
    monkeypatch.setattr("attest.ingestion.exa.LiveExaClient", lambda *a, **k: _StubExa())
    client = TestClient(create_app(AttestService(edgar=panw_two_year_edgar())))

    search = client.post(
        "/tenants/acme/historical/search",
        json={"entity": "Palo Alto Networks", "doc_types": ["release"], "quarters": 4},
    )
    assert search.status_code == 200
    body = search.json()
    candidate = body["candidates"][0]

    ingest = client.post(
        "/tenants/acme/historical/ingest",
        json={
            "entity": body["entity"],
            "items": [
                {
                    "url": candidate["url"],
                    "title": candidate["title"],
                    "period": candidate["period"],
                    "doc_type": candidate["doc_type"],
                }
            ],
        },
    )
    assert ingest.status_code == 200
    return body, ingest.json()


def test_user_types_a_company_name_and_gets_ticker_titled_results(workflow) -> None:
    search_body, ingest_body = workflow
    assert search_body["entity"] == "PANW"
    assert search_body["candidates"][0]["title"].startswith("PANW Earnings release · FY2026-Q3")
    # The loaded workspace document is named exactly as reviewed.
    assert ingest_body["documents"][0]["title"] == search_body["candidates"][0]["title"]
    assert ingest_body["entity"] == "PANW"


def test_every_figure_in_the_release_is_linked(workflow) -> None:
    """Every number the detector can see becomes a claim with a span — the chip
    the user clicks. A number with no claim is invisible to verification: a 0."""
    _, ingest_body = workflow
    doc = ingest_body["documents"][0]
    text = doc["text"]

    claim_spans = [tuple(c["span"]) for c in doc["claims"] if c.get("span")]
    assert len(claim_spans) == len(doc["claims"])  # no span-less claims

    def covered(span: tuple[int, int]) -> bool:
        return any(s <= span[0] and span[1] <= e for s, e in claim_spans)

    missed = [c.text for c in detect_candidates(text) if not covered(c.span)]
    assert missed == [], f"figures in the prose with no linked claim: {missed}"

    # Spans anchor exactly: the text under each span is the figure as displayed
    # (modulo the sign the extractor normalizes for losses/declines).
    for claim in doc["claims"]:
        s, e = claim["span"]
        assert text[s:e].strip() == claim["displayed_text"].lstrip("-")

    # And every claim has a verdict — nothing detected is silently dropped.
    verdict_ids = {v["claim_id"] for v in doc["verdicts"]}
    assert {c["claim_id"] for c in doc["claims"]} == verdict_ids


def test_no_figure_is_falsely_linked(workflow) -> None:
    """Grade each figure against the contract: expected classes only, zero
    conflicts on a correct release, and every traced figure cites its source."""
    _, ingest_body = workflow
    doc = ingest_body["documents"][0]
    verdicts = {v["displayed_text"]: v for v in doc["verdicts"]}

    failures: list[str] = []
    for figure, allowed in EXPECTED_VERDICTS.items():
        v = verdicts.get(figure)
        if v is None:
            failures.append(f"{figure!r}: NOT LINKED (no claim/verdict)")
            continue
        if v["verdict"] not in allowed:
            failures.append(
                f"{figure!r}: {v['verdict']} (expected {sorted(allowed)}) — {v['reason']}"
            )
    assert failures == [], "\n".join(failures)

    # A release whose numbers are right must produce zero conflicts.
    conflicts = [v for v in doc["verdicts"] if v["verdict"] == "conflict"]
    assert conflicts == [], [f"{v['displayed_text']} -> {v['reason']}" for v in conflicts]

    # Traced figures carry the source pointer the chip cites.
    for figure in MUST_CITE:
        prov = verdicts[figure].get("provenance") or {}
        assert prov.get("label"), f"{figure!r} traced but cites no source"
        assert "10-Q" in prov["label"]

    # The recomputed figures say what they were recomputed from.
    assert "vs FY2025-Q3" in verdicts["20%"]["reason"]
    assert verdicts["13.0%"]["verdict"] == "traced"
    assert "operating_income / total_revenue" in verdicts["13.0%"]["reason"]


def test_wrong_growth_figure_is_caught_not_traced(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dual of the contract: a release that *overstates* growth must conflict —
    linking it to the recomputed truth — never trace, never go silent."""
    wrong = RELEASE_TEXT.replace("grew 20% year over year", "grew 24% year over year")

    class _WrongExa(_StubExa):
        def post_json(self, path: str, payload: dict) -> dict:
            data = super().post_json(path, payload)
            if path == "/contents":
                data["results"][0]["text"] = wrong
            return data

    monkeypatch.setattr("attest.ingestion.exa.LiveExaClient", lambda *a, **k: _WrongExa())
    client = TestClient(create_app(AttestService(edgar=panw_two_year_edgar())))
    r = client.post(
        "/tenants/acme/historical/ingest",
        json={"entity": "PANW", "items": [{"url": RELEASE_URL, "period": "FY2026-Q3", "doc_type": "release"}]},
    )
    doc = r.json()["documents"][0]
    growth = next(v for v in doc["verdicts"] if v["displayed_text"] == "24%")
    assert growth["verdict"] == "conflict"
    assert growth["source_value"] == "20%"  # the truth, recomputed from filed levels


def test_loading_the_same_document_twice_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user who loads the same release again (a re-search tomorrow, a second
    tab, a double click) must get the document back, verified — never a 500 on
    duplicate reference facts, and no double-counted figures."""
    monkeypatch.setattr("attest.ingestion.exa.LiveExaClient", lambda *a, **k: _StubExa())
    client = TestClient(create_app(AttestService(edgar=panw_two_year_edgar())))
    payload = {
        "entity": "Palo Alto Networks",
        "items": [{"url": RELEASE_URL, "period": "FY2026-Q3", "doc_type": "release"}],
    }
    first = client.post("/tenants/acme/historical/ingest", json=payload)
    assert first.status_code == 200
    assert first.json()["total_ingested"] > 0

    again = client.post("/tenants/acme/historical/ingest", json=payload)
    assert again.status_code == 200
    body = again.json()
    assert body["total_ingested"] == 0  # everything already on file — skipped
    # The document still comes back fully analyzed, so the UI renders it with
    # the same linked figures as the first load.
    doc = body["documents"][0]
    assert doc["text"] and doc["claims"] and doc["verdicts"]
    assert {v["verdict"] for v in doc["verdicts"]} & {"traced"}


def test_workflow_is_dynamic_across_issuers_and_fiscal_calendars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is keyed to the demo or to PANW: a different issuer with a
    December fiscal year end resolves, loads, and ties out the same way."""
    cik = 9990001
    q1_26 = ("2026-01-01", "2026-03-31")
    q1_25 = ("2025-01-01", "2025-03-31")
    edgar = StaticEdgarClient(
        tickers={"VRTM": cik},
        titles={"VRTM": "Vertex Metrics Inc"},
        fiscal_year_ends={cik: "1231"},
        concepts={
            (cik, "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"): {
                "units": {"USD": [_dur(500_000_000, *q1_26), _dur(400_000_000, *q1_25)]}
            },
            (cik, "us-gaap:EarningsPerShareDiluted"): {
                "units": {"USD/shares": [_dur(0.62, *q1_26)]}
            },
        },
    )
    text = (
        "Vertex Metrics Reports First Quarter 2026 Results\n\n"
        "BOSTON, April 30, 2026 -- Vertex Metrics (NYSE: VRTM) reported results for "
        "the first quarter 2026, ended March 31, 2026.\n\n"
        "Total revenue grew 25% year over year to $500 million. GAAP diluted EPS was $0.62."
    )

    class _VrtmExa:
        def post_json(self, path: str, payload: dict) -> dict:
            if path == "/contents":
                return {"results": [{"url": "https://ir.vertexmetrics.example/q1",
                                     "title": "Vertex Metrics Reports First Quarter 2026 Results",
                                     "publishedDate": "2026-04-30", "text": text}]}
            raise AssertionError(path)

    monkeypatch.setattr("attest.ingestion.exa.LiveExaClient", lambda *a, **k: _VrtmExa())
    client = TestClient(create_app(AttestService(edgar=edgar)))
    r = client.post(
        "/tenants/acme/historical/ingest",
        json={
            "entity": "Vertex Metrics",  # company name, never a ticker
            "items": [{"url": "https://ir.vertexmetrics.example/q1", "doc_type": "release"}],
        },
    )
    body = r.json()
    assert body["entity"] == "VRTM"
    verdicts = {v["displayed_text"]: v["verdict"] for v in body["documents"][0]["verdicts"]}
    assert verdicts["$500 million"] == "traced"
    assert verdicts["25%"] == "traced"   # growth recomputed for a Dec-FYE issuer
    assert verdicts["$0.62"] == "traced"
    assert "conflict" not in verdicts.values()
