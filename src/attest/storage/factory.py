"""Storage composition — the one place that decides which backend is live.

The deterministic default is in-memory (zero dependencies, what the demo and the
test suite use). Setting ``ATTEST_DATABASE_URL`` swaps in Postgres for both the
fact store and the audit log; additionally setting ``ATTEST_REDIS_URL`` wraps the
fact store in the Redis read-through cache. This is the whole "constructor swap":
no engine, rule, or API code is aware of which path is active.
"""

from __future__ import annotations

import os

from attest.audit.log import AuditLog, InMemoryAuditLog
from attest.factstore.repository import FactStore, InMemoryFactStore
from attest.ingestion.edgar import EdgarClient
from attest.service import AttestService

_TRUTHY = {"1", "on", "true", "yes"}


def edgar_client_from_env(*, default_enabled: bool) -> EdgarClient | None:
    """The live EDGAR client to wire in, honouring ``ATTEST_EDGAR``.

    ``ATTEST_EDGAR`` overrides the default (``on``/``1``/``true`` enable, anything
    else disables); when it is unset, ``default_enabled`` decides. This is the one
    place the real transport is chosen, so a deployment can turn live SEC tie-out
    on or off without touching engine, API, or service code.
    """
    setting = os.environ.get("ATTEST_EDGAR")
    enabled = setting.strip().lower() in _TRUTHY if setting is not None else default_enabled
    if not enabled:
        return None
    from attest.ingestion.edgar import HttpEdgarClient

    return HttpEdgarClient()


def build_storage(
    *, database_url: str | None = None, redis_url: str | None = None
) -> tuple[FactStore, AuditLog]:
    """Return ``(fact_store, audit_log)`` for the given configuration.

    No URLs -> the in-memory reference stores. A database URL -> Postgres-backed
    stores (each on its own connection for clean transaction semantics). A Redis
    URL additionally fronts the fact store with the caching decorator.
    """
    if not database_url:
        return InMemoryFactStore(), InMemoryAuditLog()

    from attest.storage.postgres import PostgresAuditLog, PostgresFactStore

    store: FactStore = PostgresFactStore.connect(database_url)
    audit: AuditLog = PostgresAuditLog.connect(database_url)

    if redis_url:
        import redis

        from attest.storage.redis_cache import CachingFactStore

        store = CachingFactStore(store, redis.from_url(redis_url))

    return store, audit


def service_from_env() -> AttestService:
    """Build an :class:`AttestService` wired from the environment.

    Reads ``ATTEST_DATABASE_URL`` and ``ATTEST_REDIS_URL``; falls back to the
    in-memory stores when they are unset, so local/dev/test behaviour is unchanged.
    """
    store, audit = build_storage(
        database_url=os.environ.get("ATTEST_DATABASE_URL"),
        redis_url=os.environ.get("ATTEST_REDIS_URL"),
    )
    # `attest serve` is the interactive path where tie-out matters, so live EDGAR
    # is on unless ATTEST_EDGAR explicitly disables it.
    return AttestService(
        store=store, audit_log=audit, edgar=edgar_client_from_env(default_enabled=True)
    )
