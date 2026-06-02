"""Connector contract.

Every external source — EDGAR/XBRL, the ERP close package, market data — is an
adapter that normalizes into the fact store and reports what it did. Connectors
are deliberately thin: they map source-native records onto :class:`Fact` and hand
them to the store. They contain no verification logic.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from attest.domain.facts import Fact


class IngestionReport(BaseModel):
    """Summary of an ingestion run, suitable for the audit log payload."""

    source: str
    tenant_id: str
    ingested: int
    skipped: int = 0
    skipped_tags: tuple[str, ...] = ()


class Connector(Protocol):
    """Pull from a source and yield normalized facts."""

    def fetch(
        self, *args: object, **kwargs: object
    ) -> tuple[list[Fact], IngestionReport]: ...
