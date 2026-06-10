"""The Meridian Systems Q1 FY2026 demo close pack.

Reconstructs the three documents from the prototype (release, prepared remarks,
Q&A prep) as :class:`Document` objects with the figure claims the edge would
propose. Used to seed the API, drive the CLI demo, and anchor tests.
"""

from __future__ import annotations

from attest.domain.document import Document, DocumentKind
from attest.domain.verdicts import FigureClaim
from attest.ingestion.edgar_xbrl import load_fixture
from attest.service import AttestService

TENANT = "meridian"
ENTITY = "MRDN"
CLOUD = "MRDN:Cloud"
Q1 = "FY2026-Q1"
Q2 = "FY2026-Q2"


def _claim(cid: str, doc: str, metric: str, period: str, text: str, entity: str = ENTITY) -> FigureClaim:
    return FigureClaim(
        claim_id=cid, document_id=doc, entity=entity, metric=metric,
        period=period, displayed_text=text,
    )


def build_documents() -> list[Document]:
    release = Document(
        id="release",
        tenant_id=TENANT,
        title="Meridian Systems Reports First Quarter Fiscal 2026 Results",
        kind=DocumentKind.RELEASE,
        text=(
            "Meridian Systems reported total revenue of $1.24 billion, up 18% year over year. "
            "The company delivered GAAP diluted EPS of $0.87 and non-GAAP diluted EPS of $1.12. "
            "Cloud segment revenue reached $612 million, up 31% from the prior-year period. "
            "Operating cash flow was $338 million. Meridian repurchased $250 million of common "
            "stock. For the second quarter, the company expects total revenue in the range of "
            "$1.31 to $1.34 billion."
        ),
        claims=(
            _claim("r1", "release", "total_revenue", Q1, "$1.24 billion"),
            _claim("r2", "release", "gaap_diluted_eps", Q1, "$0.87"),
            _claim("r3", "release", "non_gaap_diluted_eps", Q1, "$1.12"),
            _claim("r4", "release", "cloud_revenue", Q1, "$612 million", CLOUD),
            _claim("r5", "release", "cloud_growth_yoy", Q1, "31%", CLOUD),
            _claim("r6", "release", "operating_cash_flow", Q1, "$338 million"),
            _claim("r7", "release", "share_repurchases", Q1, "$250 million"),
            _claim("r8", "release", "revenue_guidance", Q2, "$1.31 to $1.34 billion"),
        ),
    )

    script = Document(
        id="script",
        tenant_id=TENANT,
        title="Q1 FY2026 Earnings Call — Prepared Remarks",
        kind=DocumentKind.SCRIPT,
        text=(
            "We delivered a strong start to fiscal 2026. Total revenue was $1.24 billion, and our "
            "cloud business continued to lead, with segment revenue of $612 million, up 31% year "
            "over year. Non-GAAP diluted EPS were $1.12, and we generated $338 million of "
            "operating cash flow. We repurchased $250 million of common stock. Looking ahead, for "
            "the second quarter we expect total revenue in the range of $1.31 to $1.34 billion. "
            "Please refer to the safe-harbor statement regarding forward-looking statements."
        ),
        claims=(
            _claim("s1", "script", "total_revenue", Q1, "$1.24 billion"),
            _claim("s2", "script", "cloud_revenue", Q1, "$612 million", CLOUD),
            _claim("s3", "script", "cloud_growth_yoy", Q1, "31%", CLOUD),
            _claim("s4", "script", "non_gaap_diluted_eps", Q1, "$1.12"),
            _claim("s5", "script", "operating_cash_flow", Q1, "$338 million"),
            _claim("s6", "script", "share_repurchases", Q1, "$250 million"),
            _claim("s7", "script", "revenue_guidance", Q2, "$1.31 to $1.34 billion"),
        ),
    )

    qa = Document(
        id="qa",
        tenant_id=TENANT,
        title="Q1 FY2026 — Q&A Preparation",
        kind=DocumentKind.QA,
        text=(
            "Cloud remains our fastest-growing segment, reaching $612 million this quarter, up 31% "
            "year over year. Our guidance of $1.31 to $1.34 billion assumes continued momentum off "
            "this quarter's $1.24 billion in revenue. GAAP diluted EPS was $0.87; non-GAAP diluted "
            "EPS was $1.12. These statements are forward-looking; refer to our safe-harbor "
            "statement."
        ),
        claims=(
            _claim("q1", "qa", "cloud_revenue", Q1, "$612 million", CLOUD),
            _claim("q2", "qa", "cloud_growth_yoy", Q1, "31%", CLOUD),
            _claim("q3", "qa", "revenue_guidance", Q2, "$1.31 to $1.34 billion"),
            _claim("q4", "qa", "total_revenue", Q1, "$1.24 billion"),
            _claim("q5", "qa", "gaap_diluted_eps", Q1, "$0.87"),
            _claim("q6", "qa", "non_gaap_diluted_eps", Q1, "$1.12"),
        ),
    )

    return [release, script, qa]


def seeded_service() -> AttestService:
    """An AttestService with the Meridian filing ingested and ready to verify."""
    service = AttestService()
    service.ingest_xbrl(load_fixture("meridian_q1_fy2026"), tenant_id=TENANT)
    return service
