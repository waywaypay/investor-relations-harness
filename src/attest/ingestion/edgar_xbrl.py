"""EDGAR / XBRL ingestion adapter.

XBRL is a gift: filings arrive machine-tagged, so facts ingest *structured*
rather than scraped. This adapter consumes a simplified XBRL instance (the shape
SEC's company-facts API exposes, pared down) and maps each tagged value onto a
canonical metric via the :class:`MetricRegistry`.

The ``decimals`` attribute on an XBRL fact encodes its precision; we translate it
into the :class:`Quantity` quantum so the verification engine knows exactly how
precisely the source was reported (``decimals=-5`` -> precise to 100,000).

This adapter is one connector, not the core. The same contract is how the ERP /
close-package connector will bind internal pre-filing actuals later.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.metrics import DEFAULT_REGISTRY, MetricRegistry
from attest.domain.money import Unit
from attest.ingestion.base import IngestionReport

_FIXTURE_DIR = Path(__file__).parent / "fixtures"

_UNIT_MAP = {
    "USD": Unit.CURRENCY,
    "USD/shares": Unit.CURRENCY,
    "USD/share": Unit.CURRENCY,
    "pure": Unit.RATIO,
    "percent": Unit.PERCENT,
    "shares": Unit.SHARES,
}

_SOURCE_MAP = {
    "edgar_xbrl": SourceType.EDGAR_XBRL,
    "filing_line": SourceType.FILING_LINE,
    "management_input": SourceType.MANAGEMENT_INPUT,
    "internal_close": SourceType.INTERNAL_CLOSE,
}


def _quantum_from_decimals(decimals: int | None) -> Decimal:
    """XBRL ``decimals`` -> quantum. ``decimals=-5`` means precise to 1e5."""
    if decimals is None:
        return Decimal(0)
    return Decimal(10) ** (-decimals)


class XBRLConnector:
    """Maps a simplified XBRL instance into the fact store."""

    def __init__(self, registry: MetricRegistry | None = None) -> None:
        self.registry = registry or DEFAULT_REGISTRY

    def fetch(self, instance: dict, tenant_id: str) -> tuple[list[Fact], IngestionReport]:
        """Parse an XBRL instance dict into facts.

        Facts whose tag carries an explicit ``metric`` are mapped directly; others
        resolve through the registry's tag map. Unmapped tags are *skipped, not
        guessed* — the count is reported so coverage is observable.
        """
        accession = instance.get("accession", "unknown")
        entity_default = instance.get("entity", "unknown")
        facts: list[Fact] = []
        skipped_tags: list[str] = []

        for idx, raw in enumerate(instance.get("facts", [])):
            tag = raw.get("tag")
            metric_id = raw.get("metric")
            if metric_id is None and tag is not None:
                spec = self.registry.by_xbrl_tag(tag)
                metric_id = spec.id if spec else None
            if metric_id is None or metric_id not in self.registry:
                skipped_tags.append(tag or "<no-tag>")
                continue

            spec = self.registry.get(metric_id)
            unit = _UNIT_MAP.get(raw.get("unit", ""), spec.unit if spec else Unit.CURRENCY)
            # The metric's declared unit wins for normalization dimension.
            if spec is not None:
                unit = spec.unit
            source_type = _SOURCE_MAP.get(raw.get("source_type", "edgar_xbrl"))

            fact = Fact(
                id=f"{accession}:{metric_id}:{raw.get('period')}:{raw.get('as_of', idx)}",
                tenant_id=tenant_id,
                entity=raw.get("entity", entity_default),
                metric=metric_id,
                period=raw["period"],
                value=Decimal(str(raw["value"])),
                unit=unit,
                quantum=_quantum_from_decimals(raw.get("decimals")),
                source_type=source_type,
                source_ref=raw.get("source_ref", f"{accession}#{tag}" if tag else "none"),
                source_label=raw.get("label", ""),
                source_excerpt=raw.get("excerpt", ""),
                as_of=raw.get("as_of", "1970-01-01"),
                confidence=Confidence(raw.get("confidence", "high")),
            )
            facts.append(fact)

        report = IngestionReport(
            source=f"edgar_xbrl:{accession}",
            tenant_id=tenant_id,
            ingested=len(facts),
            skipped=len(skipped_tags),
            skipped_tags=tuple(skipped_tags),
        )
        return facts, report


def load_fixture(name: str) -> dict:
    """Load a bundled XBRL instance fixture by name (without extension)."""
    path = _FIXTURE_DIR / f"{name}.json"
    return json.loads(path.read_text())
