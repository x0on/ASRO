from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from asro.evidence.time import normalize_timestamp

CURRENT_COLLECTORS = {
    "google-news-rss",
    "company-economic-news",
    "external-competitive-pressure",
    "sec-edgar",
}


def write_collection_proof(
    connection: sqlite3.Connection,
    path: Path,
    *,
    collection_execution_id: str,
    collector_run_ids: list[int],
    workflow_run_id: str | None,
) -> None:
    rows = _proof_rows(connection, collector_run_ids)
    payload = {
        "collection_execution_id": collection_execution_id,
        "collector_run_ids": sorted(collector_run_ids),
        "workflow_run_id": workflow_run_id,
        # Keep full precision so a collector completed in this same second cannot
        # appear to finish after the proof that records it.
        "created_at": datetime.now(UTC).isoformat(timespec="microseconds"),
        "collectors": [str(row["collector"]) for row in rows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def validate_release(
    connection: sqlite3.Connection,
    snapshot_path: Path,
    proof_path: Path,
    *,
    max_age_hours: float,
    expected_workflow_run_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    current_time = now or datetime.now(UTC)
    generated_at = normalize_timestamp(str(payload["generated_at"]))
    proof_created_at = normalize_timestamp(str(proof["created_at"]))
    earliest = current_time - timedelta(hours=max_age_hours)
    if generated_at < earliest:
        raise ValueError("site snapshot is stale")
    if generated_at > current_time + timedelta(minutes=5):
        raise ValueError("site snapshot timestamp is in the future")
    if proof_created_at < earliest or proof_created_at > generated_at:
        raise ValueError("collection proof is stale or newer than the site")
    if (
        expected_workflow_run_id is not None
        and proof.get("workflow_run_id") != expected_workflow_run_id
    ):
        raise ValueError("collection proof belongs to a different workflow execution")
    run_ids = proof.get("collector_run_ids")
    if not isinstance(run_ids, list) or len(run_ids) != 4 or len(set(run_ids)) != 4:
        raise ValueError("collection proof must contain exactly four unique run IDs")
    rows = _proof_rows(connection, [int(value) for value in run_ids])
    execution_id = str(proof["collection_execution_id"])
    if len(rows) != 4 or {str(row["collector"]) for row in rows} != CURRENT_COLLECTORS:
        raise ValueError("collection proof does not contain the exact current collector set")
    if {str(row["collection_execution_id"]) for row in rows} != {execution_id}:
        raise ValueError("collector runs come from mixed collection invocations")
    for row in rows:
        name = str(row["collector"])
        if str(row["status"]) != "ok" or row["completed_at"] is None:
            raise ValueError(f"collector is not releaseable: {name}")
        started = normalize_timestamp(str(row["started_at"]))
        completed = normalize_timestamp(str(row["completed_at"]))
        if started < earliest or completed < started or completed > proof_created_at:
            raise ValueError(f"collector timing is invalid: {name}")
        if generated_at < completed:
            raise ValueError("site predates its collection proof")
    # Mirror the exact bounded queries used to serialize the public snapshot.
    document_count = int(
        connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT 1 FROM items
                   ORDER BY score DESC, discovered_at DESC LIMIT 1500
               )"""
        ).fetchone()[0]
    )
    event_count = int(
        connection.execute(
            """SELECT COUNT(*) FROM (
                   SELECT 1
                   FROM economic_events ec
                   JOIN financial_events e ON e.event_id = ec.canonical_event_id
                   JOIN items i ON i.id = e.document_id
                   WHERE ec.review_status != 'merged'
                   ORDER BY ec.first_seen DESC LIMIT 5000
               )"""
        ).fetchone()[0]
    )
    mention_count = int(connection.execute("SELECT COUNT(*) FROM financial_events").fetchone()[0])
    expected_counts = (document_count, event_count, mention_count)
    snapshot_counts = (
        int(payload["document_count"]),
        int(payload["event_count"]),
        int(payload["mention_count"]),
    )
    if min(expected_counts) < 1 or snapshot_counts != expected_counts:
        raise ValueError("site snapshot counts are empty or disagree with the database")
    return {
        "releaseable": True,
        "collection_execution_id": execution_id,
        "workflow_run_id": proof.get("workflow_run_id"),
        "generated_at": generated_at.isoformat(),
        "document_count": document_count,
        "event_count": event_count,
        "mention_count": mention_count,
        "collectors": sorted(CURRENT_COLLECTORS),
        "collector_run_ids": sorted(int(value) for value in run_ids),
    }


def _proof_rows(connection: sqlite3.Connection, run_ids: list[int]) -> list[sqlite3.Row]:
    if not run_ids:
        return []
    placeholders = ",".join("?" for _ in run_ids)
    return list(
        connection.execute(
            f"SELECT * FROM collector_runs WHERE id IN ({placeholders}) ORDER BY id", run_ids
        )
    )  # noqa: S608
