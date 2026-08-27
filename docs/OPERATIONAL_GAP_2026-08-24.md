# Hourly monitor run 107 diagnosis

- Workflow run: `32781036876`, run number 107, commit
  `c6986ab190971d9d5196b0395a30a7a96fc180c0`.
- Scheduled/start: 2026-08-24 21:43 UTC; completed 21:44 UTC.
- Exact failed step: **Collect and extract** (step 7), from 21:44:06 to 21:44:33 UTC.
- Collection failure is confirmed. Site build, data commit, upload, and deployment were skipped;
  this was not a publish or deployment failure.
- The step-level error text remains unavailable because GitHub's log-download endpoint requires
  repository-admin authentication. Public job metadata classifies the stage but not the underlying
  collector exception.

Run 108 (`32786057614`) succeeded at 22:44 UTC and its commit added four items. Its persisted
collector ledger contains four successful current-state collectors, but no explicit replay of run
107's expected interval and no provenance linking it as a repair. Two added news items were
published before the failed run, showing some rediscovery, but this does not prove complete recovery.
Therefore the run-107 collection window remains an **unverified evidence gap**, not repaired.

Migrations 9–11 and the operations module now persist workflow identity, failure stage, exact target
windows, alerts, collector-run linkage, and append-only repair assessments. A later green run does
not close a failed window. `repair-window` records the exact target separately from the wider UTC
calendar-day acquisition range required by the available historical collectors. It can finalize only
when the two required collector runs are successful, explicitly linked to that repair execution, and
match both ranges. It is idempotent for an already repaired interval.

Migration 11 additionally requires every ordinary successful hourly window to prove the exact four
current collectors (`google-news-rss`, `company-economic-news`, `external-competitive-pressure`,
and `sec-edgar`) ran during that workflow and reached an acceptable terminal state. Collector runs
are single-use across workflows, and terminal or finalized-repair records cannot be rewritten.

No repair was executed for run 107: its status intentionally remains unresolved until authenticated
logs are available and a repair can produce the required database-verifiable proof.
