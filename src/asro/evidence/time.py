from __future__ import annotations

from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum


class TimePrecision(StrEnum):
    DATE = "date"
    SECOND = "second"


def infer_time_precision(value: str | date | datetime) -> TimePrecision:
    if isinstance(value, datetime):
        return TimePrecision.SECOND
    if isinstance(value, date):
        return TimePrecision.DATE
    text = value.strip()
    if len(text) == 10:
        try:
            date.fromisoformat(text)
        except ValueError:
            pass
        else:
            return TimePrecision.DATE
    return TimePrecision.SECOND


def normalize_timestamp(value: str | date | datetime) -> datetime:
    """Return aware UTC; date-only values mean the start of that explicitly coarse UTC day."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime requires an explicit timezone")
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    else:
        text = value.strip()
        if not text:
            raise ValueError("timestamp cannot be empty")
        if len(text) == 10:
            try:
                day = date.fromisoformat(text)
            except ValueError:
                pass
            else:
                return datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unsupported timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(UTC)


def timestamp_text(value: str | date | datetime) -> str:
    return normalize_timestamp(value).isoformat()
