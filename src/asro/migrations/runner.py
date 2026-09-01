from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from asro.migrations import (
    acceptance_queue,
    candidate_quarantine,
    canonical_lineage,
    control_vintage_identity,
    ecosystem_as_of_lineage,
    ecosystem_store,
    feature_family_as_of,
    feature_family_as_of_cutoff,
    feature_store,
    historical_backfill,
    historical_backfill_integrity,
    historical_pipeline_time,
    operational_provenance,
    release_collection_identity,
    stage3_acceptance_integrity,
    stage3_final_closure,
    stage3_temporal_repair_integrity,
    v2_evidence,
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(v2_evidence.VERSION, v2_evidence.NAME, v2_evidence.STATEMENTS),
    Migration(feature_store.VERSION, feature_store.NAME, feature_store.STATEMENTS),
    Migration(canonical_lineage.VERSION, canonical_lineage.NAME, canonical_lineage.STATEMENTS),
    Migration(ecosystem_store.VERSION, ecosystem_store.NAME, ecosystem_store.STATEMENTS),
    Migration(
        historical_backfill.VERSION,
        historical_backfill.NAME,
        historical_backfill.STATEMENTS,
    ),
    Migration(
        historical_backfill_integrity.VERSION,
        historical_backfill_integrity.NAME,
        historical_backfill_integrity.STATEMENTS,
    ),
    Migration(
        candidate_quarantine.VERSION,
        candidate_quarantine.NAME,
        candidate_quarantine.STATEMENTS,
    ),
    Migration(
        stage3_acceptance_integrity.VERSION,
        stage3_acceptance_integrity.NAME,
        stage3_acceptance_integrity.STATEMENTS,
    ),
    Migration(
        operational_provenance.VERSION,
        operational_provenance.NAME,
        operational_provenance.STATEMENTS,
    ),
    Migration(
        stage3_temporal_repair_integrity.VERSION,
        stage3_temporal_repair_integrity.NAME,
        stage3_temporal_repair_integrity.STATEMENTS,
    ),
    Migration(
        stage3_final_closure.VERSION,
        stage3_final_closure.NAME,
        stage3_final_closure.STATEMENTS,
    ),
    Migration(
        release_collection_identity.VERSION,
        release_collection_identity.NAME,
        release_collection_identity.STATEMENTS,
    ),
    Migration(
        feature_family_as_of.VERSION,
        feature_family_as_of.NAME,
        feature_family_as_of.STATEMENTS,
    ),
    Migration(
        feature_family_as_of_cutoff.VERSION,
        feature_family_as_of_cutoff.NAME,
        feature_family_as_of_cutoff.STATEMENTS,
    ),
    Migration(
        ecosystem_as_of_lineage.VERSION,
        ecosystem_as_of_lineage.NAME,
        ecosystem_as_of_lineage.STATEMENTS,
    ),
    Migration(
        acceptance_queue.VERSION,
        acceptance_queue.NAME,
        acceptance_queue.STATEMENTS,
    ),
    Migration(
        historical_pipeline_time.VERSION,
        historical_pipeline_time.NAME,
        historical_pipeline_time.STATEMENTS,
    ),
    Migration(
        control_vintage_identity.VERSION,
        control_vintage_identity.NAME,
        control_vintage_identity.STATEMENTS,
    ),
)

_V2_REQUIRED_COLUMNS = {
    "observation_id",
    "supersedes_observation_id",
    "event_id",
    "source_document_id",
    "feature_key",
    "feature_version",
    "economic_scope",
    "published_at",
    "availability_at",
    "published_time_precision",
    "availability_time_precision",
    "derivation_method",
    "derivation_inputs",
    "estimation_model",
    "dispute_reason",
}
_V2_REQUIRED_TRIGGERS = {
    "observation_v2_no_update",
    "observation_v2_no_delete",
    "observation_v2_validate_correction",
    "feature_definition_no_update",
    "feature_definition_no_delete",
}


def _verify_v2_schema(connection: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(observation_v2)")}
    if not columns >= _V2_REQUIRED_COLUMNS:
        raise RuntimeError("obsolete or partially created V2 evidence schema detected")
    triggers = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    if not triggers >= _V2_REQUIRED_TRIGGERS:
        raise RuntimeError("obsolete or partially created V2 evidence triggers detected")
    foreign_targets = {
        str(row[2]) for row in connection.execute("PRAGMA foreign_key_list(observation_v2)")
    }
    if (
        not {
            "observation_v2",
            "financial_events",
            "items",
            "evidence_reviews",
            "feature_definition",
        }
        <= foreign_targets
    ):
        raise RuntimeError("obsolete or partially created V2 foreign keys detected")


def _verify_feature_store_schema(connection: sqlite3.Connection) -> None:
    required_tables = {"feature_value", "feature_value_contributor", "dataset_build"}
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not required_tables <= tables:
        raise RuntimeError("obsolete or partially created feature-store schema detected")
    feature_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(feature_value)")
    }
    if not {"fact_count", "contributor_count"} <= feature_columns:
        raise RuntimeError("obsolete feature-store schema lacks lineage counts")
    required_triggers = {
        "feature_value_no_update",
        "feature_value_no_delete",
        "feature_value_contributor_no_update",
        "feature_value_contributor_no_delete",
        "dataset_build_no_update",
        "dataset_build_no_delete",
    }
    triggers = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    if not required_triggers <= triggers:
        raise RuntimeError("obsolete or partially created feature-store triggers detected")


def _verify_canonical_lineage_schema(connection: sqlite3.Connection) -> None:
    required_tables = {
        "canonical_fact",
        "canonical_fact_assignment",
        "feature_value_fact",
        "feature_value_contributor",
        "dataset_build_finalization",
    }
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not required_tables <= tables:
        raise RuntimeError("obsolete or partially created canonical-lineage schema detected")
    if "feature_value_contributor_legacy" in tables:
        raise RuntimeError("canonical-lineage migration retained its legacy staging table")
    root_index = connection.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type = 'index' AND name = 'idx_canonical_assignment_root'"""
    ).fetchone()
    if root_index is None:
        raise RuntimeError("canonical assignment root uniqueness is not enforced")


def _verify_ecosystem_store_schema(connection: sqlite3.Connection) -> None:
    required = {
        "ecosystem_dataset_build",
        "ecosystem_feature_value",
        "ecosystem_feature_value_entity_contributor",
        "ecosystem_feature_value_fact",
        "ecosystem_dataset_build_finalization",
    }
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not required <= tables:
        raise RuntimeError("obsolete or partially created ecosystem feature store detected")
    views = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='view'")
    }
    if not {"finalized_entity_feature_value", "finalized_ecosystem_feature_value"} <= views:
        raise RuntimeError("finalized feature-store visibility views are missing")


def _verify_backfill_schema(connection: sqlite3.Connection) -> None:
    required = {
        "backfill_episode",
        "backfill_run",
        "backfill_source_snapshot",
        "backfill_build_link",
        "backfill_run_finalization",
    }
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not required <= tables:
        raise RuntimeError("obsolete or partially created historical backfill schema detected")
    finalized_view = connection.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='view' AND name='finalized_backfill_run'"""
    ).fetchone()
    if finalized_view is None:
        raise RuntimeError("finalized historical backfill visibility is missing")


def _verify_backfill_integrity_schema(connection: sqlite3.Connection) -> None:
    required = {
        "backfill_source_snapshot_v2",
        "historical_control_observation",
        "backfill_control_snapshot",
        "backfill_coverage_cell",
        "backfill_leakage_violation",
    }
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not required <= tables:
        raise RuntimeError("historical backfill integrity schema is incomplete")


def _verify_candidate_quarantine_schema(connection: sqlite3.Connection) -> None:
    required = {
        "candidate_package",
        "candidate_package_file",
        "candidate_entity",
        "candidate_event",
        "candidate_source_edge",
        "candidate_evidence_promotion",
    }
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not required <= tables:
        raise RuntimeError("candidate evidence quarantine schema is incomplete")


def apply_migrations(
    connection: sqlite3.Connection, migrations: Sequence[Migration] | None = None
) -> None:
    migrations = MIGRATIONS if migrations is None else migrations
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()
    applied = {int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")}
    unmanaged_v2 = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'observation_v2'"
    ).fetchone()
    if unmanaged_v2 and v2_evidence.VERSION not in applied:
        raise RuntimeError("unversioned or partially created V2 evidence schema detected")
    if v2_evidence.VERSION in applied:
        _verify_v2_schema(connection)
    if feature_store.VERSION in applied:
        _verify_feature_store_schema(connection)
    if canonical_lineage.VERSION in applied:
        _verify_canonical_lineage_schema(connection)
    if ecosystem_store.VERSION in applied:
        _verify_ecosystem_store_schema(connection)
    if historical_backfill.VERSION in applied:
        _verify_backfill_schema(connection)
    if historical_backfill_integrity.VERSION in applied:
        _verify_backfill_integrity_schema(connection)
    if candidate_quarantine.VERSION in applied:
        _verify_candidate_quarantine_schema(connection)

    for migration in migrations:
        if migration.version in applied:
            continue
        try:
            connection.execute("BEGIN")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
    applied_after = {
        int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
    }
    if v2_evidence.VERSION in applied_after:
        _verify_v2_schema(connection)
    if feature_store.VERSION in applied_after:
        _verify_feature_store_schema(connection)
    if canonical_lineage.VERSION in applied_after:
        _verify_canonical_lineage_schema(connection)
    if ecosystem_store.VERSION in applied_after:
        _verify_ecosystem_store_schema(connection)
    if historical_backfill.VERSION in applied_after:
        _verify_backfill_schema(connection)
    if historical_backfill_integrity.VERSION in applied_after:
        _verify_backfill_integrity_schema(connection)
    if candidate_quarantine.VERSION in applied_after:
        _verify_candidate_quarantine_schema(connection)
