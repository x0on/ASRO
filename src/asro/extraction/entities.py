from __future__ import annotations


def find_entities(text: str, known_entities: list[str]) -> list[str]:
    # Feed titles often end in "Google News". That is distribution metadata, not
    # evidence that Alphabet participated in the event described by the headline.
    lower = text.lower().replace("google news", "")
    return sorted(
        {entity for entity in known_entities if entity.lower() in lower},
        key=lambda value: lower.find(value.lower()),
    )
