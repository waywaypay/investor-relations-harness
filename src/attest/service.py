"""Application service — the orchestration seam the API and CLI share.

Holds the wiring (fact store, registry, audit log, engine) and exposes the
verbs that mutate state, each of which writes an attributable audit event:
ingest, verify, override, sign-off. Human-in-the-loop with captured
accountability: Attest never silently changes a disclosure.
"""

from __future__ import annotations

from attest.audit.events import EventType
from attest.audit.log import AuditLog, InMemoryAuditLog
from collections.abc import Iterable, Mapping

from attest.domain.document import Document, DocumentKind
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.domain.money import DEFAULT_POLICY, RoundingPolicy
from attest.extraction.claims import DEFAULT_ALIASES, AliasConfig, ClaimExtractor, infer_period
from attest.factstore.repository import FactStore, InMemoryFactStore
from attest.ingestion.base import IngestionReport
from attest.ingestion.edgar_xbrl import XBRLConnector
from attest.ingestion.guidance import GuidanceConnector
from attest.verification.engine import VerificationEngine, VerificationResult


class AttestService:
    """The composition root for the verification spine."""

    def __init__(
        self,
        store: FactStore | None = None,
        registry: MetricRegistry | None = None,
        audit_log: AuditLog | None = None,
        policy: RoundingPolicy = DEFAULT_POLICY,
        aliases: AliasConfig | None = None,
    ) -> None:
        self.store = store or InMemoryFactStore()
        self.registry = registry or DEFAULT_REGISTRY
        self.audit_log = audit_log or InMemoryAuditLog()
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

    # -- verification --------------------------------------------------------

    def verify_document(self, document: Document) -> VerificationResult:
        return self.engine.verify_document(document)

    def verify_close_pack(self, documents: list[Document]):
        return self.engine.verify_close_pack(documents)

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
        doc_id = document_id or kind.value
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

    def override(
        self, *, tenant_id: str, actor: str, claim_id: str, justification: str
    ) -> None:
        """Record that a human accepted a non-traced figure, with justification."""
        self.audit_log.append(
            actor=actor,
            type=EventType.OVERRIDE,
            tenant_id=tenant_id,
            payload={"claim_id": claim_id, "justification": justification},
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

    # -- audit ---------------------------------------------------------------

    def audit_export(self, tenant_id: str | None = None) -> list[dict]:
        return [e.model_dump() for e in self.audit_log.events(tenant_id)]

    def audit_verify(self) -> bool:
        return self.audit_log.verify()
