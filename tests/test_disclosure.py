"""Consistency checks against prior disclosures.

A figure with no filed XBRL source (non-GAAP, operational) still has a reference
once the company's prior release / transcript / deck is ingested: a later draft
that restates it must agree, and one that changed it is flagged as contradicting
prior disclosure.
"""

from fastapi.testclient import TestClient

from attest.api.app import create_app
from attest.service import AttestService

_PRIOR = "Total revenue was $1.20 billion in the first quarter of fiscal 2025."


def _verdicts(svc: AttestService, text: str) -> dict:
    _, result, *_ = svc.analyze_text(
        tenant_id="acme", text=text, entity="ACME", period="FY2025-Q1"
    )
    return {v.metric: v for v in result.verdicts}


def test_draft_contradicting_prior_disclosure_is_flagged():
    svc = AttestService()  # no EDGAR, no filed facts — only the prior disclosure
    rep = svc.ingest_disclosure(
        text=_PRIOR, tenant_id="acme", entity="ACME", period="FY2025-Q1",
        label="Q1 FY2025 release",
    )
    assert rep.ingested >= 1

    v = _verdicts(svc, "In Q1 fiscal 2025, total revenue of $1.30 billion.")["total_revenue"]
    assert v.verdict.value == "conflict"
    assert "prior disclosure" in v.reason.lower()


def test_draft_restating_prior_disclosure_is_consistent():
    svc = AttestService()
    svc.ingest_disclosure(text=_PRIOR, tenant_id="acme", entity="ACME", period="FY2025-Q1")

    v = _verdicts(svc, "In Q1 fiscal 2025, total revenue of $1.20 billion.")["total_revenue"]
    # Consistent, but a prior disclosure is not a filing — still routed for sign-off.
    assert v.verdict.value == "needs_review"
    assert "consistent" in v.reason.lower()


def test_ingest_disclosure_endpoint_text():
    client = TestClient(create_app(AttestService()))
    r = client.post(
        "/tenants/acme/ingest/disclosure",
        data={"text": _PRIOR, "entity": "ACME", "period": "FY2025-Q1", "label": "Q1 call"},
    )
    assert r.status_code == 200
    assert r.json()["ingested"] >= 1


def test_ingest_disclosure_endpoint_file():
    client = TestClient(create_app(AttestService()))
    r = client.post(
        "/tenants/acme/ingest/disclosure",
        data={"entity": "ACME", "period": "FY2025-Q1"},
        files={"file": ("q1_call.txt", _PRIOR.encode(), "text/plain")},
    )
    assert r.status_code == 200
    assert r.json()["ingested"] >= 1
