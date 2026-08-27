import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from asro.release import CURRENT_COLLECTORS, validate_release


def _inputs(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """CREATE TABLE documents(id TEXT);
           CREATE TABLE economic_events(id TEXT,review_status TEXT);
           CREATE TABLE financial_events(id TEXT);
           CREATE TABLE collector_runs(
             id INTEGER PRIMARY KEY,collector TEXT,started_at TEXT,completed_at TEXT,
             status TEXT,collection_execution_id TEXT);
           INSERT INTO documents VALUES('document');
           INSERT INTO economic_events VALUES('fact','confirmed');
           INSERT INTO financial_events VALUES('mention');"""
    )
    for run_id, collector in enumerate(sorted(CURRENT_COLLECTORS), start=1):
        connection.execute(
            "INSERT INTO collector_runs VALUES(?,?,?,?,?,?)",
            (
                run_id,
                collector,
                "2026-08-27T12:00:00+00:00",
                "2026-08-27T12:10:00+00:00",
                "ok",
                "execution-current",
            ),
        )
    snapshot = tmp_path / "snapshot.json"
    proof = tmp_path / "proof.json"
    snapshot.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-27T12:30:00+00:00",
                "document_count": 1,
                "event_count": 1,
                "mention_count": 1,
            }
        ),
        encoding="utf-8",
    )
    proof.write_text(
        json.dumps(
            {
                "collection_execution_id": "execution-current",
                "collector_run_ids": [1, 2, 3, 4],
                "workflow_run_id": "workflow-current",
                "created_at": "2026-08-27T12:15:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return connection, snapshot, proof


def _validate(connection: sqlite3.Connection, snapshot: Path, proof: Path) -> dict[str, object]:
    return validate_release(
        connection,
        snapshot,
        proof,
        max_age_hours=2,
        expected_workflow_run_id="workflow-current",
        now=datetime(2026, 8, 27, 13, tzinfo=UTC),
    )


def test_release_accepts_one_exact_successful_collection(tmp_path: Path) -> None:
    connection, snapshot, proof = _inputs(tmp_path)
    assert _validate(connection, snapshot, proof)["releaseable"] is True


@pytest.mark.parametrize("status", ["error", "degraded", "running"])
def test_release_rejects_non_ok_policy(tmp_path: Path, status: str) -> None:
    connection, snapshot, proof = _inputs(tmp_path)
    connection.execute("UPDATE collector_runs SET status=? WHERE id=1", (status,))
    with pytest.raises(ValueError, match="not releaseable"):
        _validate(connection, snapshot, proof)


def test_release_rejects_mixed_prior_invocations(tmp_path: Path) -> None:
    connection, snapshot, proof = _inputs(tmp_path)
    connection.execute("UPDATE collector_runs SET collection_execution_id='prior' WHERE id=1")
    with pytest.raises(ValueError, match="mixed collection invocations"):
        _validate(connection, snapshot, proof)


def test_release_rejects_duplicate_run_ids(tmp_path: Path) -> None:
    connection, snapshot, proof = _inputs(tmp_path)
    data = json.loads(proof.read_text())
    data["collector_run_ids"] = [1, 2, 3, 3]
    proof.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="four unique"):
        _validate(connection, snapshot, proof)


@pytest.mark.parametrize(
    ("generated_at", "message"),
    [("2026-08-27T10:00:00+00:00", "stale"), ("2026-08-27T14:00:00+00:00", "future")],
)
def test_release_rejects_stale_or_future_site(
    tmp_path: Path, generated_at: str, message: str
) -> None:
    connection, snapshot, proof = _inputs(tmp_path)
    data = json.loads(snapshot.read_text())
    data["generated_at"] = generated_at
    snapshot.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        _validate(connection, snapshot, proof)


def test_release_rejects_count_mismatch(tmp_path: Path) -> None:
    connection, snapshot, proof = _inputs(tmp_path)
    connection.execute("INSERT INTO documents VALUES('extra')")
    with pytest.raises(ValueError, match="counts"):
        _validate(connection, snapshot, proof)
