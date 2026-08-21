from __future__ import annotations


def find_entities(text: str, known_entities: list[str]) -> list[str]:
    lower = text.lower()
    return sorted(
        {entity for entity in known_entities if entity.lower() in lower},
        key=lambda value: lower.find(value.lower()),
    )
