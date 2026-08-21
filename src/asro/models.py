from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


def utcnow() -> datetime:
    return datetime.now(UTC)


class Category(StrEnum):
    CREDIT = "Credit / infrastructure"
    DISTRIBUTION = "Risk distribution"
    IPO = "IPO / public markets"
    CANNIBALIZATION = "Cannibalization / economics"
    GENERAL = "General AI capital"


class EventType(StrEnum):
    ACQUIRES = "ACQUIRES"
    ASSUMES_DEBT = "ASSUMES_DEBT"
    INVESTS_IN = "INVESTS_IN"
    LENDS_TO = "LENDS_TO"
    GUARANTEES = "GUARANTEES"
    SUPPLIES = "SUPPLIES"
    PURCHASES_FROM = "PURCHASES_FROM"
    LEASES_FROM = "LEASES_FROM"
    ALLOCATES_TO = "ALLOCATES_TO"
    REFINANCES = "REFINANCES"
    ISSUES_DEBT = "ISSUES_DEBT"
    FILES_FOR_IPO = "FILES_FOR_IPO"
    ENTERS_INDEX = "ENTERS_INDEX"
    PRICE_CUT = "PRICE_CUT"
    CAPEX_COMMITMENT = "CAPEX_COMMITMENT"
    BENCHMARK_GAIN = "BENCHMARK_GAIN"
    MODEL_RELEASE = "MODEL_RELEASE"
    FREE_CASH_FLOW = "FREE_CASH_FLOW"
    REVENUE_REPORT = "REVENUE_REPORT"
    CANCELS_PROJECT = "CANCELS_PROJECT"
    IMPAIRMENT = "IMPAIRMENT"
    DOWNGRADE = "DOWNGRADE"


class SourceItem(BaseModel):
    title: str
    url: HttpUrl
    source: str
    summary: str = ""
    published_at: str | None = None
    discovered_at: datetime = Field(default_factory=utcnow)


class ScoredItem(SourceItem):
    item_id: str
    score: int
    category: Category
    companies: list[str] = Field(default_factory=list)


class FinancialEvent(BaseModel):
    event_id: str
    document_id: str
    event_type: EventType
    source_entity: str | None = None
    target_entity: str | None = None
    amount: float | None = None
    currency: str | None = None
    instrument: str | None = None
    effective_date: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_text: str
    extractor: str
    processed_at: datetime = Field(default_factory=utcnow)
