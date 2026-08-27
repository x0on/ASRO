from __future__ import annotations

VERSION = 1
NAME = "v2_evidence_foundation"

STATEMENTS = (
    """
    CREATE TABLE feature_definition (
        feature_key TEXT NOT NULL,
        feature_version TEXT NOT NULL,
        definition_json TEXT NOT NULL CHECK(
            length(trim(definition_json)) > 0
            AND json_valid(definition_json)
            AND json_type(definition_json) = 'object'
        ),
        released_at TEXT NOT NULL,
        deprecated_at TEXT,
        PRIMARY KEY(feature_key, feature_version),
        CHECK(deprecated_at IS NULL OR deprecated_at >= released_at)
    )
    """,
    """
    CREATE TABLE observation_v2 (
        observation_id TEXT PRIMARY KEY,
        supersedes_observation_id TEXT,
        event_id TEXT NOT NULL,
        source_document_id TEXT NOT NULL,
        source_locator TEXT NOT NULL CHECK(length(trim(source_locator)) > 0),
        evidence_text TEXT NOT NULL CHECK(length(trim(evidence_text)) > 0),
        entity_id TEXT,
        counterparty_entity_id TEXT,
        entity_role TEXT,
        feature_key TEXT NOT NULL,
        feature_version TEXT NOT NULL,
        value_numeric REAL,
        value_text TEXT,
        unit TEXT,
        currency TEXT,
        denominator_feature_key TEXT,
        economic_scope TEXT,
        period_start TEXT,
        period_end TEXT,
        event_at TEXT,
        event_time_precision TEXT,
        published_at TEXT NOT NULL,
        published_time_precision TEXT NOT NULL,
        availability_at TEXT NOT NULL,
        availability_time_precision TEXT NOT NULL,
        extracted_at TEXT NOT NULL,
        fact_status TEXT NOT NULL,
        source_tier TEXT NOT NULL,
        source_quality REAL NOT NULL,
        extraction_confidence REAL NOT NULL,
        review_confidence REAL,
        extractor_name TEXT NOT NULL,
        extractor_version TEXT NOT NULL,
        review_id INTEGER,
        derivation_method TEXT,
        derivation_inputs TEXT NOT NULL DEFAULT '[]',
        estimation_model TEXT,
        dispute_reason TEXT,
        FOREIGN KEY(supersedes_observation_id) REFERENCES observation_v2(observation_id),
        FOREIGN KEY(event_id) REFERENCES financial_events(event_id),
        FOREIGN KEY(source_document_id) REFERENCES items(id),
        FOREIGN KEY(review_id) REFERENCES evidence_reviews(review_id),
        FOREIGN KEY(feature_key, feature_version)
            REFERENCES feature_definition(feature_key, feature_version),
        CHECK((value_numeric IS NOT NULL) != (value_text IS NOT NULL)),
        CHECK(value_text IS NULL OR length(trim(value_text)) > 0),
        CHECK(
            value_numeric IS NULL
            OR (value_numeric = value_numeric AND abs(value_numeric) <= 1.7976931348623157e308)
        ),
        CHECK(value_numeric IS NULL OR unit IS NOT NULL),
        CHECK(
            (currency IS NULL AND (unit IS NULL OR unit <> 'currency'))
            OR (currency IS NOT NULL AND unit = 'currency')
        ),
        CHECK(
            economic_scope IS NULL
            OR economic_scope IN ('entity', 'ecosystem', 'network', 'market')
        ),
        CHECK(
            value_numeric IS NULL
            OR (period_start IS NOT NULL AND period_end IS NOT NULL AND economic_scope IS NOT NULL)
        ),
        CHECK(source_quality BETWEEN 0.0 AND 1.0),
        CHECK(extraction_confidence BETWEEN 0.0 AND 1.0),
        CHECK(review_confidence IS NULL OR review_confidence BETWEEN 0.0 AND 1.0),
        CHECK(fact_status IN ('direct', 'inferred', 'estimated', 'disputed')),
        CHECK(source_tier IN (
            'primary', 'authoritative_secondary', 'reputable_secondary', 'other'
        )),
        CHECK(event_time_precision IS NULL OR event_time_precision IN ('date', 'second')),
        CHECK((event_at IS NULL) = (event_time_precision IS NULL)),
        CHECK(published_time_precision IN ('date', 'second')),
        CHECK(availability_time_precision IN ('date', 'second')),
        CHECK(
            event_time_precision != 'date'
            OR substr(event_at, 12) = '00:00:00+00:00'
        ),
        CHECK(
            published_time_precision != 'date'
            OR substr(published_at, 12) = '00:00:00+00:00'
        ),
        CHECK(
            availability_time_precision != 'date'
            OR substr(availability_at, 12) = '00:00:00+00:00'
        ),
        CHECK(availability_at >= published_at),
        CHECK(extracted_at >= availability_at),
        CHECK(period_start IS NULL OR period_end IS NULL OR period_end >= period_start),
        CHECK(
            fact_status NOT IN ('inferred', 'estimated')
            OR (
                length(trim(derivation_method)) > 0
                AND json_valid(derivation_inputs)
                AND json_type(derivation_inputs) = 'array'
                AND json_array_length(derivation_inputs) > 0
            )
        ),
        CHECK(json_valid(derivation_inputs) AND json_type(derivation_inputs) = 'array'),
        CHECK(fact_status != 'estimated' OR length(trim(estimation_model)) > 0),
        CHECK(fact_status != 'disputed' OR length(trim(dispute_reason)) > 0)
    )
    """,
    """CREATE UNIQUE INDEX idx_observation_v2_supersedes
       ON observation_v2(supersedes_observation_id)
       WHERE supersedes_observation_id IS NOT NULL""",
    """CREATE INDEX idx_observation_v2_feature_time
       ON observation_v2(feature_key, feature_version, availability_at)""",
    """CREATE INDEX idx_observation_v2_entity_period
       ON observation_v2(entity_id, period_start, period_end)""",
    """
    CREATE TRIGGER observation_v2_no_update BEFORE UPDATE ON observation_v2
    BEGIN SELECT RAISE(ABORT, 'observation_v2 is append-only'); END
    """,
    """
    CREATE TRIGGER observation_v2_no_delete BEFORE DELETE ON observation_v2
    BEGIN SELECT RAISE(ABORT, 'observation_v2 is append-only'); END
    """,
    """
    CREATE TRIGGER observation_v2_validate_correction
    BEFORE INSERT ON observation_v2
    WHEN NEW.supersedes_observation_id IS NOT NULL
    BEGIN
        SELECT CASE WHEN NEW.observation_id = NEW.supersedes_observation_id
            THEN RAISE(ABORT, 'observation cannot supersede itself') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM observation_v2 parent
            WHERE parent.observation_id = NEW.supersedes_observation_id
              AND (
                  NEW.event_id IS NOT parent.event_id
                  OR NEW.source_document_id IS NOT parent.source_document_id
                  OR NEW.entity_id IS NOT parent.entity_id
                  OR NEW.counterparty_entity_id IS NOT parent.counterparty_entity_id
                  OR NEW.entity_role IS NOT parent.entity_role
                  OR NEW.feature_key IS NOT parent.feature_key
                  OR NEW.feature_version IS NOT parent.feature_version
                  OR NEW.period_start IS NOT parent.period_start
                  OR NEW.period_end IS NOT parent.period_end
                  OR NEW.unit IS NOT parent.unit
                  OR NEW.currency IS NOT parent.currency
                  OR NEW.denominator_feature_key IS NOT parent.denominator_feature_key
                  OR NEW.economic_scope IS NOT parent.economic_scope
              )
        ) THEN RAISE(ABORT, 'correction changes immutable identity') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM observation_v2 parent
            WHERE parent.observation_id = NEW.supersedes_observation_id
              AND NEW.availability_at < parent.availability_at
        ) THEN RAISE(ABORT, 'correction availability is not monotonic') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM observation_v2 parent
            WHERE parent.observation_id = NEW.supersedes_observation_id
              AND NEW.extracted_at < parent.extracted_at
        ) THEN RAISE(ABORT, 'correction extraction is not monotonic') END;
    END
    """,
    """
    CREATE TRIGGER feature_definition_no_update BEFORE UPDATE ON feature_definition
    BEGIN SELECT RAISE(ABORT, 'feature_definition is append-only'); END
    """,
    """
    CREATE TRIGGER feature_definition_no_delete BEFORE DELETE ON feature_definition
    BEGIN SELECT RAISE(ABORT, 'feature_definition is append-only'); END
    """,
    """
    CREATE TABLE dataset_build (
        build_id TEXT PRIMARY KEY,
        code_commit TEXT NOT NULL,
        feature_set_version TEXT NOT NULL,
        availability_cutoff TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        row_count INTEGER NOT NULL,
        manifest_json TEXT NOT NULL,
        checksum TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        CHECK(period_end >= period_start),
        CHECK(row_count >= 0)
    )
    """,
)
