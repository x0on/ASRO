# ASRO integrated model: technical audit and migration design

Status: Stages 1–2 approved; Stage 3 integrity framework implemented but data acceptance blocked  
Scope: repository state at commit `addc05b` and the integrated Observer / Scientist / Critic brief

## Executive decision

ASRO should migrate without replacing the working collector, reviewer, graph, timeline, or static
site. The safe seam is immediately after reviewed economic events. The existing pipeline remains
the public V0 path while a versioned research path is built in parallel:

```text
sources -> items/documents -> extracted events -> evidence review
                                             |
                                             +-> legacy observations -> legacy gauge
                                             |
                                             +-> versioned observations -> feature store
                                                                         |
                                                        +----------------+----------------+
                                                        |                                 |
                                                    Scientist                           Critic
                                                        |                                 |
                                                        +---------- state estimate -------+
                                                                         |
                                                                 public signal adapter
```

No statistical model should be added until the versioned observation contract, availability-time
rules, and feature semantics below are implemented and tested.

## Current data flow

1. `collectors/*` emits normalized `SourceItem` records.
2. `scoring.py` assigns discovery priority and a category from hand-authored keyword weights.
3. `storage.py` persists items; `documents.py` fetches full text.
4. `extraction/deterministic.py` creates `FinancialEvent` records from rules and entity matching.
5. `dedupe.py` groups presumed mentions of one economic fact using event type, entities, rounded
   amount, and calendar month.
6. `reviewer.py` confirms, merges, or flags provisional events. The LLM is an evidence editor, not
   a statistical model.
7. `measurement.py` maps selected event types to observations through a fixed lookup table.
8. `indicators.py` selects the latest observation per variable/entity in a 90-day window, applies
   fixed normalization and variable weights, averages dimensions, applies an interaction gate and
   counter-evidence adjustment, and produces a convergence label.
9. `site.py` exports the legacy signal, dimension evidence, graph, timeline, and operational health
   to static JSON and HTML.
10. `.github/workflows/monitor.yml` rebuilds observations, collects, optionally reviews, builds the
    site, commits data, and deploys hourly.

## Where human assumptions enter

These controls are useful policy today, but none is empirically calibrated:

| Location | Assumption |
| --- | --- |
| `scoring.py` | Keyword weights, stress bonus, entity-count bonus, category precedence |
| `extraction/rules.py` | Phrase patterns, event types, default confidence and entity interpretation |
| `dedupe.py` | Same type/entities/rounded amount/calendar month means one economic fact |
| `measurement.py` | Event-to-variable mapping, fallback magnitudes, polarity, severity stages |
| `dictionary/registry.py` | Dimension membership, direction, weights, minimum evidence points |
| `indicators.py` | 90-day window, USD normalization scale of $10B, score scaling, directional prior |
| `indicators.py` | Materiality at 55, minimum three dimensions, interaction groups |
| `indicators.py` | Counter-evidence multiplier of 0.25 and public label boundaries |
| `lineage.py` | Curated facts and fixed score/category values used to seed the public lineage |

The legacy discovery score must not be used as a model feature or target. The legacy convergence
score must never be used as a training label.

## Current schema and gap analysis

### What is already strong

- Original URL, source, title, summary, publication time, discovery time, and fetched text exist.
- Extracted evidence retains a quoted span, extractor identity, confidence, and source document.
- Economic-event mentions are retained while canonical facts are reviewed audibly.
- Unknown dimensions and an explicit `INSUFFICIENT EVIDENCE` headline are supported.
- Provisional, confirmed, flagged, and merged states are visible.
- Collector runs, freshness, static deployment, graph, timeline, tests, linting, and typing exist.
- Collection and persistence are sufficiently separated to support an incremental migration.

### Blocking gaps

- Event date and publication date are not normalized to one typed UTC/date representation. Stored
  values include ISO dates, ISO timestamps, and RFC-style strings.
- `observed_at` records rebuild time, not when the fact became knowable. The current observations
  were regenerated within milliseconds on one date, so this column cannot drive backtests.
- An observation does not record `availability_at`, source hierarchy, direct/inferred/estimated/
  disputed status, feature-definition version, extraction version, review decision version, or
  supersession history.
- Numeric and qualitative values share one row shape. A missing USD amount may become a `signal`
  value of 1.0, which is useful descriptively but cannot be mixed with measured amounts.
- Event confidence conflates extraction confidence, source quality, review confidence, and economic
  magnitude.
- Entity roles, entity versions, denominators, currencies, periods, geographic scope, and control
  series are not modeled.
- Rebuilding observations is destructive derivation rather than a versioned, reproducible build.
- No time-aligned feature store, dataset manifest, feature coverage report, model registry,
  validation record, or critic finding exists.
- Confirmed coverage is thin and imbalanced: 271 of 973 canonical events are confirmed; many
  variables have few entities, while capital-commitment signals dominate.

## Training-row decision

Use a multi-level structure with two canonical grains:

1. **Entity-month**: one row per canonical entity and calendar month. This is the primary grain for
   company balance-sheet, financing, capex, revenue, cash-flow, maturity, and spread features.
2. **Ecosystem-month**: one row per month, derived from entity-month data plus control series and
   network features. This is the initial grain for regime discovery and public state inference.

Do not use company-day or ecosystem-week initially. Current source cadence and historical coverage
cannot support those grains honestly. Preserve exact timestamps so a future weekly view can be
generated when coverage earns it. Every ecosystem aggregate must retain contributing entity-feature
row IDs and use explicit aggregation rules that prevent repeated reporting or dual roles from being
counted twice.

## Evidence contract (minimum schema change)

Add append-only tables; do not mutate legacy tables in place during the migration.

### `observation_v2`

Required fields:

- `observation_id`, `supersedes_observation_id`
- `event_id`, `source_document_id`, `source_locator`, `evidence_text`
- `entity_id`, `counterparty_entity_id`, `entity_role`
- `feature_key`, `feature_version`
- `value_numeric`, `value_text`, `unit`, `currency`, `denominator_feature_key`
- `period_start`, `period_end`, `event_at`, `published_at`, `availability_at`, `extracted_at`
- `fact_status`: direct, inferred, estimated, or disputed
- `source_tier`, `source_quality`, `extraction_confidence`, `review_confidence`
- `extractor_name`, `extractor_version`, `review_id`

Rules:

- `availability_at` is the earliest time the exact source fact could have entered ASRO. Backtests
  filter on this field; `event_at` never substitutes for it.
- Unknown is null plus a reason code, never zero.
- Qualitative signals use `value_text`; they do not masquerade as numeric units.
- Corrections append a new row and point to the superseded row.
- Every numeric value has a unit, period, and economic scope.

### `feature_definition`

Store versioned semantics: causal role, secondary tags, entity grain, period grain, unit, direction,
aggregation, denominator, control series, allowed source tiers, missingness policy, release date, and
deprecation date.

### `feature_value`

Store `entity_id`, `period`, `feature_key`, `feature_version`, value, missingness reason, coverage,
reliability, contributing observation IDs, build ID, and availability cutoff.

### Reproducibility tables

- `dataset_build`: code commit, feature-set version, availability cutoff, window, row count, checksum.
- `model_run`: dataset build, method/version, parameters, seed, training window, validation window,
  metrics, artifact checksum, and environment lock hash.
- `critic_finding`: state/model run, claim challenged, evidence, test, result, severity, and status.

## Causal classification of executable variables

Each variable gets one primary role; secondary tags may refine interpretation.

| Current key | Primary role | Required correction before modeling |
| --- | --- | --- |
| `ai_capital_commitments` | BOOM | Split equity, debt, lease, guarantee, and capex; add period and denominator |
| `ai_external_revenue` | VALIDATION | Define external customer boundary; add recurring/nonrecurring and period |
| `vendor_financing` | VULNERABILITY | Record creditor, obligor, recourse, maturity, and funded vs guaranteed amount |
| `ai_related_debt` | VULNERABILITY | Add debt class, maturity, rate, recourse, entity scope, and cash-flow denominator |
| `refinancing_stress` | STRESS | Split refinancing event from downgrade, impairment, cancellation, and failure |
| `public_index_exposure` | TRANSMISSION | Add index/fund weights, assets, look-through date, and ownership chain |
| `public_market_transmission_stage` | TRANSMISSION | Keep descriptive/trigger-only; do not treat ordinal stage as cardinal magnitude |
| `retirement_exposure` | TRANSMISSION | Add vehicle, look-through method, direct/indirect exposure, and double-count rules |
| `model_price_pressure` | SHOCK | Separate list-price changes from realized revenue/margin effects |
| `external_capability_pressure` | SHOCK | Version benchmarks and separate capability from adoption/materiality |
| `external_price_performance_pressure` | SHOCK | Version price/performance methodology and comparable workload |
| `free_cash_flow_strength` | RESILIENCE | Add period, entity scope, sustainable/external classification, and capex ratio |
| `deleveraging` | RESILIENCE | Replace generic score with debt/guarantee/commitment changes and denominators |

The current dictionary has no first-class REAL_ECONOMY variable. Add those only after transmission
and credit features are sufficiently measured. CIRCULARITY becomes a secondary structural tag and
derived network property, not an event label by itself.

## Level, velocity, breadth, and confidence

These are separate outputs, never four aliases for one score:

- **Level**: current economically interpretable amount or ratio relative to history/control.
- **Velocity**: change over a declared interval, calculated only from comparable feature versions.
- **Breadth**: entity/network share affected, with an explicit eligible-population denominator.
- **Confidence**: coverage and reliability metadata; it must not multiply economic magnitude.

## Scientist baseline recommendation

Given the present data volume, begin with three transparent methods after V2 backfill:

1. **Robust standardized deviations with controls**: rolling median/MAD z-scores for AI-minus-
   control features. This yields interpretable levels and change points without crisis labels.
2. **Change-point detection on a small preregistered feature set**: offline segmented likelihood or
   CUSUM with minimum regime length. Report sensitivity across penalties; do not call a break risky
   until its economic meaning is separately interpreted.
3. **Regularized principal components plus small-k clustering**: only after coverage thresholds are
   met. Use time-block stability, bootstrap sensitivity, and descriptive regime names assigned after
   fitting.

Simple time-based regression may test specific lead/lag hypotheses later. Hazard models and hidden
states are premature until there are enough independent transitions. Deep learning, LLM fine-tuning,
and a learned 0-100 target are out of scope.

## Critic contract

The Critic cannot edit evidence or directly subtract an arbitrary score. It produces typed findings:

- counter-evidence observation sets;
- alternative explanation and confounder checks;
- source/selection-bias and missingness reports;
- leave-one-source, leave-one-entity, and low-confidence exclusion sensitivity;
- historical analogue comparability tests;
- falsifier statements with observable deadlines/outcomes.

The state service reports Scientist results both before and after declared robustness tests. A failed
robustness test lowers a separate robustness/confidence field or changes the state to insufficient
evidence; it does not invoke a hand-weighted critic penalty.

## Historical backfill design

Backfill is an incremental, idempotent job with a manifest:

1. Register an episode, benign comparison, entities, controls, source plan, and date boundaries.
2. Collect primary sources first; store immutable raw-document hashes and retrieval metadata.
3. Extract V2 observations under a pinned schema/extractor version.
4. Review conflicts and deduplicate economic facts without deleting mentions.
5. Build entity-month features using only observations with `availability_at <= cutoff`.
6. Build ecosystem-month aggregates with contributor IDs and coverage metrics.
7. Freeze a dataset manifest and checksum; never rewrite a historical build.
8. Produce coverage, missingness, source-tier, and revision reports before fitting.

Initial episodes: dot-com/telecom, housing/credit, shale financing, benign infrastructure/capex,
regional-bank stress, pandemic technology acceleration, and the current AI cycle. Episode names are
evaluation strata, not outcome labels.

## Staged implementation plan

### Stage 0 - Freeze and characterize legacy behavior

Files: add `docs/LEGACY_SCORING_AUDIT.md`; add golden fixtures under `tests/fixtures/legacy/`.

Acceptance: every constant and mapping listed above is covered; current snapshot output can be
reproduced from a fixed fixture; legacy code is explicitly labeled heuristic.

### Stage 1 - V2 evidence and time semantics

Files: add `src/asro/evidence/models.py`, `src/asro/evidence/schema.py`,
`src/asro/evidence/repository.py`, migrations under `src/asro/migrations/`, and tests.

Acceptance: normalized dates, append-only corrections, direct/inferred/estimated/disputed status,
separate confidence fields, provenance round-trip, and availability-time tests all pass.

Implementation status: complete after integrity rework. The schema is additive, legacy tables remain
untouched, and a migration check against the repository's current SQLite database preserves all
1,206 items and 973 canonical events while adding the V2 tables. Foreign keys, append-only triggers,
correction identity/chronology, registered feature versions, classified-fact provenance, explicit
time precision, versioned transactional migration, and canonical as-of queries are enforced. See
`docs/STAGE1_REWORK.md` for the review-to-fix matrix.

### Stage 2 - Versioned feature store

Files: add `src/asro/features/definitions.py`, `build.py`, `aggregations.py`, `quality.py`,
`manifest.py`, and CLI commands `feature-build`/`feature-audit`.

Acceptance: deterministic entity-month and ecosystem-month builds; unknown never becomes zero;
contributor IDs reconcile to observations; identical inputs/config produce identical checksums.

Implementation status: entity-month and ecosystem-month foundations implemented. Forward migrations
add immutable values, temporal canonical lineage, finalization, ecosystem rows, contributing
entity-feature links, and finalized-only visibility views. Builders enforce registered semantics,
availability-time cutoffs, explicit unknown rows, cross-entity canonical-fact deduplication,
coverage/reliability metadata, exact idempotent validation, and deterministic manifests.
Deterministic quality reports audit finalized builds only and include explicit missingness,
coverage/reliability, canonical facts, observations, source documents, source tiers, and fact-status
provenance. JSON-configured `feature-build` and finalized-build `feature-audit` CLI commands complete
Stage 2 without changing the legacy monitoring workflow.

### Stage 3 - Auditable historical backfill

Files: add `src/asro/backfill/manifest.py`, `runner.py`, episode TOML files, and coverage reports.

Acceptance: reruns are idempotent; source hashes and availability dates are retained; crisis and
benign strata are present; no backfill job delays hourly collection.

Implementation status: forward migrations 5–7 add immutable episodes, reconstructable full-content
snapshots, historically knowable availability with separate fetch time, source-to-entity links,
versioned control observations, normalized entity × month × requirement coverage cells, and
database-derived finalization checks. Exact entity-build windows, entity/feature sets,
schema/extractor/feature versions, and ecosystem source-build identity are validated. A separate
candidate quarantine preserves research-package and raw-file hashes, all candidate source edges,
and researcher assertions without creating production evidence or coverage. Promotion requires a
reviewed V2 observation after authoritative full-document acquisition.

The supplied 1,750-event research corpus currently offers discovery overlap for two manifests
(567 current-cycle candidates and 6 pandemic candidates), but zero promoted observations. All seven
manifests therefore remain unaccepted; see `STAGE3_CANDIDATE_ACCEPTANCE_REPORT.json`. Statistical
dependencies remain prohibited until real promoted data pass coverage and leakage gates.

### Stage 4 - Transparent Scientist baselines

Dependencies: add bounded `numpy`, `pandas`, `scipy`, `scikit-learn`, and `statsmodels` only here.
Files: add `src/asro/models/baselines.py`, `changepoints.py`, `regimes.py`, `validation.py`,
`uncertainty.py`, and model-run persistence.

Acceptance: time-block validation, fixed random seeds, naive baselines, coverage gates, artifact
checksums, stability/sensitivity reports, and no legacy score target.

### Stage 5 - Critic and falsification

Files: add `src/asro/critic/counterevidence.py`, `falsification.py`, `bias_checks.py`, and typed
findings persistence.

Acceptance: every published state includes counter-evidence, missingness, robustness results, and at
least one observable falsifier; Critic findings cannot modify source evidence.

### Stage 6 - State service and public adapter

Files: add `src/asro/state/models.py`, `inference.py`, and `src/asro/scoring/public_signal.py`; extend
site snapshot schema while retaining legacy keys through a deprecation window.

Acceptance: vulnerability, shock pressure, and stress remain separate; direction, confidence,
trajectory, disagreement, and insufficient evidence render; legacy dashboard remains operational.

### Stage 7 - Network propagation and continuous evaluation

Files: version graph semantics; add exposure-aware edges, scenario propagation, drift/error/false-
alarm tracking, and public methodology reports.

Acceptance: identical shocks propagate differently under resilient and vulnerable network fixtures;
false positives and critic defeats are tracked alongside successful warnings.

## Required test matrix

- Reproducibility: input hashes/config/version/seed reproduce dataset and model checksums.
- Missingness: unknown, not-applicable, not-yet-published, collection-failed, and disputed remain
  distinguishable; none becomes zero silently.
- Provenance: every feature value resolves to observations, evidence spans, and immutable sources.
- Temporal leakage: records published after an as-of cutoff cannot enter a row; corrections appear
  only after their availability time; time splits are monotonic.
- Deduplication: syndicated reports do not multiply facts; genuinely repeated events remain distinct;
  month-boundary cases are tested.
- Aggregation: entity-to-ecosystem contributor reconciliation and ownership look-through prevent
  double counting.
- Feature versioning: semantic changes require a new version and cannot splice historical series.
- Quality gates: sparse features remain descriptive; model fitting refuses inadequate coverage.
- Robustness: low-confidence/source/entity exclusions and alternate controls are reported.
- Compatibility: the hourly workflow, existing CLI, graph, timeline, and legacy snapshot continue to
  work through the migration.

## Principal risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Sparse, biased historical evidence | Coverage gates, primary-source plans, benign controls, descriptive-only features |
| Look-ahead from event dates or amended filings | Mandatory availability time and as-of query tests |
| Extraction confidence treated as magnitude | Separate fact quality from economic value |
| Double counting across news, entities, and funds | Fact identity, contributor lineage, ownership look-through rules |
| Feature meaning changes | Immutable feature versions and dataset manifests |
| Complex models overfit a handful of episodes | Transparent baselines, time blocks, stability tests, complexity gate |
| Migration breaks live observatory | Parallel V2 tables and additive snapshot schema |
| Critic becomes another arbitrary weight | Typed robustness outcomes; no direct score subtraction |

## Definition of migration readiness

Stage 1 may begin when maintainers accept the entity-month/ecosystem-month grains, causal mapping,
and availability-time definition. Statistical implementation may begin only after Stage 2 produces a
reproducible feature dataset with coverage and leakage audits. The public gauge may be recalibrated
only after out-of-sample evidence exists and must remain explicitly non-probabilistic.
