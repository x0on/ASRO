# Stage 1 integrity rework

This note maps the rejected Stage 1 review to the implemented corrections. Stage 2 remains blocked
until this rework is accepted.

| Review requirement | Enforcement |
| --- | --- |
| Canonical `as_of` cutoff | String, date, and aware datetime inputs normalize to canonical UTC before SQL comparison; offset-equivalence tests included |
| Correction identity | Event, source, feature/version, entities/roles, period, unit/currency, and denominator are immutable in repository and database trigger |
| Correction chronology | Availability and extraction times must be monotonic; self-reference, missing parents, branches, and cycles are rejected |
| Foreign keys | Enabled and verified on every connection; source, event, review, correction, and feature-version references are enforced |
| Append-only evidence | SQLite triggers reject direct UPDATE and DELETE on observations and feature definitions |
| Value semantics | Exactly one numeric/text value; finite numerics; nonblank text; currency/unit pairing; Pydantic and SQLite checks agree |
| Feature versions | Composite foreign key plus immutable registration workflow; semantic reuse of a version is rejected |
| Timezone/precision | Naive datetimes are rejected; date-only values are explicit UTC-day precision; date/second precision is persisted |
| Genuine legacy migration | Tests manually construct a pre-V2 database and upgrade it without the current initializer |
| Versioning/rollback | `schema_migrations` records migration 1; DDL is transactional; broken migrations roll back; unmanaged/obsolete schemas fail verification |
| Classified provenance | Inferred/estimated facts require derivation method and input IDs; estimates require a model; disputes require a reason |
| Strict verification | Full pytest, Ruff, format, and strict mypy checks pass |

The migration was also run against a disposable copy of the current repository database: foreign
keys were enabled, migration 1 was recorded, all 1,206 items and 973 canonical events remained, and
`PRAGMA foreign_key_check` returned no violations.

## Approved-with-changes closure

The follow-up consistency review is also resolved:

- SQLite currency/unit checks now use explicit null-safe boolean branches.
- Extraction time must be at or after availability time in both Pydantic and SQLite.
- Time precision must match timestamp presence and the original API input; stored date precision is
  retained during database round-trips.
- Numeric observations require a complete period and an explicit economic scope (`entity`,
  `ecosystem`, `network`, or `market`).
- Direct-SQL regression cases exercise currency, chronology, timestamp precision, numeric period,
  numeric scope, provenance JSON, and feature-definition JSON constraints.
- `derivation_inputs` must be a valid JSON array and `definition_json` a valid JSON object in both
  application and database paths.
