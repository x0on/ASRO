from __future__ import annotations

VERSION = 5
NAME = "auditable_historical_backfill"

STATEMENTS = (
    """CREATE TABLE backfill_episode (
        episode_id TEXT NOT NULL,
        version TEXT NOT NULL,
        stratum TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        availability_cutoff TEXT NOT NULL,
        manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
        manifest_checksum TEXT NOT NULL,
        registered_at TEXT NOT NULL,
        PRIMARY KEY(episode_id, version),
        UNIQUE(manifest_checksum),
        CHECK(stratum IN ('crisis', 'benign', 'current')),
        CHECK(period_end >= period_start)
    )""",
    """CREATE TABLE backfill_run (
        run_id TEXT PRIMARY KEY,
        episode_id TEXT NOT NULL,
        episode_version TEXT NOT NULL,
        manifest_checksum TEXT NOT NULL,
        input_checksum TEXT NOT NULL,
        coverage_json TEXT NOT NULL CHECK(json_valid(coverage_json)),
        coverage_checksum TEXT NOT NULL,
        leakage_json TEXT NOT NULL CHECK(json_valid(leakage_json)),
        leakage_checksum TEXT NOT NULL,
        coverage_passed INTEGER NOT NULL,
        leakage_passed INTEGER NOT NULL,
        source_count INTEGER NOT NULL,
        build_count INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(episode_id, episode_version)
            REFERENCES backfill_episode(episode_id, version),
        UNIQUE(episode_id, episode_version, input_checksum),
        CHECK(coverage_passed IN (0, 1)),
        CHECK(leakage_passed IN (0, 1)),
        CHECK(source_count >= 0),
        CHECK(build_count >= 0)
    )""",
    """CREATE TABLE backfill_source_snapshot (
        run_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        source_plan_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        published_at TEXT,
        discovered_at TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        availability_at TEXT NOT NULL,
        content_type TEXT,
        fetch_status TEXT NOT NULL,
        PRIMARY KEY(run_id, document_id),
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id),
        FOREIGN KEY(document_id) REFERENCES items(id),
        CHECK(length(content_sha256) = 64),
        CHECK(availability_at = fetched_at)
    )""",
    """CREATE TABLE backfill_build_link (
        run_id TEXT NOT NULL,
        grain TEXT NOT NULL,
        build_id TEXT NOT NULL,
        build_checksum TEXT NOT NULL,
        PRIMARY KEY(run_id, grain),
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id),
        CHECK(grain IN ('entity_month', 'ecosystem_month'))
    )""",
    """CREATE TRIGGER backfill_source_validate BEFORE INSERT ON backfill_source_snapshot
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM backfill_run run
           JOIN backfill_episode episode
             ON episode.episode_id = run.episode_id
            AND episode.version = run.episode_version
           JOIN json_each(episode.manifest_json, '$.source_plan') planned
           WHERE run.run_id = NEW.run_id
             AND json_extract(planned.value, '$.source_id') = NEW.source_plan_id
         ) THEN RAISE(ABORT, 'source snapshot is not declared by episode') END;
       END""",
    """CREATE TRIGGER backfill_build_link_validate BEFORE INSERT ON backfill_build_link
       BEGIN
         SELECT CASE WHEN
           (NEW.grain = 'entity_month' AND NOT EXISTS (
             SELECT 1 FROM dataset_build build
             JOIN dataset_build_finalization finalized ON finalized.build_id = build.build_id
             WHERE build.build_id = NEW.build_id AND build.checksum = NEW.build_checksum
           )) OR (NEW.grain = 'ecosystem_month' AND NOT EXISTS (
             SELECT 1 FROM ecosystem_dataset_build build
             JOIN ecosystem_dataset_build_finalization finalized
               ON finalized.build_id = build.build_id
             WHERE build.build_id = NEW.build_id AND build.checksum = NEW.build_checksum
           )) THEN RAISE(ABORT, 'backfill build link is not finalized or checksum mismatched') END;
       END""",
    """CREATE TABLE backfill_run_finalization (
        run_id TEXT PRIMARY KEY,
        finalized_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id)
    )""",
    """CREATE TRIGGER backfill_run_finalize_validate BEFORE INSERT
       ON backfill_run_finalization BEGIN
         SELECT CASE WHEN (
           SELECT COUNT(*) FROM backfill_source_snapshot WHERE run_id = NEW.run_id
         ) != (SELECT source_count FROM backfill_run WHERE run_id = NEW.run_id)
         THEN RAISE(ABORT, 'backfill source count does not match') END;
         SELECT CASE WHEN (
           SELECT COUNT(*) FROM backfill_build_link WHERE run_id = NEW.run_id
         ) != (SELECT build_count FROM backfill_run WHERE run_id = NEW.run_id)
         THEN RAISE(ABORT, 'backfill build count does not match') END;
       END""",
    """CREATE TRIGGER backfill_episode_no_update BEFORE UPDATE ON backfill_episode
       BEGIN SELECT RAISE(ABORT, 'backfill_episode is append-only'); END""",
    """CREATE TRIGGER backfill_episode_no_delete BEFORE DELETE ON backfill_episode
       BEGIN SELECT RAISE(ABORT, 'backfill_episode is append-only'); END""",
    """CREATE TRIGGER backfill_run_no_update BEFORE UPDATE ON backfill_run
       BEGIN SELECT RAISE(ABORT, 'backfill_run is append-only'); END""",
    """CREATE TRIGGER backfill_run_no_delete BEFORE DELETE ON backfill_run
       BEGIN SELECT RAISE(ABORT, 'backfill_run is append-only'); END""",
    """CREATE TRIGGER backfill_source_no_update BEFORE UPDATE ON backfill_source_snapshot
       BEGIN SELECT RAISE(ABORT, 'backfill_source_snapshot is append-only'); END""",
    """CREATE TRIGGER backfill_source_no_delete BEFORE DELETE ON backfill_source_snapshot
       BEGIN SELECT RAISE(ABORT, 'backfill_source_snapshot is append-only'); END""",
    """CREATE TRIGGER backfill_link_no_update BEFORE UPDATE ON backfill_build_link
       BEGIN SELECT RAISE(ABORT, 'backfill_build_link is append-only'); END""",
    """CREATE TRIGGER backfill_link_no_delete BEFORE DELETE ON backfill_build_link
       BEGIN SELECT RAISE(ABORT, 'backfill_build_link is append-only'); END""",
    """CREATE TRIGGER backfill_finalization_no_update BEFORE UPDATE ON backfill_run_finalization
       BEGIN SELECT RAISE(ABORT, 'backfill_run_finalization is append-only'); END""",
    """CREATE TRIGGER backfill_finalization_no_delete BEFORE DELETE ON backfill_run_finalization
       BEGIN SELECT RAISE(ABORT, 'backfill_run_finalization is append-only'); END""",
    """CREATE TRIGGER finalized_backfill_no_source BEFORE INSERT ON backfill_source_snapshot
       WHEN EXISTS (
         SELECT 1 FROM backfill_run_finalization WHERE run_id = NEW.run_id
       ) BEGIN SELECT RAISE(ABORT, 'backfill run is finalized'); END""",
    """CREATE TRIGGER finalized_backfill_no_link BEFORE INSERT ON backfill_build_link
       WHEN EXISTS (
         SELECT 1 FROM backfill_run_finalization WHERE run_id = NEW.run_id
       ) BEGIN SELECT RAISE(ABORT, 'backfill run is finalized'); END""",
    """CREATE VIEW finalized_backfill_run AS
       SELECT run.* FROM backfill_run run
       JOIN backfill_run_finalization finalized ON finalized.run_id = run.run_id""",
)
