"""Postgres-backed implementations of the storage Protocols.

These are drop-in replacements for :class:`InMemoryFactStore` and
:class:`InMemoryAuditLog` — same contracts, durable backing. The architecture
pinned this from the start: every store sits behind a ``Protocol``, so moving to
Postgres is a *constructor swap*, not an API change. Nothing in the verification
engine, the rules, or the API changes.

Two invariants are load-bearing and reproduced faithfully here:

* **Restatement order.** Facts are kept as versions per ``(tenant, entity,
  metric, period)`` scope, ordered by ``as_of`` then insertion order, so the
  engine still sees "the figure was later restated".
* **The audit hash chain.** Appends reuse the exact pure :func:`compute_hash`
  the in-memory log uses, and are serialised with a transaction-scoped advisory
  lock so the chain stays contiguous and tamper-evident under concurrency.

``psycopg`` is imported lazily and is an optional ``[storage]`` extra, so the
deterministic core still installs and runs with no database dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from attest.audit.events import AuditEvent, EventType
from attest.audit.log import GENESIS_HASH, InMemoryAuditLog, compute_hash
from attest.domain.facts import Fact

if TYPE_CHECKING:  # pragma: no cover - typing only
    import psycopg

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

# A fixed key so all appenders serialise on the same advisory lock. The audit
# chain is a single global sequence; concurrent appends must take turns.
_AUDIT_LOCK_KEY = 0x4154_5354  # "ATST"


def _connect(dsn: str):
    """Open an autocommit connection. Lazy import keeps psycopg optional."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - exercised via message only
        raise RuntimeError(
            "Postgres storage requires the 'psycopg' package. "
            "Install the storage extra: pip install '.[storage]'"
        ) from exc
    return psycopg.connect(dsn, autocommit=True)


def ensure_schema(conn) -> None:
    """Create the tables/indexes if they do not exist (idempotent bootstrap)."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA)


def _json(value):
    """Wrap a dict for JSONB insertion (psycopg needs the explicit adapter)."""
    from psycopg.types.json import Json

    return Json(value)


class PostgresFactStore:
    """A Postgres-backed :class:`~attest.factstore.repository.FactStore`."""

    def __init__(self, conn) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, dsn: str, *, bootstrap: bool = True) -> "PostgresFactStore":
        conn = _connect(dsn)
        if bootstrap:
            ensure_schema(conn)
        return cls(conn)

    def add(self, fact: Fact) -> None:
        import psycopg

        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO facts (id, tenant_id, entity, metric, period, as_of, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        fact.id, fact.tenant_id, fact.entity, fact.metric,
                        fact.period, fact.as_of, _json(fact.model_dump(mode="json")),
                    ),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError(f"duplicate fact id: {fact.id}") from exc

    def add_many(self, facts: Iterable[Fact]) -> int:
        count = 0
        for fact in facts:
            self.add(fact)
            count += 1
        return count

    def versions(self, tenant_id: str, entity: str, metric: str, period: str) -> list[Fact]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT data FROM facts "
                "WHERE tenant_id=%s AND entity=%s AND metric=%s AND period=%s "
                "ORDER BY as_of ASC, seq ASC",
                (tenant_id, entity, metric, period),
            )
            return [Fact.model_validate(row[0]) for row in cur.fetchall()]

    def latest(self, tenant_id: str, entity: str, metric: str, period: str) -> Fact | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT data FROM facts "
                "WHERE tenant_id=%s AND entity=%s AND metric=%s AND period=%s "
                "ORDER BY as_of DESC, seq DESC LIMIT 1",
                (tenant_id, entity, metric, period),
            )
            row = cur.fetchone()
            return Fact.model_validate(row[0]) if row else None

    def all(self, tenant_id: str | None = None) -> list[Fact]:
        with self._conn.cursor() as cur:
            if tenant_id is None:
                cur.execute("SELECT data FROM facts ORDER BY seq ASC")
            else:
                cur.execute(
                    "SELECT data FROM facts WHERE tenant_id=%s ORDER BY seq ASC", (tenant_id,)
                )
            return [Fact.model_validate(row[0]) for row in cur.fetchall()]

    def get(self, fact_id: str) -> Fact | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT data FROM facts WHERE id=%s", (fact_id,))
            row = cur.fetchone()
            return Fact.model_validate(row[0]) if row else None

    def close(self) -> None:
        self._conn.close()


class PostgresAuditLog:
    """A Postgres-backed :class:`~attest.audit.log.AuditLog`.

    Appends are serialised with a transaction-scoped advisory lock so the chain
    index stays contiguous and each link's ``prev_hash`` is the true predecessor,
    even with concurrent writers. Verification reuses the in-memory verifier over
    the persisted rows, so a buyer's auditor runs the *same* check on an export.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, dsn: str, *, bootstrap: bool = True) -> "PostgresAuditLog":
        conn = _connect(dsn)
        if bootstrap:
            ensure_schema(conn)
        return cls(conn)

    def append(
        self, *, actor: str, type: EventType, tenant_id: str, payload: dict | None = None,
        timestamp: str | None = None,
    ) -> AuditEvent:
        payload = payload or {}
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_AUDIT_LOCK_KEY,))
            cur.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1")
            last = cur.fetchone()
            seq = (last[0] + 1) if last else 0
            prev_hash = last[1] if last else GENESIS_HASH
            digest = compute_hash(
                seq=seq, timestamp=ts, actor=actor, type=type, tenant_id=tenant_id,
                payload=payload, prev_hash=prev_hash,
            )
            cur.execute(
                "INSERT INTO audit_events "
                "(seq, timestamp, actor, type, tenant_id, payload, prev_hash, hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (seq, ts, actor, type.value, tenant_id, _json(payload), prev_hash, digest),
            )
        return AuditEvent(
            seq=seq, timestamp=ts, actor=actor, type=type, tenant_id=tenant_id,
            payload=payload, prev_hash=prev_hash, hash=digest,
        )

    def events(self, tenant_id: str | None = None) -> list[AuditEvent]:
        with self._conn.cursor() as cur:
            if tenant_id is None:
                cur.execute(
                    "SELECT seq, timestamp, actor, type, tenant_id, payload, prev_hash, hash "
                    "FROM audit_events ORDER BY seq ASC"
                )
            else:
                cur.execute(
                    "SELECT seq, timestamp, actor, type, tenant_id, payload, prev_hash, hash "
                    "FROM audit_events WHERE tenant_id=%s ORDER BY seq ASC",
                    (tenant_id,),
                )
            return [_row_to_event(row) for row in cur.fetchall()]

    def verify(self) -> bool:
        """Recompute the full chain over the persisted rows (tenant-agnostic)."""
        return InMemoryAuditLog.verify_export(self.events())

    def close(self) -> None:
        self._conn.close()


def _row_to_event(row) -> AuditEvent:
    seq, ts, actor, type_, tenant_id, payload, prev_hash, digest = row
    return AuditEvent(
        seq=seq, timestamp=ts, actor=actor, type=EventType(type_), tenant_id=tenant_id,
        payload=payload, prev_hash=prev_hash, hash=digest,
    )
