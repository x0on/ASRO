from __future__ import annotations

import hashlib

from asro.models import FinancialEvent


def economic_fingerprint(event: FinancialEvent) -> str:
    """Identity of the underlying economic fact, independent of which article reported it.

    Two mentions are the same fact when they share event type, both entities, the amount to
    the nearest million, and the calendar month. Month — not day — because coverage of one
    transaction routinely spans days or weeks, while genuinely repeated facts (a quarterly bond
    issue, a second investment round) are months apart.
    """
    # ponytail: calendar-month bucket; a deal reported across a month boundary splits in two.
    # Upgrade path: a sliding window keyed on first_seen in economic_events.
    amount_bucket = "unknown" if event.amount is None else str(round(float(event.amount), -6))
    month = (event.effective_date or "unknown")[:7]
    raw = "|".join(
        [
            event.event_type.value,
            event.source_entity or "",
            event.target_entity or "",
            amount_bucket,
            month,
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()
