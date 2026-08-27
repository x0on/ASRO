from __future__ import annotations

VERSION = 4
NAME = "ecosystem_month_feature_store"

STATEMENTS = (
    """CREATE TABLE ecosystem_dataset_build (
        build_id TEXT PRIMARY KEY,
        source_entity_build_id TEXT NOT NULL,
        code_commit TEXT NOT NULL,
        feature_set_version TEXT NOT NULL,
        availability_cutoff TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        manifest_json TEXT NOT NULL,
        checksum TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        FOREIGN KEY(source_entity_build_id) REFERENCES dataset_build(build_id),
        CHECK(period_end >= period_start),
        CHECK(row_count >= 0)
    )""",
    """CREATE TABLE ecosystem_feature_value (
        ecosystem_feature_value_id TEXT PRIMARY KEY,
        build_id TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        source_feature_key TEXT NOT NULL,
        source_feature_version TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        feature_version TEXT NOT NULL,
        value_numeric REAL,
        missingness_reason TEXT,
        coverage REAL NOT NULL,
        reliability REAL NOT NULL,
        entity_contributor_count INTEGER NOT NULL,
        fact_count INTEGER NOT NULL,
        FOREIGN KEY(build_id) REFERENCES ecosystem_dataset_build(build_id),
        FOREIGN KEY(source_feature_key, source_feature_version)
            REFERENCES feature_definition(feature_key, feature_version),
        FOREIGN KEY(feature_key, feature_version)
            REFERENCES feature_definition(feature_key, feature_version),
        UNIQUE(build_id, period_start, period_end, feature_key, feature_version),
        CHECK((value_numeric IS NOT NULL) != (missingness_reason IS NOT NULL)),
        CHECK(
            value_numeric IS NULL
            OR (value_numeric = value_numeric AND abs(value_numeric) <= 1.7976931348623157e308)
        ),
        CHECK(missingness_reason IS NULL OR missingness_reason IN (
            'unknown', 'not_applicable', 'not_yet_published', 'collection_failed', 'disputed'
        )),
        CHECK(period_end >= period_start),
        CHECK(coverage BETWEEN 0.0 AND 1.0),
        CHECK(reliability BETWEEN 0.0 AND 1.0),
        CHECK(entity_contributor_count >= 0),
        CHECK(fact_count >= 0),
        CHECK(value_numeric IS NULL OR fact_count > 0),
        CHECK(value_numeric IS NOT NULL OR fact_count = 0)
    )""",
    """CREATE TABLE ecosystem_feature_value_entity_contributor (
        ecosystem_feature_value_id TEXT NOT NULL,
        source_feature_value_id TEXT NOT NULL,
        PRIMARY KEY(ecosystem_feature_value_id, source_feature_value_id),
        FOREIGN KEY(ecosystem_feature_value_id)
            REFERENCES ecosystem_feature_value(ecosystem_feature_value_id),
        FOREIGN KEY(source_feature_value_id) REFERENCES feature_value(feature_value_id)
    )""",
    """CREATE TABLE ecosystem_feature_value_fact (
        ecosystem_feature_value_id TEXT NOT NULL,
        canonical_fact_id TEXT NOT NULL,
        canonical_assignment_id TEXT NOT NULL,
        representative_observation_id TEXT NOT NULL,
        PRIMARY KEY(ecosystem_feature_value_id, canonical_fact_id),
        FOREIGN KEY(ecosystem_feature_value_id)
            REFERENCES ecosystem_feature_value(ecosystem_feature_value_id),
        FOREIGN KEY(canonical_fact_id) REFERENCES canonical_fact(canonical_fact_id),
        FOREIGN KEY(canonical_assignment_id)
            REFERENCES canonical_fact_assignment(assignment_id),
        FOREIGN KEY(representative_observation_id) REFERENCES observation_v2(observation_id)
    )""",
    """CREATE TRIGGER ecosystem_entity_contributor_validate BEFORE INSERT
       ON ecosystem_feature_value_entity_contributor BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM ecosystem_feature_value ecosystem
           JOIN ecosystem_dataset_build build ON build.build_id = ecosystem.build_id
           JOIN feature_value entity
             ON entity.feature_value_id = NEW.source_feature_value_id
           JOIN dataset_build_finalization finalized ON finalized.build_id = entity.build_id
           WHERE ecosystem.ecosystem_feature_value_id = NEW.ecosystem_feature_value_id
             AND entity.build_id = build.source_entity_build_id
             AND entity.period_start = ecosystem.period_start
             AND entity.period_end = ecosystem.period_end
             AND entity.feature_key = ecosystem.source_feature_key
             AND entity.feature_version = ecosystem.source_feature_version
         ) THEN RAISE(ABORT, 'entity contributor does not match ecosystem cell') END;
       END""",
    """CREATE TRIGGER ecosystem_fact_validate BEFORE INSERT
       ON ecosystem_feature_value_fact BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM ecosystem_feature_value ecosystem
           JOIN ecosystem_dataset_build build ON build.build_id = ecosystem.build_id
           JOIN observation_v2 observation
             ON observation.observation_id = NEW.representative_observation_id
           JOIN canonical_fact_assignment assignment
             ON assignment.assignment_id = NEW.canonical_assignment_id
           WHERE ecosystem.ecosystem_feature_value_id = NEW.ecosystem_feature_value_id
             AND assignment.canonical_fact_id = NEW.canonical_fact_id
             AND assignment.event_id = observation.event_id
             AND assignment.available_at <= build.availability_cutoff
             AND observation.availability_at <= build.availability_cutoff
             AND observation.feature_key = ecosystem.source_feature_key
             AND observation.feature_version = ecosystem.source_feature_version
             AND observation.economic_scope = 'entity'
             AND substr(observation.period_end, 1, 10)
                 BETWEEN ecosystem.period_start AND ecosystem.period_end
             AND NOT EXISTS (
               SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id = assignment.assignment_id
                 AND correction.available_at <= build.availability_cutoff
             )
             AND EXISTS (
               SELECT 1 FROM ecosystem_feature_value_entity_contributor contributor
               JOIN feature_value_fact source_fact
                 ON source_fact.feature_value_id = contributor.source_feature_value_id
               WHERE contributor.ecosystem_feature_value_id =
                     NEW.ecosystem_feature_value_id
                 AND source_fact.canonical_fact_id = NEW.canonical_fact_id
                 AND source_fact.canonical_assignment_id = NEW.canonical_assignment_id
                 AND source_fact.representative_observation_id =
                     NEW.representative_observation_id
             )
         ) THEN RAISE(ABORT, 'fact does not match ecosystem cell') END;
       END""",
    """CREATE TABLE ecosystem_dataset_build_finalization (
        build_id TEXT PRIMARY KEY,
        finalized_at TEXT NOT NULL,
        FOREIGN KEY(build_id) REFERENCES ecosystem_dataset_build(build_id)
    )""",
    """CREATE TRIGGER ecosystem_build_finalize_validate BEFORE INSERT
       ON ecosystem_dataset_build_finalization BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM dataset_build_finalization finalized
           JOIN ecosystem_dataset_build build
             ON build.source_entity_build_id = finalized.build_id
           WHERE build.build_id = NEW.build_id
         ) THEN RAISE(ABORT, 'source entity build is not finalized') END;
         SELECT CASE WHEN (
           SELECT COUNT(*) FROM ecosystem_feature_value WHERE build_id = NEW.build_id
         ) != (
           SELECT row_count FROM ecosystem_dataset_build WHERE build_id = NEW.build_id
         ) THEN RAISE(ABORT, 'ecosystem build row count does not match') END;
         SELECT CASE WHEN EXISTS (
           SELECT 1 FROM ecosystem_feature_value value
           WHERE value.build_id = NEW.build_id AND (
             (value.value_numeric IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM ecosystem_feature_value_fact fact
               WHERE fact.ecosystem_feature_value_id = value.ecosystem_feature_value_id
             )) OR (value.value_numeric IS NULL AND EXISTS (
               SELECT 1 FROM ecosystem_feature_value_fact fact
               WHERE fact.ecosystem_feature_value_id = value.ecosystem_feature_value_id
             )) OR value.fact_count != (
               SELECT COUNT(*) FROM ecosystem_feature_value_fact fact
               WHERE fact.ecosystem_feature_value_id = value.ecosystem_feature_value_id
             ) OR value.entity_contributor_count != (
               SELECT COUNT(*) FROM ecosystem_feature_value_entity_contributor contributor
               WHERE contributor.ecosystem_feature_value_id = value.ecosystem_feature_value_id
             ) OR value.entity_contributor_count != (
               SELECT COUNT(*) FROM finalized_entity_feature_value source
               JOIN ecosystem_dataset_build build ON build.build_id = value.build_id
               WHERE source.build_id = build.source_entity_build_id
                 AND source.period_start = value.period_start
                 AND source.period_end = value.period_end
                 AND source.feature_key = value.source_feature_key
                 AND source.feature_version = value.source_feature_version
             ) OR value.fact_count != (
               SELECT COUNT(DISTINCT source_fact.canonical_fact_id)
               FROM ecosystem_feature_value_entity_contributor contributor
               JOIN feature_value_fact source_fact
                 ON source_fact.feature_value_id = contributor.source_feature_value_id
               WHERE contributor.ecosystem_feature_value_id = value.ecosystem_feature_value_id
             )
           )
         ) THEN RAISE(ABORT, 'ecosystem build lineage is incomplete') END;
       END""",
    """CREATE TRIGGER ecosystem_build_no_update BEFORE UPDATE ON ecosystem_dataset_build
       BEGIN SELECT RAISE(ABORT, 'ecosystem_dataset_build is append-only'); END""",
    """CREATE TRIGGER ecosystem_build_no_delete BEFORE DELETE ON ecosystem_dataset_build
       BEGIN SELECT RAISE(ABORT, 'ecosystem_dataset_build is append-only'); END""",
    """CREATE TRIGGER ecosystem_value_no_update BEFORE UPDATE ON ecosystem_feature_value
       BEGIN SELECT RAISE(ABORT, 'ecosystem_feature_value is append-only'); END""",
    """CREATE TRIGGER ecosystem_value_no_delete BEFORE DELETE ON ecosystem_feature_value
       BEGIN SELECT RAISE(ABORT, 'ecosystem_feature_value is append-only'); END""",
    """CREATE TRIGGER ecosystem_contributor_no_update BEFORE UPDATE
       ON ecosystem_feature_value_entity_contributor
       BEGIN SELECT RAISE(ABORT, 'ecosystem contributor is append-only'); END""",
    """CREATE TRIGGER ecosystem_contributor_no_delete BEFORE DELETE
       ON ecosystem_feature_value_entity_contributor
       BEGIN SELECT RAISE(ABORT, 'ecosystem contributor is append-only'); END""",
    """CREATE TRIGGER ecosystem_fact_no_update BEFORE UPDATE ON ecosystem_feature_value_fact
       BEGIN SELECT RAISE(ABORT, 'ecosystem fact is append-only'); END""",
    """CREATE TRIGGER ecosystem_fact_no_delete BEFORE DELETE ON ecosystem_feature_value_fact
       BEGIN SELECT RAISE(ABORT, 'ecosystem fact is append-only'); END""",
    """CREATE TRIGGER ecosystem_finalization_no_update BEFORE UPDATE
       ON ecosystem_dataset_build_finalization
       BEGIN SELECT RAISE(ABORT, 'ecosystem finalization is append-only'); END""",
    """CREATE TRIGGER ecosystem_finalization_no_delete BEFORE DELETE
       ON ecosystem_dataset_build_finalization
       BEGIN SELECT RAISE(ABORT, 'ecosystem finalization is append-only'); END""",
    """CREATE TRIGGER finalized_ecosystem_no_value BEFORE INSERT ON ecosystem_feature_value
       WHEN EXISTS (
         SELECT 1 FROM ecosystem_dataset_build_finalization WHERE build_id = NEW.build_id
       ) BEGIN SELECT RAISE(ABORT, 'ecosystem build is finalized'); END""",
    """CREATE TRIGGER finalized_ecosystem_no_contributor BEFORE INSERT
       ON ecosystem_feature_value_entity_contributor WHEN EXISTS (
         SELECT 1 FROM ecosystem_feature_value value
         JOIN ecosystem_dataset_build_finalization finalized
           ON finalized.build_id = value.build_id
         WHERE value.ecosystem_feature_value_id = NEW.ecosystem_feature_value_id
       ) BEGIN SELECT RAISE(ABORT, 'ecosystem build is finalized'); END""",
    """CREATE TRIGGER finalized_ecosystem_no_fact BEFORE INSERT
       ON ecosystem_feature_value_fact WHEN EXISTS (
         SELECT 1 FROM ecosystem_feature_value value
         JOIN ecosystem_dataset_build_finalization finalized
           ON finalized.build_id = value.build_id
         WHERE value.ecosystem_feature_value_id = NEW.ecosystem_feature_value_id
       ) BEGIN SELECT RAISE(ABORT, 'ecosystem build is finalized'); END""",
    """CREATE VIEW finalized_entity_feature_value AS
       SELECT value.* FROM feature_value value
       JOIN dataset_build_finalization finalized ON finalized.build_id = value.build_id""",
    """CREATE VIEW finalized_ecosystem_feature_value AS
       SELECT value.* FROM ecosystem_feature_value value
       JOIN ecosystem_dataset_build_finalization finalized
         ON finalized.build_id = value.build_id""",
)
