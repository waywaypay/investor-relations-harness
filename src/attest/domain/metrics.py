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
    derived_subtrahend: str | None = Field(
        default=None,
        description="for a 'difference' derived metric, the metric subtracted from "
        "derived_base (e.g. free cash flow = operating_cash_flow - capex)",
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

    def all(self) -> list[MetricSpec]:
        """Every registered spec, in registration order."""
        return list(self._by_id.values())

    def __contains__(self, metric_id: str) -> bool:
        return metric_id in self._by_id


# Default registry. In production this is tenant-configurable; the shape stays
# the same.
DEFAULT_REGISTRY = MetricRegistry(
    [
        MetricSpec(
            id="total_revenue",
            label="Total revenue",
            unit=Unit.CURRENCY,
            # The current standard tag is listed first so it wins for issuers that
            # report under ASC 606; ``Revenues`` is the legacy fallback.
            xbrl_tags=(
                "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                "us-gaap:Revenues",
                "us-gaap:RevenueFromContractWithCustomer",
            ),
        ),
        MetricSpec(
            id="cloud_revenue",
            label="Cloud segment revenue",
            unit=Unit.CURRENCY,
            xbrl_tags=("atls:CloudSegmentRevenue",),
        ),
        MetricSpec(
            id="cloud_growth_yoy",
            label="Cloud growth, YoY",
            unit=Unit.PERCENT,
            derived_kind="yoy_growth",
            derived_base="cloud_revenue",
        ),
        # Generic growth figures every release states ("total revenue grew 14%
        # year over year", "RPO grew 36%"). Like the margins below they have no
        # XBRL tag of their own — the engine recomputes them from the filed levels
        # of this period and the prior-year period, so they trace (or conflict)
        # for any live-ingested issuer instead of reading as unattributed numbers.
        MetricSpec(
            id="revenue_growth_yoy",
            label="Total revenue growth, YoY",
            unit=Unit.PERCENT,
            derived_kind="yoy_growth",
            derived_base="total_revenue",
        ),
        MetricSpec(
            id="rpo_growth_yoy",
            label="RPO growth, YoY",
            unit=Unit.PERCENT,
            derived_kind="yoy_growth",
            derived_base="total_rpo",
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
        # General income-statement / balance-sheet metrics so a draft analyzed
        # against a real EDGAR filing (see attest.ingestion.edgar) ties out beyond
        # the reference fixture's vocabulary. Each carries the us-gaap tag the
        # connector fetches; the unit is what the engine compares in.
        MetricSpec(
            id="net_income",
            label="Net income",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:NetIncomeLoss",),
        ),
        MetricSpec(
            id="operating_income",
            label="Operating income",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:OperatingIncomeLoss",),
        ),
        MetricSpec(
            id="gross_profit",
            label="Gross profit",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:GrossProfit",),
        ),
        MetricSpec(
            id="total_rpo",
            label="Remaining performance obligations",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:RevenueRemainingPerformanceObligation",),
        ),
        MetricSpec(
            id="cash_and_equivalents",
            label="Cash and cash equivalents",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:CashAndCashEquivalentsAtCarryingValue",),
        ),
        MetricSpec(
            id="share_repurchases",
            label="Share repurchases",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:PaymentsForRepurchaseOfCommonStock",),
        ),
        MetricSpec(
            id="q2_revenue_guidance",
            label="Q2 revenue guidance",
            unit=Unit.CURRENCY,
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
            id="operating_margin_guidance",
            label="Operating margin guidance",
            unit=Unit.PERCENT,
        ),
        # Margins are not tagged in XBRL, but they are exact identities over figures
        # that are: a draft's "operating margin of 28%" recomputes from filed operating
        # income / revenue, so the engine can trace it (or catch a wrong one) rather than
        # leave it untraced.
        MetricSpec(
            id="operating_margin",
            label="Operating margin",
            unit=Unit.PERCENT,
            derived_kind="ratio_pct",
            derived_numerator="operating_income",
            derived_denominator="total_revenue",
        ),
        # Non-GAAP margins are a different (larger) number than the GAAP ratio above —
        # recomputing a "non-GAAP gross margin" against filed GAAP gross profit would
        # fire a false conflict. They have no filed source, so (like billings) they are
        # recognised and checked for *consistency* against prior disclosure, never traced.
        MetricSpec(
            id="non_gaap_operating_margin",
            label="Non-GAAP operating margin",
            unit=Unit.PERCENT,
        ),
        MetricSpec(
            id="non_gaap_gross_margin",
            label="Non-GAAP gross margin",
            unit=Unit.PERCENT,
        ),
        MetricSpec(
            id="gross_margin",
            label="Gross margin",
            unit=Unit.PERCENT,
            derived_kind="ratio_pct",
            derived_numerator="gross_profit",
            derived_denominator="total_revenue",
        ),
        MetricSpec(
            id="operating_margin_change_bps",
            label="Operating margin change",
            unit=Unit.BASIS_POINTS,
            derived_kind="delta_bps",
            derived_base="operating_margin",
        ),
        # Capital expenditure — a filed cash-flow line, and the subtrahend that turns
        # operating cash flow into free cash flow.
        MetricSpec(
            id="capex",
            label="Capital expenditure",
            unit=Unit.CURRENCY,
            xbrl_tags=("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",),
        ),
        # Free cash flow is the canonical non-GAAP cash measure: operating cash flow
        # less capex. Both operands are filed, so it is a verifiable identity. (Quarterly
        # operands are often filed only as cumulative YTD figures, which the EDGAR
        # connector skips, so single-quarter FCF can stay honestly untraced; annual FCF
        # ties out.)
        # (Left off the Reg G non-GAAP flag deliberately: FCF *is* non-GAAP, but the
        # equal-prominence / reconciliation enforcement that flag drives doesn't fit a
        # subtraction identity — wiring that up is a separate Reg G enhancement.)
        MetricSpec(
            id="free_cash_flow",
            label="Free cash flow",
            unit=Unit.CURRENCY,
            derived_kind="difference",
            derived_base="operating_cash_flow",
            derived_subtrahend="capex",
        ),
        # Billings (revenue + change in deferred revenue) is a non-GAAP operational
        # measure with no standard XBRL tag and an issuer-specific definition, so it is
        # never *traced* to a filing. Registering it means a draft's billings figure is
        # recognised (not "unidentified") and checked for *consistency* against what the
        # company previously disclosed — the path every non-filed operational figure takes.
        MetricSpec(
            id="billings",
            label="Billings",
            unit=Unit.CURRENCY,
        ),
    ]
)
