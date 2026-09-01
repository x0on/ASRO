# Historical benchmark and calibration readiness

## Status

**NOT YET CALIBRATED.** The published 0–100 indicator remains a deterministic heuristic.
This document describes what historical evidence now exists, what it supports, and the
specific things that still block a calibrated claim.

`asro.benchmark.readiness` computes that status from the database rather than from
prose. `assert_claim_supported` raises before any output can be labelled more strongly
than the evidence allows.

| Tier | Meaning | Currently |
|---|---|---|
| `heuristic` | rules-based reading, no historical reference frame | — |
| `descriptive` | comparable historical evidence exists, too few strata accepted to calibrate | **current tier** |
| `historically_calibrated` | enough accepted episodes to place a reading against history | blocked |

## What was built

**A benchmark catalog** (`asro.benchmark.catalog`) of 74 measurements spanning all eight
causal roles — boom, validation, vulnerability, shock, transmission, activated stress,
real economy, resilience. Each declares its unit, its direction, whether higher means more
or less systemic pressure, the XBRL concepts or control series that produce it, and an
explicit comparability note naming what breaks across eras. It carries no weights; weights
are a scoring decision and this layer is descriptive.

**Explicit endpoints** (`asro.benchmark.endpoints`). ASRO-0 and ASRO-100 are defined as
structured conditions across all eight roles, each with its evidence requirement, plus
explicit non-claims. ASRO-100 states in the data itself that it is not a 100 percent
probability of collapse.

**Entity evidence that is genuinely vintage-correct.** Every fact in SEC `companyfacts`
carries the date it was filed, and a restatement appears as a separate later entry.
Selecting the *earliest* fact for a period whose filing date is at or before an episode's
cutoff reconstructs the number as originally reported. Later restatements are recorded as
rejections, never silently preferred.

**Quarterly flows reconstructed honestly.** A year-to-date figure and a quarterly figure
share a tag, and confusing them corrupts every flow measurement. Duration facts are
classified by their own span; quarters are taken directly where tagged and otherwise
differenced from consecutive year-to-date figures sharing a start date, with the
derivation recorded. A gap in the chain is never summed across.

**One definition per entity per measurement.** Filers express capital expenditure and
revenue through different tags. A tag group is chosen once per entity by how well it
covers the episode window, so a series cannot change meaning partway through. The chosen
tags are recorded in each observation's source locator.

## What the evidence shows

| Episode | Stratum | Observations | Coverage | Leakage | Accepted |
|---|---|---:|---|---|---|
| shale-financing (2010-2017) | crisis | 1,463 | **PASS** (features 92.3%) | PASS | **yes** |
| regional-bank-stress (2021-01→2023-03) | crisis | 764 | **PASS** (features 96.8%) | PASS | **yes** |
| pandemic-technology-acceleration | benign | 950 | **PASS** (features 91.7%) | PASS | **yes** |
| current-ai-cycle | current | 1,667 | **PASS** (features 93.5%) | PASS | **yes** |
| benign-infrastructure-capex | benign | 1,160 | FAIL (features 84.9%) | PASS | no |
| dotcom-telecom | crisis | — | not measurable (pre-XBRL) | — | no |
| housing-credit | crisis | — | not measurable (pre-XBRL) | — | no |

**Two crisis episodes, one benign and the current cycle are accepted.** All eight causal
roles are measured. 6,004 observations from 434 SEC filings; 3,535 control observations.

Three roster and scope decisions, each recorded with its reason in `episodes.py`:

* **Whiting Petroleum → Continental Resources.** Whiting filed throughout but tags only
  86% of the required quarterly measurements, against 98% for Continental. Continental is
  the same stratum — a pure-play Bakken driller — and its filings carry the numbers.
* **Regional banks get bank measurements.** Deposit funding share, equity to assets and
  unrealised securities losses replace capital expenditure and product revenue, which a
  depository does not report. Nothing was manufactured to fill the industrial set.
* **The bank episode ends 2023-03-31**, the quarter it broke. Running later would gate the
  stratum on quarters no filing covers: SVB ceased to exist in March 2023. Its three
  uncovered months remain visible in the coverage report — the absence of a filing is the
  failure, not a data gap.

### Point-in-time credit spreads

The earlier pass concluded credit spreads were unavailable before 2023 because the ICE
BofA option-adjusted indices are licensed and truncated to a rolling three-year window.
That conclusion was wrong. **`BAA10Y`** — Moody's Baa corporate yield less the 10-year
Treasury — is freely available daily from 1986, and being market data it is final on
publication. It now carries `credit_spread_level` and `funding_market_disruption` as
genuine point-in-time evidence. It is a yield spread, not an option-adjusted spread, and
is labelled a proxy for the index rather than a replacement for it.

### Two measurements corrected after review

**`fixed_obligations_to_external_cash` no longer accepts debt alone.** The first cut let
the obligation total form from `total_debt` by itself, which restated
`debt_to_operating_cash_flow` under a second name and claimed a measurement of leases,
purchase commitments and guarantees that was never made. The total now requires at least
one leg beyond debt — lease, purchase, guarantee or contractual obligation — and stays
unknown otherwise. The consequence is honest and visible: no shale-era filer tags any such
leg before ASC 842, so the ratio is **not** measured in the shale episode and is no longer
gated on there. It is measured in the bank stratum, where operating lease liabilities and
guarantee exposures are reported.

**AOCI is no longer called an unrealised securities loss.**
`AccumulatedOtherComprehensiveIncomeLossNetOfTax` is the whole AOCI balance — available-for-sale
marks aggregated with currency translation, pension and cash-flow-hedge adjustments — and
no filer in this benchmark tags the securities-only component. It is now
`accumulated_other_comprehensive_income`, and its direction is corrected: AOCI is signed,
so a more negative balance is a larger unrecognised hole and a *higher* reading means
*less* pressure. It sits in vulnerability rather than activated stress, because it records
a loss already absorbed into equity rather than one currently forcing action. Activated
stress is carried by `credit_spread_level` (BAA10Y), which is genuinely point-in-time.

### FRED API key support

Previously the code only *documented* that a key was needed. It is now implemented end to
end: `Settings.fred_api_key` (env `ASRO_FRED_API_KEY`), an entry in `.env.example`,
`ASRO_FRED_API_KEY` in the scheduled workflow, and `fetch_series_vintage()` calling the
official `api.stlouisfed.org/fred/series/observations` endpoint with `realtime_start` and
`realtime_end` both pinned to the requested vintage date.

Provenance now carries three distinct markings, and the gate treats them differently:

| Marking | Meaning | Gate |
|---|---|---|
| `as_published` | series is never materially revised | accepted |
| `point_in_time:<date>` | retrieved via `realtime_start=realtime_end=<date>` | accepted |
| `latest_revision` | today's revised value | **blocks** |

Only revised series are fetched as vintages; a series that is never revised does not need
an API call. The key is never written into a stored URL or into provenance, and there is a
test asserting that. Vintage acquisition is covered by mocked integration tests that check
the realtime bounds are actually sent, that an unpublished observation stays missing rather
than becoming zero, and that a genuine vintage satisfies the point-in-time rule while a
revision still does not.

#### The pipeline, not just the request

A single vintage request was not enough: nothing invoked it, and calling it by hand
against a database that already held revisions collided, because a control observation's
identity did not include its vintage. Both are fixed.

* **Migration 18** adds `vintage` as a *generated* column read from the provenance already
  stored on every row, and makes (series, version, period, vintage) the uniqueness rule.
  Nothing is rewritten — the table is append-only — so existing latest-revision rows keep
  their bytes and a vintage lands beside them instead of colliding with them.
* **`asro acquire-vintages`** reads `Settings.fred_api_key`, walks the accepted episodes,
  requests each episode's revised controls **at that episode's own cutoff**, ingests them,
  rebuilds every episode so its snapshot picks up the new vintage, and rewrites
  `readiness.json`. Without a key it exits 1 with an explanation rather than leaving
  today's revisions in place looking point-in-time.
* **The scheduled workflow calls that command, before packaging.** The order is restore
  state → collect and review → acquire vintages and rebuild episodes → regenerate every
  report → package the resulting database → build the site → validate against that same
  state pointer → publish. Acquiring after packaging would publish a database that
  predates its own evidence and leave the release check validating a pointer that does not
  describe what the site serves. `ASRO_FRED_API_KEY` sits in that step's own env, with
  `continue-on-error` so a missing secret leaves the gate honestly blocking rather than
  failing the run.
* **Every report is regenerated together** by `asro.benchmark.reports.write_benchmark_reports`,
  from one connection to the rebuilt database: readiness, coverage, leakage, missingness,
  revision and vintage, episode comparison, false-positive analysis and acquisition
  receipts. Refreshing readiness alone would leave the others describing an older build,
  and they would contradict the new verdict at exactly the moment it changed.
* **Each episode gets its own vintage date**, never one shared cut. The dates the accepted
  and near-accepted episodes need are 2017-01-31, 2023-01-31 and 2026-08-01.
* **`point_in_time:YYYY-MM-DD` is parsed strictly**, and the date must not postdate the
  episode's cutoff — a vintage cut later than the cutoff is a revision wearing a
  point-in-time label, and is refused.
* **The runner picks the right version** when several are stored: an exact match for this
  episode's cutoff, then a never-revised series, then anything else.

**No key is present in this environment**, so every revised series here remains
`latest_revision` and the blocker stands. Set `ASRO_FRED_API_KEY` and run
`asro acquire-vintages`; the gate then decides on its own.

### The one remaining blocker, and why it cannot be dodged

```
control series in accepted episodes are latest-revision, not point-in-time:
  commercial_industrial_loans, real_personal_consumption, unemployment_rate
```

**There is no FRED API key in this environment.** Not in the process environment, not in
`.env.example`, not in `settings.py`, not among the `monitor.yml` workflow secrets. The
API refuses without a registered key, and no keyless vintage route exists — the public CSV
endpoint silently returns *current* data for any `vintage_date`, including fabricated ones,
which is why nothing here requests one.

Removing the three revised series does not help, and this was computed rather than assumed:

| Configuration | Result |
|---|---|
| Keep revised controls | blocked: latest-revision, not point-in-time |
| Drop revised controls | `transmission` and `real_economy` lose every observed variable and become blocking |

Those four variables — `bank_exposure`, `credit_contraction`, `household_wealth_effect`,
`unemployment_change` — are the only measured evidence for two causal roles, and every
macro series that could serve them is revised. **A FRED API key with `realtime_start` is
the only thing that resolves this.** Everything else the gate asks for is now satisfied.

## How the gate is protected

A review of the first cut found three false-positive paths and no production enforcement.
All four are closed, each with a negative test and a matching positive test.

| Rule | Failure it prevents | Test |
|---|---|---|
| Episodes counted by distinct `episode_id` | a re-run or version bump making one crisis count as two | `test_a_re_run_of_one_crisis_does_not_count_as_two`, `test_repeated_runs_of_one_version_do_not_count_twice` |
| Evidence read only from finalized builds and frozen control snapshots of runs that passed both gates | a failed episode, live daily collection or an unrelated backfill supplying coverage | `test_evidence_from_a_failed_episode_does_not_count`, `test_evidence_that_never_reached_an_accepted_build_does_not_count` |
| A documented insufficiency reports a gap, never closes it | a written excuse standing in for a measurement | `test_a_documented_insufficiency_never_closes_a_causal_role` |
| Accepted control series must be `as_published` | latest-revision GDP, unemployment or lending passing as point-in-time | `test_revised_only_control_data_blocks_calibration` |
| `build_static_site` consults the gate before writing | the dashboard publishing an unsupported calibration claim | `test_publishing_cannot_claim_calibration_when_the_gate_fails`, `test_published_snapshot_always_carries_the_verdict` |

`test_the_gate_can_pass_when_every_rule_is_genuinely_satisfied` builds the minimum that
truly earns a pass — two distinct crisis episodes, one benign, one current, every causal
role measured through real observation-to-fact-to-build lineage, and point-in-time
controls — and asserts the verdict flips. `test_removing_any_single_requirement_reblocks_the_gate`
removes one input and asserts it flips back, so no rule is decorative.

Restricting evidence to accepted episodes cut observed catalog variables from 26 to 14 and
surfaced three blockers the earlier gate had hidden: activated stress is unmeasured, the
fixed-obligation ratio is absent, and three control series are revision-only.

### Publication enforcement

`build_static_site(output_dir, database_path, claimed_tier=OutputTier.HEURISTIC)` evaluates
readiness before writing any file and calls `assert_claim_supported`. A build claiming a
tier the evidence does not support raises and produces no output. Every published
`snapshot.json` carries `signal.calibration_label`, `signal.basis` and a `calibration`
block with the verdict, per-role coverage, revision-only series and every blocking reason.

## What blocks calibration

1. ~~Fewer than two accepted crisis episodes.~~ **Cleared:** shale-financing and
   regional-bank-stress both pass coverage and leakage.
2. **Dot-com and housing cannot be measured at entity level.** XBRL began in 2009. Cisco's
   earliest structured facts postdate the dot-com episode by five years; WorldCom, Global
   Crossing, Lehman, Bear Stearns and Countrywide have none. Reconstructing them requires
   parsing unstructured filing text, which was not attempted rather than approximated.
3. **Controls are not vintage-correct.** ALFRED vintages are unreachable from this
   environment: the public CSV endpoint silently ignores a vintage suffix and returns
   current data for any date, including fabricated dates. Every revised series (GDP,
   unemployment, private fixed investment, C&I lending, consumption) is therefore today's
   revision, labelled `latest_revision` in its provenance and disqualified from carrying a
   leakage-free backtest. Genuine vintages need a FRED API key with `realtime_start`.
4. **Credit spreads are unavailable before 2023.** The ICE BofA option-adjusted spread
   series are licensed and truncated to a rolling three-year window, removing the primary
   activated-stress control for every pre-2023 episode.
5. **The shared feature set does not fit banks.** Capital expenditure and product revenue
   are meaningless for a depository. A bank stratum needs bank-appropriate measurements
   (deposit beta, unrealized securities losses, uninsured deposit share).
6. ~~Activated stress has no measured evidence.~~ **Cleared:** `credit_spread_level`
   (BAA10Y, point-in-time) and `unrealized_securities_loss` (bank AOCI) are both measured
   inside accepted episodes.
7. **Three accepted control series are latest-revision, not point-in-time** —
   `commercial_industrial_loans`, `real_personal_consumption`, `unemployment_rate`. This
   is now the *only* blocker, and it clears only with a FRED API key. `fixed_obligations_to_external_cash`
   is present in both accepted crisis episodes and no longer blocks.
8. **Shock and transmission carry a documented insufficiency**
   (`data/benchmark/documented_insufficiency.json`). The reason is reported next to the
   gap; it does not close it.

## A repository change that this required

`backfill_source_cell_temporal_validate` previously required an observation's
`extracted_at` and its review's `reviewed_at` to precede the episode's availability
cutoff. Those timestamps record when this observatory did its own work, which for a
retrospective episode is necessarily now. The condition could never be satisfied for any
historical window, so every historical episode was permanently uncoverable regardless of
the evidence behind it.

Migration 17 (`historical_pipeline_time`) separates knowability time from pipeline time.
Still enforced: `observation.availability_at` and `assignment.available_at` must precede
the cutoff, and a superseding assignment available by the cutoff still voids the cell.
`extracted_at` and `reviewed_at` remain recorded on every row and remain ordered against
`availability_at` by the `observation_v2` check constraints; they are no longer used as
as-of filters because they answer a different question. `BackfillRunner._leakage_report`
is unchanged and still rejects any source or observation whose availability postdates the
cutoff.

This weakens no leakage guarantee, but it is a change to a stated integrity rule and
should be reviewed as one.

## Reproducibility

Two independent rebuilds produce byte-identical coverage cells, coverage metrics, gate
outcomes, observations, control observations and feature values. `run_id`,
`input_checksum` and `created_at` embed the wall clock and differ per run; the substantive
dataset does not.

## Public language

Until the readiness gate returns `HISTORICALLY_CALIBRATED`, any published reading should
say:

> This reading is a deterministic heuristic. It is not calibrated against historical
> crisis or benign investment cycles. Historical evidence has been collected for five
> episodes and two of them currently meet the coverage and leakage gates; the comparison
> below is descriptive.

It should not say "historically calibrated", "compared against past crises", or anything
implying an empirical reference frame, and `assert_claim_supported` will raise if it does.
