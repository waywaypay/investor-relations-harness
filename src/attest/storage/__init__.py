"""Durable storage backends behind the in-memory reference Protocols.

Postgres for the fact store and the hash-chained audit log; an optional Redis
read-through cache for the read-heavy verification path. Selected by env via
:func:`service_from_env` / :func:`build_storage` — a constructor swap, never an
API change. All third-party drivers are imported lazily and live in the optional
``[storage]`` extra.
"""

from attest.storage.factory import build_storage, edgar_client_from_env, service_from_env

__all__ = ["build_storage", "edgar_client_from_env", "service_from_env"]
