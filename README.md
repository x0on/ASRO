# AI Systemic Risk Observatory

**ASRO is a live early-warning system for financial risks created by the AI boom.**

It reads economic news and official company filings, verifies the evidence, and tracks whether financial risks are becoming more connected—and more likely to reach markets, pensions, and ordinary investors.

In simple terms, ASRO does three things:

1. **Collect** — Read economic news and official company filings.
2. **Verify** — Separate confirmed facts from rumors and duplicate reporting.
3. **Measure** — Update nine warning signals and the overall systemic-risk reading.

ASRO does not predict that a crash will happen. It measures whether the conditions that could produce one are strengthening or weakening.

**[Explore the live observatory](https://x0on.github.io/ASRO/)**

The project tracks signals across:

- AI infrastructure debt and refinancing
- vendor financing and guarantees
- private credit and structured finance
- mega-IPOs and index inclusion
- retirement / pension / target-date exposure
- AI pricing, margin compression, and cannibalization
- public filings and corporate disclosures

The long-term goal is not to build another news scraper. It is to build a **structured financial relationship graph** that can answer questions such as:

> Which institutions ultimately finance a given AI infrastructure project?

> Where does retirement capital enter the AI capital stack?

> Are AI revenues growing fast enough relative to infrastructure commitments?

> Which companies are simultaneously investors, suppliers, customers, creditors, or guarantors?

## Status

**Early public prototype / V0**

Current capabilities:

- Google News RSS discovery
- SEC EDGAR submissions monitoring
- deterministic scoring and categorization
- SQLite storage
- CSV exports
- simple HTML report
- clean plugin-style collector architecture
- structured financial-event extraction (deterministic V1)
- provenance-preserving event storage
- freshness and collector-health tracking
- typed Python code
- unit tests and CI

Planned roadmap:

1. Full-document ingestion + hybrid event extraction
2. Entity resolution
3. PostgreSQL backend
4. Relationship graph
5. External-cash-vs-capital metrics
6. Contradiction tracking
7. Risk indicators and alerts
8. Interactive dashboard

## Philosophy

This project should separate:

- **source facts**
- **derived relationships**
- **model interpretation**
- **research hypotheses**

No article, model output, or single metric should be treated as proof of a systemic-risk thesis.

Every extracted relationship should remain traceable to its source.

## Architecture

```text
Sources
  │
  ├── RSS / News
  ├── SEC EDGAR
  ├── Company IR
  └── Future collectors
       │
       ▼
Collectors
       │
       ▼
Normalization
       │
       ▼
Scoring / Classification
       │
       ▼
Repository
       │
       ├── SQLite (V0)
       └── PostgreSQL (planned)
       │
       ▼
Reports / Exports
```

Future:

```text
Documents
   │
   ▼
Financial Event Extraction
   │
   ▼
Entity Resolution
   │
   ▼
Relationship Graph
   │
   ├── Investor → Company
   ├── Lender → Borrower
   ├── Supplier → Customer
   ├── Guarantor → Project
   └── Pension → Fund → Asset
   │
   ▼
Thesis / Risk Engine
```

## Quick start

Requires Python 3.11+.

```bash
git clone https://github.com/YOUR_USERNAME/ai-systemic-risk-observatory.git
cd ai-systemic-risk-observatory

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

Create your environment file:

```bash
cp .env.example .env
```

Set a descriptive SEC user agent:

```text
ASRO_SEC_USER_AGENT="AI-Systemic-Risk-Observatory/0.1 your-email@example.com"
```

Run:

```bash
asro run
```

Reports are written to:

```text
data/reports/
```

Run tests:

```bash
pytest
```

Lint:

```bash
ruff check .
ruff format --check .
```

Type check:

```bash
mypy src
```


## Live monitoring

ASRO is intended to run continuously, not as a manual research script.

Target freshness:

- priority news / SEC / investor-relations sources: **15-60 minutes**
- normal monitored sources: **under 24 hours**
- slow institutional disclosures: **same publication day where practical**

For local development:

```bash
asro watch
```

For production, use scheduled workers rather than one long-running process.

See [docs/LIVE_INGESTION.md](docs/LIVE_INGESTION.md).


## CLI

```bash
asro run
asro report
asro db-stats
asro freshness
asro watch
asro review
asro feature-build --config feature-build.json
asro feature-audit --build-id BUILD_ID
asro historical-backfill --manifest episode.toml
asro candidate-data-quarantine --package candidate-directory
asro candidate-episode-support --package-id SHA256 --manifest-directory episode-directory
```

`feature-build` consumes a pinned JSON configuration for either `entity_month` or
`ecosystem_month`. `feature-audit` emits deterministic JSON quality and provenance metrics and only
reads finalized builds. See [the integrated migration plan](docs/INTEGRATED_MODEL_MIGRATION.md) for
the feature contract and build semantics.

`historical-backfill` freezes full document content and finalized feature builds into an immutable,
content-addressed episode run. Public availability and local fetch time remain separate. Finalized
runs require the exact entity/month/feature/source/control matrix, exact build provenance, and no
temporal leakage; the command does not run or delay live collectors.

`candidate-data-quarantine` is the only supported entry point for untrusted research corpora. It
hashes the archive and raw files, retains every candidate source edge and assertion, and creates no
production observations. Excerpts are not treated as documents. Candidates contribute zero
coverage until authoritative full documents are acquired and a reviewer promotes matching V2
evidence. Current corpus outcomes are in
[the Stage 3 candidate report](docs/STAGE3_CANDIDATE_ACCEPTANCE_REPORT.json).

## Configuration

Queries, monitored companies and SEC CIKs live in the packaged default:

```text
src/asro/default.toml
```

To customise without editing the package, copy it and point `ASRO_CONFIG_PATH` at your copy
(see `.env.example`). The CLI works from any directory.

Do not hard-code new monitored companies or search terms inside collectors.

## Adding a collector

Implement the `Collector` protocol (structural — no inheritance needed) and add an
instance to `MonitorService._collectors()` in `src/asro/service.py`:

```python
from asro.models import SourceItem


class MyCollector:
    name = "my-source"

    def collect(self) -> list[SourceItem]:
        return []
```

Collectors should:

- return normalized `SourceItem` objects
- avoid business logic
- avoid scoring
- avoid direct database writes
- respect source rate limits and terms of service

## Data principles

1. Preserve original URLs.
2. Preserve source publication time when available.
3. Deduplicate deterministically.
4. Never overwrite source facts with model-generated interpretations.
5. Store confidence separately from facts.
6. Make every relationship auditable back to source material.
7. Prefer APIs / feeds / filings over HTML scraping.
8. Use browser automation only when necessary.

## Provisional evidence and daily review

New economic events appear immediately as **provisional**, so the public observatory can
show developing activity without pretending every first report is final. A daily evidence
reviewer compares provisional events and records one of three auditable decisions:

- **confirmed** — this is a distinct economic event
- **merged** — another report describes an existing event
- **flagged** — conflicting or uncertain evidence needs human review

The reviewer never deletes source reports or silently rewrites evidence. Its decision,
confidence, reasoning, model and timestamp are stored in `evidence_reviews`. The public
dashboard shows how many events are awaiting review and labels provisional timeline entries.

For a new installation, a maintainer can manually run the **Hourly monitor** workflow with
**backfill** enabled. This creates a bounded three-year baseline across the configured news
queries and key SEC filings. It is deliberately separate from hourly collection so historical
research cannot delay the live monitor. Re-running it is safe because documents and economic
events are deduplicated.

The OpenAI API key must be stored as the GitHub Actions secret `ASRO_OPENAI_API_KEY`; it must
never appear in this repository or in browser-side code. The static site receives generated
JSON only. Set `ASRO_REVIEW_MODEL` to override the default reviewer model.

## Responsible crawling

This project is designed to use public, lawful sources.

Contributors should:

- obey robots directives where applicable
- respect publisher terms
- use rate limits
- avoid bypassing paywalls or authentication
- prefer official APIs, RSS, and public filings

## License

Apache License 2.0.

See [LICENSE](LICENSE).

## Contributing

Contributions are welcome.

Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the roadmap in [docs/ROADMAP.md](docs/ROADMAP.md).

If you are interested in finance, data engineering, graph databases, NLP, or investigative research, there is useful work to do even without touching the crawler.

## V1 event extraction

See [docs/V1_EVENT_MODEL.md](docs/V1_EVENT_MODEL.md).

## Integrated model migration

The next architecture separates **Observer** evidence, **Scientist** statistical inference, and
**Critic** falsification. The repository audit, V2 evidence contract, feature-store grain, baseline
methods, migration stages, risks, tests, and acceptance criteria are documented in
[docs/INTEGRATED_MODEL_MIGRATION.md](docs/INTEGRATED_MODEL_MIGRATION.md). The frozen V0 assumptions
are catalogued in [docs/LEGACY_SCORING_AUDIT.md](docs/LEGACY_SCORING_AUDIT.md).



## Zero-cost public deployment

The early observatory can run without a paid server:

```text
GitHub Actions (hourly ingestion)
        ↓
SQLite + generated JSON snapshot
        ↓
GitHub Pages static dashboard
```

See [docs/ZERO_COST_DEPLOYMENT.md](docs/ZERO_COST_DEPLOYMENT.md).

## Homepage philosophy

ASRO's homepage is a living financial nebula plus a historical timeline. The nebula encodes the relationship graph; the timeline explains when evidence changed the state of the hypothesis. See `docs/DASHBOARD_DESIGN.md`.

## Data dictionary

The observatory has two dictionaries, deliberately kept apart:

- [`src/asro/dictionary/registry.py`](src/asro/dictionary/registry.py) — the **executable** dictionary: the twelve variables the pipeline measures today, with the dimension, unit, direction and weight the scoring code actually reads.
- [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) — the **research specification**: the full target dictionary (roughly eighty variables across nine dimensions), source priorities, refresh cadences, interaction triggers and warning gates. Most of it is not implemented yet; it is the roadmap the registry grows toward.

The warning gate in `docs/DATA_DICTIONARY.md` ("Interaction triggers") is implemented verbatim in [`src/asro/indicators.py`](src/asro/indicators.py).
