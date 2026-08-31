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

| Episode | Stratum | Observations | Coverage | Leakage |
|---|---|---:|---|---|
| shale-financing | crisis | 1,171 | FAIL (features 87.8% vs 90%) | PASS |
| regional-bank-stress | crisis | 636 | FAIL (features 56.4%, sources 70.8%) | PASS |
| benign-infrastructure-capex | benign | 930 | FAIL (features 84.9%) | PASS |
| pandemic-technology-acceleration | benign | 814 | **PASS** | PASS |
| current-ai-cycle | current | 1,424 | **PASS** | PASS |
| dotcom-telecom | crisis | — | not measurable | — |
| housing-credit | crisis | — | not measurable | — |

Accepted: **0 crisis, 1 benign, 1 current.** The minimum is two crisis episodes, so the
verdict is `NOT_YET_CALIBRATED` on the count alone.

The shale episode is nonetheless a real reconstruction and behaves as the history says it
should: Chesapeake Energy at $13.5–14.7bn of annual capital expenditure against free cash
flow of −$8bn to −$12bn through 2010–2012, collapsing to $1.8bn of capex by 2017;
Halliburton's revenue falling from $32.9bn (2014) to $15.9bn (2016).

### The most important result is negative

Across all five measurable episodes, **none of the eight shared features separates a
crisis episode from a benign boom.** Every crisis range overlaps a benign range.

Excluding the regional-bank episode — banks report neither capital expenditure nor product
revenue, so the industrial feature set does not apply to them — three of eight features
separate the shale crisis from the two benign booms: free cash flow (negative versus
positive), capital expenditure to revenue (0.344 versus 0.112–0.215), and liquidity runway
(0.8 months versus 5.3–67). With one crisis episode this is a hypothesis about which
measurements might matter, not evidence that they do.

On those same three features the current AI cycle presently sits with the benign episodes
rather than the crisis one. That statement covers only the five largest filers with SEC
reporting obligations. It excludes OpenAI, the neoclouds, and the special-purpose vehicles
where the leverage of this cycle actually sits, and it should not be read as a reading of
the cycle as a whole.

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

1. **Fewer than two accepted crisis episodes.** The binding constraint.
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
6. **Activated stress has no measured evidence in any accepted episode.** Impairments are
   ingested, but only into episodes that failed their coverage gate.
7. **Three accepted control series are latest-revision, not point-in-time** —
   `commercial_industrial_loans`, `real_personal_consumption`, `unemployment_rate`. This
   alone blocks calibration regardless of episode counts, and clears only with a FRED API
   key providing genuine vintages.
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
