from attest.demo import build_documents, seeded_service
from attest.domain.document import Document, DocumentKind
from attest.domain.metrics import DEFAULT_REGISTRY
from attest.domain.verdicts import FigureClaim
from attest.factstore.repository import InMemoryFactStore
from attest.verification.rules import (
    check_cross_document_consistency,
    check_forward_looking,
    check_reg_g,
)


def _doc(doc_id, text, claims, tenant="meridian"):
    return Document(id=doc_id, tenant_id=tenant, title=doc_id, kind=DocumentKind.OTHER,
                    text=text, claims=tuple(claims))


def _claim(metric, text, entity="MRDN", period="FY2026-Q1", doc="d"):
    return FigureClaim(claim_id=metric, document_id=doc, entity=entity, metric=metric,
                       period=period, displayed_text=text)


def test_reg_g_passes_with_gaap_and_recon():
    service = seeded_service()
    doc = _doc("ok", "non-GAAP $1.12 and GAAP $0.87", [
        _claim("non_gaap_diluted_eps", "$1.12"),
        _claim("gaap_diluted_eps", "$0.87"),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, service.store)
    assert findings == []


def test_reg_g_flags_missing_equal_prominence():
    service = seeded_service()
    doc = _doc("bad", "non-GAAP $1.12", [_claim("non_gaap_diluted_eps", "$1.12")])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, service.store)
    assert any(f.rule == "reg_g.equal_prominence" for f in findings)


def test_reg_g_flags_missing_reconciliation_source():
    # empty store: the GAAP counterpart fact does not exist
    store = InMemoryFactStore()
    doc = _doc("bad", "non-GAAP $1.12 and GAAP $0.87", [
        _claim("non_gaap_diluted_eps", "$1.12"),
        _claim("gaap_diluted_eps", "$0.87"),
    ])
    findings = check_reg_g(doc, DEFAULT_REGISTRY, store)
    assert any(f.rule == "reg_g.reconciliation_required" for f in findings)


def test_forward_looking_requires_safe_harbor():
    service = seeded_service()
    doc = _doc("fls", "For the second quarter we expect revenue to grow.",
               [_claim("q2_revenue_guidance", "$1.31 to $1.34 billion", period="FY2026-Q2")])
    findings = check_forward_looking(doc, service.store)
    assert any(f.rule == "forward_looking.safe_harbor_required" for f in findings)


def test_forward_looking_satisfied_with_safe_harbor():
    service = seeded_service()
    doc = _doc("fls", "We expect growth. Refer to our safe-harbor statement.",
               [_claim("q2_revenue_guidance", "$1.31 to $1.34 billion", period="FY2026-Q2")])
    findings = check_forward_looking(doc, service.store)
    assert findings == []


def test_consistency_flags_divergent_values():
    d1 = _doc("release", "rev $1.24 billion", [_claim("total_revenue", "$1.24 billion", doc="release")])
    d2 = _doc("script", "rev $1.25 billion", [_claim("total_revenue", "$1.25 billion", doc="script")])
    findings = check_cross_document_consistency([d1, d2])
    assert any(f.rule == "consistency.cross_document_mismatch" for f in findings)


def test_consistency_clean_on_matching_values():
    docs = build_documents()
    assert check_cross_document_consistency(docs) == []


def test_demo_script_flags_reg_g_and_release_flags_fls():
    service = seeded_service()
    docs = build_documents()
    results, _ = service.engine.verify_close_pack(docs)
    script = next(r for r in results if r.document_id == "script")
    release = next(r for r in results if r.document_id == "release")
    assert any(f.rule == "reg_g.equal_prominence" for f in script.findings)
    assert any(f.rule == "forward_looking.safe_harbor_required" for f in release.findings)
