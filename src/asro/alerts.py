"""Auditable news alerts, independent of numeric measurement coverage."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from asro.indicators import _parse

RULE_VERSION = "news-alerts-v1"
# Classification describes the reported mechanism, not an estimated crash likelihood.
RULES = {
    "FILES_FOR_IPO": (
        "public_exposure",
        "TRANSMISSION",
        "A filing opens a potential route to public investors; trading has not been established.",
    ),
    "COMPLETES_IPO": (
        "public_exposure",
        "TRANSMISSION",
        "Public trading makes exposure available to public investors.",
    ),
    "ENTERS_INDEX": (
        "public_exposure",
        "TRANSMISSION",
        "Index inclusion can distribute exposure through funds tracking that index.",
    ),
    "ALLOCATES_TO": (
        "public_exposure",
        "TRANSMISSION",
        "An allocation creates exposure; consult the source for whose money is involved.",
    ),
    "ISSUES_DEBT": (
        "pressure",
        "VULNERABILITY",
        "Debt adds repayment and refinancing obligations.",
    ),
    "ASSUMES_DEBT": ("pressure", "VULNERABILITY", "Assumed debt adds repayment obligations."),
    "LENDS_TO": (
        "pressure",
        "TRANSMISSION",
        "Lending connects the lender to the borrower's ability to repay.",
    ),
    "GUARANTEES": (
        "pressure",
        "VULNERABILITY",
        "A guarantee can transfer another company's losses to the guarantor.",
    ),
    "CAPEX_COMMITMENT": (
        "pressure",
        "BOOM",
        "Spending commitments increase capital dependent on future demand, not proof of distress.",
    ),
    "INVESTS_IN": (
        "pressure",
        "BOOM",
        "An investment increases capital exposed to the business succeeding.",
    ),
    "DOWNGRADE": (
        "pressure",
        "STRESS",
        "A downgrade signals deterioration in assessed credit quality.",
    ),
    "IMPAIRMENT": ("pressure", "STRESS", "An impairment recognizes a reduction in asset value."),
    "CANCELS_PROJECT": (
        "pressure",
        "STRESS",
        "Cancellation can signal retrenchment or weaker demand; consult the stated reason.",
    ),
    "PRICE_CUT": (
        "pressure",
        "VULNERABILITY",
        "Lower prices can pressure margins; volume and cost effects need separate evidence.",
    ),
    "REFINANCES": (
        "context",
        "VULNERABILITY",
        "Refinancing changes terms; the event alone establishes neither improvement nor distress.",
    ),
    "REVENUE_REPORT": (
        "context",
        "VALIDATION",
        "Revenue provides demand evidence; a report alone establishes neither growth nor profit.",
    ),
    "FREE_CASH_FLOW": (
        "context",
        "RESILIENCE",
        "Cash flow informs self-funding capacity; its sign and trend must be read from the source.",
    ),
}


def news_alerts(events: list[dict[str, Any]], as_of: datetime) -> dict[str, Any]:
    """One alert per canonical fact, with source and review knowability preserved."""
    moment = as_of.astimezone(UTC)
    selected: dict[str, dict[str, Any]] = {}
    for event in events:
        rule = RULES.get(str(event.get("event_type")))
        if rule is None or event.get("review_status") != "confirmed":
            continue
        try:
            url = urlsplit(str(event.get("url") or ""))
            if url.scheme not in {"http", "https"} or not url.hostname or url.username:
                continue
        except ValueError:
            continue
        published = _parse(event.get("published_at"))
        occurred = _parse(event.get("effective_date")) or published
        reviewed = _parse(event.get("reviewed_at"))
        if occurred is None or not moment - timedelta(days=90) <= occurred <= moment:
            continue
        if reviewed is None or reviewed > moment or (published and published > moment):
            continue
        identity = str(event.get("fingerprint") or event.get("event_id") or "")
        if not identity:
            continue
        # A filing often has several canonical IDs because articles name its backers.
        # Group explicit headline subjects, never infer issuer from the backer field.
        if event.get("event_type") == "FILES_FOR_IPO":
            match = re.match(
                r"^(.+?)\s+(?:(?:has|confidentially)\s+)*(?:files|filed)\s+for\s+"
                r"(?:an?\s+)?IPO\b",
                str(event.get("title") or ""),
                re.IGNORECASE,
            )
            if match:
                issuer = re.split(r"['’]s\s+", match[1])[-1].strip().casefold()
                identity = "ipo-filing:" + issuer
        kind, role, rationale = rule
        alert = {
            "fact_id": identity,
            "event_id": event.get("event_id"),
            "kind": kind,
            "causal_role": role,
            "rationale": rationale,
            "rule_version": RULE_VERSION,
            "event_type": event.get("event_type"),
            "title": event.get("title") or event.get("evidence_text"),
            "evidence": event.get("evidence_text"),
            "url": event["url"],
            "date": occurred.isoformat(),
            "reviewed_at": reviewed.isoformat(),
        }
        # Stable representative even if caller supplies the same fact more than once.
        sources = {str(event["url"])}
        fact_ids = {str(event.get("fingerprint") or event.get("event_id"))}
        if identity in selected:
            sources.update(selected[identity]["sources"])
            fact_ids.update(selected[identity]["underlying_fact_ids"])
            if str(alert["url"]) >= str(selected[identity]["url"]):
                alert = selected[identity]
        alert["sources"] = sorted(sources)
        alert["underlying_fact_ids"] = sorted(fact_ids)
        selected[identity] = alert
    alerts = sorted(selected.values(), key=lambda row: (row["date"], row["fact_id"]), reverse=True)
    counts = {
        kind: sum(row["kind"] == kind for row in alerts)
        for kind in ("pressure", "public_exposure", "context")
    }
    return {
        "rule_version": RULE_VERSION,
        "window_days": 90,
        "as_of": moment.isoformat(),
        "counts": counts,
        "items": alerts,
    }
