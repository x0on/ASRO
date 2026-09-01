"""Historical benchmark measurement catalog.

The live monitor's `asro.dictionary.registry` holds weighted *scoring* variables. This
catalog is a different thing: the set of measurements that must be comparable across
historical episodes before any calibration claim is defensible. Nothing here carries a
weight, because weights are a scoring decision and this layer is descriptive.

Every variable declares the causal role it evidences, the unit it is measured in, and
whether a higher reading means more or less systemic pressure. Machine-derivable
variables additionally declare the SEC XBRL concepts or control series that produce
them, so coverage can be computed rather than asserted.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class CausalRole(StrEnum):
    """Where a measurement sits in the boom-to-consequence chain."""

    BOOM = "boom"
    VALIDATION = "validation"
    VULNERABILITY = "vulnerability"
    SHOCK = "shock"
    TRANSMISSION = "transmission"
    ACTIVATED_STRESS = "activated_stress"
    REAL_ECONOMY = "real_economy"
    RESILIENCE = "resilience"


class Direction(StrEnum):
    HIGHER_IS_MORE_PRESSURE = "higher_is_more_pressure"
    HIGHER_IS_LESS_PRESSURE = "higher_is_less_pressure"
    CONTEXTUAL = "contextual"


class MeasurementScope(StrEnum):
    ENTITY = "entity"
    ECOSYSTEM = "ecosystem"
    NETWORK = "network"
    MARKET = "market"


class DerivedView(StrEnum):
    LEVEL = "level"
    VELOCITY = "velocity"
    BREADTH = "breadth"


class BenchmarkVariable(BaseModel):
    """One historically comparable measurement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    causal_role: CausalRole
    scope: MeasurementScope
    unit: str
    direction: Direction
    description: str
    numerator_concept: str | None = None
    denominator_concept: str | None = None
    xbrl_concepts: tuple[str, ...] = ()
    control_series: tuple[str, ...] = ()
    derived_views: tuple[DerivedView, ...] = (
        DerivedView.LEVEL,
        DerivedView.VELOCITY,
        DerivedView.BREADTH,
    )
    comparability: str
    requires_manual_review: bool = False

    @model_validator(mode="after")
    def validate_variable(self) -> BenchmarkVariable:
        if self.unit == "ratio" and not (self.numerator_concept and self.denominator_concept):
            raise ValueError(f"{self.key}: ratio variables require numerator and denominator")
        if not self.comparability.strip():
            raise ValueError(f"{self.key}: comparability note is required")
        if not self.derived_views:
            raise ValueError(f"{self.key}: at least one derived view is required")
        return self

    @property
    def machine_derivable(self) -> bool:
        """True when the value comes from XBRL or a control series without human judgement."""
        return bool(self.xbrl_concepts or self.control_series) and not self.requires_manual_review


def _v(
    key: str,
    label: str,
    role: CausalRole,
    scope: MeasurementScope,
    unit: str,
    direction: Direction,
    description: str,
    comparability: str,
    *,
    numerator: str | None = None,
    denominator: str | None = None,
    xbrl: tuple[str, ...] = (),
    controls: tuple[str, ...] = (),
    views: tuple[DerivedView, ...] | None = None,
    manual: bool = False,
) -> BenchmarkVariable:
    return BenchmarkVariable(
        key=key,
        label=label,
        causal_role=role,
        scope=scope,
        unit=unit,
        direction=direction,
        description=description,
        numerator_concept=numerator,
        denominator_concept=denominator,
        xbrl_concepts=xbrl,
        control_series=controls,
        derived_views=views or (DerivedView.LEVEL, DerivedView.VELOCITY, DerivedView.BREADTH),
        comparability=comparability,
        requires_manual_review=manual,
    )


_MORE = Direction.HIGHER_IS_MORE_PRESSURE
_LESS = Direction.HIGHER_IS_LESS_PRESSURE
_CTX = Direction.CONTEXTUAL
_ENTITY = MeasurementScope.ENTITY
_ECO = MeasurementScope.ECOSYSTEM
_NET = MeasurementScope.NETWORK
_MKT = MeasurementScope.MARKET
_R = CausalRole

_BOOM: tuple[BenchmarkVariable, ...] = (
    _v(
        "capital_expenditure",
        "Capital expenditure",
        _R.BOOM,
        _ENTITY,
        "currency",
        _MORE,
        "Cash spent on property, plant and equipment in the period.",
        "Directly comparable across episodes; capitalised leases changed under ASC 842 "
        "in 2019, so pre-2019 lease-heavy build-outs understate committed capacity.",
        xbrl=("PaymentsToAcquirePropertyPlantAndEquipment",),
    ),
    _v(
        "capital_growth_rate",
        "Capital deployment growth rate",
        _R.BOOM,
        _ENTITY,
        "percent",
        _MORE,
        "Year-over-year growth in capital expenditure.",
        "Comparable as a rate; base effects dominate for entities early in a build-out.",
        numerator="capital_expenditure",
        denominator="capital_expenditure_prior_year",
        xbrl=("PaymentsToAcquirePropertyPlantAndEquipment",),
    ),
    _v(
        "productive_capacity_stock",
        "Gross productive capacity",
        _R.BOOM,
        _ENTITY,
        "currency",
        _MORE,
        "Gross property, plant and equipment as a proxy for installed capacity.",
        "Gross rather than net, so depreciation-policy changes do not move the series; "
        "useful-life changes still affect the net book value comparison.",
        xbrl=("PropertyPlantAndEquipmentGross",),
    ),
    _v(
        "debt_financed_investment_share",
        "Debt-financed share of investment",
        _R.BOOM,
        _ENTITY,
        "ratio",
        _MORE,
        "Net debt issuance divided by capital expenditure in the period.",
        "Comparable; negative when an entity repays debt while investing, which is a "
        "resilience signal rather than a missing value.",
        numerator="net_debt_issuance",
        denominator="capital_expenditure",
        xbrl=(
            "ProceedsFromIssuanceOfLongTermDebt",
            "RepaymentsOfLongTermDebt",
            "PaymentsToAcquirePropertyPlantAndEquipment",
        ),
    ),
    _v(
        "equity_financed_investment",
        "Equity issuance funding investment",
        _R.BOOM,
        _ENTITY,
        "currency",
        _CTX,
        "Proceeds from issuing common and preferred stock in the period.",
        "Comparable; equity funding of a build-out is less fragile than debt funding, "
        "so this is contextual rather than directional on its own.",
        xbrl=(
            "ProceedsFromIssuanceOfCommonStock",
            "ProceedsFromIssuanceOfPreferredStockAndPreferenceStock",
        ),
    ),
    _v(
        "lease_and_guarantee_commitments",
        "Lease and guaranteed commitments",
        _R.BOOM,
        _ENTITY,
        "currency",
        _MORE,
        "Operating and finance lease obligations plus disclosed guarantee exposure.",
        "Breaks at ASC 842 (2019): before it, operating leases sat off balance sheet and "
        "must be read from commitment footnotes. Pre-2019 values are not comparable to "
        "post-2019 values without an explicit adjustment.",
        xbrl=(
            "OperatingLeaseLiability",
            "FinanceLeaseLiability",
            "LesseeOperatingLeaseLiabilityPaymentsDue",
        ),
    ),
    _v(
        "investment_acceleration",
        "Investment acceleration",
        _R.BOOM,
        _ECO,
        "percent",
        _MORE,
        "Change in the capital growth rate: the second derivative of deployment.",
        "Comparable only where at least three consecutive periods are covered; "
        "otherwise reported as unknown rather than zero.",
        numerator="capital_growth_rate",
        denominator="capital_growth_rate_prior_period",
    ),
    _v(
        "sector_valuation_multiple",
        "Sector valuation multiple",
        _R.BOOM,
        _MKT,
        "ratio",
        _MORE,
        "Forward or trailing earnings multiple of the episode's sector aggregate.",
        "Comparable only within a consistent index definition; sector composition drift "
        "makes long-run comparisons approximate and must be labelled as such.",
        numerator="sector_market_capitalisation",
        denominator="sector_earnings",
        controls=("equity_index_pe",),
        manual=True,
    ),
)

_VALIDATION: tuple[BenchmarkVariable, ...] = (
    _v(
        "external_revenue",
        "External customer revenue",
        _R.VALIDATION,
        _ENTITY,
        "currency",
        _LESS,
        "Revenue recognised from customers outside the monitored financing network.",
        "Reported revenue is directly comparable; the exclusion of intra-network revenue "
        "is not available from XBRL and must be applied as a reviewed adjustment.",
        xbrl=("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ),
    _v(
        "revenue_growth",
        "Revenue growth",
        _R.VALIDATION,
        _ENTITY,
        "percent",
        _LESS,
        "Year-over-year growth in reported revenue.",
        "Directly comparable across episodes.",
        numerator="external_revenue",
        denominator="external_revenue_prior_year",
        xbrl=("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ),
    _v(
        "free_cash_flow",
        "Free cash flow",
        _R.VALIDATION,
        _ENTITY,
        "currency",
        _LESS,
        "Operating cash flow less capital expenditure.",
        "Directly comparable; the definition is held fixed rather than following each "
        "company's own non-GAAP free-cash-flow measure.",
        numerator="operating_cash_flow",
        denominator="capital_expenditure",
        xbrl=(
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
        ),
    ),
    _v(
        "free_cash_flow_margin",
        "Free cash flow margin",
        _R.VALIDATION,
        _ENTITY,
        "ratio",
        _LESS,
        "Free cash flow divided by revenue.",
        "Directly comparable.",
        numerator="free_cash_flow",
        denominator="external_revenue",
        xbrl=(
            "NetCashProvidedByUsedInOperatingActivities",
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "Revenues",
        ),
    ),
    _v(
        "capex_to_revenue",
        "Capital expenditure to revenue",
        _R.VALIDATION,
        _ENTITY,
        "ratio",
        _MORE,
        "Capital expenditure divided by revenue: how much building each dollar of sales "
        "is supporting.",
        "Directly comparable and the single most portable boom-versus-validation measure "
        "across capital cycles.",
        numerator="capital_expenditure",
        denominator="external_revenue",
        xbrl=("PaymentsToAcquirePropertyPlantAndEquipment", "Revenues"),
    ),
    _v(
        "fixed_obligations_to_external_cash",
        "Fixed obligations to sustainable external cash",
        _R.VALIDATION,
        _ENTITY,
        "ratio",
        _MORE,
        "Debt, lease and guarantee obligations divided by operating cash flow.",
        "Comparable post-2019; earlier periods omit operating leases unless the "
        "commitment footnote has been reviewed and added.",
        numerator="total_fixed_obligations",
        denominator="operating_cash_flow",
        xbrl=("NetCashProvidedByUsedInOperatingActivities",),
    ),
    _v(
        "revenue_minus_obligation_growth",
        "Revenue growth less obligation growth",
        _R.VALIDATION,
        _ENTITY,
        "percent",
        _LESS,
        "Spread between revenue growth and growth in fixed obligations.",
        "Comparable; a persistently negative spread is the defining signature of the "
        "monitored mechanism.",
        numerator="revenue_growth",
        denominator="obligation_growth",
    ),
    _v(
        "demand_conversion_evidence",
        "Utilisation or backlog conversion",
        _R.VALIDATION,
        _ENTITY,
        "ratio",
        _LESS,
        "Share of contracted backlog or installed capacity converting into revenue.",
        "Poorly comparable: backlog and remaining performance obligations are disclosed "
        "inconsistently and only became a standard tag under ASC 606 (2018).",
        numerator="revenue_recognised_from_backlog",
        denominator="opening_backlog",
        xbrl=("RevenueRemainingPerformanceObligation",),
        manual=True,
    ),
)

_VULNERABILITY: tuple[BenchmarkVariable, ...] = (
    _v(
        "debt_to_operating_cash_flow",
        "Debt to operating cash flow",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _MORE,
        "Total debt divided by operating cash flow.",
        "Directly comparable and available for the whole XBRL era.",
        numerator="total_debt",
        denominator="operating_cash_flow",
        xbrl=(
            "DebtLongtermAndShorttermCombinedAmount",
            "LongTermDebt",
            "NetCashProvidedByUsedInOperatingActivities",
        ),
    ),
    _v(
        "debt_to_assets",
        "Debt to assets",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _MORE,
        "Total debt divided by total assets.",
        "Directly comparable; asset bases inflate under ASC 842 for lease-heavy filers, "
        "which mechanically lowers the ratio after 2019.",
        numerator="total_debt",
        denominator="total_assets",
        xbrl=("LongTermDebt", "Assets"),
    ),
    _v(
        "debt_service_coverage",
        "Debt service coverage",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _LESS,
        "Operating cash flow divided by scheduled debt service.",
        "Weakly comparable: scheduled maturities are disclosed in footnotes with "
        "inconsistent tagging, so most values require review.",
        numerator="operating_cash_flow",
        denominator="scheduled_debt_service",
        xbrl=("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",),
        manual=True,
    ),
    _v(
        "interest_coverage",
        "Interest coverage",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _LESS,
        "Operating income divided by interest expense.",
        "Directly comparable; capitalised interest during heavy construction flatters "
        "the ratio and should be noted where material.",
        numerator="operating_income",
        denominator="interest_expense",
        xbrl=("OperatingIncomeLoss", "InterestExpense"),
    ),
    _v(
        "near_term_maturities_to_liquidity",
        "Maturities within 24 months to liquid resources",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _MORE,
        "Debt maturing within 24 months divided by cash and short-term investments.",
        "Comparable where maturity schedules are tagged; otherwise reviewed from the "
        "debt footnote.",
        numerator="debt_due_within_24_months",
        denominator="liquid_resources",
        xbrl=(
            "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
            "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
            "CashCashEquivalentsAndShortTermInvestments",
        ),
    ),
    _v(
        "floating_rate_debt_share",
        "Floating-rate debt share",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _MORE,
        "Share of debt priced off a floating benchmark.",
        "Not tagged in XBRL; requires review of the debt footnote in every episode.",
        numerator="floating_rate_debt",
        denominator="total_debt",
        manual=True,
    ),
    _v(
        "guarantees_to_equity",
        "Guarantees to equity",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _MORE,
        "Disclosed guarantee and residual-value exposure divided by shareholders' equity.",
        "Comparable in principle; guarantee disclosure is narrative in most filings and "
        "must be reviewed rather than extracted.",
        numerator="guarantee_exposure",
        denominator="stockholders_equity",
        xbrl=("GuaranteeObligationsMaximumExposure", "StockholdersEquity"),
    ),
    _v(
        "liquidity_runway_months",
        "Liquidity runway",
        _R.VULNERABILITY,
        _ENTITY,
        "months",
        _LESS,
        "Months of committed spending covered by liquid resources.",
        "Comparable where both liquidity and a spending run-rate are available.",
        numerator="liquid_resources",
        denominator="monthly_committed_spending",
        xbrl=("CashCashEquivalentsAndShortTermInvestments",),
    ),
    _v(
        "covenant_headroom",
        "Covenant headroom",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _LESS,
        "Distance between a tested covenant ratio and its limit.",
        "Only available where credit agreements are filed as exhibits; absent for most "
        "investment-grade issuers, which is itself informative.",
        numerator="covenant_actual",
        denominator="covenant_limit",
        manual=True,
    ),
    _v(
        "customer_concentration",
        "Customer concentration",
        _R.VULNERABILITY,
        _ENTITY,
        "percent",
        _MORE,
        "Share of revenue from the largest disclosed customers.",
        "Comparable where the 10 percent disclosure threshold is triggered; absence of "
        "disclosure means below threshold, not zero, and is recorded as such.",
        numerator="largest_customer_revenue",
        denominator="external_revenue",
        xbrl=("ConcentrationRiskPercentage1",),
    ),
    _v(
        "funding_counterparty_concentration",
        "Supplier and funding-counterparty concentration",
        _R.VULNERABILITY,
        _NET,
        "percent",
        _MORE,
        "Share of supply or funding provided by the largest counterparties.",
        "Network measure derived from the relationship graph; comparable only where the "
        "graph has similar coverage across episodes.",
        numerator="largest_counterparty_exposure",
        denominator="total_counterparty_exposure",
        manual=True,
    ),
    _v(
        "circular_related_party_financing",
        "Circular and related-party financing",
        _R.VULNERABILITY,
        _NET,
        "currency",
        _MORE,
        "Capital flowing from a supplier or investor to a counterparty that buys from it.",
        "The defining novelty of the current cycle. Historical analogues exist (vendor "
        "financing in telecom, captive finance in autos) but are not tagged anywhere and "
        "must be reviewed from filings case by case.",
        manual=True,
    ),
    _v(
        "collateral_residual_value_assumption",
        "Collateral and residual-value assumptions",
        _R.VULNERABILITY,
        _ENTITY,
        "years",
        _MORE,
        "Assumed useful life or residual value underpinning collateral on financed assets.",
        "Comparable as a disclosed depreciation life; extensions raise reported earnings "
        "and lower apparent leverage without any economic change.",
        xbrl=("PropertyPlantAndEquipmentUsefulLife",),
    ),
    _v(
        "deposit_funding_share",
        "Deposit funding share",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _MORE,
        "Deposits divided by total assets.",
        "Bank-specific. A high share is ordinarily a sign of cheap stable funding; it "
        "becomes a vulnerability when the deposits are concentrated, uninsured and able "
        "to leave in a day, which is why it is read alongside equity_to_assets rather "
        "than alone.",
        numerator="deposits",
        denominator="total_assets",
        xbrl=("Deposits", "Assets"),
    ),
    _v(
        "equity_to_assets",
        "Equity to assets",
        _R.VULNERABILITY,
        _ENTITY,
        "ratio",
        _LESS,
        "Shareholders' equity divided by total assets: the loss-absorbing layer.",
        "Bank-specific and directly comparable. Book equity excludes unrealised "
        "held-to-maturity losses, so it overstates the buffer precisely when rates have "
        "risen, which is the failure mode this stratum exists to capture.",
        numerator="stockholders_equity",
        denominator="total_assets",
        xbrl=("StockholdersEquity", "Assets"),
    ),
    _v(
        "accumulated_other_comprehensive_income",
        "Accumulated other comprehensive income",
        _R.VULNERABILITY,
        _ENTITY,
        "currency",
        _LESS,
        "Accumulated other comprehensive income net of tax: unrealised marks carried in "
        "equity rather than through earnings.",
        "This is the whole AOCI balance, not an unrealised-securities-loss line. It "
        "aggregates available-for-sale marks with currency translation, pension and "
        "cash-flow-hedge adjustments, and no filer in this benchmark tags the "
        "securities-only component, so it must not be read as a pure securities loss. "
        "The direction is deliberate: AOCI is signed, a more negative balance is a larger "
        "unrecognised hole in the capital base, so a higher reading means less pressure. "
        "It sits in vulnerability rather than activated stress because it records a loss "
        "already absorbed into equity, not one currently forcing action.",
        xbrl=("AccumulatedOtherComprehensiveIncomeLossNetOfTax",),
    ),
    _v(
        "off_balance_sheet_exposure",
        "Private-credit, SPV and off-balance-sheet exposure",
        _R.VULNERABILITY,
        _ENTITY,
        "currency",
        _MORE,
        "Maximum exposure to loss from unconsolidated vehicles and project financings.",
        "Tagged inconsistently; the variable-interest-entity maximum-exposure disclosure "
        "is the most comparable anchor where present.",
        xbrl=("VariableInterestEntityNonconsolidatedCarryingAmountAssets",),
    ),
)

_SHOCK: tuple[BenchmarkVariable, ...] = (
    _v(
        "policy_rate_change",
        "Policy rate change",
        _R.SHOCK,
        _MKT,
        "percent",
        _MORE,
        "Change in the central bank policy rate over a trailing window.",
        "Directly comparable across every episode.",
        controls=("policy_rate",),
        views=(DerivedView.LEVEL, DerivedView.VELOCITY),
    ),
    _v(
        "demand_disappointment",
        "Demand disappointment",
        _R.SHOCK,
        _ECO,
        "percent",
        _MORE,
        "Shortfall of realised demand against the demand implied by committed capacity.",
        "Comparable only where a capacity plan was publicly quantified; otherwise unknown.",
        numerator="realised_demand",
        denominator="implied_committed_demand",
        manual=True,
    ),
    _v(
        "input_price_shock",
        "Commodity or input-price shock",
        _R.SHOCK,
        _MKT,
        "percent",
        _MORE,
        "Move in the dominant input price for the episode's sector.",
        "Episode-specific by construction: oil for shale, memory and power for AI. The "
        "series differs by episode and comparisons are made on normalised moves.",
        controls=("oil_price", "electricity_price"),
        views=(DerivedView.LEVEL, DerivedView.VELOCITY),
    ),
    _v(
        "regulatory_trade_shock",
        "Regulatory or trade-policy shock",
        _R.SHOCK,
        _MKT,
        "score",
        _MORE,
        "Discrete policy actions materially changing the economics of committed capital.",
        "Event-coded from primary government sources; counts are comparable, severity is "
        "not, and severity is therefore not scored.",
        manual=True,
    ),
    _v(
        "technological_obsolescence",
        "Technological obsolescence",
        _R.SHOCK,
        _MKT,
        "percent",
        _MORE,
        "Rate at which new capability per dollar strands existing installed capacity.",
        "The least historically comparable variable in the catalog. Telecom fibre and "
        "shale well productivity are the closest analogues and neither maps cleanly.",
        manual=True,
    ),
    _v(
        "asset_collateral_shock",
        "Asset or collateral value shock",
        _R.SHOCK,
        _MKT,
        "percent",
        _MORE,
        "Decline in the market value of assets pledged against financing.",
        "Comparable where a public price exists for the collateral class; for GPUs and "
        "similar equipment no public index exists and the value stays unknown.",
        manual=True,
    ),
    _v(
        "funding_market_disruption",
        "Funding-market disruption",
        _R.SHOCK,
        _MKT,
        "basis_points",
        _MORE,
        "Widening in the cost or availability of new funding for the sector.",
        "Directly comparable through public spread series.",
        controls=("corporate_bond_spread", "yield_curve_slope"),
        views=(DerivedView.LEVEL, DerivedView.VELOCITY),
    ),
)

_TRANSMISSION: tuple[BenchmarkVariable, ...] = (
    _v(
        "bank_exposure",
        "Bank exposure",
        _R.TRANSMISSION,
        _MKT,
        "currency",
        _MORE,
        "Bank lending to the episode's sector.",
        "Comparable through supervisory aggregates; entity-level bank exposure to a "
        "named sector is rarely disclosed.",
        controls=("commercial_industrial_loans",),
    ),
    _v(
        "insurer_exposure",
        "Insurer exposure",
        _R.TRANSMISSION,
        _MKT,
        "currency",
        _MORE,
        "Insurance-company holdings of the sector's credit.",
        "Statutory filings are not machine-readable from the sources available here; "
        "recorded as reviewed evidence or left unknown.",
        manual=True,
    ),
    _v(
        "private_credit_exposure",
        "BDC and private-credit exposure",
        _R.TRANSMISSION,
        _MKT,
        "currency",
        _MORE,
        "Business-development-company and private-credit-fund exposure to the sector.",
        "Only meaningful from roughly 2012 onward; the asset class was immaterial during "
        "the dot-com episode, which is a true structural difference rather than a gap.",
        xbrl=("InvestmentOwnedAtFairValue",),
    ),
    _v(
        "leveraged_fund_exposure",
        "Bondholder and leveraged-fund exposure",
        _R.TRANSMISSION,
        _MKT,
        "currency",
        _MORE,
        "Holdings of the sector's debt by mutual funds, ETFs and leveraged vehicles.",
        "Fund holdings became machine-readable with N-PORT in 2019; earlier episodes rely "
        "on aggregate flow-of-funds data at lower resolution.",
        manual=True,
    ),
    _v(
        "counterparty_network_exposure",
        "Counterparty and ownership network exposure",
        _R.TRANSMISSION,
        _NET,
        "score",
        _MORE,
        "Concentration of the ownership and obligation graph around a few nodes.",
        "Graph-derived; comparable only where the graph is built to the same standard "
        "for each episode, which is not yet true.",
        manual=True,
    ),
    _v(
        "index_and_passive_exposure",
        "Index, ETF and mutual-fund exposure",
        _R.TRANSMISSION,
        _MKT,
        "percent",
        _MORE,
        "Weight of the episode's sector in broad market indexes and passive products.",
        "Comparable as an index weight; the passive share of the market has itself grown "
        "enormously since 2000, so an identical weight transmits more today.",
        controls=("equity_index_concentration",),
    ),
    _v(
        "retirement_household_exposure",
        "Pension, target-date and household exposure",
        _R.TRANSMISSION,
        _MKT,
        "percent",
        _MORE,
        "Look-through exposure reaching retirement accounts and household portfolios.",
        "The end of the transmission chain and the hardest to measure. Requires "
        "fund-level look-through; never inferred from institutional filings alone.",
        manual=True,
    ),
    _v(
        "forced_selling_transmission",
        "Forced selling and credit contraction",
        _R.TRANSMISSION,
        _MKT,
        "score",
        _MORE,
        "Evidence that losses forced asset sales or a withdrawal of credit elsewhere.",
        "Event-coded from primary sources; presence is comparable, magnitude is not.",
        manual=True,
    ),
)

_ACTIVATED: tuple[BenchmarkVariable, ...] = (
    _v(
        "credit_spread_level",
        "Credit spreads",
        _R.ACTIVATED_STRESS,
        _MKT,
        "basis_points",
        _MORE,
        "Option-adjusted spread on the relevant credit index.",
        "Measured through Moody's Baa-over-Treasury, which is market-priced and final on "
        "publication, so it is comparable and point-in-time across every episode. It is a "
        "bond-yield spread, not an option-adjusted spread; the OAS indices are licensed.",
        controls=("corporate_bond_spread", "high_grade_bond_spread"),
    ),
    _v(
        "issuer_spread_change",
        "Issuer spread change",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "basis_points",
        _MORE,
        "Change in an individual issuer's spread against a rating-matched benchmark.",
        "Requires issuer-level bond pricing, which is not freely available; recorded from "
        "primary disclosure where an issuer discusses its own cost of funds.",
        manual=True,
    ),
    _v(
        "rating_downgrade",
        "Rating downgrades",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "count",
        _MORE,
        "Rating actions lowering an issuer's credit rating or outlook.",
        "Comparable as counts from public rating-agency releases and issuer filings.",
        manual=True,
    ),
    _v(
        "refinancing_failure",
        "Refinancing failures",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "count",
        _MORE,
        "Failed, withdrawn or materially repriced refinancings.",
        "Comparable as counts; requires review of filings and offering documents.",
        manual=True,
    ),
    _v(
        "distressed_exchange",
        "Distressed exchanges",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "count",
        _MORE,
        "Debt exchanges at below par imposing a loss on lenders.",
        "Comparable as counts from filings and rating-agency definitions.",
        manual=True,
    ),
    _v(
        "default_rate",
        "Defaults and restructurings",
        _R.ACTIVATED_STRESS,
        _MKT,
        "percent",
        _MORE,
        "Speculative-grade default rate for the period.",
        "Directly comparable across all episodes through public series.",
        controls=("speculative_default_rate",),
    ),
    _v(
        "emergency_financing",
        "Emergency financing",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "currency",
        _MORE,
        "Financing raised on terms materially worse than the issuer's prior cost.",
        "Comparable where terms are disclosed; judgement is required on what counts as "
        "emergency, so every instance is reviewed.",
        manual=True,
    ),
    _v(
        "covenant_amendment",
        "Covenant amendments",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "count",
        _MORE,
        "Waivers and amendments loosening a financial covenant.",
        "Comparable as counts from filed credit-agreement amendments.",
        manual=True,
    ),
    _v(
        "impairment",
        "Impairments",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "currency",
        _MORE,
        "Write-downs of goodwill, intangible or long-lived assets.",
        "Directly comparable and well tagged across the XBRL era.",
        xbrl=(
            "GoodwillImpairmentLoss",
            "ImpairmentOfLongLivedAssetsHeldAndUsed",
            "AssetImpairmentCharges",
        ),
    ),
    _v(
        "collateral_markdown",
        "Collateral markdowns",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "percent",
        _MORE,
        "Reduction in the carrying value of pledged collateral.",
        "Comparable where lenders or borrowers disclose it; usually absent.",
        manual=True,
    ),
    _v(
        "forced_asset_sale",
        "Forced sales",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "count",
        _MORE,
        "Asset disposals undertaken to meet obligations rather than for strategy.",
        "Comparable as counts; intent must be reviewed, never inferred from the sale.",
        manual=True,
    ),
    _v(
        "project_cancellation",
        "Project cancellations",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "count",
        _MORE,
        "Announced capacity that was cancelled, deferred or written off.",
        "Comparable as counts from primary announcements and filings.",
        manual=True,
    ),
    _v(
        "revolver_utilisation",
        "Revolver and liquidity-facility usage",
        _R.ACTIVATED_STRESS,
        _ENTITY,
        "ratio",
        _MORE,
        "Drawn share of committed revolving credit facilities.",
        "Comparable where both drawn and committed amounts are disclosed.",
        numerator="revolver_drawn",
        denominator="revolver_committed",
        xbrl=(
            "LineOfCreditFacilityAmountOutstanding",
            "LineOfCreditFacilityMaximumBorrowingCapacity",
        ),
    ),
)

_REAL_ECONOMY: tuple[BenchmarkVariable, ...] = (
    _v(
        "unemployment_change",
        "Unemployment change",
        _R.REAL_ECONOMY,
        _MKT,
        "percent",
        _MORE,
        "Change in the unemployment rate over a trailing window.",
        "Directly comparable; must be read from the vintage available at the time, since "
        "the series is revised.",
        controls=("unemployment_rate",),
        views=(DerivedView.LEVEL, DerivedView.VELOCITY),
    ),
    _v(
        "investment_contraction",
        "Investment contraction",
        _R.REAL_ECONOMY,
        _MKT,
        "percent",
        _MORE,
        "Decline in private fixed investment.",
        "Directly comparable; heavily revised, so vintage matters.",
        controls=("private_fixed_investment",),
        views=(DerivedView.LEVEL, DerivedView.VELOCITY),
    ),
    _v(
        "supplier_failure",
        "Supplier failures",
        _R.REAL_ECONOMY,
        _NET,
        "count",
        _MORE,
        "Insolvencies among the sector's suppliers.",
        "Comparable as counts from court and filing records.",
        manual=True,
    ),
    _v(
        "credit_contraction",
        "Commercial and industrial credit contraction",
        _R.REAL_ECONOMY,
        _MKT,
        "percent",
        _MORE,
        "Decline in commercial and industrial lending.",
        "Directly comparable through weekly supervisory aggregates.",
        controls=("commercial_industrial_loans",),
        views=(DerivedView.LEVEL, DerivedView.VELOCITY),
    ),
    _v(
        "household_wealth_effect",
        "Consumption and household wealth effects",
        _R.REAL_ECONOMY,
        _MKT,
        "percent",
        _MORE,
        "Change in household net worth or consumption attributable to the episode.",
        "Attribution to a single sector is not identified in the data; recorded as "
        "context and never as a causal claim.",
        controls=("real_personal_consumption",),
        manual=True,
    ),
    _v(
        "bank_losses",
        "Bank losses",
        _R.REAL_ECONOMY,
        _MKT,
        "currency",
        _MORE,
        "Charge-offs and failures among lenders exposed to the sector.",
        "Comparable through supervisory data; sector attribution requires review.",
        controls=("charge_off_rate",),
    ),
    _v(
        "real_gdp_growth",
        "Real GDP growth",
        _R.REAL_ECONOMY,
        _MKT,
        "percent",
        _LESS,
        "Growth in real gross domestic product.",
        "Directly comparable; substantially revised, so the vintage available at the time "
        "is the only honest reading for a backtest.",
        controls=("real_gdp",),
        views=(DerivedView.LEVEL, DerivedView.VELOCITY),
    ),
)

_RESILIENCE: tuple[BenchmarkVariable, ...] = (
    _v(
        "resilient_liquidity_runway",
        "Liquidity runway (resilience view)",
        _R.RESILIENCE,
        _ENTITY,
        "months",
        _LESS,
        "Months of committed spending covered without new external funding.",
        "Same measurement as the vulnerability view, read in the opposite direction; "
        "carried separately so resilience cannot be silently netted away.",
        numerator="liquid_resources",
        denominator="monthly_committed_spending",
        xbrl=("CashCashEquivalentsAndShortTermInvestments",),
    ),
    _v(
        "deleveraging",
        "Deleveraging",
        _R.RESILIENCE,
        _ENTITY,
        "currency",
        _LESS,
        "Net reduction in total debt over the period.",
        "Directly comparable.",
        xbrl=("RepaymentsOfLongTermDebt", "ProceedsFromIssuanceOfLongTermDebt"),
    ),
    _v(
        "external_cash_generation",
        "External cash generation",
        _R.RESILIENCE,
        _ENTITY,
        "currency",
        _LESS,
        "Operating cash flow from customers outside the financing network.",
        "Reported operating cash flow is comparable; the external-only adjustment is "
        "reviewed rather than extracted.",
        xbrl=("NetCashProvidedByUsedInOperatingActivities",),
    ),
    _v(
        "ordinary_refinancing",
        "Refinancing on ordinary terms",
        _R.RESILIENCE,
        _ENTITY,
        "count",
        _LESS,
        "Refinancings completed at or inside the issuer's prior cost of funds.",
        "Comparable as counts; the counterpart to refinancing failure and required so "
        "that only failures are not counted.",
        manual=True,
    ),
    _v(
        "margin_improvement",
        "Improving margins",
        _R.RESILIENCE,
        _ENTITY,
        "percent",
        _LESS,
        "Change in operating margin.",
        "Directly comparable.",
        numerator="operating_income",
        denominator="external_revenue",
        xbrl=("OperatingIncomeLoss", "Revenues"),
    ),
    _v(
        "counterparty_diversification",
        "Customer and funding diversification",
        _R.RESILIENCE,
        _NET,
        "percent",
        _LESS,
        "Reduction in concentration among customers and funding providers.",
        "Comparable where concentration itself is measured.",
        numerator="largest_counterparty_exposure",
        denominator="total_counterparty_exposure",
        manual=True,
    ),
    _v(
        "guarantee_reduction",
        "Reduced guarantees",
        _R.RESILIENCE,
        _ENTITY,
        "currency",
        _LESS,
        "Decline in outstanding guarantee and backstop exposure.",
        "Comparable where guarantees are disclosed at all.",
        xbrl=("GuaranteeObligationsMaximumExposure",),
    ),
    _v(
        "alternative_asset_use",
        "Alternative profitable uses for infrastructure",
        _R.RESILIENCE,
        _ECO,
        "score",
        _LESS,
        "Evidence that built capacity retains value in a different use.",
        "The decisive difference between fibre in 2001 and warehouses in 2015. Reviewed "
        "qualitatively and never scored numerically.",
        manual=True,
    ),
    _v(
        "shock_absorbed",
        "Shocks absorbed without propagation",
        _R.RESILIENCE,
        _MKT,
        "count",
        _LESS,
        "Adverse events that did not produce measurable onward transmission.",
        "Comparable as counts; the benign episodes exist chiefly to populate this.",
        manual=True,
    ),
)

CATALOG_VERSION = "1.0.0"

BENCHMARK_VARIABLES: dict[str, BenchmarkVariable] = {
    variable.key: variable
    for variable in (
        *_BOOM,
        *_VALIDATION,
        *_VULNERABILITY,
        *_SHOCK,
        *_TRANSMISSION,
        *_ACTIVATED,
        *_REAL_ECONOMY,
        *_RESILIENCE,
    )
}

if len(BENCHMARK_VARIABLES) != sum(
    len(group)
    for group in (
        _BOOM,
        _VALIDATION,
        _VULNERABILITY,
        _SHOCK,
        _TRANSMISSION,
        _ACTIVATED,
        _REAL_ECONOMY,
        _RESILIENCE,
    )
):  # pragma: no cover - guards a duplicate key at import time
    raise ValueError("benchmark variable keys must be unique")


def variables_for_role(role: CausalRole) -> tuple[BenchmarkVariable, ...]:
    return tuple(
        variable for variable in BENCHMARK_VARIABLES.values() if variable.causal_role is role
    )


def machine_derivable_variables() -> tuple[BenchmarkVariable, ...]:
    return tuple(item for item in BENCHMARK_VARIABLES.values() if item.machine_derivable)


def required_control_series() -> tuple[str, ...]:
    series: set[str] = set()
    for variable in BENCHMARK_VARIABLES.values():
        series.update(variable.control_series)
    return tuple(sorted(series))


def required_xbrl_concepts() -> tuple[str, ...]:
    concepts: set[str] = set()
    for variable in BENCHMARK_VARIABLES.values():
        concepts.update(variable.xbrl_concepts)
    return tuple(sorted(concepts))
