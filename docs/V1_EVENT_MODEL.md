# V1 Financial Event Model

V1 turns monitored documents into auditable structured events.

## Principle

The database must never confuse:

1. **source document**
2. **extracted fact**
3. **research interpretation**

V1 only implements the first two.

## Event shape

```text
event_id
document_id
event_type
source_entity
target_entity
amount
currency
instrument
effective_date
confidence
evidence_text
extractor
processed_at
```

## Example

Source text:

```text
Nvidia guarantees $30 billion of financing for OpenAI.
```

Stored event:

```text
event_type: GUARANTEES
source_entity: Nvidia
target_entity: OpenAI
amount: 30000000000
currency: USD
instrument: guarantee
confidence: 0.88
evidence_text: Nvidia guarantees $30 billion of financing for OpenAI.
```

The original document remains available separately.

## Why evidence_text matters

No graph edge should exist without human-auditable evidence.

A future UI should allow a user to click:

```text
Nvidia ── GUARANTEES ──> OpenAI
```

and immediately inspect the sentence and original source that created the relationship.

## Current extractor

V1 begins with deterministic phrase rules.

This is intentionally conservative.

Benefits:

- reproducible
- testable
- no hallucination
- cheap
- easy to audit

Limitations:

- cannot understand complex sentence structure
- may reverse source/target in ambiguous prose
- misses implicit relationships
- does not resolve subsidiaries or aliases

## Planned hybrid extraction

Future:

```text
Deterministic extraction
        │
        ├── high confidence → store directly
        │
        └── ambiguous
                │
                ▼
            LLM extractor
                │
                ▼
         schema validation
                │
                ▼
         provenance retained
```

LLM outputs will be stored as derived events with:

- extractor/model name
- confidence
- evidence span
- source document ID

They will never overwrite the raw source.
