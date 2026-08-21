from __future__ import annotations

import re

_AMOUNT_RE = re.compile(
    r"(?P<currency_symbol>\$|€|£)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>billion|million|trillion|bn|mn|b|m|t)?",
    re.IGNORECASE,
)

_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP"}
_MULTIPLIER = {
    "million": 1_000_000,
    "mn": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "b": 1_000_000_000,
    "trillion": 1_000_000_000_000,
    "t": 1_000_000_000_000,
}


def extract_amount(text: str) -> tuple[float | None, str | None]:
    for match in _AMOUNT_RE.finditer(text):
        symbol = match.group("currency_symbol")
        unit = (match.group("unit") or "").lower()
        # Avoid treating arbitrary plain integers as money.
        if not symbol and unit not in _MULTIPLIER:
            continue

        value = float(match.group("value"))
        value *= _MULTIPLIER.get(unit, 1)
        return value, _CURRENCY.get(symbol)

    return None, None
