from __future__ import annotations

import hashlib
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from asro.collectors.base import Collector
from asro.collectors.external_pressure import ExternalPressureCollector
from asro.collectors.google_news import (
    CompanyEconomicNewsCollector,
    GoogleNewsCollector,
    HistoricalGoogleNewsCollector,
)
from asro.collectors.sec import HistoricalSecCollector, SecCollector
from asro.dedupe import economic_fingerprint
from asro.documents import DocumentFetcher
from asro.extraction.deterministic import DeterministicEventExtractor
from asro.indicators import compute_convergence, compute_dimension_scores
from asro.lineage import seed_verified_lineage
from asro.measurement import event_to_observation
from asro.models import FinancialEvent, ScoredItem
from asro.reporting import write_csv, write_html
from asro.scoring import score
from asro.settings import Settings, load_project_config
from asro.storage import SqliteRepository


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class _CollectorStats:
    seen: int = 0
    new: int = 0
    fetch_failures: int = 0


@dataclass
class RunSummary:
    new_items: int = 0
    failed: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    collector_run_ids: list[int] = field(default_factory=list)
    collection_execution_id: str = ""

    @property
    def ok(self) -> bool:
        return not self.failed


class MonitorService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._config = load_project_config(settings.config_path)
        self._repository = SqliteRepository(settings.database_path)
        self._fetcher = DocumentFetcher(settings.sec_user_agent)
        self._extractor = DeterministicEventExtractor(self._config["entities"]["companies"])

    def _collectors(self) -> list[Collector]:
        news_cfg = self._config["news"]
        sec_cfg = self._config["sec"]
        today = datetime.now(UTC).date()

        return [
            GoogleNewsCollector(news_cfg["queries"], max_items=len(news_cfg["queries"]) * 2),
            CompanyEconomicNewsCollector(
                self._company_news_queries(),
                since=today - timedelta(days=2),
                until=today + timedelta(days=1),
                max_items=36,
            ),
            ExternalPressureCollector(),
            SecCollector(
                sec_cfg["companies"],
                user_agent=self._settings.sec_user_agent,
            ),
        ]

    def _company_news_queries(self) -> list[str]:
        companies = dict.fromkeys(self._config["entities"]["companies"])
        economic_terms = (
            "earnings OR revenue OR debt OR financing OR investment OR capex OR "
            "acquisition OR valuation OR IPO OR layoffs OR partnership"
        )
        return [f'"{company}" ({economic_terms})' for company in companies]

    def run(
        self,
        collectors: list[Collector] | None = None,
        repair_provenance: tuple[str, str, str, str, str] | None = None,
    ) -> RunSummary:
        """Run every collector once. Each collector is atomic: its items, documents, events
        and observations are committed together or rolled back together. The run record
        itself is committed separately so a failed collector still leaves an audit row.
        """
        monitor_cfg = self._config["monitor"]
        companies = self._config["entities"]["companies"]
        summary = RunSummary(collection_execution_id=str(uuid.uuid4()))
        repair_values = repair_provenance or (None, None, None, None, None)

        with self._repository.connect() as connection:
            for collector in self._collectors() if collectors is None else collectors:
                # Commits: nothing else is pending, so only the run row is persisted here.
                run_id = self._repository.start_collector_run(
                    connection,
                    collector.name,
                    _now(),
                    repair_execution_id=repair_values[0],
                    target_window_start=repair_values[1],
                    target_window_end=repair_values[2],
                    acquisition_start=repair_values[3],
                    acquisition_end=repair_values[4],
                    collection_execution_id=summary.collection_execution_id,
                )
                summary.collector_run_ids.append(run_id)
                stats = _CollectorStats()

                try:
                    for item in collector.collect():
                        stats.seen += 1
                        scored = score(item, companies)
                        if not self._repository.insert(connection, scored):
                            continue
                        stats.new += 1
                        self._ingest(connection, scored, stats)
                    connection.commit()
                    status = "degraded" if stats.fetch_failures else "ok"
                    error = (
                        f"{stats.fetch_failures} of {stats.new} document fetches failed"
                        if stats.fetch_failures
                        else None
                    )
                except Exception as exc:
                    connection.rollback()
                    status, error = "error", f"{type(exc).__name__}: {exc}"
                    summary.failed.append(collector.name)
                else:
                    summary.new_items += stats.new
                    if status == "degraded":
                        summary.degraded.append(collector.name)

                # Commits the run row (and, after a rollback, nothing else).
                self._repository.finish_collector_run(
                    connection, run_id, _now(), status, stats.seen, stats.new, error
                )
                if repair_provenance is not None:
                    connection.execute(
                        "INSERT INTO repair_execution_collector VALUES(?,?)",
                        (repair_provenance[0], run_id),
                    )
                    connection.commit()
                time.sleep(float(monitor_cfg["request_delay_seconds"]))

            observations = [
                dict(r) for r in self._repository.recent_observations(connection, limit=5000)
            ]
            dimensions = compute_dimension_scores(observations)
            convergence = compute_convergence(dimensions)
            self._repository.insert_snapshot(
                connection, _now(), convergence.score, convergence.label, dimensions
            )
            connection.commit()
            self._write_reports(connection)

        return summary

    def backfill(
        self,
        years: int = 3,
        news_limit: int = 500,
        sec_per_company: int = 24,
    ) -> RunSummary:
        """Build a bounded historical baseline without changing the hourly collectors."""
        today = datetime.now(UTC).date()
        since = today - timedelta(days=365 * years)
        config = self._config
        collectors: list[Collector] = [
            HistoricalGoogleNewsCollector(
                [*config["news"]["queries"], *self._company_news_queries()],
                since=since,
                until=today + timedelta(days=1),
                max_items=news_limit,
            ),
            HistoricalSecCollector(
                config["sec"]["companies"],
                user_agent=self._settings.sec_user_agent,
                since=since,
                max_per_company=sec_per_company,
            ),
        ]
        return self.run(collectors)

    def repair_interval(self, since: datetime, until: datetime) -> tuple[RunSummary, str]:
        """Re-query day-bounded sources while preserving the narrower target interval."""
        if until <= since:
            raise ValueError("repair interval end must follow its start")
        config = self._config
        acquisition_start = datetime.combine(since.date(), datetime.min.time(), UTC)
        acquisition_end = datetime.combine(
            until.date() + timedelta(days=1), datetime.min.time(), UTC
        )
        started_at = datetime.now(UTC).isoformat(timespec="seconds")
        repair_execution_id = hashlib.sha256(
            f"{since.isoformat()}|{until.isoformat()}|{started_at}".encode()
        ).hexdigest()
        provenance = (
            repair_execution_id,
            since.isoformat(timespec="seconds"),
            until.isoformat(timespec="seconds"),
            acquisition_start.isoformat(timespec="seconds"),
            acquisition_end.isoformat(timespec="seconds"),
        )
        with self._repository.connect() as connection:
            connection.execute(
                "INSERT INTO repair_execution VALUES(?,?,?,?,?,?)",
                (*provenance, started_at),
            )
            connection.commit()
        collectors: list[Collector] = [
            HistoricalGoogleNewsCollector(
                [*config["news"]["queries"], *self._company_news_queries()],
                since=acquisition_start.date(),
                until=acquisition_end.date(),
                max_items=500,
            ),
            HistoricalSecCollector(
                config["sec"]["companies"],
                user_agent=self._settings.sec_user_agent,
                since=acquisition_start.date(),
                until=acquisition_end.date(),
                max_per_company=100,
            ),
        ]
        summary = self.run(collectors, repair_provenance=provenance)
        if summary.ok and not summary.degraded:
            with self._repository.connect() as connection:
                connection.execute(
                    "INSERT INTO repair_execution_finalization VALUES(?,?)",
                    (repair_execution_id, datetime.now(UTC).isoformat(timespec="seconds")),
                )
                connection.commit()
        return summary, repair_execution_id

    def _ingest(
        self, connection: sqlite3.Connection, scored: ScoredItem, stats: _CollectorStats
    ) -> None:
        """Fetch the full document, extract events, and record new economic facts."""
        fetched = self._fetcher.fetch(str(scored.url))
        if fetched.status != "ok":
            stats.fetch_failures += 1
        self._repository.upsert_document(
            connection, scored.item_id, _now(), fetched.content_type, fetched.status, fetched.text
        )
        for event in self._extractor.extract(scored, fetched.text):
            is_new_fact = self._repository.register_economic_event(
                connection, economic_fingerprint(event), event.event_id, _now()
            )
            self._repository.insert_event(connection, event)
            if is_new_fact and (observation := event_to_observation(event)) is not None:
                self._repository.insert_observation(connection, observation)

    def report(self) -> tuple[Path, Path]:
        with self._repository.connect() as connection:
            return self._write_reports(connection)

    def db_count(self) -> int:
        with self._repository.connect() as connection:
            return self._repository.count(connection)

    def seed_lineage(self) -> int:
        return seed_verified_lineage(self._repository)

    def rebuild_observations(self) -> int:
        """Recompute the derived measurement layer from stored source events."""
        inserted = 0
        with self._repository.connect() as connection:
            events = self._repository.all_financial_events(connection)
            connection.execute("DELETE FROM observations")
            for row in events:
                event = FinancialEvent.model_validate(dict(row))
                if observation := event_to_observation(event):
                    inserted += int(self._repository.insert_observation(connection, observation))
            connection.commit()
        return inserted

    def event_count(self) -> int:
        """Canonical economic events (deduplicated facts)."""
        with self._repository.connect() as connection:
            return self._repository.canonical_event_count(connection)

    def mention_count(self) -> int:
        """Every extracted event, including repeat reporting of the same fact."""
        with self._repository.connect() as connection:
            return self._repository.event_count(connection)

    def freshness(self) -> list[sqlite3.Row]:
        with self._repository.connect() as connection:
            return self._repository.latest_runs(connection)

    def events(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._repository.connect() as connection:
            return self._repository.recent_events(connection, limit=limit)

    def _write_reports(self, connection: sqlite3.Connection) -> tuple[Path, Path]:
        monitor_cfg = self._config["monitor"]
        rows = self._repository.top_items(
            connection,
            limit=int(monitor_cfg["report_limit"]),
        )
        report_dir = Path("data/reports")

        csv_path = write_csv(rows, report_dir)
        html_path = write_html(
            rows,
            report_dir,
            high_signal_threshold=int(monitor_cfg["high_signal_threshold"]),
        )
        return csv_path, html_path
