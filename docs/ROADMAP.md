# Roadmap

## V0 — Source monitor

- [x] RSS/news discovery
- [x] SEC filings
- [x] deterministic scoring
- [x] SQLite
- [x] CSV + HTML reports
- [x] typed package structure
- [x] tests
- [x] CI
- [x] collector run tracking
- [x] continuous watch mode
- [x] scheduled GitHub Actions prototype
- [x] freshness documentation

## V1 — Financial event extraction

- [x] source-document persistence
- [x] event schema
- [x] deterministic amount / currency extraction
- [x] first-pass counterparties
- [x] instrument classification
- [x] extraction confidence
- [x] evidence text / provenance
- [x] processed-at timestamp
- [ ] full article/document body ingestion
- [ ] LLM-assisted extraction for ambiguous prose
- [ ] canonical source hashing

## V2 — Entity resolution

- [ ] canonical organizations
- [ ] aliases
- [ ] subsidiaries
- [ ] funds
- [ ] projects / SPVs
- [ ] people where materially relevant

## V3 — Money graph

- [ ] investor relationships
- [ ] debt relationships
- [ ] supplier/customer relationships
- [ ] guarantees
- [ ] pension/fund look-through
- [ ] graph queries

## V4 — Thesis engine

- [ ] circular-financing indicator
- [ ] credit-stress indicator
- [ ] retirement-risk diffusion indicator
- [ ] cannibalization indicator
- [ ] external-cash-validation indicator
- [ ] contradiction tracking

## V5 — Research dashboard

- [ ] interactive company pages
- [ ] relationship visualization
- [ ] timelines
- [ ] source provenance
- [ ] alert subscriptions
- [ ] exportable research reports

## V6 — Early-warning research

Only after sufficient historical data exists:

- [ ] anomaly detection
- [ ] regime changes
- [ ] risk clustering
- [ ] scenario analysis

Predictive claims must remain explicitly probabilistic.
