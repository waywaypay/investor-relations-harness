import pytest

from decimal import Decimal

from attest.demo import build_documents, seeded_service
from attest.domain.verdicts import FigureClaim, Verdict
from attest.verification.candidates import detect_candidates


@pytest.fixture
def service():
    return seeded_service()


def _verify(service, metric, period, text, entity="MRDN"):
    claim = FigureClaim(
        claim_id="c", document_id="d", entity=entity, metric=metric,
        period=period, displayed_text=text,
    )
    return service.engine.verify_claim(claim, "meridian")


def test_traced_within_rounding(service):
    v = _verify(service, "total_revenue", "FY2026-Q1", "$1.24 billion")
    assert v.verdict == Verdict.TRACED
    assert v.provenance.source_type.value == "edgar_xbrl"
    assert v.source_value is not None


def test_conflict_on_wrong_value(service):
    v = _verify(service, "gaap_diluted_eps", "FY2026-Q1", "$0.91")
    assert v.verdict == Verdict.CONFLICT


def test_restatement_conflict_matches_superseded(service):
    v = _verify(service, "cloud_growth_yoy", "FY2026-Q1", "31%", entity="MRDN:Cloud")
    assert v.verdict == Verdict.CONFLICT
    assert "restated" in v.reason.lower() or "superseded" in v.reason.lower()


def test_corrected_value_traces(service):
    v = _verify(service, "cloud_growth_yoy", "FY2026-Q1", "29%", entity="MRDN:Cloud")
    assert v.verdict == Verdict.TRACED


def test_guidance_needs_review_even_as_range(service):
    v = _verify(service, "revenue_guidance", "FY2026-Q2", "$1.31 to $1.34 billion")
    assert v.verdict == Verdict.NEEDS_REVIEW


def test_untraced_when_no_source(service):
    v = _verify(service, "deferred_revenue", "FY2026-Q1", "$500 million")
    assert v.verdict == Verdict.UNTRACED


def test_verdict_writes_audit_event(service):
    before = len(service.audit_log.events())
    _verify(service, "total_revenue", "FY2026-Q1", "$1.24 billion")
    after = len(service.audit_log.events())
    assert after == before + 1


def test_candidate_detection_overdetects_not_under():
    text = "Revenue was $1.24 billion, up 31%, with $338 million of cash flow."
    cands = detect_candidates(text)
    texts = {c.text for c in cands}
    assert "$1.24 billion" in texts
    assert "31%" in texts
    assert "$338 million" in texts


def test_close_pack_verdict_shape():
    service = seeded_service()
    docs = build_documents()
    results, consistency = service.engine.verify_close_pack(docs)
    release = next(r for r in results if r.document_id == "release")
    assert release.counts["traced"] == 6
    assert release.counts["conflict"] == 1
    assert release.counts["needs_review"] == 1
    # a conflict blocks publish
    assert release.publishable is False
    # figures agree across documents
    assert consistency == []


def test_detector_sees_every_negative_convention():
    text = "Net loss of -$1,409 million versus $ (1,409 ) as filed, margin -12.4%."
    found = [c.text for c in detect_candidates(text)]
    assert "-$1,409 million" in found
    assert "$ (1,409 )" in found
    assert "-12.4%" in found


def test_detector_sees_bare_scaled_numbers_and_leaves_prose_asides_positive():
    cands = {c.text: c for c in detect_candidates(
        "Operating costs of 1,000 million; repurchases ($1.2 billion) continued. Up 10-12% range."
    )}
    assert cands["1,000 million"].quantity.value == Decimal(1_000_000_000)
    # A dollar sign *inside* parens is a prose aside, not a negative...
    assert cands["$1.2 billion"].quantity.value == Decimal(1_200_000_000)
    # ...and a digit-adjacent hyphen ("10-12%") is a range separator, not a minus.
    assert cands["12%"].quantity.value == Decimal(12)
