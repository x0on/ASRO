from __future__ import annotations

from asro.dictionary.models import Dimension, VariableDefinition

VARIABLES: dict[str, VariableDefinition] = {
    "ai_capital_commitments": VariableDefinition(
        key="ai_capital_commitments",
        label="AI capital commitments",
        dimension=Dimension.CAPITAL,
        description=(
            "New equity, debt, leases, guarantees, capex and project commitments tied to AI."
        ),
        unit="USD",
        direction="higher_is_riskier",
        weight=1.3,
    ),
    "ai_external_revenue": VariableDefinition(
        key="ai_external_revenue",
        label="External AI revenue",
        dimension=Dimension.MONETIZATION,
        description="Revenue paid by external end customers for AI products and services.",
        unit="USD",
        direction="higher_is_safer",
        weight=1.5,
    ),
    "vendor_financing": VariableDefinition(
        key="vendor_financing",
        label="Vendor financing",
        dimension=Dimension.CIRCULARITY,
        description=(
            "Supplier financing or guarantees supporting customers that buy supplier products."
        ),
        unit="USD",
        direction="higher_is_riskier",
        weight=1.4,
    ),
    "ai_related_debt": VariableDefinition(
        key="ai_related_debt",
        label="AI-related debt",
        dimension=Dimension.FRAGILITY,
        description="Debt materially linked to AI infrastructure or compute expansion.",
        unit="USD",
        direction="higher_is_riskier",
        weight=1.4,
    ),
    "refinancing_stress": VariableDefinition(
        key="refinancing_stress",
        label="Refinancing stress",
        dimension=Dimension.STRESS,
        description=(
            "Failed, delayed, expensive or emergency refinancing linked to AI infrastructure."
        ),
        unit="score",
        direction="higher_is_riskier",
        weight=1.7,
    ),
    "public_index_exposure": VariableDefinition(
        key="public_index_exposure",
        label="Public index exposure",
        dimension=Dimension.TRANSMISSION,
        description="AI-related equity exposure embedded in major indexes and passive products.",
        unit="percent",
        direction="higher_is_riskier",
        weight=1.2,
    ),
    "retirement_exposure": VariableDefinition(
        key="retirement_exposure",
        label="Retirement exposure",
        dimension=Dimension.TRANSMISSION,
        description=(
            "Estimated AI-linked exposure held through pensions, "
            "target-date funds, 401(k)s or insurers."
        ),
        unit="USD",
        direction="higher_is_riskier",
        weight=1.5,
    ),
    "model_price_pressure": VariableDefinition(
        key="model_price_pressure",
        label="Model price pressure",
        dimension=Dimension.CANNIBALIZATION,
        description="Decline in frontier model/API pricing and price-performance.",
        unit="percent",
        direction="higher_is_riskier",
        weight=1.0,
    ),
    "external_capability_pressure": VariableDefinition(
        key="external_capability_pressure",
        label="External capability pressure",
        dimension=Dimension.EXTERNAL_PRESSURE,
        description=(
            "Capability convergence from foreign, open-weight or non-incumbent frontier models."
        ),
        unit="score",
        direction="higher_is_riskier",
        weight=1.3,
    ),
    "external_price_performance_pressure": VariableDefinition(
        key="external_price_performance_pressure",
        label="External price-performance pressure",
        dimension=Dimension.EXTERNAL_PRESSURE,
        description="Pressure from models delivering similar capability at materially lower cost.",
        unit="score",
        direction="higher_is_riskier",
        weight=1.5,
    ),
    "free_cash_flow_strength": VariableDefinition(
        key="free_cash_flow_strength",
        label="Free cash flow strength",
        dimension=Dimension.COUNTER_EVIDENCE,
        description=(
            "Evidence that major AI participants are funding expansion from durable free cash flow."
        ),
        unit="USD",
        direction="higher_is_safer",
        weight=1.5,
    ),
    "deleveraging": VariableDefinition(
        key="deleveraging",
        label="Deleveraging",
        dimension=Dimension.COUNTER_EVIDENCE,
        description=(
            "Reduction in debt, guarantees, vendor support or externally financed AI capex."
        ),
        unit="score",
        direction="higher_is_safer",
        weight=1.2,
    ),
}
