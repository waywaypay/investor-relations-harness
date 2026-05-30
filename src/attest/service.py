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
from attest.edge.service import EdgeService
from attest.factstore.repository import FactStore, InMemoryFactStore
from attest.ingestion.base import IngestionReport
from attest.ingestion.edgar_xbrl import XBRLConnector
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
        edge: EdgeService | None = None,
    ) -> None:
        self.store = store or InMemoryFactStore()
        self.registry = registry or DEFAULT_REGISTRY
        self.audit_log = audit_log or InMemoryAuditLog()
        # The probabilistic edge is optional. When absent, the service behaves
        # exactly as the deterministic-only v1 — the core never depends on it.
        self.edge = edge
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

    # -- human-in-the-loop accountability ------------------------------------

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

    # -- audit ---------------------------------------------------------------

    def audit_export(self, tenant_id: str | None = None) -> list[dict]:
        return [e.model_dump() for e in self.audit_log.events(tenant_id)]

    def audit_verify(self) -> bool:
        return self.audit_log.verify()
