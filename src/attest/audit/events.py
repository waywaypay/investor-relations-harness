"""Audit event types — every meaningful action is an immutable, attributable event."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    """The vocabulary of the audit log.

    The set is deliberately small and closed: every state change in Attest must
    map to one of these. "Export audit trail" is just a projection over them.
    """

    INGEST = "ingest"        # a source was ingested into the fact store
    BIND = "bind"            # a figure was bound to a source
    VERDICT = "verdict"      # the engine rendered a verdict on a figure
    EDIT = "edit"            # a drafter changed a figure or wording
    OVERRIDE = "override"    # a human accepted a value despite a non-traced verdict
    SIGN_OFF = "sign_off"    # a reviewer attested to a document/section


class AuditEvent(BaseModel):
    """One link in the hash chain.

    ``hash`` covers the full content *and* ``prev_hash``, so any retroactive edit
    to an earlier event breaks every subsequent link — tamper-evidence by
    construction. Hashing is computed in :mod:`attest.audit.log`.
    """

    model_config = ConfigDict(frozen=True)

    seq: int = Field(description="monotonic position in the chain, starting at 0")
    timestamp: str = Field(description="ISO-8601 UTC timestamp")
    actor: str = Field(description="user id or system component that caused the event")
    type: EventType
    tenant_id: str
    payload: dict = Field(default_factory=dict, description="event-specific, canonicalised")
    prev_hash: str = Field(description="hash of the previous event, or the genesis seed")
    hash: str = Field(default="", description="sha256 over the canonical event body")
