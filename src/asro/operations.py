from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from asro.evidence.time import normalize_timestamp


@dataclass(frozen=True)
class WorkflowRunRecord:
    workflow_run_id: str
    run_number: int
    run_attempt: int
    workflow_name: str
    head_sha: str
    event_name: str
    scheduled_for: str
    started_at: str
    completed_at: str
    conclusion: str
    failure_stage: str | None
    steps: list[dict[str, object]]
    window_start: str
    window_end: str
    collector_runs: list[int]
    repair_execution_id: str | None = None


def record_workflow_run(connection: sqlite3.Connection, record: WorkflowRunRecord) -> str:
    times = [
        normalize_timestamp(value).isoformat(timespec="seconds")
        for value in (
            record.scheduled_for,
            record.started_at,
            record.completed_at,
            record.window_start,
            record.window_end,
        )
    ]
    steps_json = _json(record.steps)
    run_values = (
        record.workflow_run_id,
        record.run_number,
        record.run_attempt,
        record.workflow_name,
        record.head_sha,
        record.event_name,
        *times[:3],
        record.conclusion,
        record.failure_stage,
        steps_json,
    )
    existing = connection.execute(
        "SELECT * FROM workflow_run_provenance WHERE workflow_run_id=?",
        (record.workflow_run_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != run_values:
            raise ValueError("workflow run identity has different provenance")
        assessment = connection.execute(
            "SELECT assessment_id FROM collection_window_assessment WHERE workflow_run_id=?",
            (record.workflow_run_id,),
        ).fetchone()
        return str(assessment[0])
    status = _window_status(record.conclusion, record.failure_stage)
    assessment_id = hashlib.sha256(
        f"{record.workflow_run_id}|{times[3]}|{times[4]}|{status}".encode()
    ).hexdigest()
    with connection:
        connection.execute(
            "INSERT INTO workflow_run_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", run_values
        )
        connection.executemany(
            "INSERT INTO workflow_run_collector VALUES(?,?)",
            [(record.workflow_run_id, run_id) for run_id in sorted(record.collector_runs)],
        )
        connection.execute(
            "INSERT INTO collection_window_assessment VALUES(?,?,?,?,?,?,?,?)",
            (
                assessment_id,
                None,
                record.workflow_run_id,
                times[3],
                times[4],
                status,
                _json(sorted(record.collector_runs)),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        if record.conclusion != "success":
            alert_id = hashlib.sha256(f"failed|{record.workflow_run_id}".encode()).hexdigest()
            connection.execute(
                "INSERT INTO operational_alert VALUES(?,?,?,?,?,?,?,NULL)",
                (
                    alert_id,
                    "scheduled_run_failed",
                    record.workflow_run_id,
                    times[3],
                    times[4],
                    _json({"failure_stage": record.failure_stage}),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
    return assessment_id


def missing_hourly_windows(
    connection: sqlite3.Connection, start: str, end: str
) -> list[tuple[str, str]]:
    """Audit the retired hourly cadence used by historical run 107."""

    return _missing_windows(connection, start, end, minute=17, hours=1)


def missing_daily_windows(
    connection: sqlite3.Connection, start: str, end: str
) -> list[tuple[str, str]]:
    """Return missing 24-hour collection windows anchored at 10:17 UTC."""

    cursor = normalize_timestamp(start).replace(hour=10, minute=17, second=0, microsecond=0)
    if cursor < normalize_timestamp(start):
        cursor += timedelta(days=1)
    return _missing_windows(connection, cursor.isoformat(), end, minute=17, hours=24)


def _missing_windows(
    connection: sqlite3.Connection,
    start: str,
    end: str,
    *,
    minute: int,
    hours: int,
) -> list[tuple[str, str]]:
    cursor = normalize_timestamp(start).replace(minute=minute, second=0, microsecond=0)
    finish = normalize_timestamp(end)
    missing: list[tuple[str, str]] = []
    while cursor < finish:
        window_end = cursor + timedelta(hours=hours)
        found = connection.execute(
            """SELECT 1 FROM current_collection_window
               WHERE window_start=? AND window_end=? AND status IN ('complete','repaired')""",
            (cursor.isoformat(timespec="seconds"), window_end.isoformat(timespec="seconds")),
        ).fetchone()
        if found is None:
            missing.append(
                (cursor.isoformat(timespec="seconds"), window_end.isoformat(timespec="seconds"))
            )
        cursor = window_end
    return missing


def alert_missing_hourly_windows(connection: sqlite3.Connection, start: str, end: str) -> list[str]:
    """Persist alerts for the retired hourly cadence and historical gap audits."""

    return _alert_missing_windows(
        connection,
        missing_hourly_windows(connection, start, end),
        cadence_minutes=60,
    )


def alert_missing_daily_windows(connection: sqlite3.Connection, start: str, end: str) -> list[str]:
    """Persist alerts for missing current daily collection windows."""

    return _alert_missing_windows(
        connection,
        missing_daily_windows(connection, start, end),
        cadence_minutes=24 * 60,
    )


def _alert_missing_windows(
    connection: sqlite3.Connection,
    windows: list[tuple[str, str]],
    *,
    cadence_minutes: int,
) -> list[str]:
    alert_ids: list[str] = []
    for window_start, window_end in windows:
        alert_id = hashlib.sha256(f"missing|{window_start}|{window_end}".encode()).hexdigest()
        connection.execute(
            """INSERT OR IGNORE INTO operational_alert(
               alert_id,alert_type,workflow_run_id,window_start,window_end,
               detail_json,created_at,resolved_by_assessment_id
            ) VALUES(?, 'expected_window_missing', NULL, ?, ?, ?, ?, NULL)""",
            (
                alert_id,
                window_start,
                window_end,
                _json({"expected_cadence_minutes": cadence_minutes}),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        alert_ids.append(alert_id)
    connection.commit()
    return alert_ids


def record_window_repair(
    connection: sqlite3.Connection,
    record: WorkflowRunRecord,
) -> str:
    start = normalize_timestamp(record.window_start).isoformat(timespec="seconds")
    end = normalize_timestamp(record.window_end).isoformat(timespec="seconds")
    current = connection.execute(
        """SELECT * FROM current_collection_window
           WHERE window_start=? AND window_end=? ORDER BY recorded_at DESC LIMIT 1""",
        (start, end),
    ).fetchone()
    if current is not None and str(current["status"]) == "repaired":
        return str(current["assessment_id"])
    if current is None or str(current["status"]) not in {"collection_failed"}:
        raise ValueError("repair requires a recorded failed collection window")
    if record.conclusion != "success" or record.failure_stage is not None:
        raise ValueError("a failed repair cannot close a collection gap")
    if record.repair_execution_id is None:
        raise ValueError("repair workflow requires a repair execution identity")
    finalized = connection.execute(
        """SELECT repair.* FROM repair_execution repair
           JOIN repair_execution_finalization finalized
             ON finalized.repair_execution_id=repair.repair_execution_id
           WHERE repair.repair_execution_id=?
             AND repair.target_window_start=? AND repair.target_window_end=?""",
        (record.repair_execution_id, start, end),
    ).fetchone()
    if finalized is None:
        raise ValueError("repair execution is not finalized for the target window")
    linked_ids = {
        int(row[0])
        for row in connection.execute(
            "SELECT collector_run_id FROM repair_execution_collector WHERE repair_execution_id=?",
            (record.repair_execution_id,),
        )
    }
    if linked_ids != set(record.collector_runs):
        raise ValueError("repair workflow collector links are not exact")
    times = [
        normalize_timestamp(value).isoformat(timespec="seconds")
        for value in (record.scheduled_for, record.started_at, record.completed_at)
    ]
    run_values = (
        record.workflow_run_id,
        record.run_number,
        record.run_attempt,
        record.workflow_name,
        record.head_sha,
        record.event_name,
        *times,
        record.conclusion,
        record.failure_stage,
        _json(record.steps),
    )
    existing_run = connection.execute(
        "SELECT * FROM workflow_run_provenance WHERE workflow_run_id=?",
        (record.workflow_run_id,),
    ).fetchone()
    if existing_run is not None and tuple(existing_run) != run_values:
        raise ValueError("repair workflow identity has different provenance")
    assessment_id = hashlib.sha256(
        f"repair|{record.workflow_run_id}|{start}|{end}".encode()
    ).hexdigest()
    with connection:
        if existing_run is None:
            connection.execute(
                "INSERT INTO workflow_run_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                run_values,
            )
            connection.executemany(
                "INSERT INTO workflow_run_collector VALUES(?,?)",
                [(record.workflow_run_id, run_id) for run_id in sorted(record.collector_runs)],
            )
        connection.execute(
            "INSERT INTO collection_window_assessment VALUES(?,?,?,?,?,?,?,?)",
            (
                assessment_id,
                current["assessment_id"],
                record.workflow_run_id,
                start,
                end,
                "repaired",
                _json(sorted(record.collector_runs)),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        for alert in connection.execute(
            """SELECT alert_id FROM operational_alert
               WHERE window_start=? AND window_end=?
                 AND NOT EXISTS(SELECT 1 FROM operational_alert_resolution resolution
                                WHERE resolution.alert_id=operational_alert.alert_id)""",
            (start, end),
        ):
            connection.execute(
                "INSERT INTO operational_alert_resolution VALUES(?,?,?)",
                (alert[0], assessment_id, datetime.now(UTC).isoformat(timespec="seconds")),
            )
    return assessment_id


def _window_status(conclusion: str, stage: str | None) -> str:
    if conclusion == "success":
        return "complete"
    return {"publish": "publish_failed", "deployment": "deployment_failed"}.get(
        stage or "collection", "collection_failed"
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
