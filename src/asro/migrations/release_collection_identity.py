from __future__ import annotations

VERSION = 12
NAME = "release_collection_execution_identity"

STATEMENTS = (
    "ALTER TABLE collector_runs ADD COLUMN collection_execution_id TEXT",
    """CREATE INDEX idx_collector_collection_execution
       ON collector_runs(collection_execution_id,collector)""",
    """CREATE TRIGGER collector_collection_execution_immutable BEFORE UPDATE ON collector_runs
       WHEN NEW.collection_execution_id IS NOT OLD.collection_execution_id
       BEGIN SELECT RAISE(ABORT,'collector collection execution is immutable'); END""",
)
