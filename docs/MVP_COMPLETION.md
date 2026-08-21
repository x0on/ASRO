# ASRO MVP completion contract

The MVP is the 11-point pipeline agreed for the project:

1. source discovery
2. source normalization
3. event extraction
4. observation/Data Dictionary mapping
5. confidence and provenance
6. nine-dimension engine
7. convergence engine
8. network / money graph
9. timeline
10. public visualization
11. live operation

## Implementation status

The codebase is an end-to-end **prototype** of the pipeline: every stage exists and runs,
including full-page text retrieval, canonical entity aliases, economic-event deduplication
(graph, timeline and counts are built from deduplicated facts; repeat articles are kept only
as provenance), per-collector atomic ingestion, historical system snapshots, and the documented
two-group warning gate. Dimension scores use only the newest observation per variable and
entity inside a 90-day window, so repeat coverage cannot inflate them.

Parts of the measurement contract in `docs/DATA_DICTIONARY.md` are **not implemented yet** and
the public dashboard says so rather than guessing:

- trend / direction over time (`direction` is always `unknown`; snapshots are stored but not compared)
- stock-vs-flow aggregation (a 90-day latest-value window is applied uniformly to every variable)
- source-health gating of the convergence label
- the nine derived indicators (CEI, HTI, SPI, …) and interaction-pattern escalation
- novelty, network-centrality and acceleration terms of the scoring formula

## What “MVP complete” does not mean

It does not mean the model is scientifically validated. The weights, thresholds, entity ontology, source coverage and historical calibration require real observations and backtesting. Production deployment also requires the repository owner to enable GitHub Pages/Actions and configure the SEC user-agent secret.

## Evidence discipline

- unknown is not zero
- repeated articles are not repeated economic events
- original source text is stored when fetchable
- observations retain evidence and confidence
- high-convergence states require cross-dimensional evidence
- counter-evidence is first-class
