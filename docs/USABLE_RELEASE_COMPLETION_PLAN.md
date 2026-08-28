# Shortest path to a usable ASRO release

## Runnable now

- The live collectors, SQLite store, deterministic extraction, evidence reviewer, reports, and
  static dashboard run end to end with `asro run`, `asro review`, and `asro build-site`.
- The current repository database contains 1,206 documents, 964 deduplicated economic events, and
  2,363 source mentions. A fresh static site builds successfully from it.
- One genuine V2 acceptance slice (Meta, October 2025) has finalized entity/ecosystem matrices and
  passes both coverage and leakage gates.

## Initial-release blockers

1. GitHub secrets must contain a descriptive SEC user agent and, for private review, an OpenAI key.
2. Every deployment must prove that the four current collectors came from one exact `asro run`
   invocation and that the subsequently generated site agrees with the database. `asro run` writes
   the execution-bound proof and `asro release-check` enforces it before Pages deployment.
3. The public release must remain labelled an evidence-monitoring prototype; it cannot claim a
   validated predictive model or complete historical coverage.

Run #107 is an unresolved historical collection gap, but it does not block an initial evidence-
monitoring release unless its precise hour is used by a published accepted dataset.

## Work that can continue after release

- Promote authoritative evidence for the remaining current-AI entity/month cells.
- Acquire historically appropriate control vintages.
- Complete the other six episode matrices.
- Prove and repair run #107 only when its exact collection interval is needed.

## Minimum threshold before a conservative baseline model

Do not model from the single accepted slice. The smallest defensible threshold is:

- at least three accepted strata (current, benign, and crisis);
- at least 24 accepted months in each stratum;
- at least four required entities per multi-entity episode;
- 100% source/control grid completion and at least 90% evidence-backed feature completion;
- no leakage violations, with time-ordered train/validation splits fixed before fitting.

This is a minimum for a conservative descriptive baseline, not for a production forecast.

## Exact next milestone

Ship an initial evidence-monitoring release candidate through the existing daily workflow. The
workflow must collect, build the static site, pass `asro release-check`, and deploy. In parallel,
expand the accepted current-AI slice from Meta/October 2025 to four entities across six consecutive
months; this is the shortest useful block toward the 24-month modeling threshold without starting
model development.
