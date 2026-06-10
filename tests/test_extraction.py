"""Extraction tests — the replaceable edge in front of the deterministic spine.

Covers both halves: recovering prose from real file containers (txt/html/docx/pdf)
and proposing figure claims from that prose. The contract under test is *not* that
the heuristic is perfect (it is explicitly allowed to over-detect), but that it
never under-detects a stated figure and never asserts a tie-out itself.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from attest.demo import seeded_service
from attest.domain.document import DocumentKind
from attest.domain.facts import Confidence
from attest.extraction.claims import DEFAULT_ALIASES, AliasConfig, ClaimExtractor, infer_period
from attest.extraction.text import extract_text

RELEASE = (
    "Meridian Systems reported total revenue of $1.24 billion, up 18% year over year. "
    "The company delivered GAAP diluted EPS of $0.87 and non-GAAP diluted EPS of $1.12. "
    "Cloud segment revenue reached $612 million, up 31% from the prior-year period. "
    "Operating cash flow was $338 million. Meridian repurchased $250 million of common "
    "stock. For the second quarter, the company expects total revenue in the range of "
    "$1.31 to $1.34 billion."
)

# A spoken-earnings-call transcript: unlike the release above, the metric label
# routinely *trails* the figure ("$338 million of operating cash flow") rather than
# leading it. This is the phrasing that broke attribution before — each case below
# is a figure whose label does not sit immediately to its left.
TRANSCRIPT = (
    "Total revenue was $1.24 billion. Our Cloud segment was the standout, with "
    "revenue of $612 million, up 31% year over year. We generated $338 million of "
    "operating cash flow and returned $250 million to shareholders through buybacks. "
    "GAAP diluted EPS was $0.87 and non-GAAP diluted EPS was $1.12."
)


# -- text extraction ---------------------------------------------------------

def test_extract_plain_text():
    out = extract_text("draft.txt", b"Total revenue of $1.24 billion.")
    assert out.kind == "text"
    assert "$1.24 billion" in out.text
    assert out.warnings == []


def test_extract_html_strips_tags_and_unescapes():
    html = b"<html><body><h1>Q1</h1><p>Revenue was $1.24&nbsp;billion &amp; growing.</p>" \
           b"<script>var x=1;</script></body></html>"
    out = extract_text("release.html", html)
    assert out.kind == "html"
    assert "$1.24" in out.text and "billion" in out.text
    assert "<" not in out.text and "var x" not in out.text  # tags and script gone


def test_extract_docx_reads_paragraphs():
    buf = io.BytesIO()
    doc_xml = (
        '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
        "<w:p><w:r><w:t>Total revenue of $1.24 billion.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Cloud segment revenue of $612 million.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
    out = extract_text("remarks.docx", buf.getvalue())
    assert out.kind == "docx"
    assert "$1.24 billion" in out.text
    assert "$612 million" in out.text


def test_extract_pdf_best_effort_and_honest_warning():
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Length 60>>stream\n"
        b"BT /F1 12 Tf (Total revenue was $1.24 billion.) Tj ET\n"
        b"endstream endobj\n%%EOF"
    )
    out = extract_text("release.pdf", pdf)
    assert out.kind == "pdf"
    assert "$1.24 billion" in out.text

    blank = extract_text("scan.pdf", b"%PDF-1.4\n%%EOF")
    assert blank.text == ""
    assert blank.warnings  # image-only / unreadable PDFs are reported, not hidden


def test_unknown_extension_falls_back_with_warning():
    out = extract_text("notes.weird", b"Revenue $1.24 billion")
    assert "$1.24 billion" in out.text
    assert out.warnings


# -- period inference --------------------------------------------------------

def test_infer_period_from_title_phrasing():
    assert infer_period("Reports First Quarter Fiscal 2026 Results", "") == "FY2026-Q1"


def test_infer_period_prefers_explicit_token():
    assert infer_period("Some draft", "see FY2025-Q3 results") == "FY2025-Q3"


def test_infer_period_none_when_unknown():
    assert infer_period("a press release", "no period here") is None


# -- claim extraction (the model-free edge) ----------------------------------

def test_extractor_maps_real_release_prose_to_canonical_metrics():
    svc = seeded_service()
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        RELEASE, document_id="release", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    by_text = {c.displayed_text: c for c in claims}

    assert by_text["$1.24 billion"].metric == "total_revenue"
    assert by_text["$0.87"].metric == "gaap_diluted_eps"
    assert by_text["$1.12"].metric == "non_gaap_diluted_eps"  # not confused with cloud
    # The cloud figure is attributed to the segment entity, learned from ingested facts.
    cloud = by_text["$612 million"]
    assert cloud.metric == "cloud_revenue" and cloud.entity == "MRDN:Cloud"
    assert by_text["31%"].metric == "cloud_growth_yoy"
    # The guidance range is one span, attributed by its clause ("expects total
    # revenue ... for the second quarter") to revenue guidance for that quarter.
    guidance = by_text["$1.31 to $1.34 billion"]
    assert guidance.metric == "revenue_guidance" and guidance.period == "FY2026-Q2"


def test_extractor_overdetects_but_never_asserts_unknowns():
    svc = seeded_service()
    text = RELEASE + " International markets contributed 40% of bookings."
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        text, document_id="release", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    # A percent with no metric label and no change wording is still surfaced, but
    # as a low-confidence 'unidentified' claim — the engine will route/leave it
    # rather than assert it.
    unknown = next(c for c in claims if c.displayed_text == "40%")
    assert unknown.metric == "unidentified"
    assert unknown.detect_confidence == Confidence.LOW


def test_extractor_attributes_growth_percent_to_its_adjacent_base_figure():
    svc = seeded_service()
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        RELEASE, document_id="release", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    # "total revenue of $1.24 billion, up 18% year over year": no alias names the
    # 18%, but its base is the adjacent revenue figure — the registry's
    # revenue_growth_yoy over total_revenue — so the engine can recompute it.
    growth = next(c for c in claims if c.displayed_text == "18%")
    assert growth.metric == "revenue_growth_yoy"
    assert growth.entity == "MRDN" and growth.period == "FY2026-Q1"
    assert growth.detect_confidence == Confidence.HIGH


def test_growth_attribution_normalizes_a_decline_to_a_signed_value():
    svc = seeded_service()
    text = "Total revenue of $1.0 billion, down 5% year over year."
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        text, document_id="d", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    growth = next(c for c in claims if c.metric == "revenue_growth_yoy")
    # The recompute compares signed values, so "down 5%" must read as -5%.
    assert growth.displayed_text == "-5%"


@pytest.mark.parametrize(
    "text",
    [
        # A sequential basis is a different formula than YoY — never bind it.
        "Total revenue of $1.24 billion, up 4% sequentially.",
        # Constant currency is a different basis than the filed levels.
        "Total revenue of $1.24 billion, up 18% in constant currency.",
        # Forward-looking context is a forecast, not this period's reported growth.
        "Looking ahead, we expect total revenue of $1.31 billion, up 18% year over year.",
    ],
)
def test_growth_attribution_guards_leave_other_bases_unidentified(text):
    svc = seeded_service()
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        text, document_id="d", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    percents = [c for c in claims if c.displayed_text.endswith("%")]
    assert percents and all(c.metric == "unidentified" for c in percents)


def test_growth_attribution_never_double_binds_one_base():
    svc = seeded_service()
    text = "Total revenue of $1.24 billion, up 18% year over year and up 22% over two years."
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        text, document_id="d", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    by_text = {c.displayed_text: c for c in claims}
    assert by_text["18%"].metric == "revenue_growth_yoy"  # nearest percent wins the base
    assert by_text["22%"].metric == "unidentified"        # the base is already grown


def test_every_claim_carries_a_span_for_highlighting():
    svc = seeded_service()
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        RELEASE, document_id="release", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    for c in claims:
        assert c.span is not None
        s, e = c.span
        assert RELEASE[s:e].strip() == c.displayed_text


def test_extractor_attributes_trailing_labels_in_transcript_prose():
    # The edge must read a figure's label whether it leads or trails the number.
    svc = seeded_service()
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        TRANSCRIPT, document_id="call", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    by_text = {c.displayed_text: c for c in claims}

    # A trailing label ("$338 million of operating cash flow") attributes correctly...
    assert by_text["$338 million"].metric == "operating_cash_flow"
    # ...and its successor must not inherit it — "$250 million ... buybacks" is its own
    # metric, not a second cash-flow claim (the off-by-one that produced false conflicts).
    assert by_text["$250 million"].metric == "share_repurchases"
    # A bare "revenue" beside a segment figure resolves to the segment's own metric.
    cloud = by_text["$612 million"]
    assert cloud.metric == "cloud_revenue" and cloud.entity == "MRDN:Cloud"
    # Adjacent GAAP / non-GAAP EPS are kept distinct, not swapped or cross-bound.
    assert by_text["$0.87"].metric == "gaap_diluted_eps"
    assert by_text["$1.12"].metric == "non_gaap_diluted_eps"
    # The cloud-growth percent still reads as a YoY change (the restatement conflict).
    assert by_text["31%"].metric == "cloud_growth_yoy"


def test_transcript_pipeline_ties_out_every_filed_figure():
    # End to end: every filed figure in transcript phrasing ties out; the only flag is
    # the genuine 31% cloud-growth restatement — no false conflicts from mis-attribution.
    svc = seeded_service()
    _, result, entity, period = svc.analyze_text(
        tenant_id="meridian", text=TRANSCRIPT, title="Q1 FY2026 earnings call",
        kind=DocumentKind.SCRIPT, entity="MRDN", period="FY2026-Q1",
    )
    assert (entity, period) == ("MRDN", "FY2026-Q1")
    assert result.counts["traced"] == 6     # revenue, cloud rev, OCF, buybacks, both EPS
    assert result.counts["conflict"] == 1   # the 31% cloud-growth restatement
    assert result.counts["untraced"] == 0   # nothing mis-binds to the wrong metric


# -- tenant-configurable vocabulary ------------------------------------------

def test_alias_config_extend_unions_and_replaces():
    base = AliasConfig({"total_revenue": ("revenue",)})
    unioned = base.extend({"total_revenue": ["Topline", "revenue"]})
    assert unioned.for_metric("total_revenue") == ("revenue", "topline")  # deduped, lowercased
    replaced = base.extend({"total_revenue": ["net sales"]}, replace=True)
    assert replaced.for_metric("total_revenue") == ("net sales",)


def test_alias_config_leaves_other_metrics_untouched():
    extended = DEFAULT_ALIASES.extend({"total_revenue": ["topline"]})
    assert "topline" in extended.for_metric("total_revenue")
    assert extended.for_metric("gaap_diluted_eps") == DEFAULT_ALIASES.for_metric("gaap_diluted_eps")


def test_extractor_honours_tenant_house_style():
    svc = seeded_service()
    text = "Topline was $1.24 billion for the quarter."
    # Default vocabulary doesn't know "topline" — the figure is unattributed.
    default = ClaimExtractor(svc.registry, svc.store).extract(
        text, document_id="d", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    assert default[0].metric == "unidentified"
    # With the tenant's configured synonym, it attributes — and the engine traces it.
    aliases = DEFAULT_ALIASES.extend({"total_revenue": ["topline"]})
    tuned = ClaimExtractor(svc.registry, svc.store, aliases).extract(
        text, document_id="d", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    assert tuned[0].metric == "total_revenue"


def test_service_configure_aliases_is_per_tenant_and_validates():
    svc = seeded_service()
    svc.configure_aliases("meridian", {"total_revenue": ["topline"]})
    assert "topline" in svc.aliases_for("meridian").for_metric("total_revenue")
    # A different tenant is unaffected.
    assert "topline" not in svc.aliases_for("acme").for_metric("total_revenue")
    # Unknown metric ids are rejected, not silently accepted.
    with pytest.raises(ValueError):
        svc.configure_aliases("meridian", {"not_a_metric": ["x"]})


def test_full_pipeline_reproduces_the_restatement_conflict():
    svc = seeded_service()
    _, result, entity, period = svc.analyze_text(
        tenant_id="meridian", text=RELEASE, title="Q1 FY2026 release",
        kind=DocumentKind.RELEASE, entity="MRDN", period="FY2026-Q1",
    )
    assert entity == "MRDN" and period == "FY2026-Q1"
    assert result.counts["traced"] == 7  # incl. the 18% recomputed from filed levels
    assert result.counts["conflict"] == 1  # the 31% cloud-growth restatement
    assert result.counts["needs_review"] == 1  # guidance
    assert not result.publishable
    rules = {f.rule for f in result.findings}
    assert "forward_looking.safe_harbor_required" in rules
    assert "derived.recomputation_mismatch" in rules
