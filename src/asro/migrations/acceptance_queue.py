"""Append-only reviewer queue for authoritative acceptance candidates."""

VERSION = 16
NAME = "authoritative_acceptance_queue"

STATEMENTS = (
    """CREATE TABLE acceptance_queue_run (
        run_id TEXT PRIMARY KEY,
        manifest_sha256 TEXT NOT NULL,
        manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
        item_count INTEGER NOT NULL CHECK(item_count>=0),
        created_at TEXT NOT NULL,
        CHECK(length(run_id)=64 AND length(manifest_sha256)=64)
    )""",
    """CREATE TABLE acceptance_queue_item (
        queue_item_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        lead_id TEXT NOT NULL,
        document_sha256 TEXT NOT NULL,
        source_url TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        counterparty_entity_id TEXT NOT NULL,
        entity_role TEXT NOT NULL,
        counterparty_role TEXT NOT NULL,
        feature_key TEXT NOT NULL,
        feature_version TEXT NOT NULL,
        value_numeric REAL NOT NULL,
        unit TEXT NOT NULL,
        currency TEXT,
        event_at TEXT NOT NULL,
        public_availability_at TEXT NOT NULL,
        passage_locator TEXT NOT NULL,
        passage_text TEXT NOT NULL,
        status TEXT NOT NULL,
        rejection_reason TEXT,
        canonical_fact_match TEXT,
        candidate_json TEXT NOT NULL CHECK(json_valid(candidate_json)),
        FOREIGN KEY(run_id) REFERENCES acceptance_queue_run(run_id),
        FOREIGN KEY(canonical_fact_match) REFERENCES canonical_fact(canonical_fact_id),
        UNIQUE(run_id, lead_id),
        CHECK(length(document_sha256)=64),
        CHECK(value_numeric=value_numeric AND abs(value_numeric)<=1.7976931348623157e308),
        CHECK(length(trim(passage_locator))>0 AND length(trim(passage_text))>0),
        CHECK(entity_id<>counterparty_entity_id),
        CHECK(status IN ('pending_review','duplicate_fact','rejected')),
        CHECK((status='pending_review' AND rejection_reason IS NULL
               AND canonical_fact_match IS NULL)
           OR (status='duplicate_fact' AND rejection_reason IS NULL
               AND canonical_fact_match IS NOT NULL)
           OR (status='rejected' AND rejection_reason IS NOT NULL AND canonical_fact_match IS NULL))
    )""",
    """CREATE TRIGGER acceptance_queue_run_no_update BEFORE UPDATE ON acceptance_queue_run
       BEGIN SELECT RAISE(ABORT,'acceptance queue run is append-only'); END""",
    """CREATE TRIGGER acceptance_queue_run_no_delete BEFORE DELETE ON acceptance_queue_run
       BEGIN SELECT RAISE(ABORT,'acceptance queue run is append-only'); END""",
    """CREATE TRIGGER acceptance_queue_item_no_update BEFORE UPDATE ON acceptance_queue_item
       BEGIN SELECT RAISE(ABORT,'acceptance queue item is append-only'); END""",
    """CREATE TRIGGER acceptance_queue_item_no_delete BEFORE DELETE ON acceptance_queue_item
       BEGIN SELECT RAISE(ABORT,'acceptance queue item is append-only'); END""",
)
