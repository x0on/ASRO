from __future__ import annotations

import json
import time

import typer

from asro.service import MonitorService, RunSummary
from asro.settings import Settings
from asro.site import build_static_site

app = typer.Typer(
    help="AI Systemic Risk Observatory command-line interface.",
    no_args_is_help=True,
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
