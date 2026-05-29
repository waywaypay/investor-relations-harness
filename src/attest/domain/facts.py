"""The Fact record — provenance is a first-class data type.

A number without provenance is not displayable as "traced". Every value in the
system is a :class:`Fact`: a value-with-unit, scoped to an entity / metric /
period, that knows exactly where it came from, as of when, and with what
confidence. New capabilities are new *consumers* of this record, not new silos.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from attest.domain.money import Quantity, Unit


class SourceType(str, Enum):
    """Where a fact originated. Ordering encodes how "filed" a source is."""

    EDGAR_XBRL = "edgar_xbrl"        # machine-tagged from a filing — the gold standard
    FILING_LINE = "filing_line"      # a specific line on a filed statement
    INTERNAL_CLOSE = "internal_close"  # a cell in the company's pre-filing close package
    ANALYST_MODEL = "analyst_model"  # a parsed sell-side estimate
    MANAGEMENT_INPUT = "management_input"  # guidance / forward-looking — no filed source

    @property
    def is_filed(self) -> bool:
        """True when the source is a public filing a figure can be *traced* to."""
        return self in (SourceType.EDGAR_XBRL, SourceType.FILING_LINE)


class Confidence(str, Enum):
    """Confidence in the binding of a value to its source.

    The probabilistic edge attaches this; the deterministic core routes anything
    below ``HIGH`` to a human rather than asserting it as a fact.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Provenance(BaseModel):
    """The pointer back to a source, rich enough to render and to prove.

    ``ref`` is a stable, machine-resolvable locator: an EDGAR accession + XBRL
    tag, a ``doc#cell`` address in a close package, or ``"none"`` for guidance.
    """

    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    ref: str = Field(description="accession+tag, doc+cell, or 'none' for guidance")
    label: str = Field(default="", description="human-readable citation, e.g. '10-Q p.4'")
    excerpt: str = Field(default="", description="short source snippet for the UI")

    @property
    def is_filed(self) -> bool:
        return self.source_type.is_filed and self.ref != "none"


class Fact(BaseModel):
    """A single normalized fact-with-provenance. The spine record.

    Facts are immutable; a restatement is a *new* fact for the same
    ``(tenant, entity, metric, period)`` with a later ``as_of``. The store keeps
    every version so cross-filing restatement conflicts can be detected.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    entity: str = Field(description="issuer or segment, e.g. 'MRDN' or 'MRDN:Cloud'")
    metric: str = Field(description="canonical metric id, e.g. 'total_revenue'")
    period: str = Field(description="fiscal period, e.g. 'FY2026-Q1'")

    value: Decimal
    unit: Unit
    quantum: Decimal = Field(
        default=Decimal(0),
        description="precision of the source value; 0 means exact/unrounded",
    )

    source_type: SourceType
    source_ref: str = Field(default="none")
    source_label: str = Field(default="")
    source_excerpt: str = Field(default="")

    as_of: str = Field(description="ISO date the value was established/restated")
    confidence: Confidence = Confidence.HIGH

    def quantity(self) -> Quantity:
        """View the fact's value as a comparable :class:`Quantity`."""
        return Quantity(value=self.value, unit=self.unit, quantum=self.quantum)

    def provenance(self) -> Provenance:
        return Provenance(
            source_type=self.source_type,
            ref=self.source_ref,
            label=self.source_label,
            excerpt=self.source_excerpt,
        )

    @property
    def is_filed(self) -> bool:
        return self.source_type.is_filed and self.source_ref != "none"

    def scope_key(self) -> tuple[str, str, str, str]:
        """The identity a fact resolves on, ignoring ``as_of`` (restatements)."""
        return (self.tenant_id, self.entity, self.metric, self.period)
