# Legacy scoring audit

This document freezes ASRO V0 inference behavior before the integrated-model migration. The legacy
pipeline remains operational during migration, but it is a heuristic communication system—not a
trained statistical model and not a probability of collapse.

## Discovery score (`src/asro/scoring.py`)

The item score is a source-discovery priority value. It sums fixed keyword points, one point per
matched configured company, and a four-point bonus when any stress term appears.

Keyword points range from 2 (`capex`) to 6 (`default`, `distress`, `impairment`, `write-down`, and
`lease cancellation`). Categories are assigned by ordered keyword groups: distribution, credit,
IPO, cannibalization, then general. This score affects report ordering and evidence-node metadata;
it must not be used as a Scientist feature or target.

## Extraction assumptions

`src/asro/extraction/rules.py` defines phrases, event types, entity roles, and extraction confidence.
`src/asro/extraction/deterministic.py` applies those rules to fetched text. These outputs are
candidate facts until evidence review. Extraction confidence describes the extractor's confidence,
not source quality, economic materiality, or risk.

## Economic-fact deduplication (`src/asro/dedupe.py`)

The fingerprint is a hash of:

```text
event type | source entity | target entity | amount rounded to $1M | calendar month
```

Known limitation: reports of one event across a month boundary split; distinct events with identical
fields inside one month can merge. Mentions remain stored, but only the canonical event reaches the
graph/timeline/counts.

## Event-to-observation mapping (`src/asro/measurement.py`)

| Event type | Variable | Fallback | Polarity |
| --- | --- | ---: | --- |
| ASSUMES_DEBT, LENDS_TO, ISSUES_DEBT | `ai_related_debt` | 1.0 signal | risk |
| GUARANTEES | `vendor_financing` | 1.0 signal | risk |
| REFINANCES | `refinancing_stress` | 1.0 score | risk |
| DOWNGRADE, IMPAIRMENT | `refinancing_stress` | 2.0 score | risk |
| CANCELS_PROJECT | `refinancing_stress` | 1.5 score | risk |
| INVESTS_IN, CAPEX_COMMITMENT | `ai_capital_commitments` | 1.0 signal | risk |
| COMPLETES_IPO | `public_market_transmission_stage` | 1.0 score | risk |
| ENTERS_INDEX | `public_market_transmission_stage` | 2.0 score | risk |
| ALLOCATES_TO | `retirement_exposure` | 1.0 signal | risk |
| PRICE_CUT | `model_price_pressure` | 1.0 signal | risk |
| REVENUE_REPORT | `ai_external_revenue` | 1.0 signal | safety |
| FREE_CASH_FLOW | `free_cash_flow_strength` | 1.0 signal | safety |
| MODEL_RELEASE, BENCHMARK_GAIN | `external_capability_pressure` | 1.0 score | risk |

For USD definitions, a supported USD amount is retained; otherwise the fallback becomes a
qualitative `signal`. Percent definitions always become qualitative signals. Score definitions use
the fallback severity and do not use transaction amount.

## Executable variable weights

| Variable | Dimension | Weight | Minimum points | Direction |
| --- | --- | ---: | ---: | --- |
| `ai_capital_commitments` | capital | 1.3 | 5 | higher risk |
| `ai_external_revenue` | monetization | 1.5 | 5 | higher safety |
| `vendor_financing` | circularity | 1.4 | 5 | higher risk |
| `ai_related_debt` | fragility | 1.4 | 5 | higher risk |
| `refinancing_stress` | stress | 1.7 | 5 | higher risk |
| `public_index_exposure` | transmission | 1.2 | 5 | higher risk |
| `public_market_transmission_stage` | transmission | 1.0 | 1 | higher risk |
| `retirement_exposure` | transmission | 1.5 | 5 | higher risk |
| `model_price_pressure` | cannibalization | 1.0 | 5 | higher risk |
| `external_capability_pressure` | external pressure | 1.3 | 5 | higher risk |
| `external_price_performance_pressure` | external pressure | 1.5 | 5 | higher risk |
| `free_cash_flow_strength` | counter-evidence | 1.5 | 5 | higher safety |
| `deleveraging` | counter-evidence | 1.2 | 5 | higher safety |

## Dimension computation (`src/asro/indicators.py`)

- Window: newest record per `(variable, entity)` within 90 days.
- USD normalization: `100 * (1 - 1 / (1 + value / $10B))`.
- Percent: clamp to 0-100.
- Score/signal: multiply values at or below 5 by 20, otherwise clamp to 0-100.
- Safer-direction definitions invert normalized value.
- Numeric point: normalized value multiplied by extraction confidence and variable weight.
- A dimension averages points when its applicable minimum count is met.
- Otherwise, at least one qualitative directional point creates an estimate centered on 50, with a
  fixed prior strength of 5 and maximum deviation of 25.
- Values above $5T, nonpositive USD, unit mismatches, stale rows, and future rows are excluded.

Consequences: confidence and weight scale magnitude directly; weights can push individual points
above 100 before the dimension result is capped; the $10B scale applies to economically different
USD variables; and directional evidence can create numeric-looking estimates without measured
magnitude.

## Convergence computation

- Risk dimensions: capital, circularity, monetization, cannibalization, fragility, transmission,
  stress, and external pressure.
- At least three must be known; otherwise the result is `INSUFFICIENT EVIDENCE`.
- Base score: unweighted mean of known dimension scores.
- Reassuring counter-evidence below 50 reduces the base by 25% of its distance below 50.
- Materiality threshold: 55.
- Highest-state gate requires one material dimension from fragility/stress/transmission and one from
  monetization/capital/circularity.
- Labels: below 25 `DISPERSED`; below 45 `FORMING`; below 65 `BUILDING PRESSURE`; below 80 or failed
  gate `FRAGILE`; otherwise `HIGH CONVERGENCE`.
- Direction is hard-coded to `unknown` because a validated trend is not yet computed.

## Data snapshot at audit

At repository commit `addc05b`:

- 1,206 items/documents;
- 2,363 extracted financial-event mentions;
- 973 canonical economic events: 271 confirmed, 678 flagged, 15 provisional, 9 merged;
- 2,269 legacy observations;
- 72 system snapshots over roughly two days;
- observation `observed_at` values fall within milliseconds on one rebuild date, so they do not
  constitute a historical time series;
- effective/publication dates mix ISO dates, ISO timestamps, and RFC-style date strings.

The inventory contains historical facts, but it is not currently a leakage-safe feature store.

