"""8-K Item 4.02 restatement harvester — real, automatable conflict labels.

SEC 8-K **Item 4.02** ("non-reliance on previously issued financial statements")
filings are the highest-value free label source for a disclosure-verification
corpus: each is an *adjudicated* case where a previously reported number was wrong
and a corrected value was disclosed. That gives two gold labels per restatement,
determined by the filing itself, not by us:

* a draft still citing the **original** value -> ``conflict`` (the restatement case)
* a draft citing the **restated** value -> ``traced``

Unlike synthetic perturbations, these reflect how disclosures *actually* go wrong.
They carry the adjudicating accession as provenance and are tagged
``label_source="edgar_restatement"``.

This module reads a normalized restatement record. A bundled fixture stands in
for the SEC source so the harvester is testable offline; a live adapter (an
``edgar`` connector, or the SEC MCP when wired) populates the same record shape —
the harvester and labels are unchanged either way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.money import Unit
from attest.domain.verdicts import Verdict

_FIXTURE_DIR = Path(__file__).parent / "restatement_fixtures"
LABEL_SOURCE = "edgar_restatement"

_UNIT = {
    "currency": Unit.CURRENCY,
    "percent": Unit.PERCENT,
    "basis_points": Unit.BASIS_POINTS,
    "shares": Unit.SHARES,
    "ratio": Unit.RATIO,
    "count": Unit.COUNT,
}


@dataclass(frozen=True)
class RestatementCase:
    id: str
    entity: str
    metric: str
    period: str
    text: str
    expected: Verdict
    accession: str
    rationale: str
    label_source: str = LABEL_SOURCE

    def as_golden_row(self) -> dict:
        return {
            "id": self.id,
            "entity": self.entity,
            "metric": self.metric,
            "period": self.period,
            "text": self.text,
            "expected": self.expected.value,
            "accession": self.accession,
            "rationale": self.rationale,
            "label_source": self.label_source,
        }


def load_restatement_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text())


def cases_from_restatement(rec: dict) -> list[RestatementCase]:
    """Derive the original->conflict and restated->traced cases from a record."""
    entity, metric, period = rec["entity"], rec["metric"], rec["period"]
    acc = rec["accession"]
    base = f"rst_{metric}_{period}".replace(":", "_")
    return [
        RestatementCase(
            id=f"{base}_original",
            entity=entity, metric=metric, period=period,
            text=rec["original"]["display"], expected=Verdict.CONFLICT,
            accession=acc,
            rationale="Original value superseded by an Item 4.02 restatement; "
            "a draft still citing it conflicts with the corrected filing.",
        ),
        RestatementCase(
            id=f"{base}_restated",
            entity=entity, metric=metric, period=period,
            text=rec["restated"]["display"], expected=Verdict.TRACED,
            accession=acc,
            rationale="The restated value as filed in the Item 4.02 correction.",
        ),
    ]


def facts_from_restatement(rec: dict) -> list[Fact]:
    """Build the two fact versions (original + restated) for the restated scope."""
    entity, metric, period = rec["entity"], rec["metric"], rec["period"]
    unit = _UNIT.get(rec.get("unit", "currency"), Unit.CURRENCY)
    quantum = Decimal(10) ** (-int(rec["decimals"])) if rec.get("decimals") is not None else Decimal(0)
    tenant = rec["tenant"]

    def mk(leg: dict, stype: SourceType) -> Fact:
        return Fact(
            id=f"{rec['accession']}:{metric}:{period}:{leg['as_of']}",
            tenant_id=tenant, entity=entity, metric=metric, period=period,
            value=Decimal(str(leg["value"])), unit=unit, quantum=quantum,
            source_type=stype, source_ref=leg["source_ref"], source_label=leg["label"],
            as_of=leg["as_of"], confidence=Confidence.HIGH,
        )

    return [
        mk(rec["original"], SourceType.FILING_LINE),
        mk(rec["restated"], SourceType.EDGAR_XBRL),
    ]


def build_store_from_restatement(rec: dict, service) -> None:
    """Load the original+restated facts into a service's store (for verification)."""
    service.store.add_many(facts_from_restatement(rec))
