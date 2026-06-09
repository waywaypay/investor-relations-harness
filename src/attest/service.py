"""Application service — the orchestration seam the API and CLI share.

Holds the wiring (fact store, registry, audit log, engine) and exposes the
verbs that mutate state, each of which writes an attributable audit event:
ingest, verify, edit, override, sign-off. Human-in-the-loop with captured
accountability: Attest never silently changes a disclosure.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from attest.audit.events import EventType
from attest.audit.log import AuditLog, InMemoryAuditLog
from collections.abc import Iterable, Mapping

from attest.domain.document import Document, DocumentKind
from attest.domain.facts import UNDATED_AS_OF
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.domain.money import DEFAULT_POLICY, RoundingPolicy
from attest.extraction.claims import (
    DEFAULT_ALIASES,
    AliasConfig,
    ClaimExtractor,
    infer_entity_ticker,
    infer_period,
)
from attest.edge.service import EdgeService
from attest.factstore.repository import FactStore, InMemoryFactStore
from attest.ingestion.base import IngestionReport
from attest.ingestion.edgar import EdgarClient, EdgarConnector, EdgarUnavailable
from attest.ingestion.edgar_xbrl import XBRLConnector
from attest.ingestion.guidance import DisclosureConnector, GuidanceConnector
from attest.ingestion.exa import ExaCandidate, ExaDocument, ExaHttpClient, HistoricalFetcher
from attest.ingestion.prior_period import EdgarHttpClient, FetchedExhibit, PriorPeriodFetcher, prior_period as _prior_period
from attest.verification.engine import VerificationEngine, VerificationResult
from attest.verification.rules import check_cross_document_consistency


# An Exa historical doc_type maps to the document kind it analyzes under, so the
# right wording rules run (a transcript is a script, a release is a release).
_HISTORICAL_DOC_KIND: dict[str, DocumentKind] = {
    "release": DocumentKind.RELEASE,
    "transcript": DocumentKind.SCRIPT,
}

# What a bare ticker symbol looks like ("PANW", "BRK.B") — vs. a company name.
_TICKER_SHAPE = re.compile(r"[A-Za-z]{1,5}(?:[.\-][A-Za-z0-9]{1,2})?")


@dataclass
class HistoricalDocResult:
    """One fetched historical document, ingested *and* analyzed.

    ``report`` summarizes filing it as a prior-disclosure reference fact; ``analysis``
    is the verdict the spine renders for the document's own figures against the
    issuer's filed (EDGAR/XBRL) sources, so each highlighted figure links to the SEC
    source instead of reading as an unattributed reference number. ``title`` is the
    display title the run resolved (the reviewed candidate's, falling back to the
    page's own) and ``entity`` the issuer ticker the run resolved — both echoed to
    the caller so the rendered document matches what the user reviewed.
    """

    document: ExaDocument
    report: IngestionReport
    period: str | None
    analyzed: Document
    analysis: VerificationResult
    title: str = ""
    entity: str = ""


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
        as_of: str = UNDATED_AS_OF,
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
        as_of: str = UNDATED_AS_OF,
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

        When no ``period`` is supplied (the common case — a user files a past release
        or transcript without typing one), it is inferred from the document's own
        words, exactly as the draft (:meth:`analyze_text`) and historical-fetch paths
        do. Without this every figure in a period-less upload is anchored to the empty
        period and silently dropped, so the disclosure ingests nothing.
        """
        period = period or infer_period(label or "", text)
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

    def resolve_entity_ticker(self, entity: str | None, *texts: str) -> str | None:
        """Resolve what a user typed — a ticker *or a company name* — to the ticker.

        The UI invites either form ("PANW or Palo Alto Networks"), but every
        downstream step (EDGAR fact loading, fact scoping, auto-titles) needs the
        symbol: a company name resolves no CIK, loads no filed facts, and leaves
        every figure untraced. Resolution order, all best-effort:

        1. SEC's company-tickers map via the EDGAR client — an exact symbol passes
           through unchanged; a company name matches the registrant title.
        2. The documents themselves: earnings materials identify the issuer as
           "Company (NASDAQ: TICKER)", which works even with EDGAR disabled.
        3. Honest fallback: a ticker-shaped string upper-cased, anything else as
           typed — never a guess.
        """
        typed = (entity or "").strip()
        resolver = getattr(self.edgar, "resolve_ticker", None) if self.edgar else None
        if typed and resolver is not None:
            try:
                resolved = resolver(typed)
            except EdgarUnavailable:
                resolved = None  # resolution is best-effort; an outage never blocks
            if resolved:
                return resolved
        detected = infer_entity_ticker(*texts)
        if detected:
            return detected
        if not typed:
            return None
        return typed.upper() if _TICKER_SHAPE.fullmatch(typed) else typed

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

    def search_historical(
        self,
        *,
        entity: str,
        doc_types: tuple[str, ...] = ("release", "transcript"),
        quarters: int = 4,
        exa_client: ExaHttpClient | None = None,
    ) -> list[ExaCandidate]:
        """Search the web (via Exa) for an issuer's historical earnings documents.

        The discovery half of the SEC-independent path: returns auto-titled
        candidates — one release (and one transcript) per fiscal period, newest
        first — for the most recent ``quarters`` periods, for the caller to review
        before ingesting any. Nothing is stored here — this is a pure lookup.

        ``exa_client`` is injected in tests to avoid real network calls. Raises
        :class:`~attest.ingestion.exa.ExaUnavailable` (or its ``ExaNotConfigured``
        subclass) when Exa can't be reached, so the API can distinguish that from
        an empty result.
        """
        fetcher = HistoricalFetcher(client=exa_client)
        # Search the web with what the user typed (a company name finds its own IR
        # pages better than a bare symbol), but label the candidates with the
        # resolved ticker so every auto-title reads "PANW …", not a shouted name.
        label_entity = self.resolve_entity_ticker(entity) or entity
        return fetcher.search(
            entity=entity,
            doc_types=list(doc_types),
            quarters=quarters,
            label_entity=label_entity,
        )

    def ingest_historical(
        self,
        *,
        tenant_id: str,
        entity: str,
        items: list[dict],
        exa_client: ExaHttpClient | None = None,
        actor: str = "system:historical-fetch",
    ) -> list[HistoricalDocResult]:
        """Fetch the selected web documents, tie their figures to the SEC source,
        and file them as prior disclosures.

        ``items`` are the reviewed candidates to load — each a mapping with a
        ``url`` and optional ``title`` / ``period`` / ``doc_type``. Their full text
        is fetched via Exa, then handled in two passes so the result is both
        verifiable and a reference:

        * **Analyze** each document against the issuer's *filed* sources — the
          issuer's EDGAR/XBRL facts are loaded first (best-effort) so a historical
          release's revenue/EPS figures tie out to the SEC database and the UI can
          show the linking, instead of every number reading as an unattributed
          reference figure. All analysis happens before any disclosure is filed, so
          a figure ties out to the *filed* source, never to another fetched doc.
        * **File** each as a cited ``PRIOR_DISCLOSURE`` reference fact (web source,
          not a filing) via the same :class:`DisclosureConnector` a manually
          uploaded transcript takes — the reference corpus later drafts check
          against. Like every ingestion, each document writes an audit event.

        Returns a :class:`HistoricalDocResult` per successfully fetched document, so
        the caller can render the document itself (with per-figure verdicts) and not
        just a figure count. A candidate whose text can't be fetched is silently
        dropped, mirroring the connectors' never-guess rule.
        """
        fetcher = HistoricalFetcher(client=exa_client)
        title_by_url = {it["url"]: it.get("title") for it in items if it.get("url")}
        period_by_url = {it["url"]: it.get("period") for it in items if it.get("url")}
        kind_by_url = {it["url"]: it.get("doc_type") for it in items if it.get("url")}
        docs = fetcher.fetch_contents(urls=list(title_by_url))

        # Resolve the typed entity (ticker or company name) to the issuer's ticker,
        # falling back to the fetched documents' own "(NASDAQ: XXXX)" self-
        # identification — otherwise a company-name entity resolves no CIK, loads
        # no filed facts, and every figure below lands untraced.
        doc_texts = [s for d in docs for s in (d.title, d.text)]
        entity = self.resolve_entity_ticker(entity, *doc_texts) or entity

        # Load the issuer's filed facts up front (best-effort) so the documents'
        # figures can trace to the SEC source. Honest no-op when EDGAR is disabled.
        self.ensure_issuer_facts(tenant_id, entity)

        # Pass 1 — analyze every fetched document against the filed sources, before
        # any of them is filed as a disclosure, so a figure ties out to the SEC
        # source rather than to a sibling fetched document of the same period.
        analyzed: list[tuple[ExaDocument, str | None, Document, VerificationResult]] = []
        for doc in docs:
            # Anchor unperiodized figures to the document's own reporting period:
            # the period the reviewer saw (read from the candidate), else inferred
            # from the full text now in hand — never a calendar guess off the date.
            period = period_by_url.get(doc.url) or infer_period(doc.title, doc.text)
            kind = _HISTORICAL_DOC_KIND.get(kind_by_url.get(doc.url) or "", DocumentKind.OTHER)
            document, result, _, _ = self.analyze_text(
                tenant_id=tenant_id,
                text=doc.text,
                title=title_by_url.get(doc.url) or doc.title,
                kind=kind,
                entity=entity,
                period=period,
            )
            analyzed.append((doc, period, document, result))

        # Pass 2 — file each as a prior-disclosure reference for later drafts.
        results: list[HistoricalDocResult] = []
        for doc, period, document, result in analyzed:
            facts, report = DisclosureConnector(
                self.registry, self.store, self.aliases_for(tenant_id)
            ).fetch(
                text=doc.text,
                tenant_id=tenant_id,
                entity=entity,
                period=period,
                as_of=doc.published_date or UNDATED_AS_OF,
                source_ref=doc.url,
                label=title_by_url.get(doc.url) or doc.title,
            )
            self.store.add_many(facts)
            self.audit_log.append(
                actor=actor,
                type=EventType.INGEST,
                tenant_id=tenant_id,
                payload=report.model_dump(),
            )
            results.append(
                HistoricalDocResult(
                    document=doc,
                    report=report,
                    period=period,
                    analyzed=document,
                    analysis=result,
                    title=document.title,
                    entity=entity,
                )
            )

        return results

    # -- audit ---------------------------------------------------------------

    def audit_export(self, tenant_id: str | None = None) -> list[dict]:
        return [e.model_dump() for e in self.audit_log.events(tenant_id)]

    def audit_verify(self) -> bool:
        return self.audit_log.verify()
