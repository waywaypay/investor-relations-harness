"""FastAPI application factory.

Thin and stateless per request: every endpoint delegates to :class:`AttestService`.
The service (and its in-memory stores) lives on ``app.state`` for this reference
build; swapping in Postgres/Redis-backed stores is a constructor change, not an
API change.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from attest.api.schemas import (
    AuditVerifyResponse,
    ClosePackResponse,
    IngestResponse,
    OverrideRequest,
    SignOffRequest,
    VerifyResponse,
)
from attest.domain.document import Document
from attest.service import AttestService
from attest.verification.engine import VerificationResult


def _to_verify_response(result: VerificationResult) -> VerifyResponse:
    return VerifyResponse(
        document_id=result.document_id,
        verdicts=list(result.verdicts),
        findings=list(result.findings),
        counts=result.counts,
        publishable=result.publishable,
    )


def create_app(service: AttestService | None = None) -> FastAPI:
    app = FastAPI(
        title="Attest API",
        version="0.1.0",
        description="Deterministic disclosure-verification spine for investor relations.",
    )
    app.state.service = service or AttestService()

    def get_service() -> AttestService:
        return app.state.service

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "audit_intact": app.state.service.audit_verify()}

    @app.post("/tenants/{tenant_id}/ingest/xbrl", response_model=IngestResponse)
    def ingest_xbrl(
        tenant_id: str, instance: dict, svc: AttestService = Depends(get_service)
    ) -> IngestResponse:
        if "facts" not in instance:
            raise HTTPException(status_code=422, detail="instance missing 'facts'")
        report = svc.ingest_xbrl(instance, tenant_id=tenant_id)
        return IngestResponse(
            source=report.source,
            ingested=report.ingested,
            skipped=report.skipped,
            skipped_tags=list(report.skipped_tags),
        )

    @app.get("/tenants/{tenant_id}/facts")
    def list_facts(tenant_id: str, svc: AttestService = Depends(get_service)) -> list[dict]:
        return [f.model_dump(mode="json") for f in svc.store.all(tenant_id)]

    @app.post("/tenants/{tenant_id}/verify", response_model=VerifyResponse)
    def verify(
        tenant_id: str, document: Document, svc: AttestService = Depends(get_service)
    ) -> VerifyResponse:
        if document.tenant_id != tenant_id:
            raise HTTPException(status_code=422, detail="document.tenant_id mismatch")
        return _to_verify_response(svc.verify_document(document))

    @app.post("/tenants/{tenant_id}/verify-close-pack", response_model=ClosePackResponse)
    def verify_close_pack(
        tenant_id: str, documents: list[Document], svc: AttestService = Depends(get_service)
    ) -> ClosePackResponse:
        for doc in documents:
            if doc.tenant_id != tenant_id:
                raise HTTPException(status_code=422, detail="document.tenant_id mismatch")
        results, consistency = svc.verify_close_pack(documents)
        responses = [_to_verify_response(r) for r in results]
        publishable = all(r.publishable for r in responses) and not consistency
        return ClosePackResponse(
            documents=responses,
            consistency_findings=list(consistency),
            publishable=publishable,
        )

    @app.post("/tenants/{tenant_id}/documents/{document_id}/sign-off")
    def sign_off(
        tenant_id: str, document_id: str, req: SignOffRequest,
        svc: AttestService = Depends(get_service),
    ) -> dict:
        svc.sign_off(
            tenant_id=tenant_id, actor=req.actor, document_id=document_id, scope=req.scope
        )
        return {"status": "recorded"}

    @app.post("/tenants/{tenant_id}/override")
    def override(
        tenant_id: str, req: OverrideRequest, svc: AttestService = Depends(get_service)
    ) -> dict:
        svc.override(
            tenant_id=tenant_id, actor=req.actor, claim_id=req.claim_id,
            justification=req.justification,
        )
        return {"status": "recorded"}

    @app.get("/tenants/{tenant_id}/audit")
    def audit_export(tenant_id: str, svc: AttestService = Depends(get_service)) -> list[dict]:
        return svc.audit_export(tenant_id)

    @app.get("/audit/verify", response_model=AuditVerifyResponse)
    def audit_verify(svc: AttestService = Depends(get_service)) -> AuditVerifyResponse:
        return AuditVerifyResponse(
            intact=svc.audit_verify(), event_count=len(svc.audit_log.events())
        )

    return app
