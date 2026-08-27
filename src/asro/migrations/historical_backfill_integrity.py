from __future__ import annotations

VERSION = 6
NAME = "historical_backfill_scientific_integrity"

STATEMENTS = (
    "ALTER TABLE backfill_source_snapshot ADD COLUMN entity_id TEXT",
    "ALTER TABLE backfill_source_snapshot ADD COLUMN availability_basis TEXT",
    "ALTER TABLE backfill_source_snapshot ADD COLUMN url TEXT",
    "ALTER TABLE backfill_source_snapshot ADD COLUMN title TEXT",
    "ALTER TABLE backfill_source_snapshot ADD COLUMN source_name TEXT",
    "ALTER TABLE backfill_source_snapshot ADD COLUMN content_text TEXT",
    """CREATE TABLE backfill_source_snapshot_v2 (
        run_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        source_plan_id TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        published_at TEXT,
        discovered_at TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        availability_at TEXT NOT NULL,
        availability_basis TEXT NOT NULL,
        content_type TEXT,
        fetch_status TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        source_name TEXT NOT NULL,
        content_text TEXT NOT NULL,
        PRIMARY KEY(run_id, document_id, entity_id),
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id),
        FOREIGN KEY(document_id) REFERENCES items(id),
        CHECK(length(content_sha256)=64),
        CHECK(availability_basis IN ('published_at', 'first_observed_at'))
    )""",
    """CREATE TABLE historical_control_observation (
        control_observation_id TEXT PRIMARY KEY,
        series_id TEXT NOT NULL,
        series_version TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        availability_at TEXT NOT NULL,
        value_numeric REAL NOT NULL,
        unit TEXT NOT NULL,
        provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
        CHECK(period_end >= period_start),
        CHECK(availability_at >= observed_at),
        CHECK(value_numeric = value_numeric AND abs(value_numeric) <= 1.7976931348623157e308)
    )""",
    """CREATE TRIGGER historical_control_no_update BEFORE UPDATE
       ON historical_control_observation
       BEGIN SELECT RAISE(ABORT, 'historical_control_observation is append-only'); END""",
    """CREATE TRIGGER historical_control_no_delete BEFORE DELETE
       ON historical_control_observation
       BEGIN SELECT RAISE(ABORT, 'historical_control_observation is append-only'); END""",
    """CREATE TABLE backfill_control_snapshot (
        run_id TEXT NOT NULL,
        control_observation_id TEXT NOT NULL,
        series_id TEXT NOT NULL,
        series_version TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        availability_at TEXT NOT NULL,
        value_numeric REAL NOT NULL,
        unit TEXT NOT NULL,
        provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
        PRIMARY KEY(run_id, control_observation_id),
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id),
        FOREIGN KEY(control_observation_id)
            REFERENCES historical_control_observation(control_observation_id)
    )""",
    """CREATE TABLE backfill_coverage_cell (
        run_id TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        dimension TEXT NOT NULL,
        requirement_key TEXT NOT NULL,
        requirement_version TEXT NOT NULL,
        present INTEGER NOT NULL,
        missingness_reason TEXT,
        PRIMARY KEY(
            run_id, entity_id, period_start, period_end,
            dimension, requirement_key, requirement_version
        ),
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id),
        CHECK(dimension IN ('feature', 'source', 'control')),
        CHECK(present IN (0, 1)),
        CHECK((present = 1 AND missingness_reason IS NULL)
           OR (present = 0 AND length(trim(missingness_reason)) > 0))
    )""",
    """CREATE TABLE backfill_leakage_violation (
        run_id TEXT NOT NULL,
        violation_type TEXT NOT NULL,
        identity TEXT NOT NULL,
        detail TEXT NOT NULL,
        PRIMARY KEY(run_id, violation_type, identity, detail),
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id)
    )""",
    "ALTER TABLE backfill_run ADD COLUMN control_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE backfill_run ADD COLUMN coverage_cell_count INTEGER NOT NULL DEFAULT 0",
    "DROP TRIGGER backfill_run_finalize_validate",
    """CREATE TRIGGER backfill_source_integrity_validate BEFORE INSERT
       ON backfill_source_snapshot_v2 BEGIN
         SELECT CASE WHEN sha256(NEW.content_text) != NEW.content_sha256
           THEN RAISE(ABORT, 'source snapshot content hash mismatched') END;
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM backfill_run run
           JOIN backfill_episode episode
             ON episode.episode_id = run.episode_id AND episode.version = run.episode_version
           JOIN json_each(episode.manifest_json, '$.entities') entity
           WHERE run.run_id = NEW.run_id AND entity.value = NEW.entity_id
         ) THEN RAISE(ABORT, 'source entity is not declared by episode') END;
         SELECT CASE WHEN NEW.availability_at > (
           SELECT episode.availability_cutoff FROM backfill_run run
           JOIN backfill_episode episode
             ON episode.episode_id = run.episode_id AND episode.version = run.episode_version
           WHERE run.run_id = NEW.run_id
         ) THEN RAISE(ABORT, 'source public availability exceeds cutoff') END;
       END""",
    """CREATE TRIGGER backfill_control_validate BEFORE INSERT
       ON backfill_control_snapshot BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM backfill_run run
           JOIN backfill_episode episode
             ON episode.episode_id = run.episode_id AND episode.version = run.episode_version
           JOIN json_each(episode.manifest_json, '$.controls') control
           WHERE run.run_id = NEW.run_id
             AND json_extract(control.value, '$.series_id') = NEW.series_id
             AND json_extract(control.value, '$.version') = NEW.series_version
             AND json_extract(control.value, '$.unit') = NEW.unit
         ) THEN RAISE(ABORT, 'control is not declared by episode') END;
       END""",
    """CREATE TRIGGER backfill_coverage_cell_validate BEFORE INSERT
       ON backfill_coverage_cell BEGIN
         SELECT CASE WHEN NEW.dimension='source' AND NEW.present != EXISTS(
           SELECT 1 FROM backfill_source_snapshot_v2 snapshot
           WHERE snapshot.run_id=NEW.run_id AND snapshot.entity_id=NEW.entity_id
             AND snapshot.source_plan_id=NEW.requirement_key
             AND (EXISTS(SELECT 1 FROM financial_events event
                         WHERE event.document_id=snapshot.document_id
                           AND event.effective_date BETWEEN NEW.period_start AND NEW.period_end)
                  OR EXISTS(SELECT 1 FROM observation_v2 observation
                            WHERE observation.source_document_id=snapshot.document_id
                              AND observation.period_start<=NEW.period_end
                              AND observation.period_end>=NEW.period_start))
         ) THEN RAISE(ABORT, 'source coverage cell is not derived from evidence') END;
         SELECT CASE WHEN NEW.dimension='control' AND NEW.present != EXISTS(
           SELECT 1 FROM backfill_control_snapshot control
           WHERE control.run_id=NEW.run_id AND control.series_id=NEW.requirement_key
             AND control.series_version=NEW.requirement_version
             AND control.period_start=NEW.period_start AND control.period_end=NEW.period_end
         ) THEN RAISE(ABORT, 'control coverage cell is not derived from evidence') END;
         SELECT CASE WHEN NEW.dimension='feature' AND NEW.present != EXISTS(
           SELECT 1 FROM backfill_build_link link
           JOIN finalized_entity_feature_value value ON value.build_id=link.build_id
           WHERE link.run_id=NEW.run_id AND link.grain='entity_month'
             AND value.entity_id=NEW.entity_id
             AND value.period_start=NEW.period_start AND value.period_end=NEW.period_end
             AND value.feature_key=NEW.requirement_key
             AND value.feature_version=NEW.requirement_version
         ) THEN RAISE(ABORT, 'feature coverage cell is not derived from evidence') END;
       END""",
    """CREATE TRIGGER backfill_run_finalize_validate BEFORE INSERT
       ON backfill_run_finalization BEGIN
         SELECT CASE WHEN (SELECT manifest_checksum FROM backfill_run WHERE run_id=NEW.run_id)
           != (SELECT episode.manifest_checksum FROM backfill_run run JOIN backfill_episode episode
                 ON episode.episode_id=run.episode_id AND episode.version=run.episode_version
                WHERE run.run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill manifest identity mismatched') END;
         SELECT CASE WHEN (SELECT run_id FROM backfill_run WHERE run_id=NEW.run_id)
           != sha256((SELECT episode_id || '|' || episode_version || '|' || input_checksum
                      FROM backfill_run WHERE run_id=NEW.run_id))
           THEN RAISE(ABORT, 'backfill run identity mismatched') END;
         SELECT CASE WHEN sha256((SELECT coverage_json FROM backfill_run WHERE run_id=NEW.run_id))
           != (SELECT coverage_checksum FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill coverage checksum mismatched') END;
         SELECT CASE WHEN sha256((SELECT leakage_json FROM backfill_run WHERE run_id=NEW.run_id))
           != (SELECT leakage_checksum FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill leakage checksum mismatched') END;
         SELECT CASE WHEN (SELECT COUNT(*) FROM backfill_source_snapshot_v2 WHERE run_id=NEW.run_id)
           != (SELECT source_count FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill source count does not match') END;
         SELECT CASE WHEN (SELECT COUNT(*) FROM backfill_build_link WHERE run_id=NEW.run_id)
           != (SELECT build_count FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill build count does not match') END;
         SELECT CASE WHEN (SELECT COUNT(*) FROM backfill_control_snapshot WHERE run_id=NEW.run_id)
           != (SELECT control_count FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill control count does not match') END;
         SELECT CASE WHEN (SELECT COUNT(*) FROM backfill_coverage_cell WHERE run_id=NEW.run_id)
           != (SELECT coverage_cell_count FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill coverage cell count does not match') END;
         SELECT CASE WHEN (SELECT coverage_passed FROM backfill_run WHERE run_id=NEW.run_id)
           != ((SELECT COUNT(*) FROM backfill_coverage_cell WHERE run_id=NEW.run_id) > 0
               AND NOT EXISTS(SELECT 1 FROM backfill_coverage_cell
                              WHERE run_id=NEW.run_id AND present=0))
           THEN RAISE(ABORT, 'backfill coverage pass flag is forged') END;
         SELECT CASE WHEN (SELECT leakage_passed FROM backfill_run WHERE run_id=NEW.run_id)
           != NOT EXISTS(SELECT 1 FROM backfill_leakage_violation WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill leakage pass flag is forged') END;
         SELECT CASE WHEN json_extract(
             (SELECT coverage_json FROM backfill_run WHERE run_id=NEW.run_id), '$.cell_count')
           != (SELECT coverage_cell_count FROM backfill_run WHERE run_id=NEW.run_id)
           OR json_extract(
             (SELECT coverage_json FROM backfill_run WHERE run_id=NEW.run_id), '$.passed')
           != (SELECT coverage_passed FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill coverage report is inconsistent') END;
         SELECT CASE WHEN json_extract(
             (SELECT leakage_json FROM backfill_run WHERE run_id=NEW.run_id), '$.violation_count')
           != (SELECT COUNT(*) FROM backfill_leakage_violation WHERE run_id=NEW.run_id)
           OR json_extract(
             (SELECT leakage_json FROM backfill_run WHERE run_id=NEW.run_id), '$.passed')
           != (SELECT leakage_passed FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill leakage report is inconsistent') END;
       END""",
    """CREATE TRIGGER backfill_control_no_update BEFORE UPDATE ON backfill_control_snapshot
       BEGIN SELECT RAISE(ABORT, 'backfill_control_snapshot is append-only'); END""",
    """CREATE TRIGGER backfill_control_no_delete BEFORE DELETE ON backfill_control_snapshot
       BEGIN SELECT RAISE(ABORT, 'backfill_control_snapshot is append-only'); END""",
    """CREATE TRIGGER backfill_cell_no_update BEFORE UPDATE ON backfill_coverage_cell
       BEGIN SELECT RAISE(ABORT, 'backfill_coverage_cell is append-only'); END""",
    """CREATE TRIGGER backfill_cell_no_delete BEFORE DELETE ON backfill_coverage_cell
       BEGIN SELECT RAISE(ABORT, 'backfill_coverage_cell is append-only'); END""",
    """CREATE TRIGGER backfill_source_v2_no_update BEFORE UPDATE ON backfill_source_snapshot_v2
       BEGIN SELECT RAISE(ABORT, 'backfill_source_snapshot_v2 is append-only'); END""",
    """CREATE TRIGGER backfill_source_v2_no_delete BEFORE DELETE ON backfill_source_snapshot_v2
       BEGIN SELECT RAISE(ABORT, 'backfill_source_snapshot_v2 is append-only'); END""",
    """CREATE TRIGGER finalized_backfill_no_source_v2 BEFORE INSERT ON backfill_source_snapshot_v2
       WHEN EXISTS (SELECT 1 FROM backfill_run_finalization WHERE run_id=NEW.run_id)
       BEGIN SELECT RAISE(ABORT, 'backfill run is finalized'); END""",
)
