from __future__ import annotations

VERSION = 3
NAME = "temporal_canonical_lineage"

STATEMENTS = (
    """CREATE TABLE canonical_fact (
        canonical_fact_id TEXT PRIMARY KEY CHECK(length(trim(canonical_fact_id)) > 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE canonical_fact_assignment (
        assignment_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL,
        canonical_fact_id TEXT NOT NULL,
        available_at TEXT NOT NULL,
        supersedes_assignment_id TEXT,
        reviewer_id INTEGER,
        assigned_by TEXT NOT NULL CHECK(length(trim(assigned_by)) > 0),
        assignment_method TEXT NOT NULL CHECK(length(trim(assignment_method)) > 0),
        provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
        created_at TEXT NOT NULL,
        FOREIGN KEY(event_id) REFERENCES financial_events(event_id),
        FOREIGN KEY(canonical_fact_id) REFERENCES canonical_fact(canonical_fact_id),
        FOREIGN KEY(supersedes_assignment_id)
            REFERENCES canonical_fact_assignment(assignment_id),
        FOREIGN KEY(reviewer_id) REFERENCES evidence_reviews(review_id),
        CHECK(created_at >= available_at),
        CHECK(
          datetime(available_at) IS NOT NULL
          AND substr(available_at, 11, 1) = 'T'
          AND substr(available_at, -6) = '+00:00'
          AND strftime('%Y-%m-%dT%H:%M:%S', available_at) = substr(available_at, 1, 19)
        ),
        CHECK(
          datetime(created_at) IS NOT NULL
          AND substr(created_at, 11, 1) = 'T'
          AND substr(created_at, -6) = '+00:00'
          AND strftime('%Y-%m-%dT%H:%M:%S', created_at) = substr(created_at, 1, 19)
        )
    )""",
    """CREATE UNIQUE INDEX idx_canonical_assignment_root
       ON canonical_fact_assignment(event_id)
       WHERE supersedes_assignment_id IS NULL""",
    """CREATE UNIQUE INDEX idx_canonical_assignment_supersedes
       ON canonical_fact_assignment(supersedes_assignment_id)
       WHERE supersedes_assignment_id IS NOT NULL""",
    """CREATE TRIGGER canonical_assignment_validate BEFORE INSERT
       ON canonical_fact_assignment WHEN NEW.supersedes_assignment_id IS NOT NULL
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM canonical_fact_assignment prior
           WHERE prior.assignment_id = NEW.supersedes_assignment_id
             AND prior.event_id = NEW.event_id
             AND NEW.available_at >= prior.available_at
         ) THEN RAISE(ABORT, 'invalid canonical assignment correction') END;
       END""",
    """CREATE TRIGGER canonical_fact_no_update BEFORE UPDATE ON canonical_fact
       BEGIN SELECT RAISE(ABORT, 'canonical_fact is append-only'); END""",
    """CREATE TRIGGER canonical_fact_no_delete BEFORE DELETE ON canonical_fact
       BEGIN SELECT RAISE(ABORT, 'canonical_fact is append-only'); END""",
    """CREATE TRIGGER canonical_assignment_no_update
       BEFORE UPDATE ON canonical_fact_assignment
       BEGIN SELECT RAISE(ABORT, 'canonical_fact_assignment is append-only'); END""",
    """CREATE TRIGGER canonical_assignment_no_delete
       BEFORE DELETE ON canonical_fact_assignment
       BEGIN SELECT RAISE(ABORT, 'canonical_fact_assignment is append-only'); END""",
    """INSERT INTO canonical_fact(canonical_fact_id)
       SELECT DISTINCT event_id FROM observation_v2""",
    """INSERT INTO canonical_fact_assignment (
         assignment_id, event_id, canonical_fact_id, available_at, assigned_by,
         assignment_method, provenance_json, created_at
       ) SELECT 'legacy:' || event_id, event_id, event_id, MIN(availability_at),
                'migration', 'legacy_event_identity', '{}', MIN(extracted_at)
         FROM observation_v2 GROUP BY event_id""",
    "ALTER TABLE feature_value_contributor RENAME TO feature_value_contributor_legacy",
    "DROP TRIGGER feature_value_contributor_no_update",
    "DROP TRIGGER feature_value_contributor_no_delete",
    """CREATE TABLE feature_value_fact (
        feature_value_id TEXT NOT NULL,
        canonical_fact_id TEXT NOT NULL,
        canonical_assignment_id TEXT NOT NULL,
        representative_observation_id TEXT NOT NULL,
        PRIMARY KEY(feature_value_id, canonical_fact_id),
        FOREIGN KEY(feature_value_id) REFERENCES feature_value(feature_value_id),
        FOREIGN KEY(canonical_fact_id) REFERENCES canonical_fact(canonical_fact_id),
        FOREIGN KEY(canonical_assignment_id)
            REFERENCES canonical_fact_assignment(assignment_id),
        FOREIGN KEY(representative_observation_id) REFERENCES observation_v2(observation_id)
    )""",
    """CREATE TABLE feature_value_contributor (
        feature_value_id TEXT NOT NULL,
        canonical_fact_id TEXT NOT NULL,
        canonical_assignment_id TEXT NOT NULL,
        observation_id TEXT NOT NULL,
        PRIMARY KEY(feature_value_id, observation_id),
        FOREIGN KEY(feature_value_id, canonical_fact_id)
            REFERENCES feature_value_fact(feature_value_id, canonical_fact_id),
        FOREIGN KEY(canonical_assignment_id)
            REFERENCES canonical_fact_assignment(assignment_id),
        FOREIGN KEY(observation_id) REFERENCES observation_v2(observation_id)
    )""",
    """CREATE TRIGGER feature_value_fact_validate BEFORE INSERT ON feature_value_fact
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM feature_value value
           JOIN dataset_build build ON build.build_id = value.build_id
           JOIN observation_v2 observation
             ON observation.observation_id = NEW.representative_observation_id
           JOIN canonical_fact_assignment assignment
             ON assignment.assignment_id = NEW.canonical_assignment_id
           WHERE value.feature_value_id = NEW.feature_value_id
             AND assignment.canonical_fact_id = NEW.canonical_fact_id
             AND assignment.event_id = observation.event_id
             AND assignment.available_at <= build.availability_cutoff
             AND observation.availability_at <= build.availability_cutoff
             AND observation.entity_id = value.entity_id
             AND observation.feature_key = value.feature_key
             AND observation.feature_version = value.feature_version
             AND observation.economic_scope = 'entity'
             AND substr(observation.period_end, 1, 10)
                 BETWEEN value.period_start AND value.period_end
             AND NOT EXISTS (
               SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id = assignment.assignment_id
                 AND correction.available_at <= build.availability_cutoff
             )
         ) THEN RAISE(ABORT, 'fact lineage does not match feature cell') END;
       END""",
    """CREATE TRIGGER feature_value_contributor_validate
       BEFORE INSERT ON feature_value_contributor
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM feature_value value
           JOIN dataset_build build ON build.build_id = value.build_id
           JOIN observation_v2 observation ON observation.observation_id = NEW.observation_id
           JOIN canonical_fact_assignment assignment
             ON assignment.assignment_id = NEW.canonical_assignment_id
           WHERE value.feature_value_id = NEW.feature_value_id
             AND assignment.canonical_fact_id = NEW.canonical_fact_id
             AND assignment.event_id = observation.event_id
             AND assignment.available_at <= build.availability_cutoff
             AND observation.availability_at <= build.availability_cutoff
             AND observation.entity_id = value.entity_id
             AND observation.feature_key = value.feature_key
             AND observation.feature_version = value.feature_version
             AND observation.economic_scope = 'entity'
             AND substr(observation.period_end, 1, 10)
                 BETWEEN value.period_start AND value.period_end
             AND NOT EXISTS (
               SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id = assignment.assignment_id
                 AND correction.available_at <= build.availability_cutoff
             )
         ) THEN RAISE(ABORT, 'contributor does not match feature cell') END;
       END""",
    """INSERT INTO feature_value_fact (
         feature_value_id, canonical_fact_id, canonical_assignment_id,
         representative_observation_id
       ) SELECT legacy.feature_value_id, observation.event_id,
                'legacy:' || observation.event_id, (
                  SELECT candidate.observation_id
                  FROM feature_value_contributor_legacy candidate_legacy
                  JOIN observation_v2 candidate
                    ON candidate.observation_id = candidate_legacy.observation_id
                  WHERE candidate_legacy.feature_value_id = legacy.feature_value_id
                    AND candidate.event_id = observation.event_id
                  ORDER BY COALESCE(candidate.review_confidence, 0.0) DESC,
                           candidate.source_quality DESC,
                           candidate.extraction_confidence DESC,
                           candidate.availability_at DESC,
                           candidate.observation_id DESC
                  LIMIT 1
                )
         FROM feature_value_contributor_legacy legacy
         JOIN observation_v2 observation ON observation.observation_id = legacy.observation_id
         GROUP BY legacy.feature_value_id, observation.event_id""",
    """INSERT INTO feature_value_contributor (
         feature_value_id, canonical_fact_id, canonical_assignment_id, observation_id
       ) SELECT legacy.feature_value_id, observation.event_id,
                'legacy:' || observation.event_id, legacy.observation_id
         FROM feature_value_contributor_legacy legacy
         JOIN observation_v2 observation ON observation.observation_id = legacy.observation_id""",
    """CREATE TRIGGER feature_value_fact_no_update BEFORE UPDATE ON feature_value_fact
       BEGIN SELECT RAISE(ABORT, 'feature_value_fact is append-only'); END""",
    """CREATE TRIGGER feature_value_fact_no_delete BEFORE DELETE ON feature_value_fact
       BEGIN SELECT RAISE(ABORT, 'feature_value_fact is append-only'); END""",
    """CREATE TRIGGER feature_value_contributor_no_update
       BEFORE UPDATE ON feature_value_contributor
       BEGIN SELECT RAISE(ABORT, 'feature_value_contributor is append-only'); END""",
    """CREATE TRIGGER feature_value_contributor_no_delete
       BEFORE DELETE ON feature_value_contributor
       BEGIN SELECT RAISE(ABORT, 'feature_value_contributor is append-only'); END""",
    """CREATE TABLE dataset_build_finalization (
        build_id TEXT PRIMARY KEY,
        finalized_at TEXT NOT NULL,
        FOREIGN KEY(build_id) REFERENCES dataset_build(build_id)
    )""",
    """CREATE TRIGGER dataset_build_finalize_validate
       BEFORE INSERT ON dataset_build_finalization
       BEGIN
         SELECT CASE WHEN (
           SELECT COUNT(*) FROM feature_value WHERE build_id = NEW.build_id
         ) != (SELECT row_count FROM dataset_build WHERE build_id = NEW.build_id)
         THEN RAISE(ABORT, 'build row count does not match') END;
         SELECT CASE WHEN EXISTS (
           SELECT 1 FROM feature_value value
           WHERE value.build_id = NEW.build_id AND (
             (value.value_numeric IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM feature_value_fact fact
               WHERE fact.feature_value_id = value.feature_value_id
             )) OR
             (value.value_numeric IS NULL AND EXISTS (
               SELECT 1 FROM feature_value_fact fact
               WHERE fact.feature_value_id = value.feature_value_id
             )) OR
             value.fact_count != (
               SELECT COUNT(*) FROM feature_value_fact fact
               WHERE fact.feature_value_id = value.feature_value_id
             ) OR value.contributor_count != (
               SELECT COUNT(*) FROM feature_value_contributor contributor
               WHERE contributor.feature_value_id = value.feature_value_id
             )
           )
         ) THEN RAISE(ABORT, 'build lineage is incomplete') END;
       END""",
    """CREATE TRIGGER dataset_build_finalization_no_update
       BEFORE UPDATE ON dataset_build_finalization
       BEGIN SELECT RAISE(ABORT, 'dataset_build_finalization is append-only'); END""",
    """CREATE TRIGGER dataset_build_finalization_no_delete
       BEFORE DELETE ON dataset_build_finalization
       BEGIN SELECT RAISE(ABORT, 'dataset_build_finalization is append-only'); END""",
    """CREATE TRIGGER finalized_build_no_feature_value
       BEFORE INSERT ON feature_value
       WHEN EXISTS (
         SELECT 1 FROM dataset_build_finalization WHERE build_id = NEW.build_id
       ) BEGIN SELECT RAISE(ABORT, 'build is finalized'); END""",
    """CREATE TRIGGER finalized_build_no_fact
       BEFORE INSERT ON feature_value_fact
       WHEN EXISTS (
         SELECT 1 FROM feature_value value
         JOIN dataset_build_finalization finalization ON finalization.build_id = value.build_id
         WHERE value.feature_value_id = NEW.feature_value_id
       ) BEGIN SELECT RAISE(ABORT, 'build is finalized'); END""",
    """CREATE TRIGGER finalized_build_no_contributor
       BEFORE INSERT ON feature_value_contributor
       WHEN EXISTS (
         SELECT 1 FROM feature_value value
         JOIN dataset_build_finalization finalization ON finalization.build_id = value.build_id
         WHERE value.feature_value_id = NEW.feature_value_id
       ) BEGIN SELECT RAISE(ABORT, 'build is finalized'); END""",
    """INSERT INTO dataset_build_finalization(build_id, finalized_at)
       SELECT build_id, created_at FROM dataset_build""",
    "DROP TABLE feature_value_contributor_legacy",
)
