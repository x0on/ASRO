from __future__ import annotations

from dataclasses import dataclass

from asro.models import EventType


@dataclass(frozen=True)
class EventRule:
    event_type: EventType
    phrases: tuple[str, ...]
    confidence: float
    instrument: str | None = None


RULES: tuple[EventRule, ...] = (
    EventRule(EventType.INVESTS_IN, ("invests in", "invested in", "investment in"), 0.86, "equity"),
    EventRule(EventType.LENDS_TO, ("lends to", "loan to", "credit facility"), 0.82, "debt"),
    EventRule(
        EventType.GUARANTEES, ("guarantees", "guarantee for", "backstops"), 0.88, "guarantee"
    ),
    EventRule(EventType.SUPPLIES, ("supplies", "supplier to", "provides chips to"), 0.78, None),
    EventRule(EventType.PURCHASES_FROM, ("purchases from", "buys from", "orders from"), 0.78, None),
    EventRule(EventType.LEASES_FROM, ("leases from", "lease with", "leasing from"), 0.80, "lease"),
    EventRule(EventType.ALLOCATES_TO, ("allocates to", "allocation to"), 0.80, None),
    EventRule(EventType.REFINANCES, ("refinances", "refinancing"), 0.82, "debt"),
    EventRule(
        EventType.ISSUES_DEBT, ("issues debt", "bond issuance", "issues bonds"), 0.86, "debt"
    ),
    EventRule(
        EventType.FILES_FOR_IPO,
        ("files for ipo", "filed for ipo", "confidentially filed"),
        0.90,
        None,
    ),
    EventRule(
        EventType.ENTERS_INDEX,
        ("enters the nasdaq-100", "joins the nasdaq-100", "enters the s&p 500"),
        0.92,
        None,
    ),
    EventRule(EventType.PRICE_CUT, ("price cut", "cuts prices", "reduced prices"), 0.82, None),
    EventRule(
        EventType.CAPEX_COMMITMENT,
        ("capital expenditure", "capex", "infrastructure commitment"),
        0.76,
        None,
    ),
    EventRule(
        EventType.DOWNGRADE,
        ("downgraded", "credit rating downgrade", "rating lowered"),
        0.88,
        "credit",
    ),
    EventRule(EventType.IMPAIRMENT, ("impairment charge", "write-down", "writedown"), 0.86, None),
    EventRule(
        EventType.CANCELS_PROJECT,
        ("cancels project", "cancelled project", "lease cancellation", "scrapped plans"),
        0.84,
        None,
    ),
    EventRule(
        EventType.REVENUE_REPORT, ("ai revenue", "artificial intelligence revenue"), 0.72, None
    ),
    EventRule(EventType.FREE_CASH_FLOW, ("free cash flow",), 0.80, None),
    EventRule(
        EventType.MODEL_RELEASE,
        ("released model", "launches model", "new frontier model"),
        0.72,
        None,
    ),
    EventRule(EventType.BENCHMARK_GAIN, ("benchmark", "outperforms", "beats"), 0.62, None),
)
