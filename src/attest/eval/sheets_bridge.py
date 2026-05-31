"""Bridge between the Google Sheets corpus workbook and the fact model.

The corpus is authored in Sheets (see the 'Attest Golden Corpus' workbook): a
``02_Facts`` tab whose columns mirror the :class:`Fact` fields. Exported as CSV,
that tab loads here into :class:`Fact` objects, which can then drive the
perturbation generator or seed a fact store.

This is the cheap-half automation: Layer 1 (real filed values) flows from the
sheet by CSV; the human-judgment layers (expected verdicts, must-not-flag
findings) stay in their own tabs and are never synthesised here.
"""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from typing import TextIO

from attest.domain.facts import Confidence, Fact, SourceType
from attest.domain.money import Unit
from attest.ingestion.edgar_xbrl import _quantum_from_decimals  # reuse precision mapping

_REQUIRED = {"entity", "metric", "period", "value_base", "unit", "source_type", "as_of"}


def _decimal_or_none(raw: str) -> int | None:
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def facts_from_csv(stream: TextIO, tenant_id: str) -> list[Fact]:
    """Parse a ``02_Facts`` CSV export into Fact objects.

    ``value_base`` is taken as the authoritative base-unit value (the sheet's
    SWITCH formula computes it). ``value_num``/``scale`` are human-entry columns
    and are ignored here to avoid double-applying the scale.
    """
    reader = csv.DictReader(stream)
    missing = _REQUIRED - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"02_Facts CSV missing required columns: {sorted(missing)}")

    facts: list[Fact] = []
    for i, row in enumerate(reader):
        metric = (row.get("metric") or "").strip()
        period = (row.get("period") or "").strip()
        if not metric or not period:
            continue  # skip blank/spacer rows
        try:
            value = Decimal(str(row["value_base"]).replace(",", "").strip())
        except (InvalidOperation, KeyError, AttributeError):
            continue  # unparseable value_base (e.g. a stray formula error) — skip, don't guess

        source_type = SourceType(row.get("source_type", "edgar_xbrl").strip())
        filing_id = (row.get("filing_id") or "sheet").strip()
        facts.append(
            Fact(
                id=f"{filing_id}:{metric}:{period}:{row.get('as_of', i)}",
                tenant_id=tenant_id,
                entity=(row.get("entity") or "").strip(),
                metric=metric,
                period=period,
                value=value,
                unit=Unit(row.get("unit", "currency").strip()),
                quantum=_quantum_from_decimals(_decimal_or_none(row.get("decimals", ""))),
                source_type=source_type,
                source_ref=(row.get("source_ref") or "none").strip(),
                source_label=(row.get("source_label") or "").strip(),
                as_of=(row.get("as_of") or "1970-01-01").strip(),
                confidence=Confidence((row.get("confidence") or "high").strip() or "high"),
            )
        )
    return facts


def facts_from_csv_path(path: str, tenant_id: str) -> list[Fact]:
    with open(path, newline="", encoding="utf-8") as fh:
        return facts_from_csv(fh, tenant_id)
