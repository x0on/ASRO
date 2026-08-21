from __future__ import annotations

import hashlib

from asro.models import FinancialEvent
from asro.observations import Observation

EVENT_VARIABLE_MAP: dict[str, tuple[str, float, str]] = {
    "ASSUMES_DEBT": ("ai_related_debt", 1.0, "risk"),
    "GUARANTEES": ("vendor_financing", 1.0, "risk"),
    "LENDS_TO": ("ai_related_debt", 1.0, "risk"),
    "ISSUES_DEBT": ("ai_related_debt", 1.0, "risk"),
    "REFINANCES": ("refinancing_stress", 1.0, "risk"),
    "INVESTS_IN": ("ai_capital_commitments", 1.0, "risk"),
    "CAPEX_COMMITMENT": ("ai_capital_commitments", 1.0, "risk"),
    "ENTERS_INDEX": ("public_index_exposure", 1.0, "risk"),
    "ALLOCATES_TO": ("retirement_exposure", 1.0, "risk"),
    "PRICE_CUT": ("model_price_pressure", 1.0, "risk"),
    "DOWNGRADE": ("refinancing_stress", 2.0, "risk"),
    "IMPAIRMENT": ("refinancing_stress", 2.0, "risk"),
    "CANCELS_PROJECT": ("refinancing_stress", 1.5, "risk"),
    "REVENUE_REPORT": ("ai_external_revenue", 1.0, "safety"),
    "FREE_CASH_FLOW": ("free_cash_flow_strength", 1.0, "safety"),
    "MODEL_RELEASE": ("external_capability_pressure", 1.0, "risk"),
    "BENCHMARK_GAIN": ("external_capability_pressure", 1.0, "risk"),
}


def event_to_observation(event: FinancialEvent) -> Observation | None:
    mapped = EVENT_VARIABLE_MAP.get(event.event_type.value)
    if not mapped:
        return None
    variable_key, fallback_value, polarity = mapped
    value = event.amount if event.amount is not None else fallback_value
    unit = event.currency if event.amount is not None else "signal"
    fingerprint = "|".join([event.event_id, variable_key, event.source_entity or "", str(value)])
    return Observation(
        observation_id=hashlib.sha256(fingerprint.encode()).hexdigest(),
        event_id=event.event_id,
        variable_key=variable_key,
        entity=event.source_entity,
        value=float(value),
        unit=unit,
        effective_date=event.effective_date,
        confidence=event.confidence,
        source_document_id=event.document_id,
        evidence_text=event.evidence_text,
        extractor=event.extractor,
        polarity=polarity,
    )
