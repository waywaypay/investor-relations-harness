"""Application service — the orchestration seam the API and CLI share.

Holds the wiring (fact store, registry, audit log, engine) and exposes the
verbs that mutate state, each of which writes an attributable audit event:
ingest, verify, override, sign-off. Human-in-the-loop with captured
accountability: Attest never silently changes a disclosure.
"""

from __future__ import annotations

from attest.audit.events import EventType
from attest.audit.log import AuditLog, InMemoryAuditLog
from attest.domain.document import Document
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.domain.money import DEFAULT_POLICY, RoundingPolicy
from attest.factstore.repository import FactStore, InMemoryFactStore
from attest.ingestion.base import IngestionReport
from attest.ingestion.edgar_xbrl import XBRLConnector
from attest.verification.engine import VerificationEngine, VerificationResult


class AttestService:
    """The composition root for the verification spine."""

    def __init__(
        self,
        store: FactStore | None = None,
        registry: MetricRegistry | None = None,
        audit_log: AuditLog | None = None,
        policy: RoundingPolicy = DEFAULT_POLICY,
    ) -> None:
        self.store = store or InMemoryFactStore()
        self.registry = registry or DEFAULT_REGISTRY
        self.audit_log = audit_log or InMemoryAuditLog()
        self.engine = VerificationEngine(
            self.store, self.registry, policy=policy, audit_log=self.audit_log
        )

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

    # -- verification --------------------------------------------------------

    def verify_document(self, document: Document) -> VerificationResult:
        return self.engine.verify_document(document)

    def verify_close_pack(self, documents: list[Document]):
        return self.engine.verify_close_pack(documents)

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
