from __future__ import annotations

import hashlib
import re

from asro.dictionary.registry import VARIABLES
from asro.indicators import _parse
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
    # These are stages in a broader transmission channel, not 0/100 verdicts.
    # 0.5 = filing (potential exposure); 1 = trading; 2 = index distribution.
    "FILES_FOR_IPO": ("public_market_transmission_stage", 0.5, "risk"),
    # Higher stages require measured index weight and retirement-fund exposure.
    "COMPLETES_IPO": ("public_market_transmission_stage", 1.0, "risk"),
    "ENTERS_INDEX": ("public_market_transmission_stage", 2.0, "risk"),
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
    if event.event_type.value == "FILES_FOR_IPO" and not filing_issuer_matches(
        event.source_entity, event.evidence_text
    ):
        # A backer named in a filing headline is not necessarily the issuer.
        return None
    mapped = EVENT_VARIABLE_MAP.get(event.event_type.value)
    if not mapped:
        return None
    variable_key, fallback_value, polarity = mapped
    definition = VARIABLES[variable_key]
    value: float
    unit: str | None
    if definition.unit == "USD":
        if event.amount is None or event.currency != "USD":
            value, unit = fallback_value, "signal"
        else:
            value, unit = event.amount, "USD"
    elif definition.unit == "percent":
        # Preserve a confirmed qualitative direction without pretending the keyword
        # hit is a measured percentage. Numeric scoring rejects this signal unit.
        value, unit = fallback_value, "signal"
    else:
        # Score variables describe event severity; a transaction's dollar amount is
        # not itself a 0-100 stress or capability score.
        value, unit = fallback_value, definition.unit
    fingerprint = "|".join([event.event_id, variable_key, event.source_entity or "", str(value)])
    return Observation(
        observation_id=hashlib.sha256(fingerprint.encode()).hexdigest(),
        event_id=event.event_id,
        variable_key=variable_key,
        entity=event.source_entity,
        value=float(value),
        unit=unit,
        effective_date=(
            parsed.isoformat() if (parsed := _parse(event.effective_date)) else event.effective_date
        ),
        confidence=event.confidence,
        source_document_id=event.document_id,
        evidence_text=event.evidence_text,
        extractor=event.extractor,
        polarity=polarity,
    )


def filing_issuer_matches(entity: str | None, text: str) -> bool:
    """Conservative attribution guard; uncertainty never becomes an issuer measurement."""
    return bool(
        entity
        and re.search(
            rf"\b{re.escape(entity)}\s+(?:(?:has|have|confidentially)\s+)*"
            r"(?:files?|filed)\s+(?:for\s+(?:an?\s+)?(?:IPO|initial public offering)"
            r"|(?:an?\s+)?(?:confidential\s+)?draft\s+S-1)",
            text,
            re.IGNORECASE,
        )
    )
