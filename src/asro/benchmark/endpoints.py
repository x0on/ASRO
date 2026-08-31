"""Explicit definitions of the ASRO 0 and 100 endpoints.

The indicator is bounded, so both ends must mean something specific. These are the
definitions the readiness gate points at, and the ones any published reading must be
interpretable against.

ASRO-100 is emphatically not a 100 percent probability of collapse. It is the maximum
*empirically supported* convergence toward the monitored mechanism: the configuration in
which every stage of the chain is simultaneously evidenced. A system can sit at a high
reading and not break.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from asro.benchmark.catalog import CausalRole


class EndpointCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    causal_role: CausalRole
    condition: str
    evidence_requirement: str


class EndpointDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: int
    name: str
    summary: str
    conditions: tuple[EndpointCondition, ...]
    explicit_non_claims: tuple[str, ...]

    def roles_covered(self) -> frozenset[CausalRole]:
        return frozenset(item.causal_role for item in self.conditions)


ASRO_ZERO = EndpointDefinition(
    value=0,
    name="Empirically resilient configuration",
    summary=(
        "Investment is supported by sustainable external demand, obligations are readily "
        "serviceable, and adverse events are absorbed without onward propagation."
    ),
    conditions=(
        EndpointCondition(
            causal_role=CausalRole.BOOM,
            condition="Capital deployment grows no faster than the cash generation funding it.",
            evidence_requirement=(
                "capex_to_revenue and capital_growth_rate within their own historical "
                "distribution and not accelerating"
            ),
        ),
        EndpointCondition(
            causal_role=CausalRole.VALIDATION,
            condition="External customer demand covers the obligations being created.",
            evidence_requirement=(
                "fixed_obligations_to_external_cash low and revenue_minus_obligation_growth "
                "positive across consecutive periods"
            ),
        ),
        EndpointCondition(
            causal_role=CausalRole.VULNERABILITY,
            condition="Leverage is low or falling, maturities are manageable, liquidity is strong.",
            evidence_requirement=(
                "debt_to_operating_cash_flow low, near_term_maturities_to_liquidity below one, "
                "liquidity_runway_months ample"
            ),
        ),
        EndpointCondition(
            causal_role=CausalRole.SHOCK,
            condition="No adverse shock is materially impairing expected cash flows or collateral.",
            evidence_requirement="no active shock variable outside its historical range",
        ),
        EndpointCondition(
            causal_role=CausalRole.TRANSMISSION,
            condition="Exposure is contained rather than distributed through ordinary portfolios.",
            evidence_requirement=(
                "index_and_passive_exposure and retirement_household_exposure measured and low"
            ),
        ),
        EndpointCondition(
            causal_role=CausalRole.ACTIVATED_STRESS,
            condition="No refinancing stress, downgrades, defaults, impairments or forced sales.",
            evidence_requirement="activated-stress counts at or near zero with adequate coverage",
        ),
        EndpointCondition(
            causal_role=CausalRole.REAL_ECONOMY,
            condition="No measurable deterioration in credit, investment, employment or output.",
            evidence_requirement="real-economy controls within their normal ranges",
        ),
        EndpointCondition(
            causal_role=CausalRole.RESILIENCE,
            condition="Counter-evidence is strong: cash generation, deleveraging, diversification.",
            evidence_requirement="resilience variables measured and improving",
        ),
    ),
    explicit_non_claims=(
        "A zero reading is not a prediction that nothing can go wrong.",
        "A zero reading is not a statement that the sector is correctly valued.",
    ),
)

ASRO_HUNDRED = EndpointDefinition(
    value=100,
    name="Maximum empirically supported convergence",
    summary=(
        "Every stage of the monitored mechanism is simultaneously evidenced: capital has "
        "outrun validation, obligations are extreme, a shock is biting, exposure has "
        "reached ordinary portfolios, stress is activating, and the real economy is "
        "deteriorating, with weak counter-evidence."
    ),
    conditions=(
        EndpointCondition(
            causal_role=CausalRole.BOOM,
            condition="Capital deployment substantially outruns economic validation.",
            evidence_requirement=(
                "capex_to_revenue and investment_acceleration at the extreme of the "
                "pooled historical distribution"
            ),
        ),
        EndpointCondition(
            causal_role=CausalRole.VALIDATION,
            condition="Fixed obligations exceed any plausible external cash generation.",
            evidence_requirement=(
                "fixed_obligations_to_external_cash extreme and "
                "revenue_minus_obligation_growth persistently negative"
            ),
        ),
        EndpointCondition(
            causal_role=CausalRole.VULNERABILITY,
            condition=(
                "Leverage is extreme, refinancing walls are near, liquidity is weak, "
                "counterparties and funding are concentrated, guarantees and circular "
                "financing are widespread."
            ),
            evidence_requirement=(
                "vulnerability variables jointly extreme, including at least one "
                "concentration and one off-balance-sheet measure"
            ),
        ),
        EndpointCondition(
            causal_role=CausalRole.SHOCK,
            condition="An adverse shock is materially affecting expected cash flows or collateral.",
            evidence_requirement="at least one shock variable active and outside its range",
        ),
        EndpointCondition(
            causal_role=CausalRole.TRANSMISSION,
            condition="Exposure is distributed through institutions and ordinary portfolios.",
            evidence_requirement=(
                "measured index, fund and retirement look-through, not inferred from "
                "institutional manager filings"
            ),
        ),
        EndpointCondition(
            causal_role=CausalRole.ACTIVATED_STRESS,
            condition="Refinancing stress, downgrades, defaults, impairments or forced sales.",
            evidence_requirement="multiple independent activated-stress variables non-zero",
        ),
        EndpointCondition(
            causal_role=CausalRole.REAL_ECONOMY,
            condition="Credit, investment, employment, consumption or output are deteriorating.",
            evidence_requirement="real-economy controls deteriorating beyond normal variation",
        ),
        EndpointCondition(
            causal_role=CausalRole.RESILIENCE,
            condition="Resilience and counter-evidence are weak.",
            evidence_requirement="resilience variables measured and at the low end of their range",
        ),
    ),
    explicit_non_claims=(
        "A hundred reading is not a 100 percent probability of collapse.",
        "A hundred reading is not a forecast of timing.",
        "A hundred reading is a statement about evidenced configuration, not about outcome.",
    ),
)

ENDPOINTS: tuple[EndpointDefinition, ...] = (ASRO_ZERO, ASRO_HUNDRED)
