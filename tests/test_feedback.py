"""Tests for the production-feedback exporter.

Captures the four cautions from the design discussion:
1. An override is ambiguous -> a structured `reason` disambiguates it.
2. Only `engine_wrong` overrides become candidate labels; `accepting_risk` and
   `dismissing` do NOT (treating them as "engine wrong" would poison precision).
3. MNPI -> values are anonymized out of the candidate record.
4. Candidates are tagged `production_feedback` (a third bucket) and are
   *candidates*, never auto-promoted labels.
"""

from attest.audit.log import InMemoryAuditLog
from attest.eval.feedback import OverrideReason, candidates_from_audit
from attest.service import AttestService


def _service_with_overrides() -> AttestService:
    svc = AttestService(audit_log=InMemoryAuditLog())
    # engine_wrong: a real false-positive signal -> should become a candidate
    svc.override(
        tenant_id="acme", actor="iro@acme", claim_id="rev_q1",
        justification="$1.24B is correct; rounds from filed $1,241.3M",
        reason=OverrideReason.ENGINE_WRONG, metric="total_revenue", period="FY2026-Q1",
        displayed_text="$1.24 billion",
    )
    # accepting_risk: a business decision, not an engine error -> excluded
    svc.override(
        tenant_id="acme", actor="cfo@acme", claim_id="guid_q2",
        justification="guidance, accepting the flag", reason=OverrideReason.ACCEPTING_RISK,
        metric="q2_revenue_guidance", period="FY2026-Q2", displayed_text="$1.31 to $1.34B",
    )
    # dismissing: noise click-through -> excluded
    svc.override(
        tenant_id="acme", actor="iro@acme", claim_id="x", justification="",
        reason=OverrideReason.DISMISSING, metric="total_revenue", period="FY2026-Q1",
        displayed_text="$1.24 billion",
    )
    return svc


def test_only_engine_wrong_becomes_candidate():
    svc = _service_with_overrides()
    cands = candidates_from_audit(svc.audit_log)
    assert len(cands) == 1
    assert cands[0].reason == OverrideReason.ENGINE_WRONG
    assert cands[0].metric == "total_revenue"


def test_candidates_are_tagged_production_feedback():
    svc = _service_with_overrides()
    cands = candidates_from_audit(svc.audit_log)
    assert all(c.label_source == "production_feedback" for c in cands)
    # candidate, not a promoted label: the suggested verdict is advisory
    assert cands[0].suggested_verdict == "traced"
    assert cands[0].promoted is False


def test_anonymization_scrubs_mnpi_values():
    svc = _service_with_overrides()
    cands = candidates_from_audit(svc.audit_log, anonymize=True)
    c = cands[0]
    # the literal figure text and justification (which may contain MNPI) are dropped
    assert "1.24" not in (c.displayed_text or "")
    assert c.justification == ""
    # but the structural signal survives: metric, period, reason, verdict
    assert c.metric == "total_revenue"
    assert c.period == "FY2026-Q1"


def test_raw_export_keeps_values_for_single_tenant_use():
    svc = _service_with_overrides()
    cands = candidates_from_audit(svc.audit_log, anonymize=False)
    assert cands[0].displayed_text == "$1.24 billion"


def test_override_without_reason_defaults_and_is_excluded():
    # Back-compat: an override logged without a structured reason is treated as
    # unspecified and not promoted to a candidate (we never guess intent).
    svc = AttestService(audit_log=InMemoryAuditLog())
    svc.override(tenant_id="acme", actor="a", claim_id="x", justification="legacy")
    cands = candidates_from_audit(svc.audit_log)
    assert cands == []
