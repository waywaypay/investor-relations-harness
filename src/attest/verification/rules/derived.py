"""Derived-figure recomputation (stub — implemented in the green step)."""

from __future__ import annotations

from attest.domain.document import Document
from attest.domain.metrics import MetricRegistry
from attest.domain.verdicts import RuleFinding
from attest.factstore.repository import FactStore


def check_derived_consistency(
    document: Document, registry: MetricRegistry, store: FactStore
) -> list[RuleFinding]:
    return []
