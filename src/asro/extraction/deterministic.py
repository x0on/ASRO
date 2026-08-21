from __future__ import annotations

import hashlib
import re

from asro.entities import canonicalize
from asro.extraction.amounts import extract_amount
from asro.extraction.entities import find_entities
from asro.extraction.rules import RULES
from asro.models import FinancialEvent, ScoredItem

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class DeterministicEventExtractor:
    name = "deterministic-rules-v1"

    def __init__(self, known_entities: list[str]) -> None:
        self._known_entities = known_entities

    def extract(self, document: ScoredItem, full_text: str = "") -> list[FinancialEvent]:
        text = " ".join(part for part in (document.title, document.summary, full_text) if part)
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
        events: list[FinancialEvent] = []

        for sentence in sentences:
            lower = sentence.lower()
            entities = find_entities(sentence, self._known_entities)

            for rule in RULES:
                if not any(phrase in lower for phrase in rule.phrases):
                    continue

                source_entity = canonicalize(entities[0]) if entities else None
                target_entity = canonicalize(entities[1]) if len(entities) > 1 else None
                amount, currency = extract_amount(sentence)

                fingerprint = "|".join(
                    [
                        document.item_id,
                        rule.event_type.value,
                        source_entity or "",
                        target_entity or "",
                        sentence,
                    ]
                )
                event_id = hashlib.sha256(fingerprint.encode()).hexdigest()

                events.append(
                    FinancialEvent(
                        event_id=event_id,
                        document_id=document.item_id,
                        event_type=rule.event_type,
                        source_entity=source_entity,
                        target_entity=target_entity,
                        amount=amount,
                        currency=currency,
                        instrument=rule.instrument,
                        effective_date=document.published_at,
                        confidence=rule.confidence,
                        evidence_text=sentence,
                        extractor=self.name,
                    )
                )

        return events
