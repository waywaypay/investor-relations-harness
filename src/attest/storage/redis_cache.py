"""A Redis read-through cache in front of any :class:`FactStore`.

Verification is read-heavy on a narrow hot set: for every figure claim the engine
resolves the versions (and the latest) for one ``(tenant, entity, metric, period)``
scope. During a close pack the same scopes are read again and again across the
release, the script, and the Q&A. This decorator caches those per-scope reads in
Redis and invalidates them on write — turning repeated tie-outs into O(1) lookups
without the durable store ever seeing the traffic.

It is a *decorator*, not a store: it wraps an inner ``FactStore`` (Postgres in
production, in-memory in tests) and satisfies the same Protocol, so it composes
transparently. ``redis`` is a lazy, optional ``[storage]`` dependency.
"""

from __future__ import annotations

import json
from typing import Iterable

from attest.domain.facts import Fact
from attest.factstore.repository import FactStore

_DEFAULT_TTL = 300  # seconds; bounds staleness even if an invalidation is ever missed


class CachingFactStore:
    """Wrap a ``FactStore`` with a per-scope Redis cache, invalidated on write."""

    def __init__(self, inner: FactStore, redis_client, *, ttl: int = _DEFAULT_TTL,
                 namespace: str = "attest:facts") -> None:
        self._inner = inner
        self._redis = redis_client
        self._ttl = ttl
        self._ns = namespace

    # -- key helpers ---------------------------------------------------------

    def _key(self, tenant_id: str, entity: str, metric: str, period: str) -> str:
        return f"{self._ns}:{tenant_id}\x1f{entity}\x1f{metric}\x1f{period}"

    def _load(self, key: str) -> list[Fact] | None:
        raw = self._redis.get(key)
        if raw is None:
            return None
        return [Fact.model_validate(d) for d in json.loads(raw)]

    def _store(self, key: str, facts: list[Fact]) -> None:
        payload = json.dumps([f.model_dump(mode="json") for f in facts])
        self._redis.set(key, payload, ex=self._ttl)

    # -- writes (write-through + invalidate) ---------------------------------

    def add(self, fact: Fact) -> None:
        self._inner.add(fact)
        self._redis.delete(self._key(*fact.scope_key()))

    def add_many(self, facts: Iterable[Fact]) -> int:
        facts = list(facts)
        count = self._inner.add_many(facts)
        scopes = {f.scope_key() for f in facts}
        if scopes:
            self._redis.delete(*[self._key(*s) for s in scopes])
        return count

    # -- reads (cached) ------------------------------------------------------

    def versions(self, tenant_id: str, entity: str, metric: str, period: str) -> list[Fact]:
        key = self._key(tenant_id, entity, metric, period)
        cached = self._load(key)
        if cached is not None:
            return cached
        versions = self._inner.versions(tenant_id, entity, metric, period)
        self._store(key, versions)
        return versions

    def latest(self, tenant_id: str, entity: str, metric: str, period: str) -> Fact | None:
        # Derived from the same cached list, so there is a single key to invalidate.
        versions = self.versions(tenant_id, entity, metric, period)
        return versions[-1] if versions else None

    def all(self, tenant_id: str | None = None) -> list[Fact]:
        # Full listing is not on the hot path; pass straight through.
        return self._inner.all(tenant_id)
