import pytest
from fastapi.testclient import TestClient

from attest.api.app import create_app
from attest.demo import build_documents
from attest.ingestion.edgar_xbrl import load_fixture
from attest.ingestion.guidance import load_press_release


@pytest.fixture
def client():
    return TestClient(create_app())


def _seed(client):
    instance = load_fixture("atlas_q1_fy2026")
    r = client.post("/tenants/atlas/ingest/xbrl", json=instance)
    assert r.status_code == 200
    return r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_reports_audit_intact(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["audit_intact"] is True


def test_ingest_and_list_facts(client):
    report = _seed(client)
    assert report["ingested"] == 15
    facts = client.get("/tenants/atlas/facts").json()
    assert len(facts) == 15


def test_ingest_guidance_endpoint_cites_the_source_line(client):
    r = client.post(
        "/tenants/atlas/ingest/guidance",
        json={
            "text": load_press_release("atlas_q1_fy2026_8k_ex99_1"),
            "entity": "ATLS",
            "accession": "0001047469-26-001200",
            "base_period": "FY2026-Q1",
            "as_of": "2026-04-28",
        },
    )
    assert r.status_code == 200
    assert r.json()["ingested"] == 4
    facts = client.get("/tenants/atlas/facts").json()
    rev = next(
        f for f in facts
        if f["metric"] == "revenue_guidance" and f["period"] == "FY2026-Q2"
    )
    assert "in the range of $1.31 to $1.34 billion" in rev["source_excerpt"]
    assert rev["source_type"] == "filing_line"


def test_verify_release_endpoint(client):
    _seed(client)
    release = next(d for d in build_documents() if d.id == "release")
    r = client.post("/tenants/atlas/verify", json=release.model_dump(mode="json"))
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["traced"] == 6
    assert body["counts"]["conflict"] == 1
    assert body["publishable"] is False


def test_verify_close_pack_endpoint(client):
    _seed(client)
    docs = [d.model_dump(mode="json") for d in build_documents()]
    r = client.post("/tenants/atlas/verify-close-pack", json=docs)
    assert r.status_code == 200
    body = r.json()
    assert len(body["documents"]) == 3
    assert body["consistency_findings"] == []
    assert body["publishable"] is False


def test_tenant_mismatch_rejected(client):
    _seed(client)
    release = next(d for d in build_documents() if d.id == "release")
    r = client.post("/tenants/other/verify", json=release.model_dump(mode="json"))
    assert r.status_code == 422


def test_edit_draft_records_audit_event(client):
    _seed(client)
    r = client.post(
        "/tenants/atlas/documents/release/edit",
        json={
            "actor": "iro@atlas",
            "claim_id": "r5",
            "before": "31%",
            "after": "29%",
            "note": "corrected for prior-year restatement",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"status": "recorded"}

    audit = client.get("/tenants/atlas/audit").json()
    edits = [e for e in audit if e["type"] == "edit"]
    assert len(edits) == 1
    assert edits[0]["actor"] == "iro@atlas"
    assert edits[0]["payload"]["before"] == "31%"
    assert edits[0]["payload"]["after"] == "29%"
    assert edits[0]["payload"]["claim_id"] == "r5"

    # the edit is a real link in the hash chain, not a silent mutation
    assert client.get("/audit/verify").json()["intact"] is True


def test_signoff_override_and_audit_chain(client):
    _seed(client)
    client.post("/tenants/atlas/documents/release/sign-off", json={"actor": "cfo@atlas"})
    client.post(
        "/tenants/atlas/override",
        json={"actor": "iro@atlas", "claim_id": "r5", "justification": "verified manually"},
    )
    audit = client.get("/tenants/atlas/audit").json()
    types = [e["type"] for e in audit]
    assert "ingest" in types and "sign_off" in types and "override" in types

    verify = client.get("/audit/verify").json()
    assert verify["intact"] is True


def test_cors_headers_present_for_dev_origin(client):
    r = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
