"""Fact store interface and a reference in-memory implementation.

The store is restatement-aware: it keeps *every* version of a fact (each with its
own ``as_of``) for a given ``(tenant, entity, metric, period)`` scope. Binding
resolves to the latest version, while older versions remain queryable so the
engine can recognise "the draft used a figure that was later restated" — the
cross-filing conflict a single-document chatbot structurally cannot catch.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from attest.domain.facts import Fact


@runtime_checkable
class FactStore(Protocol):
    """Read/write access to facts-with-provenance, scoped by tenant."""

    def add(self, fact: Fact) -> None: ...

    def add_many(self, facts: Iterable[Fact]) -> int: ...

    def versions(self, tenant_id: str, entity: str, metric: str, period: str) -> list[Fact]:
        """All versions for a scope, oldest ``as_of`` first."""
        ...

    def latest(self, tenant_id: str, entity: str, metric: str, period: str) -> Fact | None:
        """The most recently established (restated) version for a scope."""
        ...

    def all(self, tenant_id: str | None = None) -> list[Fact]: ...


class InMemoryFactStore:
    """A dict-backed reference store. The persistence boundary is intentionally
    thin so a Postgres-backed implementation can drop in behind the same Protocol.
    """

    def __init__(self) -> None:
        # scope_key -> list[Fact] (insertion order preserved, sorted on read)
        self._by_scope: dict[tuple[str, str, str, str], list[Fact]] = {}
        self._by_id: dict[str, Fact] = {}

    def add(self, fact: Fact) -> None:
        if fact.id in self._by_id:
            raise ValueError(f"duplicate fact id: {fact.id}")
        self._by_id[fact.id] = fact
        self._by_scope.setdefault(fact.scope_key(), []).append(fact)

    def add_many(self, facts: Iterable[Fact]) -> int:
        count = 0
        for fact in facts:
            self.add(fact)
            count += 1
        return count

    def versions(self, tenant_id: str, entity: str, metric: str, period: str) -> list[Fact]:
        key = (tenant_id, entity, metric, period)
        return sorted(self._by_scope.get(key, []), key=lambda f: f.as_of)

    def latest(self, tenant_id: str, entity: str, metric: str, period: str) -> Fact | None:
        versions = self.versions(tenant_id, entity, metric, period)
        return versions[-1] if versions else None

    def all(self, tenant_id: str | None = None) -> list[Fact]:
        facts = list(self._by_id.values())
        if tenant_id is not None:
            facts = [f for f in facts if f.tenant_id == tenant_id]
        return facts

    def get(self, fact_id: str) -> Fact | None:
        return self._by_id.get(fact_id)
