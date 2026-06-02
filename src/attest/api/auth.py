"""Tenant authorization for the API surface.

Multi-tenancy is only real if one tenant cannot read or mutate another's facts and
audit trail. The reference build ships **open** — :class:`AllowAllAuthorizer`, the
default — so the demo, the UI, and the test suite need no credentials. A deployment
that holds real issuer data injects a real authorizer instead, and every
``/tenants/{tenant_id}/...`` route is then gated by it.

The seam is a :class:`Protocol`, exactly like the fact store and the audit log, so
a JWT / SSO authorizer drops in the same way the bundled
:class:`StaticTokenAuthorizer` (bearer token -> the tenants it may touch) does.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

_WILDCARD = "*"


@runtime_checkable
class Authorizer(Protocol):
    """Decide whether a request bearing ``token`` may act on ``tenant_id``."""

    def authorize(self, tenant_id: str, token: str | None) -> bool: ...


class AllowAllAuthorizer:
    """Open access — the default for the reference build, demo, and tests."""

    def authorize(self, tenant_id: str, token: str | None) -> bool:
        return True


class StaticTokenAuthorizer:
    """A bearer token maps to the set of tenants it may touch (``"*"`` for all).

    Fails closed: an absent token, an unknown token, or a token without the
    requested tenant in its scope is denied.
    """

    def __init__(self, token_tenants: Mapping[str, set[str]]) -> None:
        self._tokens: dict[str, set[str]] = {t: set(s) for t, s in token_tenants.items()}

    def authorize(self, tenant_id: str, token: str | None) -> bool:
        if not token:
            return False
        scopes = self._tokens.get(token)
        if scopes is None:
            return False
        return _WILDCARD in scopes or tenant_id in scopes

    @classmethod
    def from_env(cls, raw: str) -> "StaticTokenAuthorizer":
        """Parse ``"tok1=meridian|acme,tok2=*"`` into an authorizer.

        Each comma-separated entry is ``token=tenant|tenant|...``; ``*`` grants all
        tenants. Malformed entries are skipped so a typo fails closed (no access)
        rather than crashing the server.
        """
        token_tenants: dict[str, set[str]] = {}
        for entry in raw.split(","):
            token, sep, scopes = entry.strip().partition("=")
            if not sep:
                continue
            tenants = {s.strip() for s in scopes.split("|") if s.strip()}
            if token.strip() and tenants:
                token_tenants[token.strip()] = tenants
        return cls(token_tenants)
