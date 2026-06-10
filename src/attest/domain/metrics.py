"""Canonical metric definitions.

Metrics are the vocabulary the fact store and the draft agree on. A draft says
"non-GAAP diluted EPS"; ingestion says ``us-gaap:EarningsPerShareDiluted``; both
must resolve to the same canonical id. This registry is also where Reg G
relationships live (which metrics are non-GAAP and what their GAAP counterpart is).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from attest.domain.money import Unit


class MetricSpec(BaseModel):
    """Definition of a single canonical metric."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    unit: Unit
    is_non_gaap: bool = False
    gaap_counterpart: str | None = Field(
        default=None, description="for non-GAAP metrics, the GAAP metric to reconcile against"
    )
    xbrl_tags: tuple[str, ...] = Field(
        default=(), description="us-gaap tags that map onto this metric during ingestion"
    )
    derived_kind: str | None = Field(
        default=None, description="formula type for derived metrics, e.g. 'yoy_growth'"
    )
    derived_base: str | None = Field(
        default=None, description="the base metric a derived metric is computed from"
    )
    derived_numerator: str | None = Field(
        default=None, description="for a 'ratio' derived metric, the numerator metric"
    )
    derived_denominator: str | None = Field(
        default=None, description="for a 'ratio' derived metric, the denominator metric"
    )
    derived_components: tuple[str, ...] = Field(
        default=(), description="for a 'sum' derived metric, the components that must sum to it"
    )
    reconciliation_adjustments: tuple[str, ...] = Field(
        default=(),
        description="for non-GAAP metrics, the adjustment metrics that bridge the GAAP "
        "counterpart to this measure (gaap + sum(adjustments) == this metric)",
    )


class MetricRegistry:
    """A lookup of metric specs, indexable by canonical id or XBRL tag."""

    def __init__(self, specs: list[MetricSpec] | None = None) -> None:
        self._by_id: dict[str, MetricSpec] = {}
        self._by_tag: dict[str, MetricSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: MetricSpec) -> None:
        self._by_id[spec.id] = spec
        for tag in spec.xbrl_tags:
            self._by_tag[tag] = spec

    def get(self, metric_id: str) -> MetricSpec | None:
        return self._by_id.get(metric_id)

    def metrics(self) -> list[MetricSpec]:
        """Every registered spec, in registration order."""
        return list(self._by_id.values())

    def by_xbrl_tag(self, tag: str) -> MetricSpec | None:
        return self._by_tag.get(tag)

    def non_gaap(self) -> list[MetricSpec]:
        return [s for s in self._by_id.values() if s.is_non_gaap]

    def __contains__(self, metric_id: str) -> bool:
        return metric_id in self._by_id


# The default registry: the issuer-neutral US-GAAP vocabulary every tenant starts
# from, plus two worked issuer extensions (the Meridian Systems demo and the
# UnitedHealth Group payer vocabulary). In production this is tenant-configurable;
# the shape stays the same. The ``xbrl_tags`` are the real ``us-gaap`` concepts the
# EDGAR companyfacts API exposes, so ingestion coverage is broad by default.
DEFAULT_REGISTRY = MetricRegistry(
    [
        # -- income statement --------------------------------------------------
        MetricSpec(
            id="total_revenue",
            label="Total revenue",
            unit=Unit.CURRENCY,
            xbrl_tags=(
                "us-gaap:Revenues",
                "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
            ),
        ),
        MetricSpec(
            id="cost_of_revenue",
            label="Cost of revenue",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsAndServicesSold"),
        ),
        MetricSpec(
            id="gross_profit",
            label="Gross profit",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:GrossProfit",),
        ),
        MetricSpec(
            id="rnd_expense",
            label="Research and development expense",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:ResearchAndDevelopmentExpense",),
        ),
        MetricSpec(
            id="sga_expense",
            label="Selling, general and administrative expense",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:SellingGeneralAndAdministrativeExpense",),
        ),
        MetricSpec(
            id="total_costs_and_expenses",
            label="Total operating costs and expenses",
            unit=Unit.CURRENCY,
            xbrl_tags=(
                "us-gaap:CostsAndExpenses",
                "us-gaap:OperatingExpenses",
                "us-gaap:BenefitsLossesAndExpenses",
            ),
        ),
        MetricSpec(
            id="operating_income",
            label="Earnings from operations",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:OperatingIncomeLoss",),
        ),
        MetricSpec(
            id="pretax_income",
            label="Earnings before income taxes",
            unit=Unit.CURRENCY,
            xbrl_tags=(
                "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            ),
        ),
        MetricSpec(
            id="income_tax_expense",
            label="Provision for income taxes",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:IncomeTaxExpenseBenefit",),
        ),
        MetricSpec(
            id="net_income",
            label="Net earnings",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"),
        ),
        MetricSpec(
            id="gaap_basic_eps",
            label="GAAP basic EPS",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:EarningsPerShareBasic",),
        ),
        MetricSpec(
            id="basic_shares",
            label="Weighted-average basic shares",
            unit=Unit.SHARES,
            xbrl_tags=("us-gaap:WeightedAverageNumberOfSharesOutstandingBasic",),
        ),
        MetricSpec(
            id="diluted_shares",
            label="Weighted-average diluted shares",
            unit=Unit.SHARES,
            xbrl_tags=("us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding",),
        ),
        # -- balance sheet -----------------------------------------------------
        MetricSpec(
            id="total_assets",
            label="Total assets",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:Assets",),
        ),
        MetricSpec(
            id="total_liabilities",
            label="Total liabilities",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:Liabilities",),
        ),
        MetricSpec(
            id="stockholders_equity",
            label="Shareholders' equity",
            unit=Unit.CURRENCY,
            xbrl_tags=(
                "us-gaap:StockholdersEquity",
                "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ),
        ),
        MetricSpec(
            id="cash_and_equivalents",
            label="Cash and cash equivalents",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:CashAndCashEquivalentsAtCarryingValue",),
        ),
        MetricSpec(
            id="long_term_debt",
            label="Long-term debt",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:LongTermDebt", "us-gaap:LongTermDebtNoncurrent"),
        ),
        # -- cash flow ----------------------------------------------------------
        MetricSpec(
            id="investing_cash_flow",
            label="Cash flows from investing activities",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:NetCashProvidedByUsedInInvestingActivities",),
        ),
        MetricSpec(
            id="financing_cash_flow",
            label="Cash flows from financing activities",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:NetCashProvidedByUsedInFinancingActivities",),
        ),
        MetricSpec(
            id="capex",
            label="Capital expenditures",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",),
        ),
        MetricSpec(
            id="dividends_paid",
            label="Dividends paid",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:PaymentsOfDividends", "us-gaap:PaymentsOfDividendsCommonStock"),
        ),
        # -- payer / health-insurer vocabulary (UNH-style issuers) ---------------
        MetricSpec(
            id="premium_revenue",
            label="Premium revenue",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:PremiumsEarnedNet",),
        ),
        MetricSpec(
            id="medical_costs",
            label="Medical costs",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:PolicyholderBenefitsAndClaimsIncurredNet",),
        ),
        MetricSpec(
            id="medical_costs_payable",
            label="Medical costs payable",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="medical_care_ratio",
            label="Medical care ratio",
            unit=Unit.PERCENT,
            derived_kind="ratio_pct",
            derived_numerator="medical_costs",
            derived_denominator="premium_revenue",
        ),
        MetricSpec(
            id="operating_cost_ratio",
            label="Operating cost ratio",
            unit=Unit.PERCENT,
        ),
        MetricSpec(
            id="return_on_equity",
            label="Return on equity",
            unit=Unit.PERCENT,
        ),
        # -- UNH segment vocabulary (attribution; facts arrive via the simplified
        #    instance connector until a dimension-aware instance parser lands) ----
        MetricSpec(id="unitedhealthcare_revenue", label="UnitedHealthcare revenue", unit=Unit.CURRENCY),
        MetricSpec(id="optum_revenue", label="Optum revenue", unit=Unit.CURRENCY),
        MetricSpec(id="optum_health_revenue", label="Optum Health revenue", unit=Unit.CURRENCY),
        MetricSpec(id="optum_insight_revenue", label="Optum Insight revenue", unit=Unit.CURRENCY),
        MetricSpec(id="optum_rx_revenue", label="Optum Rx revenue", unit=Unit.CURRENCY),
        MetricSpec(
            id="unitedhealthcare_operating_earnings",
            label="UnitedHealthcare earnings from operations",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(id="optum_operating_earnings", label="Optum earnings from operations", unit=Unit.CURRENCY),
        MetricSpec(
            id="optum_health_operating_earnings",
            label="Optum Health earnings from operations",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="optum_insight_operating_earnings",
            label="Optum Insight earnings from operations",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="optum_rx_operating_earnings",
            label="Optum Rx earnings from operations",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="cloud_revenue",
            label="Cloud segment revenue",
            unit=Unit.CURRENCY,
            xbrl_tags=("mrdn:CloudSegmentRevenue",),
        ),
        MetricSpec(
            id="cloud_growth_yoy",
            label="Cloud growth, YoY",
            unit=Unit.PERCENT,
            derived_kind="yoy_growth",
            derived_base="cloud_revenue",
        ),
        MetricSpec(
            id="gaap_diluted_eps",
            label="GAAP diluted EPS",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:EarningsPerShareDiluted",),
        ),
        MetricSpec(
            id="non_gaap_diluted_eps",
            label="Non-GAAP diluted EPS",
            unit=Unit.CURRENCY,
            is_non_gaap=True,
            gaap_counterpart="gaap_diluted_eps",
            reconciliation_adjustments=(
                "sbc_eps_adjustment",
                "intangibles_amort_eps_adjustment",
            ),
        ),
        MetricSpec(
            id="sbc_eps_adjustment",
            label="Stock-based compensation (per share)",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="intangibles_amort_eps_adjustment",
            label="Amortization of intangibles (per share)",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="operating_cash_flow",
            label="Operating cash flow",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:NetCashProvidedByUsedInOperatingActivities",),
        ),
        MetricSpec(
            id="share_repurchases",
            label="Share repurchases",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:PaymentsForRepurchaseOfCommonStock",),
        ),
        # Forward guidance lives in press-release prose, never in XBRL. These are the
        # period-agnostic targets the 8-K EX-99.1 guidance connector binds to; the
        # period field carries which quarter / full year a given figure is for.
        MetricSpec(
            id="revenue_guidance",
            label="Revenue guidance",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="eps_guidance",
            label="EPS guidance",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="adjusted_eps_guidance",
            label="Adjusted EPS guidance",
            unit=Unit.CURRENCY,
        ),
        MetricSpec(
            id="operating_margin_guidance",
            label="Operating margin guidance",
            unit=Unit.PERCENT,
        ),
        MetricSpec(
            id="operating_margin",
            label="Operating margin",
            unit=Unit.PERCENT,
        ),
        MetricSpec(
            id="operating_margin_change_bps",
            label="Operating margin change",
            unit=Unit.BASIS_POINTS,
            derived_kind="delta_bps",
            derived_base="operating_margin",
        ),
    ]
)
