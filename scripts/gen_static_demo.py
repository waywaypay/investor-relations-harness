"""Generate faithful, precomputed analysis output for the static GitHub Pages demo.

GitHub Pages serves static files only, so the FastAPI backend can't run there. To
keep the demo *honest* (not a hand-written mock), this script drives the real
verification engine exactly as the ``POST /analyze`` endpoint does and serializes
the resulting ``AnalyzeResponse`` to JSON. The static page embeds that JSON and
replays it, so what a visitor sees is genuine engine output.

Two cases are produced for the bundled sample release:
  * ``unseeded`` — no facts ingested; every figure is honestly ``untraced`` while
    the deterministic rules still fire.
  * ``seeded``   — the Meridian demo filing ingested first, so figures tie out and
    the cloud-growth restatement conflict surfaces.

Run from the repo root with the package installed:  python scripts/gen_static_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from attest.api.schemas import AnalyzeResponse
from attest.domain.document import DocumentKind
from attest.ingestion.edgar_xbrl import load_fixture
from attest.service import AttestService

# The exact sample text the UI's "Use sample release" button inserts.
SAMPLE = (
    "Meridian Systems Reports First Quarter Fiscal 2026 Results\n\n"
    "Meridian Systems reported total revenue of $1.24 billion, up 18% year over "
    "year. The company delivered GAAP diluted EPS of $0.87 and non-GAAP diluted "
    "EPS of $1.12. Cloud segment revenue reached $612 million, up 31% from the "
    "prior-year period. Operating cash flow was $338 million. Meridian "
    "repurchased $250 million of common stock. For the second quarter, the "
    "company expects total revenue in the range of $1.31 to $1.34 billion."
)

TENANT = "meridian"
OUT = Path(__file__).resolve().parent.parent / "docs" / "demo-data.json"


def analyze(service: AttestService) -> dict:
    document, result, entity, period = service.analyze_text(
        tenant_id=TENANT,
        text=SAMPLE,
        title="Pasted document",
        kind=DocumentKind.RELEASE,
        entity="MRDN",
        period="FY2026-Q1",
    )
    payload = AnalyzeResponse(
        document_id=result.document_id,
        verdicts=list(result.verdicts),
        findings=list(result.findings),
        counts=result.counts,
        publishable=result.publishable,
        title=document.title,
        kind=document.kind.value,
        entity=entity,
        period=period,
        text=document.text,
        claims=list(document.claims),
        warnings=[],
    )
    return payload.model_dump(mode="json")


def main() -> None:
    unseeded = analyze(AttestService())

    seeded_service = AttestService()
    report = seeded_service.ingest_xbrl(load_fixture("meridian_q1_fy2026"), tenant_id=TENANT)
    seeded = analyze(seeded_service)

    bundle = {
        "sample_text": SAMPLE,
        "ingested": report.ingested,
        "unseeded": unseeded,
        "seeded": seeded,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(Path.cwd())}  ({report.ingested} facts ingested)")
    print(f"  unseeded: {unseeded['counts']}  publishable={unseeded['publishable']}")
    print(f"  seeded:   {seeded['counts']}  publishable={seeded['publishable']}")


if __name__ == "__main__":
    main()
