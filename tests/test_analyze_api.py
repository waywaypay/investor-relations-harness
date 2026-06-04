"""API tests for the upload-and-analyze surface."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from attest.api.app import create_app
from attest.service import AttestService
from test_edgar_ingestion import panw_client

RELEASE = (
    "Meridian Systems reported total revenue of $1.24 billion, up 18% year over year. "
    "The company delivered GAAP diluted EPS of $0.87 and non-GAAP diluted EPS of $1.12. "
    "Cloud segment revenue reached $612 million, up 31% from the prior-year period. "
    "Operating cash flow was $338 million. Meridian repurchased $250 million of common "
    "stock. For the second quarter, the company expects total revenue in the range of "
    "$1.31 to $1.34 billion."
)


@pytest.fixture
def client():
    return TestClient(create_app())


def _seed_demo(client):
    r = client.post("/tenants/meridian/ingest/demo")
    assert r.status_code == 200
    assert r.json()["ingested"] == 15


def test_home_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Attest" in r.text  # the served workspace (built SPA or fallback page)


def test_ingest_demo_seeds_filed_sources(client):
    _seed_demo(client)
    facts = client.get("/tenants/meridian/facts").json()
    assert len(facts) == 15


def test_analyze_pasted_text_ties_out_against_filed_sources(client):
    _seed_demo(client)
    r = client.post(
        "/tenants/meridian/analyze",
        data={"text": RELEASE, "title": "Q1 release", "kind": "release",
              "entity": "MRDN", "period": "FY2026-Q1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["traced"] == 6
    assert body["counts"]["conflict"] == 1       # the 31% restatement
    assert body["counts"]["needs_review"] == 1   # guidance
    assert body["publishable"] is False
    assert body["entity"] == "MRDN" and body["period"] == "FY2026-Q1"
    # Claims come back with spans so the UI can highlight figures in place.
    assert all(c["span"] is not None for c in body["claims"])
    rules = {f["rule"] for f in body["findings"]}
    assert "forward_looking.safe_harbor_required" in rules


def test_analyze_without_ticker_warns_no_filed_source(client):
    # No demo seeded, no ticker supplied: every figure is untraced, and the
    # response now explains *why* instead of leaving a silent wall of "untraced".
    r = client.post(
        "/tenants/acme/analyze",
        data={"text": RELEASE, "title": "Q1 release", "kind": "release"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["counts"].get("traced", 0) == 0
    assert body["counts"]["untraced"] > 0
    assert any("no issuer ticker was provided" in w.lower() for w in body["warnings"])


def test_analyze_with_ticker_but_edgar_disabled_warns(client):
    # A ticker is given but live EDGAR tie-out is off (the default in tests), so
    # nothing loads — the response says so rather than failing silently.
    r = client.post(
        "/tenants/acme/analyze",
        data={"text": RELEASE, "title": "Q1 release", "kind": "release", "entity": "AAPL"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["counts"].get("traced", 0) == 0
    assert any("edgar" in w.lower() and "disabled" in w.lower() for w in body["warnings"])


def test_analyze_with_filed_source_does_not_warn_about_missing_source(client):
    # When the issuer's filed facts are loaded, figures trace and the
    # no-filed-source warning is absent.
    _seed_demo(client)
    r = client.post(
        "/tenants/meridian/analyze",
        data={"text": RELEASE, "title": "Q1 release", "kind": "release",
              "entity": "MRDN", "period": "FY2026-Q1"},
    )
    body = r.json()
    assert body["counts"]["traced"] == 6
    assert not any("no filed source" in w.lower() or "no issuer ticker" in w.lower()
                   for w in body["warnings"])


def test_analyze_file_upload(client):
    _seed_demo(client)
    r = client.post(
        "/tenants/meridian/analyze",
        data={"kind": "release", "entity": "MRDN", "period": "FY2026-Q1"},
        files={"file": ("release.txt", RELEASE.encode(), "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "release.txt"
    assert body["counts"]["traced"] == 6
    assert body["counts"]["conflict"] == 1


def test_analyze_docx_upload(client):
    _seed_demo(client)
    buf = io.BytesIO()
    xml = (
        '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
        "<w:p><w:r><w:t>Total revenue of $1.24 billion. "
        "Operating cash flow was $338 million.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    r = client.post(
        "/tenants/meridian/analyze",
        data={"kind": "release", "entity": "MRDN", "period": "FY2026-Q1"},
        files={"file": ("remarks.docx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["traced"] == 2  # both figures tie out


def test_analyze_without_facts_is_honest_untraced_but_runs_rules(client):
    # No demo ingested: nothing can be traced, but the prose rules still fire.
    r = client.post(
        "/tenants/meridian/analyze",
        data={"text": RELEASE, "kind": "release", "entity": "MRDN", "period": "FY2026-Q1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["traced"] == 0
    assert body["counts"]["untraced"] >= 6
    assert body["publishable"] is False
    rules = {f["rule"] for f in body["findings"]}
    assert "forward_looking.safe_harbor_required" in rules  # guidance w/o safe harbor


def test_analyze_requires_some_input(client):
    r = client.post("/tenants/meridian/analyze", data={"kind": "release"})
    assert r.status_code == 422


def test_get_aliases_returns_default_vocabulary(client):
    body = client.get("/tenants/meridian/extraction/aliases").json()
    assert body["tenant_id"] == "meridian"
    assert "total_revenue" in body["aliases"]


def test_put_aliases_then_analyze_uses_house_style(client):
    _seed_demo(client)
    text = "Topline was $1.24 billion for the quarter."
    # Without config the company's term isn't recognised.
    before = client.post(
        "/tenants/meridian/analyze",
        data={"text": text, "kind": "release", "entity": "MRDN", "period": "FY2026-Q1"},
    ).json()
    assert before["counts"]["traced"] == 0

    r = client.put(
        "/tenants/meridian/extraction/aliases",
        json={"aliases": {"total_revenue": ["topline"]}},
    )
    assert r.status_code == 200
    assert "topline" in r.json()["aliases"]["total_revenue"]

    after = client.post(
        "/tenants/meridian/analyze",
        data={"text": text, "kind": "release", "entity": "MRDN", "period": "FY2026-Q1"},
    ).json()
    assert after["counts"]["traced"] == 1  # now attributed and tied out


def test_put_aliases_rejects_unknown_metric(client):
    r = client.put(
        "/tenants/meridian/extraction/aliases",
        json={"aliases": {"not_a_metric": ["foo"]}},
    )
    assert r.status_code == 422


def test_alias_config_is_per_tenant(client):
    client.put("/tenants/meridian/extraction/aliases", json={"aliases": {"total_revenue": ["topline"]}})
    other = client.get("/tenants/acme/extraction/aliases").json()
    assert "topline" not in other["aliases"].get("total_revenue", [])


def test_analyze_records_verdicts_in_audit_chain(client):
    _seed_demo(client)
    client.post("/tenants/meridian/analyze",
                data={"text": RELEASE, "kind": "release", "entity": "MRDN", "period": "FY2026-Q1"})
    audit = client.get("/tenants/meridian/audit").json()
    assert any(e["type"] == "verdict" for e in audit)
    assert client.get("/audit/verify").json()["intact"] is True


# -- EDGAR tie-out: upload a real transcript, tie out to filed SEC facts -------

PANW_TRANSCRIPT = (
    "Palo Alto Networks Fiscal Second Quarter 2026 Earnings Call. "
    "Total revenue was $2.59 billion and grew 15%. Our remaining performance "
    "obligation, or RPO, grew 23% to $16.0 billion."
)


@pytest.fixture
def edgar_client():
    """A TestClient over an app with live EDGAR stubbed by the static PANW client."""
    return TestClient(create_app(AttestService(edgar=panw_client())))


def test_ingest_edgar_endpoint_loads_filed_facts(edgar_client):
    r = edgar_client.post("/tenants/acme/ingest/edgar", json={"ticker": "PANW"})
    assert r.status_code == 200
    assert r.json()["ingested"] >= 2
    facts = edgar_client.get("/tenants/acme/facts").json()
    metrics = {f["metric"] for f in facts}
    assert "total_revenue" in metrics and "total_rpo" in metrics


def test_ingest_edgar_returns_503_when_disabled(client):
    # The default app has no EDGAR client wired (hermetic, no network).
    r = client.post("/tenants/acme/ingest/edgar", json={"ticker": "PANW"})
    assert r.status_code == 503


def test_analyze_with_ticker_auto_ingests_and_ties_out(edgar_client):
    r = edgar_client.post(
        "/tenants/acme/analyze",
        data={"kind": "script", "entity": "PANW"},
        files={"file": ("panw_q2.txt", PANW_TRANSCRIPT.encode(), "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "FY2026-Q2"
    traced = {v["metric"] for v in body["verdicts"] if v["verdict"] == "traced"}
    assert {"total_revenue", "total_rpo"} <= traced
    assert any("PANW" in w for w in body["warnings"])  # honest note that filings loaded
