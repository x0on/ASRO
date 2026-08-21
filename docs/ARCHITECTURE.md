# Architecture

## Design goals

The project is intentionally split into small layers.

### Collectors

Collectors know how to speak to external sources.

They do **not**:

- score risk
- write directly to databases
- make thesis conclusions
- perform LLM interpretation

### Models

Normalized data contracts.

All collectors emit the same `SourceItem` model.

### Scoring

Deterministic first-pass relevance scoring.

This will remain separate from future LLM-based interpretation.

### Storage

Repositories own persistence.

V0 uses SQLite.

Planned:

- PostgreSQL
- migrations
- event tables
- entity tables
- relationship tables

### Reporting

Human-readable exports only.

No data collection or scoring logic belongs here.

## Planned event model

Future financial events should resemble:

```text
event_id
event_type
source_entity
target_entity
amount
currency
instrument
maturity
effective_date
confidence
source_document_id
```

Potential event types:

```text
INVESTS_IN
LENDS_TO
GUARANTEES
SUPPLIES
PURCHASES_FROM
LEASES_FROM
ALLOCATES_TO
REFINANCES
ISSUES_DEBT
FILES_FOR_IPO
COMPLETES_IPO
ENTERS_INDEX
PRICE_CUT
CAPEX_COMMITMENT
BENCHMARK_GAIN
MODEL_RELEASE
FREE_CASH_FLOW
REVENUE_REPORT
CANCELS_PROJECT
IMPAIRMENT
DOWNGRADE
```

## Future graph

A graph database is not required for V0.

Relationships should first be modeled correctly in PostgreSQL.

Neo4j may be introduced only if graph traversal becomes materially better than relational queries.
