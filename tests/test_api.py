import pytest
from fastapi.testclient import TestClient

from attest.api.app import create_app
from attest.demo import build_documents
from attest.ingestion.edgar_xbrl import load_fixture


@pytest.fixture
def client():
    return TestClient(create_app())


def _seed(client):
    instance = load_fixture("meridian_q1_fy2026")
    r = client.post("/tenants/meridian/ingest/xbrl", json=instance)
    assert r.status_code == 200
    return r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ingest_and_list_facts(client):
    report = _seed(client)
    assert report["ingested"] == 11
    facts = client.get("/tenants/meridian/facts").json()
    assert len(facts) == 11


def test_verify_release_endpoint(client):
    _seed(client)
    release = next(d for d in build_documents() if d.id == "release")
    r = client.post("/tenants/meridian/verify", json=release.model_dump(mode="json"))
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["traced"] == 6
    assert body["counts"]["conflict"] == 1
    assert body["publishable"] is False


def test_verify_close_pack_endpoint(client):
    _seed(client)
    docs = [d.model_dump(mode="json") for d in build_documents()]
    r = client.post("/tenants/meridian/verify-close-pack", json=docs)
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


def test_signoff_override_and_audit_chain(client):
    _seed(client)
    client.post("/tenants/meridian/documents/release/sign-off", json={"actor": "cfo@meridian"})
    client.post(
        "/tenants/meridian/override",
        json={"actor": "iro@meridian", "claim_id": "r5", "justification": "verified manually"},
    )
    audit = client.get("/tenants/meridian/audit").json()
    types = [e["type"] for e in audit]
    assert "ingest" in types and "sign_off" in types and "override" in types

    verify = client.get("/audit/verify").json()
    assert verify["intact"] is True
