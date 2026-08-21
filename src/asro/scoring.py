from __future__ import annotations

import hashlib

from asro.entities import canonicalize_many
from asro.models import Category, ScoredItem, SourceItem

HIGH_SIGNAL_TERMS: dict[str, int] = {
    "default": 6,
    "distress": 6,
    "refinancing": 5,
    "credit spread": 5,
    "downgrade": 5,
    "impairment": 5,
    "write-down": 5,
    "writedown": 5,
    "covenant": 4,
    "private credit": 4,
    "secured debt": 4,
    "syndicated loan": 4,
    "bond issuance": 3,
    "guarantee": 4,
    "vendor financing": 5,
    "special purpose vehicle": 4,
    "spv": 3,
    "gpu-backed": 5,
    "401(k)": 5,
    "pension": 4,
    "target-date": 5,
    "retirement": 4,
    "index inclusion": 5,
    "nasdaq-100": 4,
    "s&p 500": 4,
    "ipo": 3,
    "price cut": 4,
    "margin compression": 5,
    "cannibal": 5,
    "overcapacity": 5,
    "utilization": 3,
    "lease cancellation": 6,
    "capex": 2,
    "free cash flow": 3,
}

STRESS_TERMS = (
    "default",
    "distress",
    "downgrade",
    "impairment",
    "write-down",
    "writedown",
    "covenant breach",
    "canceled",
    "cancelled",
    "overcapacity",
    "margin compression",
    "refinancing risk",
    "credit spreads widened",
    "lease cancellation",
)


def stable_id(item: SourceItem) -> str:
    raw = f"{item.url}|{item.title}".encode()
    return hashlib.sha256(raw).hexdigest()


def classify(text: str) -> Category:
    value = text.lower()

    if any(
        term in value
        for term in (
            "401(k)",
            "pension",
            "retirement",
            "target-date",
            "index inclusion",
            "nasdaq-100",
            "s&p 500",
        )
    ):
        return Category.DISTRIBUTION

    if any(
        term in value
        for term in (
            "debt",
            "loan",
            "credit",
            "refinancing",
            "bond",
            "guarantee",
            "gpu-backed",
            "private credit",
        )
    ):
        return Category.CREDIT

    if any(term in value for term in ("ipo", "public offering", "listing")):
        return Category.IPO

    if any(
        term in value
        for term in (
            "price cut",
            "margin",
            "cannibal",
            "overcapacity",
            "utilization",
            "inference cost",
        )
    ):
        return Category.CANNIBALIZATION

    return Category.GENERAL


def score(source_item: SourceItem, companies: list[str]) -> ScoredItem:
    text = f"{source_item.title} {source_item.summary}".lower()

    result = 0
    for term, weight in HIGH_SIGNAL_TERMS.items():
        if term.lower() in text:
            result += weight

    matched_companies = canonicalize_many(
        sorted(company for company in companies if company.lower() in text)
    )
    result += len(matched_companies)

    if any(term in text for term in STRESS_TERMS):
        result += 4

    return ScoredItem(
        **source_item.model_dump(),
        item_id=stable_id(source_item),
        score=result,
        category=classify(text),
        companies=matched_companies,
    )
