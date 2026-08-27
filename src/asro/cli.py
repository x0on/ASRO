from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from asro.backfill import (
    BackfillRunner,
    EpisodeManifest,
    candidate_episode_support,
    ingest_candidate_package,
)
from asro.backfill.acquisition import acquire_inventory
from asro.backfill.negative_evidence import enumerate_negative_evidence_universe
from asro.evidence.time import normalize_timestamp
from asro.features import (
    EcosystemFeatureSpec,
    EcosystemFeatureStoreBuilder,
    FeatureSpec,
    FeatureStoreBuilder,
)
from asro.features.quality import audit_finalized_build
from asro.operations import WorkflowRunRecord, record_window_repair, record_workflow_run
from asro.release import validate_release, write_collection_proof
from asro.reviewer import EvidenceReviewer
from asro.service import MonitorService, RunSummary
from asro.settings import Settings
from asro.site import build_static_site
from asro.storage import SqliteRepository

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
    with SqliteRepository(Settings().database_path).connect() as connection:
        write_collection_proof(
            connection,
            Path("data/reports/current-run-proof.json"),
            collection_execution_id=summary.collection_execution_id,
            collector_run_ids=summary.collector_run_ids,
            workflow_run_id=os.getenv("GITHUB_RUN_ID"),
        )
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


@app.command("acceptance-acquire")
def acceptance_acquire(
    inventory: Annotated[Path, typer.Option()] = Path(
        "data/acceptance/current_ai_4x6_acquisition_inventory.json"
    ),
    output: Annotated[Path, typer.Option()] = Path("data/acceptance/acquired/current-ai-4x6"),
) -> None:
    """Reacquire declared authoritative candidates without accepting their claims."""
    settings = Settings()
    result = acquire_inventory(
        inventory,
        output,
        user_agent=settings.sec_user_agent,
    )
    typer.echo(json.dumps(result, sort_keys=True, separators=(",", ":")))


@app.command("acceptance-negative-universe")
def acceptance_negative_universe(
    inventory: Annotated[Path, typer.Option()] = Path(
        "data/acceptance/current_ai_4x6_negative_universe_inventory.json"
    ),
    receipts: Annotated[Path, typer.Option()] = Path(
        "data/acceptance/acquired/current-ai-negative-universe/acquisition-receipts.json"
    ),
    acquired: Annotated[Path, typer.Option()] = Path(
        "data/acceptance/acquired/current-ai-negative-universe"
    ),
    output: Annotated[Path, typer.Option()] = Path(
        "data/acceptance/current_ai_4x6_negative_universe_report.json"
    ),
) -> None:
    """Enumerate the bounded SEC review universe without creating numeric zeros."""
    result = enumerate_negative_evidence_universe(inventory, receipts, acquired)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(json.dumps(result, sort_keys=True, separators=(",", ":")))


@app.command("release-check")
def release_check(
    max_age_hours: float = typer.Option(26.0, min=1.0, max=168.0),
    proof: Annotated[Path, typer.Option()] = Path("data/reports/current-run-proof.json"),
) -> None:
    """Verify that the database and generated public site form a usable release."""
    settings = Settings()
    try:
        with SqliteRepository(settings.database_path).connect() as connection:
            result = validate_release(
                connection,
                Path("site/data/snapshot.json"),
                proof,
                max_age_hours=max_age_hours,
                expected_workflow_run_id=os.getenv("GITHUB_RUN_ID"),
            )
        typer.echo(json.dumps(result, sort_keys=True, separators=(",", ":")))
    except (OSError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        typer.echo(f"Release check failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


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
def review(limit: int = 100, batch_size: int = 10, retry_flagged_limit: int = 0) -> None:
    """Review new evidence and optionally retry quarantined events with source context."""
    try:
        reviewed = EvidenceReviewer(Settings()).run(
            limit=limit,
            batch_size=batch_size,
            retry_flagged_limit=retry_flagged_limit,
        )
    except (ValueError, OSError) as exc:
        _write_review_status("error", error=exc)
        typer.echo(f"Evidence review failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _write_review_status("ok", reviewed=reviewed)
    typer.echo(f"Reviewed or re-reviewed {reviewed} economic events.")


@app.command("feature-build")
def feature_build(
    config: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Build a finalized entity-month or ecosystem-month feature dataset from JSON config."""
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("feature build config must contain a JSON object")
        grain = payload.get("grain")
        with SqliteRepository(Settings().database_path).connect() as connection:
            if grain == "entity_month":
                specs = [FeatureSpec.model_validate(item) for item in payload["specs"]]
                result = FeatureStoreBuilder(connection).build_entity_month(
                    specs=specs,
                    availability_cutoff=payload["availability_cutoff"],
                    expected_entities=payload["expected_entities"],
                    code_commit=payload["code_commit"],
                    feature_set_version=payload["feature_set_version"],
                    period_start=payload["period_start"],
                    period_end=payload["period_end"],
                )
            elif grain == "ecosystem_month":
                ecosystem_specs = [
                    EcosystemFeatureSpec.model_validate(item) for item in payload["specs"]
                ]
                result = EcosystemFeatureStoreBuilder(connection).build_months(
                    source_entity_build_id=payload["source_entity_build_id"],
                    specs=ecosystem_specs,
                    code_commit=payload["code_commit"],
                    feature_set_version=payload["feature_set_version"],
                )
            else:
                raise ValueError("grain must be entity_month or ecosystem_month")
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        typer.echo(f"Feature build failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        json.dumps(
            {
                "build_id": result.build_id,
                "checksum": result.checksum,
                "row_count": result.row_count,
            },
            sort_keys=True,
        )
    )


@app.command("feature-audit")
def feature_audit(
    build_id: Annotated[str, typer.Option()],
    grain: Annotated[str, typer.Option()] = "auto",
    output: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
) -> None:
    """Emit a deterministic quality and provenance report for one finalized feature build."""
    try:
        with SqliteRepository(Settings().database_path).connect() as connection:
            report = audit_finalized_build(connection, build_id, grain)
        serialized = report.canonical_json()
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(f"{serialized}\n", encoding="utf-8")
            typer.echo(str(output))
        else:
            typer.echo(serialized)
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        typer.echo(f"Feature audit failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("historical-backfill")
def historical_backfill(
    manifest: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
    entity_build_id: Annotated[str | None, typer.Option()] = None,
    ecosystem_build_id: Annotated[str | None, typer.Option()] = None,
    output: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
) -> None:
    """Freeze one historical episode and emit deterministic coverage/leakage results."""
    try:
        episode = EpisodeManifest.from_toml(manifest)
        with SqliteRepository(Settings().database_path).connect() as connection:
            result = BackfillRunner(connection).run(
                episode,
                entity_build_id=entity_build_id,
                ecosystem_build_id=ecosystem_build_id,
            )
            stored = connection.execute(
                """SELECT coverage_json, leakage_json FROM finalized_backfill_run
                   WHERE run_id = ?""",
                (result.run_id,),
            ).fetchone()
        payload = json.dumps(
            {
                "run_id": result.run_id,
                "input_checksum": result.input_checksum,
                "source_count": result.source_count,
                "coverage_passed": result.coverage_passed,
                "leakage_passed": result.leakage_passed,
                "coverage": json.loads(str(stored["coverage_json"])),
                "leakage": json.loads(str(stored["leakage_json"])),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(f"{payload}\n", encoding="utf-8")
            typer.echo(str(output))
        else:
            typer.echo(payload)
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError, RuntimeError) as exc:
        typer.echo(f"Historical backfill failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("candidate-data-quarantine")
def candidate_data_quarantine(
    package: Annotated[Path, typer.Option(exists=True, file_okay=False, readable=True)],
) -> None:
    """Validate and quarantine a research package without promoting it to evidence."""
    try:
        with SqliteRepository(Settings().database_path).connect() as connection:
            result = ingest_candidate_package(connection, package)
        typer.echo(json.dumps(result.__dict__, sort_keys=True, separators=(",", ":")))
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        typer.echo(f"Candidate quarantine failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("candidate-episode-support")
def candidate_support_report(
    package_id: Annotated[str, typer.Option()],
    manifest_directory: Annotated[Path, typer.Option(exists=True, file_okay=False)],
) -> None:
    """Report candidate discovery separately from promoted episode evidence."""
    try:
        manifests = [
            EpisodeManifest.from_toml(path) for path in sorted(manifest_directory.glob("*.toml"))
        ]
        with SqliteRepository(Settings().database_path).connect() as connection:
            report = candidate_episode_support(connection, package_id, manifests)
        typer.echo(json.dumps(report, sort_keys=True, separators=(",", ":")))
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        typer.echo(f"Candidate support report failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("workflow-record")
def workflow_record(
    input_path: Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Persist one scheduled workflow identity, outcome, and collection window."""
    try:
        record = WorkflowRunRecord(**json.loads(input_path.read_text(encoding="utf-8")))
        with SqliteRepository(Settings().database_path).connect() as connection:
            assessment_id = record_workflow_run(connection, record)
        typer.echo(assessment_id)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        typer.echo(f"Workflow provenance failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("repair-window")
def repair_window(
    window_start: Annotated[str, typer.Option()],
    window_end: Annotated[str, typer.Option()],
    workflow_run_id: Annotated[str, typer.Option()],
) -> None:
    """Idempotently re-query a recorded day-bounded acquisition range for one exact gap."""
    settings = Settings()
    start = normalize_timestamp(window_start)
    end = normalize_timestamp(window_end)
    with SqliteRepository(settings.database_path).connect() as connection:
        existing = connection.execute(
            """SELECT assessment_id FROM current_collection_window
               WHERE window_start=? AND window_end=? AND status='repaired'""",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        ).fetchone()
    if existing is not None:
        typer.echo(str(existing[0]))
        return
    summary, repair_execution_id = MonitorService(settings).repair_interval(start, end)
    if not summary.ok:
        _report_health(summary)
        raise typer.Exit(code=1)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with SqliteRepository(settings.database_path).connect() as connection:
        assessment = record_window_repair(
            connection,
            WorkflowRunRecord(
                workflow_run_id=workflow_run_id,
                run_number=0,
                run_attempt=1,
                workflow_name="interval-repair",
                head_sha="manual-repair",
                event_name="workflow_dispatch",
                scheduled_for=now,
                started_at=now,
                completed_at=datetime.now(UTC).isoformat(timespec="seconds"),
                conclusion="success",
                failure_stage=None,
                steps=[{"name": "bounded-source-repair", "conclusion": "success"}],
                window_start=start.isoformat(timespec="seconds"),
                window_end=end.isoformat(timespec="seconds"),
                collector_runs=summary.collector_run_ids,
                repair_execution_id=repair_execution_id,
            ),
        )
    typer.echo(assessment)
