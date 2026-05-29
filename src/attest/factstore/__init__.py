"""The fact store: a single normalized store of facts-with-provenance."""

from attest.factstore.repository import FactStore, InMemoryFactStore

__all__ = ["FactStore", "InMemoryFactStore"]
