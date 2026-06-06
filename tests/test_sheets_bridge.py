"""The Sheets bridge: read the 02_Facts CSV export into Fact objects, then feed
the perturbation generator. Lets the corpus workbook drive synthetic generation.
"""

import io
from decimal import Decimal

from attest.domain.facts import SourceType
from attest.domain.money import Unit
from attest.eval.sheets_bridge import facts_from_csv

CSV = (
    "filing_id,entity,metric,period,value_num,scale,value_base,unit,decimals,"
    "source_type,source_ref,source_label,as_of,confidence\n"
    "F_ATLS,ATLS,total_revenue,FY2026-Q1,1241.3,millions,1241300000,currency,-5,"
    "edgar_xbrl,acc#rev,10-Q p.4,2026-04-28,high\n"
    "F_ATLS,ATLS,q2_revenue_guidance,FY2026-Q2,1325.0,millions,1325000000,currency,,"
    "management_input,none,memo,2026-04-21,medium\n"
)


def test_facts_from_csv_parses_rows():
    facts = facts_from_csv(io.StringIO(CSV), tenant_id="atlas")
    assert len(facts) == 2
    rev = next(f for f in facts if f.metric == "total_revenue")
    assert rev.value == Decimal("1241300000")
    assert rev.unit == Unit.CURRENCY
    assert rev.source_type == SourceType.EDGAR_XBRL
    assert rev.quantum == Decimal("100000")  # decimals=-5 -> 1e5


def test_blank_decimals_is_exact_quantum():
    facts = facts_from_csv(io.StringIO(CSV), tenant_id="atlas")
    guidance = next(f for f in facts if f.metric == "q2_revenue_guidance")
    assert guidance.quantum == Decimal(0)
    assert guidance.is_filed is False


def test_csv_facts_drive_perturbation():
    from attest.eval.perturbation import perturb_facts
    facts = facts_from_csv(io.StringIO(CSV), tenant_id="atlas")
    cases = perturb_facts(facts)
    # only the filed currency fact yields cases; guidance is skipped
    assert cases
    assert all(c.metric == "total_revenue" for c in cases)
