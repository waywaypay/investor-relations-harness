"""The hash-chained append-only audit log.

Design notes:

* **Append-only.** There is no update or delete. The only mutation is ``append``.
* **Hash-chained.** Each event's ``hash`` is ``sha256`` over a canonical encoding
  of its body together with the previous event's hash. Recomputing the chain and
  comparing is O(n) and detects any retroactive tampering.
* **Deterministic canonicalisation.** Payloads are serialised with sorted keys and
  no insignificant whitespace, so the same logical event always hashes the same.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable, Protocol, runtime_checkable

from attest.audit.events import AuditEvent, EventType

GENESIS_HASH = "0" * 64


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def compute_hash(
    *, seq: int, timestamp: str, actor: str, type: EventType, tenant_id: str,
    payload: dict, prev_hash: str,
) -> str:
    """Compute the chain hash for an event body. Pure function — used by writer and verifier."""
    body = {
        "seq": seq,
        "timestamp": timestamp,
        "actor": actor,
        "type": type.value,
        "tenant_id": tenant_id,
        "payload": payload,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(_canonical(body)).hexdigest()


class ChainIntegrityError(Exception):
    """Raised when the audit chain fails verification (i.e. evidence of tampering)."""


@runtime_checkable
class AuditLog(Protocol):
    def append(
        self, *, actor: str, type: EventType, tenant_id: str, payload: dict | None = None,
        timestamp: str | None = None,
    ) -> AuditEvent: ...

    def events(self, tenant_id: str | None = None) -> list[AuditEvent]: ...

    def verify(self) -> bool: ...


class InMemoryAuditLog:
    """A list-backed reference log. A production log persists each event durably
    (the architecture pins this to an event-sourced Postgres table) behind the
    same append/verify contract.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(
        self, *, actor: str, type: EventType, tenant_id: str, payload: dict | None = None,
        timestamp: str | None = None,
    ) -> AuditEvent:
        payload = payload or {}
        seq = len(self._events)
        prev_hash = self._events[-1].hash if self._events else GENESIS_HASH
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        digest = compute_hash(
            seq=seq, timestamp=ts, actor=actor, type=type, tenant_id=tenant_id,
            payload=payload, prev_hash=prev_hash,
        )
        event = AuditEvent(
            seq=seq, timestamp=ts, actor=actor, type=type, tenant_id=tenant_id,
            payload=payload, prev_hash=prev_hash, hash=digest,
        )
        self._events.append(event)
        return event

    def events(self, tenant_id: str | None = None) -> list[AuditEvent]:
        if tenant_id is None:
            return list(self._events)
        return [e for e in self._events if e.tenant_id == tenant_id]

    def verify(self) -> bool:
        """Recompute the chain; return True iff every link is intact.

        This is the function the "export audit trail" feature stands on: a buyer's
        auditor can independently re-run it against the exported events.
        """
        prev_hash = GENESIS_HASH
        for idx, event in enumerate(self._events):
            if event.seq != idx:
                raise ChainIntegrityError(f"seq gap at position {idx}: got {event.seq}")
            if event.prev_hash != prev_hash:
                raise ChainIntegrityError(f"prev_hash mismatch at seq {event.seq}")
            expected = compute_hash(
                seq=event.seq, timestamp=event.timestamp, actor=event.actor,
                type=event.type, tenant_id=event.tenant_id, payload=event.payload,
                prev_hash=event.prev_hash,
            )
            if expected != event.hash:
                raise ChainIntegrityError(f"hash mismatch at seq {event.seq}")
            prev_hash = event.hash
        return True

    @staticmethod
    def verify_export(events: Iterable[AuditEvent]) -> bool:
        """Verify an exported list of events without an instance (auditor-side check)."""
        log = InMemoryAuditLog()
        log._events = list(events)
        return log.verify()
