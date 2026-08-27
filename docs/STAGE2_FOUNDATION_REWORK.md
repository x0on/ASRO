# Stage 2 foundation rework

The entity-month feature builder uses an explicit inclusive requested month window. It emits
rows for every requested entity, month, feature key, and feature version, including months with
no evidence.

Feature specifications are canonically ordered by key and version before row generation and
manifest serialization. The manifest includes the code commit, feature-set version, cutoff,
requested window, feature semantics, and rows. Build and row identities therefore distinguish
code, feature-set, feature version, time, and content while remaining stable under input reorder.

For aggregation, `canonical_fact_id` is the economic-fact identity. Append-only mappings connect
each extracted event mention to one canonical fact, allowing distinct documents and extracted
events to describe the same economic event. All observations remain contributors, while only the
highest-quality deterministic representative per canonical fact participates in aggregation.

Migrations 1 and 2 remain unchanged. Forward migration 3 introduces temporal canonical-fact
assignments, normalized lineage, and build finalization while backfilling databases created by the
approved schemas. Assignments carry availability time, reviewer/provenance fields, and append-only
supersession, so historical builds resolve the assignment known at their cutoff.

Each event has exactly one root assignment chain. Competing roots are rejected by a partial unique
index and repository validation, while temporal resolution raises if corrupted legacy state is
ambiguous. Assignment timestamps are canonical UTC, creation cannot precede availability, and
offset-equivalent cutoffs resolve identically. Migration backfill selects representatives using the
same confidence, quality, availability, and stable-ID ordering as the builder, then transactionally
drops its legacy contributor staging table.

Lineage is normalized through `feature_value_fact` and `feature_value_contributor`. Finalization
checks stored compatibility counts against those rows, requires facts for numeric cells, forbids
facts for missing cells, and prevents further lineage insertion after completion. Database triggers
also require every representative and contributor to match the cell's entity, month, feature,
version, entity scope, and build cutoff. The manifest omits generated row IDs; canonical manifest
content produces the build ID, and the build ID plus complete row grain produces each feature-value
ID.

Idempotent reuse requires a finalization record and revalidates the exact persisted feature rows,
fact lineage, and contributor lineage against the canonical build content before returning.

Ecosystem-month builds consume finalized entity-month builds only. Each ecosystem cell retains all
source entity-feature row IDs, derives coverage as the mean of those source cells including explicit
missing rows, and deduplicates canonical facts across entities before aggregation. This prevents a
transaction reported under multiple documents or entity roles from being counted twice. Separate
finalization checks and finalized-only views keep incomplete ecosystem builds out of consumption.
Database finalization requires the entity-contributor set to equal every finalized source cell for
the source build/month/feature/version and the canonical-fact set to equal their deduplicated fact
union. Each ecosystem representative must be an exact fact/assignment/observation tuple from one of
those linked cells; counts cannot be lowered to conceal omitted lineage. Ecosystem numeric storage
also rejects non-finite SQLite REAL values.

Coverage is not presence. Each registered feature declares `expected_facts_per_period`, and a
cell's coverage is `min(1, distinct_facts / expected_facts_per_period)`. Missing cells have zero
facts, contributors, coverage, and reliability with an explicit missingness reason.
