from __future__ import annotations

VERSION = 2
NAME = "versioned_feature_store"

STATEMENTS = (
    """
    CREATE TABLE feature_value (
        feature_value_id TEXT PRIMARY KEY,
        build_id TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        feature_version TEXT NOT NULL,
        value_numeric REAL,
        missingness_reason TEXT,
        coverage REAL NOT NULL,
        reliability REAL NOT NULL,
        fact_count INTEGER NOT NULL,
        contributor_count INTEGER NOT NULL,
        FOREIGN KEY(build_id) REFERENCES dataset_build(build_id),
        FOREIGN KEY(feature_key, feature_version)
            REFERENCES feature_definition(feature_key, feature_version),
        UNIQUE(build_id, entity_id, period_start, period_end, feature_key, feature_version),
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
        CHECK(fact_count >= 0),
        CHECK(contributor_count >= 0),
        CHECK(fact_count <= contributor_count),
        CHECK(value_numeric IS NULL OR fact_count > 0),
        CHECK(value_numeric IS NOT NULL OR fact_count = 0),
        CHECK(value_numeric IS NULL OR contributor_count > 0),
        CHECK(value_numeric IS NOT NULL OR contributor_count = 0)
    )
    """,
    """
    CREATE TABLE feature_value_contributor (
        feature_value_id TEXT NOT NULL,
        observation_id TEXT NOT NULL,
        PRIMARY KEY(feature_value_id, observation_id),
        FOREIGN KEY(feature_value_id) REFERENCES feature_value(feature_value_id),
        FOREIGN KEY(observation_id) REFERENCES observation_v2(observation_id)
    )
    """,
    """CREATE INDEX idx_feature_value_lookup
       ON feature_value(feature_key, feature_version, period_end, entity_id)""",
    """
    CREATE TRIGGER feature_value_no_update BEFORE UPDATE ON feature_value
    BEGIN SELECT RAISE(ABORT, 'feature_value is append-only'); END
    """,
    """
    CREATE TRIGGER feature_value_no_delete BEFORE DELETE ON feature_value
    BEGIN SELECT RAISE(ABORT, 'feature_value is append-only'); END
    """,
    """
    CREATE TRIGGER feature_value_contributor_no_update
    BEFORE UPDATE ON feature_value_contributor
    BEGIN SELECT RAISE(ABORT, 'feature_value_contributor is append-only'); END
    """,
    """
    CREATE TRIGGER feature_value_contributor_no_delete
    BEFORE DELETE ON feature_value_contributor
    BEGIN SELECT RAISE(ABORT, 'feature_value_contributor is append-only'); END
    """,
    """
    CREATE TRIGGER dataset_build_no_update BEFORE UPDATE ON dataset_build
    BEGIN SELECT RAISE(ABORT, 'dataset_build is append-only'); END
    """,
    """
    CREATE TRIGGER dataset_build_no_delete BEFORE DELETE ON dataset_build
    BEGIN SELECT RAISE(ABORT, 'dataset_build is append-only'); END
    """,
)
