from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import typer

from asro.reviewer import EvidenceReviewer
from asro.service import MonitorService, RunSummary
from asro.settings import Settings
from asro.site import build_static_site

app = typer.Typer(
    help="AI Systemic Risk Observatory command-line interface.",
    no_args_is_help=True,
)
REVIEW_STATUS_PATH = Path("data/reviewer-status.json")


def _write_review_status(status: str, reviewed: int = 0, error: Exception | None = None) -> None:
    REVIEW_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    message = ""
    if error is not None:
        message = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", str(error))[:500]
    REVIEW_STATUS_PATH.write_text(
        json.dumps(
            {
                "status": status,
                "reviewed": reviewed,
                "error_type": type(error).__name__ if error else None,
                "message": message,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _report_health(summary: RunSummary) -> None:
    for name in summary.degraded:
        typer.echo(f"DEGRADED {name}: some documents could not be fetched", err=True)
    for name in summary.failed:
        typer.echo(f"FAILED   {name}: collector raised; its data was rolled back", err=True)


@app.command()
def run() -> None:
    """Collect new source items, extract events, and regenerate reports once."""
    service = MonitorService(Settings())
    summary = service.run()
    typer.echo(
        f"Collected {summary.new_items} new items. "
        f"Database now contains {service.event_count()} economic events."
    )
    _report_health(summary)
    if not summary.ok:
        raise typer.Exit(code=1)


@app.command()
def backfill(
    years: int = typer.Option(3, min=1, max=10),
    news_limit: int = typer.Option(500, min=1, max=1000),
    sec_per_company: int = typer.Option(24, min=1, max=100),
) -> None:
    """Collect a bounded historical news and SEC baseline once."""
    service = MonitorService(Settings())
    summary = service.backfill(
        years=years,
        news_limit=news_limit,
        sec_per_company=sec_per_company,
    )
    typer.echo(
        f"Historical baseline added {summary.new_items} documents. "
        f"Database now contains {service.event_count()} economic events."
    )
    _report_health(summary)
    if not summary.ok:
        raise typer.Exit(code=1)


@app.command()
def watch() -> None:
    """Continuously poll collectors at the configured interval."""
    settings = Settings()
    interval_seconds = max(5, settings.poll_interval_minutes * 60)

    typer.echo(
        f"Watching sources every {settings.poll_interval_minutes} minute(s). Press Ctrl+C to stop."
    )

    while True:
        service = MonitorService(settings)
        new_items = service.run()
        typer.echo(
            f"Collected {new_items} new items; {service.event_count()} total economic events."
        )
        time.sleep(interval_seconds)


@app.command()
def report() -> None:
    """Regenerate reports from the local database."""
    service = MonitorService(Settings())
    csv_path, html_path = service.report()
    typer.echo(f"CSV: {csv_path}")
    typer.echo(f"HTML: {html_path}")


@app.command("db-stats")
def db_stats() -> None:
    """Show local source-document and event counts."""
    service = MonitorService(Settings())
    typer.echo(f"Documents: {service.db_count()}")
    typer.echo(f"Economic events (deduplicated): {service.event_count()}")
    typer.echo(f"Event mentions (all sources): {service.mention_count()}")


@app.command()
def freshness() -> None:
    """Show latest collector status."""
    service = MonitorService(Settings())
    runs = service.freshness()

    if not runs:
        typer.echo("No collector runs recorded yet.")
        raise typer.Exit()

    for row in runs:
        typer.echo(
            f"{row['collector']}: {row['status']} | "
            f"started={row['started_at']} | completed={row['completed_at']} | "
            f"seen={row['items_seen']} | new={row['items_new']}"
        )


@app.command()
def events(limit: int = 25) -> None:
    """Print recently extracted financial events as JSON lines."""
    service = MonitorService(Settings())

    for row in service.events(limit=limit):
        typer.echo(json.dumps(dict(row), default=str))


@app.command("build-site")
def build_site() -> None:
    """Build the zero-server static dashboard into ./site."""
    path = build_static_site()
    typer.echo(f"Static site built at: {path}")


@app.command("seed-lineage")
def seed_lineage() -> None:
    """Add primary-source ownership and liability lineage to the evidence base."""
    added = MonitorService(Settings()).seed_lineage()
    typer.echo(f"Added {added} verified lineage facts.")


@app.command("rebuild-observations")
def rebuild_observations() -> None:
    """Recompute measurements after extraction or scoring-policy changes."""
    rebuilt = MonitorService(Settings()).rebuild_observations()
    typer.echo(f"Rebuilt {rebuilt} derived observations.")


@app.command()
def review(limit: int = 100, batch_size: int = 10) -> None:
    """Review provisional economic events with the configured evidence-review model."""
    try:
        reviewed = EvidenceReviewer(Settings()).run(limit=limit, batch_size=batch_size)
    except (ValueError, OSError) as exc:
        _write_review_status("error", error=exc)
        typer.echo(f"Evidence review failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _write_review_status("ok", reviewed=reviewed)
    typer.echo(f"Reviewed {reviewed} provisional economic events.")
