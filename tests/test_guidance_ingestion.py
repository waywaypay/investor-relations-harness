"""Forward-guidance ingestion from 8-K Exhibit 99.1 prose.

Guidance is the one figure class that never lands in XBRL — management states its
next-period revenue / EPS / margin outlook in the press-release *prose*. This
connector is the prose analog of :class:`XBRLConnector`: it turns the EX-99.1 text
(the bytes the SEC connector already fetches) into citable :class:`Fact` records,
each carrying the *exact sentence* the number came from so the spine can answer
"here is the number management gave, and here is the line it came from."

The contract under test mirrors the rest of the spine: the connector is
deterministic and model-free, only emits a fact when it can both attribute a
metric and parse a figure, and never guesses — anything else is reported as
skipped.
"""

from __future__ import annotations

from decimal import Decimal

from attest.demo import seeded_service
from attest.domain.document import Document, DocumentKind
from attest.domain.facts import SourceType
from attest.domain.money import Unit
from attest.domain.verdicts import FigureClaim, Verdict
from attest.factstore.repository import InMemoryFactStore
from attest.ingestion.guidance import GuidanceConnector, load_press_release

PRESS_RELEASE = load_press_release("meridian_q1_fy2026_8k_ex99_1")
ACCESSION = "0001047469-26-001200"


def _ingest(text: str = PRESS_RELEASE):
    return GuidanceConnector().fetch(
        text=text,
        tenant_id="meridian",
        entity="MRDN",
        accession=ACCESSION,
        base_period="FY2026-Q1",
        as_of="2026-04-28",
    )


def _by_scope(facts, metric, period):
    return next(f for f in facts if f.metric == metric and f.period == period)


# -- core extraction ---------------------------------------------------------

def test_extracts_next_quarter_revenue_guidance_as_range_midpoint():
    facts, _ = _ingest()
    rev = _by_scope(facts, "revenue_guidance", "FY2026-Q2")
    # "$1.31 to $1.34 billion" -> midpoint, normalized to base units.
    assert rev.value == Decimal("1325000000")
    assert rev.unit == Unit.CURRENCY


def test_extracts_eps_and_margin_guidance():
    facts, _ = _ingest()
    eps = _by_scope(facts, "eps_guidance", "FY2026-Q2")
    assert eps.value == Decimal("1.175")  # midpoint of $1.15 to $1.20
    assert eps.unit == Unit.CURRENCY

    margin = _by_scope(facts, "operating_margin_guidance", "FY2026-Q2")
    assert margin.value == Decimal("23")  # "approximately 23%"
    assert margin.unit == Unit.PERCENT


def test_extracts_full_year_revenue_guidance_to_its_own_period():
    facts, _ = _ingest()
    fy = _by_scope(facts, "revenue_guidance", "FY2026-FY")
    assert fy.value == Decimal("5475000000")  # midpoint of $5.40 to $5.55 billion


def test_every_guidance_fact_cites_the_exact_sentence():
    """The whole point: the number is bound to the line it came from."""
    facts, _ = _ingest()
    rev = _by_scope(facts, "revenue_guidance", "FY2026-Q2")
    assert "in the range of $1.31 to $1.34 billion" in rev.source_excerpt
    # and it points back to the filed exhibit, not 'none'.
    assert rev.source_ref == f"{ACCESSION}#exhibit-99.1"
    assert rev.source_excerpt.endswith(".")


def test_published_guidance_is_a_filed_traceable_source():
    """Guidance in a filed 8-K exhibit is citable and filed — distinct from the
    internal-planning MANAGEMENT_INPUT guidance the XBRL fixture carries."""
    facts, _ = _ingest()
    for f in facts:
        assert f.source_type == SourceType.FILING_LINE
        assert f.is_filed is True


def test_does_not_extract_reported_actuals_as_guidance():
    """A sentence that *reports* a figure ('revenue was $1,241.3 million') is not a
    guidance sentence — only forward-looking statements are pulled."""
    facts, _ = _ingest()
    values = {f.value for f in facts}
    assert Decimal("1241300000") not in values  # the reported actual
    assert Decimal("0.87") not in values        # reported GAAP EPS actual


def test_skips_are_reported_not_guessed():
    facts, report = _ingest()
    assert report.ingested == len(facts) == 4
    assert report.source == f"guidance_8k:{ACCESSION}"
    assert report.skipped >= 0


def test_dedupes_repeated_guidance_for_a_scope():
    text = (
        "For the second quarter, the company expects total revenue in the range of "
        "$1.31 to $1.34 billion. We reiterate that we expect second quarter revenue "
        "of $1.31 to $1.34 billion."
    )
    facts, _ = GuidanceConnector().fetch(
        text=text, tenant_id="t", entity="MRDN", accession="acc",
        base_period="FY2026-Q1", as_of="2026-04-28",
    )
    scopes = [(f.metric, f.period) for f in facts]
    assert scopes.count(("revenue_guidance", "FY2026-Q2")) == 1


def test_fact_ids_are_unique_and_storeable():
    facts, _ = _ingest()
    store = InMemoryFactStore()
    store.add_many(facts)  # would raise on a duplicate id
    assert len(store.all("meridian")) == len(facts)


# -- service + end-to-end ----------------------------------------------------

def test_service_ingest_guidance_writes_facts_and_audits():
    svc = seeded_service()
    before = len(svc.audit_export("meridian"))
    report = svc.ingest_guidance(
        text=PRESS_RELEASE, tenant_id="meridian", entity="MRDN",
        accession=ACCESSION, base_period="FY2026-Q1", as_of="2026-04-28",
    )
    assert report.ingested == 4
    assert svc.store.latest("meridian", "MRDN", "revenue_guidance", "FY2026-Q2") is not None
    assert len(svc.audit_export("meridian")) == before + 1
    assert svc.audit_verify()


def test_a_draft_that_reaffirms_guidance_traces_to_the_cited_line():
    """The buyer-pain demonstration: a later draft reaffirms prior guidance, and the
    engine ties it out to the exact published sentence — 'here is the number
    management gave, here is where it came from.'"""
    svc = seeded_service()
    svc.ingest_guidance(
        text=PRESS_RELEASE, tenant_id="meridian", entity="MRDN",
        accession=ACCESSION, base_period="FY2026-Q1", as_of="2026-04-28",
    )
    draft = Document(
        id="reaffirm", tenant_id="meridian", kind=DocumentKind.SCRIPT,
        title="Investor conference remarks",
        text="We continue to expect second-quarter revenue of approximately $1.33 billion.",
        claims=(
            FigureClaim(
                claim_id="d1", document_id="reaffirm", entity="MRDN",
                metric="revenue_guidance", period="FY2026-Q2",
                displayed_text="$1.33 billion",
            ),
        ),
    )
    result = svc.verify_document(draft)
    verdict = result.verdicts[0]
    assert verdict.verdict == Verdict.TRACED
    assert "$1.31 to $1.34 billion" in verdict.provenance.excerpt
