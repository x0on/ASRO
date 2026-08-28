# Zero-cost deployment architecture

ASRO is intentionally designed so the early public observatory can run without a paid server.

## V0 deployment

```text
GitHub public repository
        │
        ├── GitHub Actions (daily at 10:17 UTC)
        │      ├── collect sources
        │      ├── extract events
        │      ├── update SQLite
        │      └── build static snapshot
        │
        ▼
     site/
        │
        ▼
GitHub Pages
```

There is no application server. The browser loads a generated JSON snapshot.

## Limits

Git is not a database. Migrate away from repository-persisted SQLite when:

- the database approaches roughly 50-100 MB
- repository history grows rapidly
- multiple writers are needed
- graph queries become interactive
- full source-document storage becomes large

## Local operation

```bash
asro watch
asro build-site
python -m http.server 8000 -d site
```

## Freshness

The included workflow runs once per day at 10:17 UTC. Manual dispatch remains available for
operator-triggered collection, while historical backfill remains a separate non-deploying path.

## GitHub Pages setup

Repository Settings → Pages → Source: **GitHub Actions**.

## Required secret

```text
ASRO_SEC_USER_AGENT
```

Example:

```text
AI-Systemic-Risk-Observatory/0.1 research-contact@example.com
```
