"""Application service — the orchestration seam the API and CLI share.

Holds the wiring (fact store, registry, audit log, engine) and exposes the
verbs that mutate state, each of which writes an attributable audit event:
ingest, verify, edit, override, sign-off. Human-in-the-loop with captured
accountability: Attest never silently changes a disclosure.
"""

from __future__ import annotations

import uuid

from attest.audit.events import EventType
from attest.audit.log import AuditLog, InMemoryAuditLog
from collections.abc import Iterable, Mapping

from attest.domain.document import Document, DocumentKind
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.domain.money import DEFAULT_POLICY, RoundingPolicy
from attest.extraction.claims import DEFAULT_ALIASES, AliasConfig, ClaimExtractor, infer_period
from attest.edge.service import EdgeService
from attest.factstore.repository import FactStore, InMemoryFactStore
from attest.ingestion.base import IngestionReport
from attest.ingestion.edgar import EdgarClient, EdgarConnector, EdgarUnavailable
from attest.ingestion.edgar_xbrl import XBRLConnector
from attest.ingestion.guidance import DisclosureConnector, GuidanceConnector
from attest.ingestion.prior_period import EdgarHttpClient, FetchedExhibit, PriorPeriodFetcher, prior_period as _prior_period
from attest.verification.engine import VerificationEngine, VerificationResult
from attest.verification.rules import check_cross_document_consistency


class AttestService:
    """The composition root for the verification spine."""

    def __init__(
        self,
        store: FactStore | None = None,
        registry: MetricRegistry | None = None,
        audit_log: AuditLog | None = None,
        policy: RoundingPolicy = DEFAULT_POLICY,
        aliases: AliasConfig | None = None,
        edge: EdgeService | None = None,
        edgar: EdgarClient | None = None,
    ) -> None:
        self.store = store or InMemoryFactStore()
        self.registry = registry or DEFAULT_REGISTRY
        self.audit_log = audit_log or InMemoryAuditLog()
        # The probabilistic edge is optional. When absent, the service behaves
        # exactly as the deterministic-only v1 — the core never depends on it.
        self.edge = edge
        # Optional live EDGAR transport. When set, an uploaded draft can tie out
        # against the issuer's *real* filed facts (see ingest_edgar /
        # ensure_issuer_facts); when None the service is unchanged.
        self.edgar = edgar
        self.engine = VerificationEngine(
            self.store, self.registry, policy=policy, audit_log=self.audit_log
        )
        # The extraction edge's metric vocabulary, per tenant, over a default.
        self._default_aliases = aliases or DEFAULT_ALIASES
        self._aliases_by_tenant: dict[str, AliasConfig] = {}

    # -- ingestion -----------------------------------------------------------

    def ingest_xbrl(
        self, instance: dict, tenant_id: str, actor: str = "system:ingestion"
    ) -> IngestionReport:
        facts, report = XBRLConnector(self.registry).fetch(instance, tenant_id)
        self.store.add_many(facts)
        self.audit_log.append(
            actor=actor,
            type=EventType.INGEST,
            tenant_id=tenant_id,
            payload=report.model_dump(),
        )
        return report

    def ingest_guidance(
        self,
        *,
        text: str,
        tenant_id: str,
        entity: str,
        accession: str,
        base_period: str | None = None,
        as_of: str = "1970-01-01",
        label: str | None = None,
        actor: str = "system:ingestion",
    ) -> IngestionReport:
        """Ingest management's forward guidance from 8-K EX-99.1 press-release prose.

        The prose analog of :meth:`ingest_xbrl`: it plugs the one hole XBRL leaves —
        guidance never lands in a tagged fact — and lands each figure with the exact
        sentence it came from, so a later draft's reaffirmed guidance ties out to the
        published line. Like every ingestion, it writes an attributable audit event.
        """
        facts, report = GuidanceConnector(self.registry).fetch(
            text=text,
            tenant_id=tenant_id,
            entity=entity,
            accession=accession,
            base_period=base_period,
            as_of=as_of,
            label=label,
        )
        self.store.add_many(facts)
        self.audit_log.append(
            actor=actor,
            type=EventType.INGEST,
            tenant_id=tenant_id,
            payload=report.model_dump(),
        )
        return report

    def ingest_disclosure(
        self,
        *,
        text: str,
        tenant_id: str,
        entity: str,
        period: str | None = None,
        as_of: str = "1970-01-01",
        source_ref: str = "prior-disclosure",
        label: str | None = None,
        actor: str = "system:ingestion",
    ) -> IngestionReport:
        """Ingest a prior public disclosure (past release / transcript / deck) as
        non-filed "previously disclosed" facts.

        The corpus for consistency checks: figures with no filed XBRL source
        (non-GAAP, operational) still get a reference, so a later draft that restates
        one and *changed it* is flagged as contradicting prior disclosure. Like every
        ingestion, it writes an attributable audit event.
        """
        facts, report = DisclosureConnector(
            self.registry, self.store, self.aliases_for(tenant_id)
        ).fetch(
            text=text,
            tenant_id=tenant_id,
            entity=entity,
            period=period,
            as_of=as_of,
            source_ref=source_ref,
            label=label,
        )
        self.store.add_many(facts)
        self.audit_log.append(
            actor=actor,
            type=EventType.INGEST,
            tenant_id=tenant_id,
            payload=report.model_dump(),
        )
        return report

    def ingest_edgar(
        self,
        ticker: str,
        tenant_id: str,
        *,
        max_years: int = 3,
        actor: str = "system:ingestion",
    ) -> IngestionReport:
        """Pull an issuer's real filed facts from SEC EDGAR into the fact store.

        The live sibling of :meth:`ingest_xbrl`: instead of a hand-shaped instance
        it fetches the company's machine-tagged ``us-gaap`` facts by ticker, scopes
        them to ``entity=<TICKER>``, and lands them with full provenance — so a draft
        analyzed under that issuer ties out against its as-filed numbers. Requires
        the service to have been constructed with an ``edgar`` client.
        """
        if self.edgar is None:
            raise RuntimeError(
                "ingest_edgar requires an EDGAR client. Construct AttestService with "
                "edgar=HttpEdgarClient() (see attest.ingestion.edgar)."
            )
        facts, report = EdgarConnector(self.registry, self.edgar).fetch(
            ticker, tenant_id, max_years=max_years
        )
        self.store.add_many(facts)
        self.audit_log.append(
            actor=actor,
            type=EventType.INGEST,
            tenant_id=tenant_id,
            payload=report.model_dump(),
        )
        return report

    def ensure_issuer_facts(self, tenant_id: str, ticker: str) -> list[str]:
        """Best-effort: make sure ``ticker``'s filed facts are loaded for tie-out.

        Called on the upload path so an uploaded transcript "just ties out" to the
        issuer's filings without a separate ingest step. Returns human-readable
        warnings (never raises): a no-op when EDGAR isn't configured or the issuer
        is already loaded, and an honest note rather than a failure when EDGAR is
        unreachable — the draft still analyzes, its figures simply stay untraced.
        """
        if self.edgar is None or not ticker.strip():
            return []
        entity = ticker.strip().upper()
        if any(f.entity == entity and f.is_filed for f in self.store.all(tenant_id)):
            return []  # already loaded — don't refetch
        try:
            report = self.ingest_edgar(ticker, tenant_id)
        except EdgarUnavailable as exc:
            return [
                f"Could not reach SEC EDGAR to load {entity}'s filings ({exc}); "
                "figures could not be tied out to a filed source."
            ]
        if report.ingested == 0:
            return [
                f"No EDGAR filings found for '{ticker}'. Check the ticker symbol; "
                "without a filed source these figures can't be traced."
            ]
        return [f"Loaded {report.ingested} filed facts for {entity} from SEC EDGAR."]

    # -- verification --------------------------------------------------------

    def verify_document(
        self, document: Document, *, use_llm: bool = False
    ) -> VerificationResult:
        """Verify a document.

        With ``use_llm=False`` (the default) the document's attached claims are
        verified by the deterministic core, unchanged from v1. With
        ``use_llm=True`` the configured :class:`EdgeService` re-proposes the
        claims from the prose and contributes non-blocking narrative findings —
        but the verdict on every figure is still rendered solely by the
        deterministic engine. The LLM never gets to say ``traced``.
        """
        if not use_llm:
            return self.engine.verify_document(document)

        edge = self._require_edge()
        document = edge.propose_claims(document)
        result = self.engine.verify_document(document)
        narrative = edge.narrate(document, self.store)
        if not narrative:
            return result
        return result.model_copy(
            update={"findings": result.findings + tuple(narrative)}
        )

    def verify_close_pack(self, documents: list[Document], *, use_llm: bool = False):
        if not use_llm:
            return self.engine.verify_close_pack(documents)

        edge = self._require_edge()
        documents = [edge.propose_claims(doc) for doc in documents]
        results = [self.verify_document(doc, use_llm=False) for doc in documents]
        results = [
            r.model_copy(
                update={"findings": r.findings + tuple(edge.narrate(doc, self.store))}
            )
            for r, doc in zip(results, documents)
        ]
        consistency = check_cross_document_consistency(documents)
        return results, consistency

    def _require_edge(self) -> EdgeService:
        if self.edge is None:
            raise RuntimeError(
                "use_llm=True requires an LLM edge. Construct AttestService with "
                "edge=EdgeService(...) (see attest.edge)."
            )
        return self.edge

    # -- upload & analyze (the real-document entry point) ---------------------

    def analyze_text(
        self,
        *,
        tenant_id: str,
        text: str,
        title: str = "Uploaded document",
        kind: DocumentKind = DocumentKind.OTHER,
        entity: str | None = None,
        period: str | None = None,
        document_id: str | None = None,
    ) -> tuple[Document, VerificationResult, str, str | None]:
        """Run a real, uploaded draft end to end.

        The probabilistic edge (:class:`ClaimExtractor`) proposes figure claims
        from the prose; the deterministic core then disposes exactly as it does for
        a hand-authored :class:`Document`. The verdict events the engine writes give
        the upload an audit footprint without inventing a new event type.

        Returns the constructed document, its verification result, and the resolved
        primary entity / period the analysis ran under.
        """
        entity = entity or self.default_entity(tenant_id)
        period = period or infer_period(title, text)
        # Each analyzed upload is a distinct document: its verdict/sign-off/override
        # events must be attributable to *this* draft, not conflated with the last
        # one of the same kind. Default to a unique, human-readable id rather than
        # the bare kind ("release"), which two different releases would have shared.
        doc_id = document_id or f"{kind.value}-{uuid.uuid4().hex[:12]}"
        extractor = ClaimExtractor(self.registry, self.store, self.aliases_for(tenant_id))
        claims = extractor.extract(
            text, document_id=doc_id, tenant_id=tenant_id, entity=entity, period=period
        )
        document = Document(
            id=doc_id, tenant_id=tenant_id, title=title, kind=kind, text=text, claims=claims
        )
        return document, self.engine.verify_document(document), entity, period

    def default_entity(self, tenant_id: str) -> str:
        """Pick the most plausible issuer entity from what's been ingested."""
        for fact in self.store.all(tenant_id):
            if ":" not in fact.entity:  # a parent issuer, not a segment
                return fact.entity
        return tenant_id.upper()

    def has_filed_facts(self, tenant_id: str, entity: str) -> bool:
        """True iff a filed source exists for ``entity`` (or one of its segments).

        The precondition for any figure of ``entity`` to trace: with no filed fact
        loaded there is nothing to tie out against, so every figure stays untraced.
        """
        prefix = f"{entity}:"
        return any(
            f.is_filed and (f.entity == entity or f.entity.startswith(prefix))
            for f in self.store.all(tenant_id)
        )

    # -- extraction vocabulary (tenant-configurable) -------------------------

    def aliases_for(self, tenant_id: str) -> AliasConfig:
        """The metric-attribution vocabulary in effect for a tenant."""
        return self._aliases_by_tenant.get(tenant_id, self._default_aliases)

    def configure_aliases(
        self,
        tenant_id: str,
        overrides: Mapping[str, Iterable[str]],
        *,
        replace: bool = False,
    ) -> AliasConfig:
        """Set a tenant's extraction synonyms, layered over its current config.

        ``replace`` overwrites the named metrics' lists; otherwise the phrases are
        unioned in. Unknown metric ids are rejected so a typo can't silently create
        a phantom metric the engine will never bind.
        """
        unknown = sorted(m for m in overrides if m not in self.registry)
        if unknown:
            raise ValueError(f"unknown metric id(s): {', '.join(unknown)}")
        config = self.aliases_for(tenant_id).extend(overrides, replace=replace)
        self._aliases_by_tenant[tenant_id] = config
        return config

    # -- human-in-the-loop accountability ------------------------------------

    def edit_draft(
        self,
        *,
        tenant_id: str,
        actor: str,
        document_id: str,
        before: str,
        after: str,
        claim_id: str | None = None,
        note: str = "",
    ) -> None:
        """Record that a drafter changed a figure or wording in a draft.

        Captures the ``before``/``after`` so the edit is a self-describing,
        tamper-evident link in the chain — not a silent mutation of the draft.
        """
        self.audit_log.append(
            actor=actor,
            type=EventType.EDIT,
            tenant_id=tenant_id,
            payload={
                "document_id": document_id,
                "claim_id": claim_id,
                "before": before,
                "after": after,
                "note": note,
            },
        )

    def override(
        self,
        *,
        tenant_id: str,
        actor: str,
        claim_id: str,
        justification: str,
        reason: str | None = None,
        metric: str | None = None,
        period: str | None = None,
        displayed_text: str | None = None,
    ) -> None:
        """Record that a human accepted a non-traced figure, with justification.

        ``reason`` is the structured disambiguation (see
        :class:`attest.eval.feedback.OverrideReason`): only an ``engine_wrong``
        override is a false-positive signal worth feeding back. ``metric`` /
        ``period`` / ``displayed_text`` are captured so the feedback exporter can
        build a candidate label without re-joining to the document.
        """
        payload: dict = {"claim_id": claim_id, "justification": justification}
        if reason is not None:
            payload["reason"] = str(reason)
        if metric is not None:
            payload["metric"] = metric
        if period is not None:
            payload["period"] = period
        if displayed_text is not None:
            payload["displayed_text"] = displayed_text
        self.audit_log.append(
            actor=actor,
            type=EventType.OVERRIDE,
            tenant_id=tenant_id,
            payload=payload,
        )

    def sign_off(
        self, *, tenant_id: str, actor: str, document_id: str, scope: str = "document"
    ) -> None:
        """Record an attestation over a document or section."""
        self.audit_log.append(
            actor=actor,
            type=EventType.SIGN_OFF,
            tenant_id=tenant_id,
            payload={"document_id": document_id, "scope": scope},
        )

    # -- prior-period auto-fetch ---------------------------------------------

    def fetch_prior_period(
        self,
        *,
        tenant_id: str,
        entity: str,
        period: str,
        cik: str | None = None,
        edgar_client: EdgarHttpClient | None = None,
        actor: str = "system:prior-period-fetch",
    ) -> tuple[list[IngestionReport], str | None, list[FetchedExhibit]]:
        """Auto-fetch and ingest the prior quarter's 8-K press release from EDGAR.

        Derives the prior fiscal quarter from ``period``, searches EDGAR for the
        matching earnings 8-K, fetches Exhibit 99.1, and ingests each exhibit via
        :class:`GuidanceConnector`. Extracted figures are added to the fact store
        with full provenance so later drafts can trace cross-period references.

        Returns ``(reports, prior_period_str, exhibits)``. When no filing is found
        ``reports`` and ``exhibits`` are empty and ``prior_period_str`` is still
        set so the caller can surface an informative "no prior period data found"
        message.

        ``edgar_client`` is injected in tests to avoid real network calls.
        """
        prev = _prior_period(period)
        fetcher = PriorPeriodFetcher(client=edgar_client)
        exhibits = fetcher.fetch_exhibits(ticker=entity, period=period, cik=cik)

        reports: list[IngestionReport] = []
        for exhibit in exhibits:
            facts, report = GuidanceConnector(self.registry).fetch(
                text=exhibit.text,
                tenant_id=tenant_id,
                entity=entity,
                accession=exhibit.accession,
                base_period=prev,
                as_of=exhibit.filing_date,
                label=exhibit.label,
            )
            self.store.add_many(facts)
            self.audit_log.append(
                actor=actor,
                type=EventType.INGEST,
                tenant_id=tenant_id,
                payload=report.model_dump(),
            )
            reports.append(report)

        return reports, prev, exhibits

    # -- audit ---------------------------------------------------------------

    def audit_export(self, tenant_id: str | None = None) -> list[dict]:
        return [e.model_dump() for e in self.audit_log.events(tenant_id)]

    def audit_verify(self) -> bool:
        return self.audit_log.verify()
