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
    # The guidance range is one span, routed to the next period as guidance.
    guidance = by_text["$1.31 to $1.34 billion"]
    assert guidance.metric == "q2_revenue_guidance" and guidance.period == "FY2026-Q2"


def test_extractor_overdetects_but_never_asserts_unknowns():
    svc = seeded_service()
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        RELEASE, document_id="release", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    # The unattributed 18% growth figure is still surfaced, but as a low-confidence
    # 'unidentified' claim — the engine will route/leave it rather than assert it.
    unknown = next(c for c in claims if c.displayed_text == "18%")
    assert unknown.metric == "unidentified"
    assert unknown.detect_confidence == Confidence.LOW


def test_every_claim_carries_a_span_for_highlighting():
    svc = seeded_service()
    claims = ClaimExtractor(svc.registry, svc.store).extract(
        RELEASE, document_id="release", tenant_id="meridian", entity="MRDN", period="FY2026-Q1"
    )
    for c in claims:
        assert c.span is not None
        s, e = c.span
        assert RELEASE[s:e].strip() == c.displayed_text


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
    assert result.counts["traced"] == 6
    assert result.counts["conflict"] == 1  # the 31% cloud-growth restatement
    assert result.counts["needs_review"] == 1  # guidance
    assert not result.publishable
    rules = {f.rule for f in result.findings}
    assert "forward_looking.safe_harbor_required" in rules
    assert "derived.recomputation_mismatch" in rules


# -- spoken / transcript figure dialect --------------------------------------

# The same quarter as RELEASE, phrased the way an earnings-call transcript reads:
# no "$" symbols and "percent" spelled out. Pasted prose and Word exports land here
# too. Under-detection is the failure mode, so these must still be located.
TRANSCRIPT = (
    "Thanks everyone for joining the call. Total revenue was 1.24 billion dollars, "
    "up 18 percent year over year. Cloud segment revenue reached 612 million dollars, "
    "up 31 percent from the prior-year period. Operating cash flow was 338 million dollars. "
    "We repurchased 250 million dollars of common stock. For the second quarter, we expect "
    "total revenue in the range of 1.31 to 1.34 billion."
)


def test_candidate_detection_handles_spoken_figures():
    from attest.domain.money import Unit, parse_quantity
    from attest.verification.candidates import detect_candidates

    texts = {c.text for c in detect_candidates(TRANSCRIPT)}
    # Symbol-free currency (scale word or "dollars" anchors it) and spelled percent.
    assert "1.24 billion" in texts
    assert "612 million" in texts
    assert "31 percent" in texts
    # The values normalize to the same quantities as their "$"/"%" equivalents.
    assert parse_quantity("1.24 billion") == parse_quantity("$1.24 billion")
    assert parse_quantity("31 percent").unit is Unit.PERCENT
    assert parse_quantity("31 percent") == parse_quantity("31%")
    # "87 cents" is $0.87, not $87 — the trailing money word changes the scale.
    assert parse_quantity("87 cents") == parse_quantity("$0.87")
    # A bare year is not money: no scale word, no "$", no "dollars".
    assert not detect_candidates("In fiscal 2026 we expanded the platform.")


def test_full_pipeline_traces_a_spoken_transcript():
    svc = seeded_service()
    _, result, entity, period = svc.analyze_text(
        tenant_id="meridian", text=TRANSCRIPT, title="Q1 FY2026 earnings call",
        kind=DocumentKind.SCRIPT, entity="MRDN", period="FY2026-Q1",
    )
    assert entity == "MRDN" and period == "FY2026-Q1"
    # The regression: spoken figures used to yield zero candidates and trace nothing.
    # Now the symbol-free currency figures bind to their filed sources.
    assert result.counts["traced"] == 4  # revenue, cloud revenue, cash flow, repurchase
    assert result.counts["conflict"] == 1  # the 31% cloud-growth restatement still trips
    assert result.counts["needs_review"] == 1  # the guidance range still needs sign-off
