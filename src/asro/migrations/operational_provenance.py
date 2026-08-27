from __future__ import annotations

VERSION = 9
NAME = "scheduled_collection_operational_provenance"

STATEMENTS = (
    """CREATE TABLE workflow_run_provenance (
        workflow_run_id TEXT PRIMARY KEY,
        run_number INTEGER NOT NULL,
        run_attempt INTEGER NOT NULL,
        workflow_name TEXT NOT NULL,
        head_sha TEXT NOT NULL,
        event_name TEXT NOT NULL,
        scheduled_for TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        conclusion TEXT NOT NULL,
        failure_stage TEXT,
        steps_json TEXT NOT NULL CHECK(json_valid(steps_json)),
        CHECK(conclusion IN ('success','failure','cancelled')),
        CHECK(failure_stage IS NULL OR failure_stage IN ('collection','publish','deployment'))
    )""",
    """CREATE TABLE collection_window_assessment (
        assessment_id TEXT PRIMARY KEY,
        supersedes_assessment_id TEXT UNIQUE,
        workflow_run_id TEXT NOT NULL,
        window_start TEXT NOT NULL,
        window_end TEXT NOT NULL,
        status TEXT NOT NULL,
        collector_runs_json TEXT NOT NULL CHECK(json_valid(collector_runs_json)),
        recorded_at TEXT NOT NULL,
        FOREIGN KEY(supersedes_assessment_id)
            REFERENCES collection_window_assessment(assessment_id),
        FOREIGN KEY(workflow_run_id) REFERENCES workflow_run_provenance(workflow_run_id),
        CHECK(window_end>window_start),
        CHECK(status IN (
            'complete','collection_failed','publish_failed','deployment_failed','repaired'
        ))
    )""",
    """CREATE TABLE operational_alert (
        alert_id TEXT PRIMARY KEY,
        alert_type TEXT NOT NULL,
        workflow_run_id TEXT,
        window_start TEXT,
        window_end TEXT,
        detail_json TEXT NOT NULL CHECK(json_valid(detail_json)),
        created_at TEXT NOT NULL,
        resolved_by_assessment_id TEXT,
        FOREIGN KEY(workflow_run_id) REFERENCES workflow_run_provenance(workflow_run_id),
        FOREIGN KEY(resolved_by_assessment_id)
            REFERENCES collection_window_assessment(assessment_id),
        CHECK(alert_type IN ('scheduled_run_failed','expected_window_missing'))
    )""",
    """CREATE VIEW current_collection_window AS
       SELECT assessment.* FROM collection_window_assessment assessment
       WHERE NOT EXISTS(SELECT 1 FROM collection_window_assessment correction
                        WHERE correction.supersedes_assessment_id=assessment.assessment_id)""",
    """CREATE TABLE operational_alert_resolution (
        alert_id TEXT PRIMARY KEY,
        assessment_id TEXT NOT NULL,
        resolved_at TEXT NOT NULL,
        FOREIGN KEY(alert_id) REFERENCES operational_alert(alert_id),
        FOREIGN KEY(assessment_id) REFERENCES collection_window_assessment(assessment_id)
    )""",
    *tuple(
        f"""CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
        for table in (
            "workflow_run_provenance",
            "collection_window_assessment",
            "operational_alert",
            "operational_alert_resolution",
        )
        for action in ("update", "delete")
    ),
)
