"""FastAPI application factory.

Thin and stateless per request: every endpoint delegates to :class:`AttestService`.
The service (and its in-memory stores) lives on ``app.state`` for this reference
build; swapping in Postgres/Redis-backed stores is a constructor change, not an
API change.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from attest.api.schemas import (
    AliasConfigRequest,
    AliasConfigResponse,
    AnalyzeResponse,
    AuditVerifyResponse,
    ClosePackResponse,
    GuidanceIngestRequest,
    IngestResponse,
    OverrideRequest,
    SignOffRequest,
    VerifyResponse,
)
from attest.domain.document import Document, DocumentKind
from attest.extraction.text import extract_text
from attest.ingestion.edgar_xbrl import load_fixture
from attest.service import AttestService
from attest.verification.engine import VerificationResult

_STATIC_DIR = Path(__file__).parent / "static"
_DEMO_FIXTURE = "meridian_q1_fy2026"


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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home() -> HTMLResponse:
        index = _STATIC_DIR / "index.html"
        if not index.exists():  # pragma: no cover - packaging guard
            return HTMLResponse("<h1>Attest</h1><p>UI asset missing.</p>", status_code=500)
        return HTMLResponse(index.read_text(encoding="utf-8"))

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

    @app.post("/tenants/{tenant_id}/ingest/guidance", response_model=IngestResponse)
    def ingest_guidance(
        tenant_id: str, req: GuidanceIngestRequest, svc: AttestService = Depends(get_service)
    ) -> IngestResponse:
        """Extract management's forward guidance from 8-K EX-99.1 prose into the fact
        store, each figure cited back to the exact sentence it came from."""
        report = svc.ingest_guidance(
            text=req.text,
            tenant_id=tenant_id,
            entity=req.entity,
            accession=req.accession,
            base_period=req.base_period,
            as_of=req.as_of,
            label=req.label,
        )
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

    @app.post("/tenants/{tenant_id}/ingest/demo", response_model=IngestResponse)
    def ingest_demo(tenant_id: str, svc: AttestService = Depends(get_service)) -> IngestResponse:
        """Convenience: ingest the bundled Meridian filing so an upload has filed
        sources to tie out against, straight from the UI."""
        report = svc.ingest_xbrl(load_fixture(_DEMO_FIXTURE), tenant_id=tenant_id)
        return IngestResponse(
            source=report.source,
            ingested=report.ingested,
            skipped=report.skipped,
            skipped_tags=list(report.skipped_tags),
        )

    @app.get("/tenants/{tenant_id}/extraction/aliases", response_model=AliasConfigResponse)
    def get_aliases(tenant_id: str, svc: AttestService = Depends(get_service)) -> AliasConfigResponse:
        """The extraction vocabulary (metric -> synonyms) in effect for the tenant."""
        return AliasConfigResponse(tenant_id=tenant_id, aliases=svc.aliases_for(tenant_id).as_dict())

    @app.put("/tenants/{tenant_id}/extraction/aliases", response_model=AliasConfigResponse)
    def put_aliases(
        tenant_id: str, req: AliasConfigRequest, svc: AttestService = Depends(get_service)
    ) -> AliasConfigResponse:
        """Configure the tenant's extraction synonyms (house style, segment names,
        non-GAAP labels). Unknown metric ids are rejected."""
        try:
            config = svc.configure_aliases(tenant_id, req.aliases, replace=req.replace)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return AliasConfigResponse(tenant_id=tenant_id, aliases=config.as_dict())

    @app.post("/tenants/{tenant_id}/analyze", response_model=AnalyzeResponse)
    async def analyze(
        tenant_id: str,
        file: UploadFile | None = File(default=None),
        text: str | None = Form(default=None),
        title: str | None = Form(default=None),
        kind: str = Form(default="other"),
        entity: str | None = Form(default=None),
        period: str | None = Form(default=None),
        svc: AttestService = Depends(get_service),
    ) -> AnalyzeResponse:
        """Upload a press release / script / Q&A (or paste it) and analyze it.

        Accepts a multipart ``file`` *or* a ``text`` field. Text is recovered from
        the file, the edge proposes figure claims, and the deterministic engine
        renders verdicts and runs every rule — the same spine the demo close pack
        flows through, now driven by a real document.
        """
        warnings: list[str] = []
        resolved_title = title
        if file is not None and file.filename:
            extracted = extract_text(file.filename, await file.read())
            doc_text = extracted.text
            warnings = list(extracted.warnings)
            resolved_title = title or file.filename
        elif text:
            doc_text = text
        else:
            raise HTTPException(status_code=422, detail="provide a file upload or text")

        if not doc_text.strip():
            raise HTTPException(
                status_code=422,
                detail="no readable text could be recovered from the upload",
            )

        try:
            doc_kind = DocumentKind(kind)
        except ValueError:
            doc_kind = DocumentKind.OTHER

        document, result, resolved_entity, resolved_period = svc.analyze_text(
            tenant_id=tenant_id,
            text=doc_text,
            title=resolved_title or "Uploaded document",
            kind=doc_kind,
            entity=entity or None,
            period=period or None,
        )
        base = _to_verify_response(result)
        return AnalyzeResponse(
            document_id=base.document_id,
            verdicts=base.verdicts,
            findings=base.findings,
            counts=base.counts,
            publishable=base.publishable,
            title=document.title,
            kind=document.kind.value,
            entity=resolved_entity,
            period=resolved_period,
            text=document.text,
            claims=list(document.claims),
            warnings=warnings,
        )

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
