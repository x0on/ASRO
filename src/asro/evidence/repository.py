from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from asro.evidence.models import CanonicalFactAssignment, FeatureDefinitionV2, ObservationV2
from asro.evidence.time import timestamp_text

_CORRECTION_IDENTITY_FIELDS = (
    "event_id",
    "source_document_id",
    "entity_id",
    "counterparty_entity_id",
    "entity_role",
    "feature_key",
    "feature_version",
    "period_start",
    "period_end",
    "unit",
    "currency",
    "denominator_feature_key",
    "economic_scope",
)


class EvidenceRepository:
    """Persistence operations for append-only V2 observations."""

    @staticmethod
    def register_canonical_fact(connection: sqlite3.Connection, canonical_fact_id: str) -> bool:
        if not canonical_fact_id.strip():
            raise ValueError("canonical fact ID cannot be blank")
        cursor = connection.execute(
            "INSERT OR IGNORE INTO canonical_fact(canonical_fact_id) VALUES (?)",
            (canonical_fact_id,),
        )
        return cursor.rowcount == 1

    @staticmethod
    def assign_canonical_fact(
        connection: sqlite3.Connection, assignment: CanonicalFactAssignment
    ) -> bool:
        existing = connection.execute(
            "SELECT 1 FROM canonical_fact_assignment WHERE assignment_id = ?",
            (assignment.assignment_id,),
        ).fetchone()
        if existing:
            return False
        if assignment.supersedes_assignment_id is None:
            competing_root = connection.execute(
                """SELECT 1 FROM canonical_fact_assignment
                   WHERE event_id = ? AND supersedes_assignment_id IS NULL""",
                (assignment.event_id,),
            ).fetchone()
            if competing_root:
                raise ValueError("event already has a canonical assignment root")
        else:
            prior = connection.execute(
                """SELECT event_id FROM canonical_fact_assignment
                   WHERE assignment_id = ?""",
                (assignment.supersedes_assignment_id,),
            ).fetchone()
            if prior is None or str(prior[0]) != assignment.event_id:
                raise ValueError("canonical assignment correction has an invalid parent")
        values = assignment.model_dump(mode="json", exclude={"provenance"})
        values["available_at"] = timestamp_text(assignment.available_at)
        values["created_at"] = timestamp_text(assignment.created_at)
        values["provenance_json"] = json.dumps(assignment.provenance, sort_keys=True)
        cursor = connection.execute(
            """INSERT INTO canonical_fact_assignment (
                assignment_id, event_id, canonical_fact_id, available_at,
                supersedes_assignment_id, reviewer_id, assigned_by, assignment_method,
                provenance_json, created_at
            ) VALUES (
                :assignment_id, :event_id, :canonical_fact_id, :available_at,
                :supersedes_assignment_id, :reviewer_id, :assigned_by, :assignment_method,
                :provenance_json, :created_at
            )""",
            values,
        )
        return cursor.rowcount == 1

    @staticmethod
    def canonical_assignments_as_of(
        connection: sqlite3.Connection, cutoff: str | date | datetime
    ) -> dict[str, tuple[str, str]]:
        cutoff_text = timestamp_text(cutoff)
        rows = connection.execute(
            """SELECT current.event_id, current.canonical_fact_id, current.assignment_id
               FROM canonical_fact_assignment current
               WHERE current.available_at <= ? AND NOT EXISTS (
                 SELECT 1 FROM canonical_fact_assignment correction
                 WHERE correction.supersedes_assignment_id = current.assignment_id
                   AND correction.available_at <= ?
               )""",
            (cutoff_text, cutoff_text),
        )
        resolved: dict[str, tuple[str, str]] = {}
        for row in rows:
            event_id = str(row[0])
            if event_id in resolved:
                raise RuntimeError(f"ambiguous canonical assignments for event {event_id}")
            resolved[event_id] = (str(row[1]), str(row[2]))
        return resolved

    @staticmethod
    def register_feature(connection: sqlite3.Connection, definition: FeatureDefinitionV2) -> bool:
        values = definition.model_dump(mode="json")
        values["released_at"] = definition.released_at.isoformat()
        values["deprecated_at"] = (
            definition.deprecated_at.isoformat() if definition.deprecated_at else None
        )
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO feature_definition (
                feature_key, feature_version, definition_json, released_at, deprecated_at
            ) VALUES (
                :feature_key, :feature_version, :definition_json, :released_at, :deprecated_at
            )
            """,
            values,
        )
        if cursor.rowcount == 0:
            stored = connection.execute(
                """SELECT feature_key, feature_version, definition_json, released_at, deprecated_at
                   FROM feature_definition WHERE feature_key = ? AND feature_version = ?""",
                (definition.feature_key, definition.feature_version),
            ).fetchone()
            if FeatureDefinitionV2.model_validate(dict(stored)) != definition:
                raise ValueError("feature version is already registered with different semantics")
            return False
        return True

    @staticmethod
    def insert(connection: sqlite3.Connection, observation: ObservationV2) -> bool:
        if observation.supersedes_observation_id == observation.observation_id:
            raise ValueError("an observation cannot supersede itself")
        existing = connection.execute(
            "SELECT 1 FROM observation_v2 WHERE observation_id = ?",
            (observation.observation_id,),
        ).fetchone()
        if existing is not None:
            return False
        if observation.supersedes_observation_id:
            prior = connection.execute(
                "SELECT * FROM observation_v2 WHERE observation_id = ?",
                (observation.supersedes_observation_id,),
            ).fetchone()
            if prior is None:
                raise ValueError("superseded observation does not exist")
            prior_observation = ObservationV2.model_validate(
                dict(prior), context={"from_storage": True}
            )
            changed = [
                field
                for field in _CORRECTION_IDENTITY_FIELDS
                if getattr(prior_observation, field) != getattr(observation, field)
            ]
            if changed:
                raise ValueError(
                    f"correction changes immutable identity fields: {', '.join(changed)}"
                )
            if observation.availability_at < prior_observation.availability_at:
                raise ValueError(
                    "correction availability cannot precede the superseded observation"
                )
            if observation.extracted_at < prior_observation.extracted_at:
                raise ValueError("correction extraction cannot precede the superseded observation")
            cycle = connection.execute(
                """
                WITH RECURSIVE ancestors(observation_id, supersedes_observation_id) AS (
                    SELECT observation_id, supersedes_observation_id
                    FROM observation_v2 WHERE observation_id = ?
                    UNION ALL
                    SELECT parent.observation_id, parent.supersedes_observation_id
                    FROM observation_v2 parent
                    JOIN ancestors child
                      ON parent.observation_id = child.supersedes_observation_id
                )
                SELECT 1 FROM ancestors WHERE observation_id = ?
                """,
                (observation.supersedes_observation_id, observation.observation_id),
            ).fetchone()
            if cycle:
                raise ValueError("correction would create a cycle")
        values = observation.model_dump(mode="json")
        values["derivation_inputs"] = json.dumps(values["derivation_inputs"])
        for field in (
            "period_start",
            "period_end",
            "event_at",
            "published_at",
            "availability_at",
            "extracted_at",
        ):
            value = getattr(observation, field)
            values[field] = value.isoformat() if value else None
        cursor = connection.execute(
            """
            INSERT INTO observation_v2 (
                observation_id, supersedes_observation_id, event_id, source_document_id,
                source_locator, evidence_text, entity_id, counterparty_entity_id, entity_role,
                feature_key, feature_version, value_numeric, value_text, unit, currency,
                denominator_feature_key, economic_scope, period_start, period_end, event_at,
                published_at,
                event_time_precision, published_time_precision, availability_at,
                availability_time_precision, extracted_at, fact_status, source_tier, source_quality,
                extraction_confidence, review_confidence, extractor_name, extractor_version,
                review_id, derivation_method, derivation_inputs, estimation_model, dispute_reason
            ) VALUES (
                :observation_id, :supersedes_observation_id, :event_id, :source_document_id,
                :source_locator, :evidence_text, :entity_id, :counterparty_entity_id, :entity_role,
                :feature_key, :feature_version, :value_numeric, :value_text, :unit, :currency,
                :denominator_feature_key, :economic_scope, :period_start, :period_end, :event_at,
                :published_at,
                :event_time_precision, :published_time_precision, :availability_at,
                :availability_time_precision, :extracted_at, :fact_status, :source_tier,
                :source_quality,
                :extraction_confidence, :review_confidence, :extractor_name, :extractor_version,
                :review_id, :derivation_method, :derivation_inputs, :estimation_model,
                :dispute_reason
            )
            """,
            values,
        )
        return cursor.rowcount == 1

    @staticmethod
    def get(connection: sqlite3.Connection, observation_id: str) -> ObservationV2 | None:
        row = connection.execute(
            "SELECT * FROM observation_v2 WHERE observation_id = ?", (observation_id,)
        ).fetchone()
        return (
            ObservationV2.model_validate(dict(row), context={"from_storage": True}) if row else None
        )

    @staticmethod
    def as_of(
        connection: sqlite3.Connection,
        availability_cutoff: str | date | datetime,
        feature_key: str | None = None,
    ) -> list[ObservationV2]:
        canonical_cutoff = timestamp_text(availability_cutoff)
        parameters: list[Any] = [canonical_cutoff]
        feature_filter = ""
        if feature_key is not None:
            feature_filter = " AND current.feature_key = ?"
            parameters.append(feature_key)
        rows = connection.execute(
            f"""
            SELECT current.*
            FROM observation_v2 current
            WHERE current.availability_at <= ?
              {feature_filter}
              AND NOT EXISTS (
                  SELECT 1 FROM observation_v2 correction
                  WHERE correction.supersedes_observation_id = current.observation_id
                    AND correction.availability_at <= ?
              )
            ORDER BY current.availability_at, current.observation_id
            """,
            [*parameters, canonical_cutoff],
        )
        return [
            ObservationV2.model_validate(dict(row), context={"from_storage": True}) for row in rows
        ]
