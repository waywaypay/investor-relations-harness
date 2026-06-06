"""FastAPI application factory.

Thin and stateless per request: every endpoint delegates to :class:`AttestService`.
The service (and its in-memory stores) lives on ``app.state`` for this reference
build; swapping in Postgres/Redis-backed stores is a constructor change, not an
API change.
"""

from __future__ import annotations

import os

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from attest.api.frontend import spa_html
from attest.api.schemas import (
    AliasConfigRequest,
    AliasConfigResponse,
    AnalyzeResponse,
    AuditVerifyResponse,
    ClosePackResponse,
    EditRequest,
    EdgarIngestRequest,
    GuidanceIngestRequest,
    IngestResponse,
    OverrideRequest,
    PriorPeriodExhibit,
    PriorPeriodIngestRequest,
    PriorPeriodIngestResponse,
    SignOffRequest,
    VerifyResponse,
)
from attest.domain.document import Document, DocumentKind
from attest.extraction.claims import infer_entity_ticker
from attest.extraction.text import extract_text
from attest.ingestion.edgar import EdgarUnavailable
from attest.ingestion.edgar_xbrl import load_fixture
from attest.service import AttestService
from attest.verification.engine import VerificationResult

_DEMO_FIXTURE = "meridian_q1_fy2026"


def _to_verify_response(result: VerificationResult) -> VerifyResponse:
    return VerifyResponse(
        document_id=result.document_id,
        verdicts=list(result.verdicts),
        findings=list(result.findings),
        counts=result.counts,
        publishable=result.publishable,
    )


def create_app(service: AttestService | None = None, *, seed_demo: bool = False) -> FastAPI:
    app = FastAPI(
        title="Attest API",
        version="0.1.0",
        description="Deterministic disclosure-verification spine for investor relations.",
    )
    if service is None:
        # The container entry point (uvicorn --factory) passes no args. Wire a live
        # EDGAR client so an uploaded draft tagged with an issuer ticker ties out to
        # that issuer's as-filed XBRL (see /analyze -> ensure_issuer_facts). It is
        # opt-in via ATTEST_EDGAR (default off) so importing this module in tests
        # never reaches for the network.
        from attest.storage.factory import edgar_client_from_env

        service = AttestService(edgar=edgar_client_from_env(default_enabled=False))

        # ATTEST_SEED_DEMO ships the ready-to-explore Meridian instance — the
        # bundled filing is ingested so the front-end ties its figures out the
        # moment the page loads, with no separate ingest step. Layered onto the
        # EDGAR-enabled service above (not seeded_service(), which wires no EDGAR)
        # so the seeded demo can *also* tie out real ticker-tagged uploads.
        env_seed = os.environ.get("ATTEST_SEED_DEMO", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if seed_demo or env_seed:
            from attest.demo import TENANT

            service.ingest_xbrl(load_fixture(_DEMO_FIXTURE), tenant_id=TENANT)
    app.state.service = service

    # CORS so the web workspace (Vite dev server on :5173) can call the API
    # directly in development. Override the allowed origins via ATTEST_CORS_ORIGINS
    # (comma-separated) in any deployment with a known front-end origin.
    origins_env = os.environ.get("ATTEST_CORS_ORIGINS", "").strip()
    allow_origins = (
        [o.strip() for o in origins_env.split(",") if o.strip()]
        if origins_env
        else ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_service() -> AttestService:
        return app.state.service

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> str:
        return spa_html()

    @app.get("/health")
    def health() -> dict:
        """Liveness: cheap, no work. This is the probe a load balancer / App Runner
        hits every few seconds, so it must not re-derive the audit chain."""
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        """Readiness + integrity: confirms the service is wired and the audit
        hash-chain still re-derives. Returns 503 if the chain is broken so an
        orchestrator pulls the instance out of rotation. When a persistent store
        lands, add its connectivity check here."""
        intact = app.state.service.audit_verify()
        status = 200 if intact else 503
        return JSONResponse({"status": "ready" if intact else "degraded",
                             "audit_intact": intact}, status_code=status)

    @app.post("/tenants/{tenant_id}/ingest/xbrl", response_model=IngestResponse)
    def ingest_xbrl(
        tenant_id: str, instance: dict, svc: AttestService = Depends(get_service)
    ) -> IngestResponse:
        if "facts" not in instance:
            raise HTTPException(status_code=422, detail="instance missing 'facts'")
        try:
            report = svc.ingest_xbrl(instance, tenant_id=tenant_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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

    @app.post("/tenants/{tenant_id}/ingest/disclosure", response_model=IngestResponse)
    async def ingest_disclosure(
        tenant_id: str,
        file: UploadFile | None = File(default=None),
        text: str | None = Form(default=None),
        entity: str | None = Form(default=None),
        period: str | None = Form(default=None),
        label: str | None = Form(default=None),
        as_of: str = Form(default="1970-01-01"),
        svc: AttestService = Depends(get_service),
    ) -> IngestResponse:
        """Ingest a prior public disclosure (past release / transcript / deck) so a
        later draft can be checked for consistency: a figure restated with a changed
        value is flagged as contradicting what the company previously disclosed —
        even for non-GAAP / operational numbers that have no filed source.

        Accepts a multipart ``file`` or a ``text`` field, mirroring /analyze, so any
        upload type is recovered server-side. The issuer is taken from ``entity`` if
        given, else detected from the document, else the tenant's primary issuer."""
        if file is not None and file.filename:
            extracted = extract_text(file.filename, await file.read())
            doc_text = extracted.text
            label = label or file.filename
        elif text:
            doc_text = text
        else:
            raise HTTPException(status_code=422, detail="provide a file upload or text")
        if not doc_text.strip():
            raise HTTPException(
                status_code=422,
                detail="no readable text could be recovered from the disclosure",
            )
        resolved_entity = (
            entity or infer_entity_ticker(label or "", doc_text) or svc.default_entity(tenant_id)
        )
        report = svc.ingest_disclosure(
            text=doc_text,
            tenant_id=tenant_id,
            entity=resolved_entity,
            period=period or None,
            as_of=as_of,
            source_ref=f"disclosure:{label or 'prior'}",
            label=label,
        )
        return IngestResponse(
            source=report.source,
            ingested=report.ingested,
            skipped=report.skipped,
            skipped_tags=list(report.skipped_tags),
        )

    @app.post("/tenants/{tenant_id}/ingest/edgar", response_model=IngestResponse)
    def ingest_edgar(
        tenant_id: str, req: EdgarIngestRequest, svc: AttestService = Depends(get_service)
    ) -> IngestResponse:
        """Pull an issuer's real filed facts from SEC EDGAR by ticker, so an uploaded
        draft for that issuer ties out against its as-filed numbers."""
        if svc.edgar is None:
            raise HTTPException(status_code=503, detail="EDGAR ingestion is not enabled")
        try:
            report = svc.ingest_edgar(req.ticker, tenant_id, max_years=req.max_years)
        except EdgarUnavailable as exc:
            raise HTTPException(status_code=502, detail=f"EDGAR unreachable: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        tenant_id: str,
        document: Document,
        use_llm: bool = False,
        svc: AttestService = Depends(get_service),
    ) -> VerifyResponse:
        if document.tenant_id != tenant_id:
            raise HTTPException(status_code=422, detail="document.tenant_id mismatch")
        try:
            result = svc.verify_document(document, use_llm=use_llm)
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _to_verify_response(result)

    @app.post("/tenants/{tenant_id}/ingest/prior-period", response_model=PriorPeriodIngestResponse)
    def ingest_prior_period(
        tenant_id: str, req: PriorPeriodIngestRequest, svc: AttestService = Depends(get_service)
    ) -> PriorPeriodIngestResponse:
        """Auto-fetch the prior quarter's 8-K press release from EDGAR and ingest it.

        Derives the prior fiscal period from ``period``, locates the matching
        earnings 8-K on EDGAR (item 2.02 · Results of Operations), fetches
        Exhibit 99.1 prose, and ingests it via the guidance connector. Each
        extracted figure is stored with a citation to the exact sentence it came
        from, so later drafts that reference prior-period figures can tie out to
        the filed source automatically.

        If ``cik`` is omitted it is resolved from ``entity`` via the SEC
        company-tickers list. Returns an empty ``exhibits`` list when no matching
        filing is found — the caller can surface this as "no prior period data
        available" without treating it as an error.
        """
        reports, prev, exhibits = svc.fetch_prior_period(
            tenant_id=tenant_id,
            entity=req.entity,
            period=req.period,
            cik=req.cik,
        )
        exhibit_summaries = [
            PriorPeriodExhibit(
                accession=ex.accession,
                filing_date=ex.filing_date,
                label=ex.label,
                ingested=rpt.ingested,
                skipped=rpt.skipped,
            )
            for ex, rpt in zip(exhibits, reports)
        ]
        return PriorPeriodIngestResponse(
            prior_period=prev,
            exhibits=exhibit_summaries,
            total_ingested=sum(r.ingested for r in reports),
        )

    @app.post("/tenants/{tenant_id}/ingest/demo", response_model=IngestResponse)
    def ingest_demo(tenant_id: str, svc: AttestService = Depends(get_service)) -> IngestResponse:
        """Convenience: ingest the bundled Meridian filing so an upload has filed
        sources to tie out against, straight from the UI.

        Idempotent: re-seeding a tenant that already has the demo filing is a no-op
        (every figure was already ingested), so clicking "seed" twice — or seeding a
        tenant the server already seeded at startup — never errors."""
        fixture = load_fixture(_DEMO_FIXTURE)
        accession = fixture.get("accession", "")
        already = sum(1 for f in svc.store.all(tenant_id) if f.id.startswith(f"{accession}:"))
        if already:
            return IngestResponse(
                source=f"edgar_xbrl:{accession}", ingested=0, skipped=already, skipped_tags=[]
            )
        report = svc.ingest_xbrl(fixture, tenant_id=tenant_id)
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

        # No ticker typed? Recover it from the draft itself — earnings materials
        # name the issuer as "Company (NASDAQ: TICKER)" — so the upload ties out
        # to the right company with nothing to type.
        if not entity:
            detected = infer_entity_ticker(resolved_title or "", doc_text)
            if detected:
                entity = detected
                warnings = warnings + [
                    f"Detected issuer ticker {detected} in the document."
                ]

        # When an issuer ticker is supplied (or detected) and live EDGAR ingestion
        # is enabled, load that issuer's filed facts first so the draft ties out
        # against its as-filed numbers. Best-effort and honest: an outage adds a
        # warning, never a failure — the draft still analyzes, figures stay untraced.
        if entity:
            warnings = warnings + svc.ensure_issuer_facts(tenant_id, entity)

        document, result, resolved_entity, resolved_period = svc.analyze_text(
            tenant_id=tenant_id,
            text=doc_text,
            title=resolved_title or "Uploaded document",
            kind=doc_kind,
            entity=entity or None,
            period=period or None,
        )

        # A figure can only link to a source when filed facts for the issuer are
        # loaded. When none are, every figure is honestly "untraced" — but the two
        # silent paths (no ticker supplied, or EDGAR disabled) leave that
        # unexplained. Say it plainly. The ticker-given-but-unreachable / not-found
        # cases are already explained by ensure_issuer_facts above.
        if result.counts.get("untraced") and not svc.has_filed_facts(
            tenant_id, resolved_entity
        ):
            if not entity:
                warnings = warnings + [
                    "No issuer ticker was provided, and none could be detected in the "
                    "document, so no filed source was loaded — these figures can't be "
                    "traced. Re-upload with the issuer's ticker (e.g. 'NASDAQ: ACME' in "
                    "the text, or the ticker field) to tie out against its filings.",
                ]
            elif svc.edgar is None:
                warnings = warnings + [
                    f"Live EDGAR tie-out is disabled in this deployment, so no filed "
                    f"source was loaded for {resolved_entity} — its figures stay "
                    f"untraced. Enable EDGAR (set ATTEST_EDGAR) or ingest the issuer's "
                    f"filing first.",
                ]

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
        tenant_id: str,
        documents: list[Document],
        use_llm: bool = False,
        svc: AttestService = Depends(get_service),
    ) -> ClosePackResponse:
        for doc in documents:
            if doc.tenant_id != tenant_id:
                raise HTTPException(status_code=422, detail="document.tenant_id mismatch")
        try:
            results, consistency = svc.verify_close_pack(documents, use_llm=use_llm)
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        responses = [_to_verify_response(r) for r in results]
        publishable = all(r.publishable for r in responses) and not consistency
        return ClosePackResponse(
            documents=responses,
            consistency_findings=list(consistency),
            publishable=publishable,
        )

    @app.post("/tenants/{tenant_id}/documents/{document_id}/edit")
    def edit_draft(
        tenant_id: str, document_id: str, req: EditRequest,
        svc: AttestService = Depends(get_service),
    ) -> dict:
        svc.edit_draft(
            tenant_id=tenant_id, actor=req.actor, document_id=document_id,
            before=req.before, after=req.after, claim_id=req.claim_id, note=req.note,
        )
        return {"status": "recorded"}

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
            justification=req.justification, reason=req.reason, metric=req.metric,
            period=req.period, displayed_text=req.displayed_text,
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


app = create_app()
