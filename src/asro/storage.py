from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from asro.models import FinancialEvent, ScoredItem
from asro.observations import Observation


class SqliteRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        self._initialize(connection)
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                discovered_at TEXT NOT NULL,
                published_at TEXT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                summary TEXT NOT NULL,
                score INTEGER NOT NULL,
                category TEXT NOT NULL,
                companies TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
                item_id TEXT PRIMARY KEY,
                fetched_at TEXT NOT NULL,
                content_type TEXT,
                fetch_status TEXT NOT NULL,
                text TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS economic_events (
                fingerprint TEXT PRIMARY KEY,
                canonical_event_id TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                mention_count INTEGER NOT NULL DEFAULT 1,
                review_status TEXT NOT NULL DEFAULT 'provisional',
                reviewed_at TEXT,
                reviewer_model TEXT,
                merged_into TEXT
            );

            CREATE TABLE IF NOT EXISTS system_snapshots (
                captured_at TEXT PRIMARY KEY,
                score REAL,
                label TEXT NOT NULL,
                dimensions TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS financial_events (
                event_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source_entity TEXT,
                target_entity TEXT,
                amount REAL,
                currency TEXT,
                instrument TEXT,
                effective_date TEXT,
                confidence REAL NOT NULL,
                evidence_text TEXT NOT NULL,
                extractor TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES items(id)
            );

            CREATE INDEX IF NOT EXISTS idx_events_type
                ON financial_events(event_type);

            CREATE INDEX IF NOT EXISTS idx_events_source_target
                ON financial_events(source_entity, target_entity);

            CREATE INDEX IF NOT EXISTS idx_items_published
                ON items(published_at);

            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY,
                event_id TEXT,
                variable_key TEXT NOT NULL,
                entity TEXT,
                value REAL NOT NULL,
                unit TEXT,
                observed_at TEXT NOT NULL,
                effective_date TEXT,
                confidence REAL NOT NULL,
                source_document_id TEXT NOT NULL,
                evidence_text TEXT NOT NULL,
                extractor TEXT NOT NULL,
                polarity TEXT NOT NULL,
                FOREIGN KEY(source_document_id) REFERENCES items(id)
            );

            CREATE INDEX IF NOT EXISTS idx_observations_variable
                ON observations(variable_key);

            CREATE INDEX IF NOT EXISTS idx_observations_date
                ON observations(effective_date);

            CREATE TABLE IF NOT EXISTS collector_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collector TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                items_seen INTEGER NOT NULL DEFAULT 0,
                items_new INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS evidence_reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL,
                decision TEXT NOT NULL,
                canonical_fingerprint TEXT,
                confidence REAL NOT NULL,
                reasoning TEXT NOT NULL,
                model TEXT NOT NULL,
                reviewed_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(economic_events)")}
        for name, declaration in {
            "review_status": "TEXT NOT NULL DEFAULT 'provisional'",
            "reviewed_at": "TEXT",
            "reviewer_model": "TEXT",
            "merged_into": "TEXT",
        }.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE economic_events ADD COLUMN {name} {declaration}")
        observation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(observations)")
        }
        if "event_id" not in observation_columns:
            connection.execute("ALTER TABLE observations ADD COLUMN event_id TEXT")
        connection.commit()

    @staticmethod
    def insert(connection: sqlite3.Connection, item: ScoredItem) -> bool:
        """Insert a source item. Returns False if it was already stored."""
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO items (
                id, discovered_at, published_at, source, title, url,
                summary, score, category, companies
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.item_id,
                item.discovered_at.isoformat(),
                item.published_at,
                item.source,
                item.title,
                str(item.url),
                item.summary,
                item.score,
                item.category.value,
                json.dumps(item.companies),
            ),
        )
        return cursor.rowcount == 1

    @staticmethod
    def upsert_document(
        connection: sqlite3.Connection,
        item_id: str,
        fetched_at: str,
        content_type: str,
        status: str,
        text: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO documents (item_id, fetched_at, content_type, fetch_status, text)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                fetched_at = excluded.fetched_at,
                content_type = excluded.content_type,
                fetch_status = excluded.fetch_status,
                text = excluded.text
            """,
            (item_id, fetched_at, content_type, status, text),
        )

    @staticmethod
    def register_economic_event(
        connection: sqlite3.Connection, fingerprint: str, event_id: str, seen_at: str
    ) -> bool:
        """Register a mention of an economic fact. Returns True the first time it is seen."""
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO economic_events (
                fingerprint, canonical_event_id, first_seen, last_seen, mention_count
            )
            VALUES (?, ?, ?, ?, 1)
            """,
            (fingerprint, event_id, seen_at, seen_at),
        )
        if cursor.rowcount == 1:
            return True
        connection.execute(
            """
            UPDATE economic_events
            SET last_seen = ?, mention_count = mention_count + 1
            WHERE fingerprint = ?
            """,
            (seen_at, fingerprint),
        )
        return False

    @staticmethod
    def insert_snapshot(
        connection: sqlite3.Connection,
        captured_at: str,
        score: float | None,
        label: str,
        dimensions: dict[str, float | None],
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO system_snapshots (captured_at, score, label, dimensions)
            VALUES (?, ?, ?, ?)
            """,
            (captured_at, score, label, json.dumps(dimensions)),
        )

    @staticmethod
    def recent_snapshots(connection: sqlite3.Connection, limit: int = 365) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                "SELECT * FROM system_snapshots ORDER BY captured_at DESC LIMIT ?", (limit,)
            )
        )

    @staticmethod
    def insert_event(connection: sqlite3.Connection, event: FinancialEvent) -> bool:
        """Insert an extracted event. Returns False if it was already stored."""
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO financial_events (
                event_id, document_id, event_type, source_entity, target_entity,
                amount, currency, instrument, effective_date, confidence,
                evidence_text, extractor, processed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.document_id,
                event.event_type.value,
                event.source_entity,
                event.target_entity,
                event.amount,
                event.currency,
                event.instrument,
                event.effective_date,
                event.confidence,
                event.evidence_text,
                event.extractor,
                event.processed_at.isoformat(),
            ),
        )
        return cursor.rowcount == 1

    @staticmethod
    def top_items(connection: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT *
                FROM items
                ORDER BY score DESC, discovered_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    @staticmethod
    def recent_events(connection: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT e.*, i.title, i.url, i.source, i.discovered_at, i.published_at
                FROM financial_events e
                JOIN items i ON i.id = e.document_id
                ORDER BY e.processed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    @staticmethod
    def all_financial_events(connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return list(connection.execute("SELECT * FROM financial_events ORDER BY processed_at"))

    @staticmethod
    def canonical_events(connection: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
        """One row per economic fact (first mention wins), with how many sources reported it.

        Duplicate articles about the same transaction stay in `financial_events` as
        provenance, but never reach the graph, timeline, or counts.
        """
        return list(
            connection.execute(
                """
                SELECT e.*, ec.fingerprint, ec.mention_count, ec.first_seen, ec.last_seen,
                       ec.review_status, ec.reviewed_at,
                       i.title, i.url, i.source, i.discovered_at, i.published_at
                FROM economic_events ec
                JOIN financial_events e ON e.event_id = ec.canonical_event_id
                JOIN items i ON i.id = e.document_id
                WHERE ec.review_status != 'merged'
                ORDER BY ec.first_seen DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    @staticmethod
    def canonical_event_count(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM economic_events WHERE review_status != 'merged'"
        ).fetchone()
        return int(row["count"])

    @staticmethod
    def insert_observation(connection: sqlite3.Connection, observation: Observation) -> bool:
        """Insert a measured observation. Returns False if it was already stored."""
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO observations (
                observation_id, event_id, variable_key, entity, value, unit, observed_at,
                effective_date, confidence, source_document_id, evidence_text,
                extractor, polarity
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.observation_id,
                observation.event_id,
                observation.variable_key,
                observation.entity,
                observation.value,
                observation.unit,
                observation.observed_at.isoformat(),
                observation.effective_date,
                observation.confidence,
                observation.source_document_id,
                observation.evidence_text,
                observation.extractor,
                observation.polarity,
            ),
        )
        return cursor.rowcount == 1

    @staticmethod
    def provisional_events(connection: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT ec.fingerprint, ec.canonical_event_id, ec.first_seen, ec.last_seen,
                       ec.mention_count, e.event_type, e.source_entity, e.target_entity,
                       e.amount, e.currency, e.effective_date, e.evidence_text,
                       i.title, i.url, i.source, i.published_at, d.text AS source_text
                FROM economic_events ec
                JOIN financial_events e ON e.event_id = ec.canonical_event_id
                JOIN items i ON i.id = e.document_id
                LEFT JOIN documents d ON d.item_id = i.id
                WHERE ec.review_status = 'provisional'
                ORDER BY ec.first_seen
                LIMIT ?
                """,
                (limit,),
            )
        )

    @staticmethod
    def flagged_events(
        connection: sqlite3.Connection, limit: int = 100, max_reviews: int = 2
    ) -> list[sqlite3.Row]:
        """Return quarantined events that have not yet received a second source-aware review."""
        return list(
            connection.execute(
                """
                SELECT ec.fingerprint, ec.canonical_event_id, ec.first_seen, ec.last_seen,
                       ec.mention_count, e.event_type, e.source_entity, e.target_entity,
                       e.amount, e.currency, e.effective_date, e.evidence_text,
                       i.title, i.url, i.source, i.published_at, d.text AS source_text,
                       COUNT(r.review_id) AS review_count,
                       (
                           SELECT prior.reasoning
                           FROM evidence_reviews prior
                           WHERE prior.fingerprint = ec.fingerprint
                           ORDER BY prior.review_id DESC
                           LIMIT 1
                       ) AS previous_flag_reason
                FROM economic_events ec
                JOIN financial_events e ON e.event_id = ec.canonical_event_id
                JOIN items i ON i.id = e.document_id
                LEFT JOIN documents d ON d.item_id = i.id
                LEFT JOIN evidence_reviews r ON r.fingerprint = ec.fingerprint
                WHERE ec.review_status = 'flagged'
                GROUP BY ec.fingerprint
                HAVING COUNT(r.review_id) < ?
                ORDER BY ec.reviewed_at, ec.first_seen
                LIMIT ?
                """,
                (max_reviews, limit),
            )
        )

    @staticmethod
    def apply_review(
        connection: sqlite3.Connection,
        fingerprint: str,
        decision: str,
        canonical_fingerprint: str | None,
        confidence: float,
        reasoning: str,
        model: str,
        reviewed_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO evidence_reviews (
                fingerprint, decision, canonical_fingerprint, confidence,
                reasoning, model, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fingerprint,
                decision,
                canonical_fingerprint,
                confidence,
                reasoning,
                model,
                reviewed_at,
            ),
        )
        if decision == "merge" and canonical_fingerprint and canonical_fingerprint != fingerprint:
            source = connection.execute(
                """
                SELECT canonical_event_id, mention_count
                FROM economic_events WHERE fingerprint = ?
                """,
                (fingerprint,),
            ).fetchone()
            target = connection.execute(
                "SELECT 1 FROM economic_events WHERE fingerprint = ? AND review_status != 'merged'",
                (canonical_fingerprint,),
            ).fetchone()
            if source is None or target is None:
                raise ValueError("Reviewer referenced an unknown or merged canonical event")
            connection.execute(
                """
                UPDATE economic_events SET mention_count = mention_count + ?
                WHERE fingerprint = ?
                """,
                (source["mention_count"], canonical_fingerprint),
            )
            connection.execute(
                "DELETE FROM observations WHERE event_id = ?", (source["canonical_event_id"],)
            )
            connection.execute(
                """
                UPDATE economic_events
                SET review_status = 'merged', reviewed_at = ?, reviewer_model = ?, merged_into = ?
                WHERE fingerprint = ?
                """,
                (reviewed_at, model, canonical_fingerprint, fingerprint),
            )
        else:
            status = "confirmed" if decision == "confirm" else "flagged"
            connection.execute(
                """
                UPDATE economic_events
                SET review_status = ?, reviewed_at = ?, reviewer_model = ?
                WHERE fingerprint = ?
                """,
                (status, reviewed_at, model, fingerprint),
            )

    @staticmethod
    def review_counts(connection: sqlite3.Connection) -> dict[str, int]:
        counts = {"provisional": 0, "confirmed": 0, "flagged": 0, "flagged_retry_pending": 0}
        for row in connection.execute(
            "SELECT review_status, COUNT(*) count FROM economic_events GROUP BY review_status"
        ):
            if row["review_status"] in counts:
                counts[row["review_status"]] = int(row["count"])
        retry_row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM economic_events ec
            WHERE ec.review_status = 'flagged'
              AND (
                  SELECT COUNT(*) FROM evidence_reviews r
                  WHERE r.fingerprint = ec.fingerprint
              ) < 2
            """
        ).fetchone()
        counts["flagged_retry_pending"] = int(retry_row["count"])
        return counts

    @staticmethod
    def recent_observations(connection: sqlite3.Connection, limit: int = 1000) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT o.*
                FROM observations o
                JOIN economic_events ec ON ec.canonical_event_id = o.event_id
                WHERE ec.review_status = 'confirmed'
                ORDER BY o.observed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    @staticmethod
    def count(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COUNT(*) AS count FROM items").fetchone()
        return int(row["count"])

    @staticmethod
    def event_count(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT COUNT(*) AS count FROM financial_events").fetchone()
        return int(row["count"])

    @staticmethod
    def start_collector_run(connection: sqlite3.Connection, collector: str, started_at: str) -> int:
        cur = connection.execute(
            """
            INSERT INTO collector_runs (collector, started_at, status)
            VALUES (?, ?, 'running')
            """,
            (collector, started_at),
        )
        connection.commit()
        assert cur.lastrowid is not None
        return int(cur.lastrowid)

    @staticmethod
    def finish_collector_run(
        connection: sqlite3.Connection,
        run_id: int,
        completed_at: str,
        status: str,
        items_seen: int,
        items_new: int,
        error: str | None = None,
    ) -> None:
        connection.execute(
            """
            UPDATE collector_runs
            SET completed_at = ?, status = ?, items_seen = ?, items_new = ?, error = ?
            WHERE id = ?
            """,
            (completed_at, status, items_seen, items_new, error, run_id),
        )
        connection.commit()

    @staticmethod
    def latest_runs(connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                """
                SELECT cr.*
                FROM collector_runs cr
                INNER JOIN (
                    SELECT collector, MAX(id) AS max_id
                    FROM collector_runs
                    GROUP BY collector
                ) latest ON latest.max_id = cr.id
                ORDER BY collector
                """
            )
        )
