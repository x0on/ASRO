# Live ingestion and freshness

ASRO is designed to be a continuously updated research system.

## Freshness target

The project target is:

- **Normal sources:** discovered within 24 hours
- **News / RSS:** target 1 hour
- **SEC EDGAR:** target 15-60 minutes
- **Company investor-relations feeds:** target 15-60 minutes
- **Daily / slower institutional sources:** within their publication day

The system records collector runs so freshness can be measured instead of assumed.

## Local watch mode

```bash
asro watch
```

Default:

```text
ASRO_POLL_INTERVAL_MINUTES=60
```

This is suitable for development.

## Production architecture

Do not rely on one permanent Python process in production.

Recommended production layout:

```text
Scheduler
   │
   ├── News collector        every 15-60 min
   ├── SEC collector         every 15-30 min
   ├── Company IR collector  every 15-30 min
   ├── Ratings / credit      every 1-6 h
   └── Pension / filings     daily
          │
          ▼
      Job queue
          │
          ▼
      Collectors
          │
          ▼
     Normalization
          │
          ▼
     Event extraction
          │
          ▼
      PostgreSQL
```

For V0, GitHub Actions cron or a small hosted scheduler is sufficient.

For production, use a managed scheduler plus workers.

Potential choices:

- GitHub Actions for early public prototype
- Fly.io / Render / Railway worker for simple continuous hosting
- Cloud Run Jobs + Cloud Scheduler
- AWS EventBridge + ECS/Fargate
- Temporal when workflows become complex

## Why different source schedules?

Polling every source every minute wastes resources and can violate rate limits.

Freshness should match source behavior.

For example, SEC and investor-relations feeds deserve frequent checks.
Annual pension reports do not.

## Source latency metric

Every future raw document should preserve:

```text
published_at
discovered_at
processed_at
```

From these we calculate:

```text
discovery_lag = discovered_at - published_at
processing_lag = processed_at - discovered_at
total_lag = processed_at - published_at
```

These become operational metrics.

## Reliability targets

Long-term production goals:

```text
95% of priority-source documents discovered < 2 hours
99% of priority-source documents discovered < 24 hours
collector errors visible immediately
no silent collector failures
```

## Important

"Live" does not mean unsafe aggressive scraping.

ASRO should still:

- prefer feeds and APIs
- respect rate limits
- avoid bypassing access controls
- use conditional HTTP requests where supported
- back off on failures
