from __future__ import annotations

VERSION = 7
NAME = "candidate_research_quarantine"

STATEMENTS = (
    """CREATE TABLE candidate_package (
        package_id TEXT PRIMARY KEY,
        archive_sha256 TEXT NOT NULL UNIQUE,
        events_sha256 TEXT NOT NULL,
        entities_sha256 TEXT NOT NULL,
        dedupe_sha256 TEXT NOT NULL,
        research_as_of TEXT NOT NULL,
        schema_name TEXT NOT NULL,
        event_count INTEGER NOT NULL,
        entity_count INTEGER NOT NULL,
        imported_at TEXT NOT NULL,
        CHECK(length(archive_sha256)=64),
        CHECK(event_count>=0 AND entity_count>=0)
    )""",
    """CREATE TABLE candidate_entity (
        package_id TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        assertion_json TEXT NOT NULL CHECK(json_valid(assertion_json)),
        is_stub INTEGER NOT NULL,
        PRIMARY KEY(package_id, canonical_name),
        FOREIGN KEY(package_id) REFERENCES candidate_package(package_id),
        CHECK(is_stub IN (0,1))
    )""",
    """CREATE TABLE candidate_package_file (
        package_id TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        byte_count INTEGER NOT NULL,
        PRIMARY KEY(package_id, relative_path),
        FOREIGN KEY(package_id) REFERENCES candidate_package(package_id),
        CHECK(length(sha256)=64 AND byte_count>=0)
    )""",
    """CREATE TABLE candidate_event (
        package_id TEXT NOT NULL,
        candidate_event_id TEXT NOT NULL,
        event_group_id TEXT,
        effective_date TEXT NOT NULL,
        event_type_asserted TEXT NOT NULL,
        primary_entity TEXT NOT NULL,
        counterparty_entity TEXT,
        eligible_as_of INTEGER NOT NULL,
        quarantine_reason TEXT NOT NULL,
        assertion_json TEXT NOT NULL CHECK(json_valid(assertion_json)),
        PRIMARY KEY(package_id, candidate_event_id),
        FOREIGN KEY(package_id, primary_entity)
            REFERENCES candidate_entity(package_id, canonical_name),
        CHECK(eligible_as_of IN (0,1))
    )""",
    """CREATE TABLE candidate_source_edge (
        package_id TEXT NOT NULL,
        candidate_event_id TEXT NOT NULL,
        source_ordinal INTEGER NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        publisher TEXT NOT NULL,
        published_at TEXT,
        source_tier_asserted TEXT,
        source_type_asserted TEXT,
        excerpt TEXT NOT NULL,
        is_primary_asserted INTEGER NOT NULL,
        full_document_sha256 TEXT,
        promoted_document_id TEXT,
        PRIMARY KEY(package_id, candidate_event_id, source_ordinal),
        FOREIGN KEY(package_id, candidate_event_id)
            REFERENCES candidate_event(package_id, candidate_event_id),
        FOREIGN KEY(promoted_document_id) REFERENCES items(id),
        CHECK(is_primary_asserted IN (0,1)),
        CHECK((full_document_sha256 IS NULL) = (promoted_document_id IS NULL))
    )""",
    """CREATE TABLE candidate_evidence_promotion (
        package_id TEXT NOT NULL,
        candidate_event_id TEXT NOT NULL,
        observation_id TEXT NOT NULL UNIQUE,
        reviewed_at TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        decision_json TEXT NOT NULL CHECK(json_valid(decision_json)),
        PRIMARY KEY(package_id, candidate_event_id, observation_id),
        FOREIGN KEY(package_id, candidate_event_id)
            REFERENCES candidate_event(package_id, candidate_event_id),
        FOREIGN KEY(observation_id) REFERENCES observation_v2(observation_id)
    )""",
    """CREATE TRIGGER candidate_promotion_validate BEFORE INSERT
       ON candidate_evidence_promotion BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM observation_v2 observation
           JOIN candidate_event candidate
             ON candidate.package_id=NEW.package_id
            AND candidate.candidate_event_id=NEW.candidate_event_id
           WHERE observation.observation_id=NEW.observation_id
             AND observation.entity_id=candidate.primary_entity
         ) THEN RAISE(ABORT, 'candidate promotion lacks matching V2 evidence') END;
       END""",
    *tuple(
        f"""CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
        for table in (
            "candidate_package",
            "candidate_entity",
            "candidate_package_file",
            "candidate_event",
            "candidate_source_edge",
            "candidate_evidence_promotion",
        )
        for action in ("update", "delete")
    ),
)
