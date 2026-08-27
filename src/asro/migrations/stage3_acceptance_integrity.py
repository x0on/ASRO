from __future__ import annotations

VERSION = 8
NAME = "stage3_acceptance_semantic_integrity"

STATEMENTS = (
    """CREATE TABLE backfill_coverage_metric (
        run_id TEXT NOT NULL,
        dimension TEXT NOT NULL,
        present_count INTEGER NOT NULL,
        total_count INTEGER NOT NULL,
        threshold REAL NOT NULL,
        PRIMARY KEY(run_id, dimension),
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id),
        CHECK(dimension IN ('feature','source','control')),
        CHECK(present_count BETWEEN 0 AND total_count),
        CHECK(total_count > 0),
        CHECK(threshold BETWEEN 0.0 AND 1.0)
    )""",
    """CREATE TABLE control_series_definition (
        series_id TEXT NOT NULL,
        series_version TEXT NOT NULL,
        unit TEXT NOT NULL,
        provenance_schema_json TEXT NOT NULL CHECK(
            json_valid(provenance_schema_json) AND json_type(provenance_schema_json)='object'
        ),
        registered_at TEXT NOT NULL CHECK(
            strftime('%Y-%m-%dT%H:%M:%S+00:00', registered_at)=registered_at
        ),
        PRIMARY KEY(series_id, series_version)
    )""",
    """CREATE TABLE historical_control_observation_v2 (
        control_observation_id TEXT PRIMARY KEY,
        series_id TEXT NOT NULL,
        series_version TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        observed_at TEXT NOT NULL CHECK(
            strftime('%Y-%m-%dT%H:%M:%S+00:00', observed_at)=observed_at
        ),
        availability_at TEXT NOT NULL CHECK(
            strftime('%Y-%m-%dT%H:%M:%S+00:00', availability_at)=availability_at
        ),
        value_numeric REAL NOT NULL,
        unit TEXT NOT NULL,
        provenance_json TEXT NOT NULL CHECK(
            json_valid(provenance_json) AND json_type(provenance_json)='object'
            AND COALESCE(json_type(provenance_json,'$.publisher')='text',0)
            AND COALESCE(json_type(provenance_json,'$.source_url')='text',0)
            AND COALESCE(json_type(provenance_json,'$.vintage')='text',0)
            AND length(trim(json_extract(provenance_json,'$.publisher'))) > 0
            AND length(trim(json_extract(provenance_json,'$.source_url'))) > 0
            AND length(trim(json_extract(provenance_json,'$.vintage'))) > 0
        ),
        FOREIGN KEY(series_id, series_version)
            REFERENCES control_series_definition(series_id, series_version),
        CHECK(period_end >= period_start),
        CHECK(availability_at >= observed_at),
        CHECK(value_numeric=value_numeric AND abs(value_numeric)<=1.7976931348623157e308)
    )""",
    """CREATE TABLE backfill_control_snapshot_v2 (
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
        provenance_json TEXT NOT NULL,
        PRIMARY KEY(run_id,control_observation_id),
        FOREIGN KEY(run_id) REFERENCES backfill_run(run_id),
        FOREIGN KEY(control_observation_id)
            REFERENCES historical_control_observation_v2(control_observation_id)
    )""",
    """CREATE TRIGGER historical_control_v2_semantics BEFORE INSERT
       ON historical_control_observation_v2 BEGIN
         SELECT CASE WHEN NOT EXISTS(
           SELECT 1 FROM control_series_definition definition
           WHERE definition.series_id=NEW.series_id
             AND definition.series_version=NEW.series_version
             AND definition.unit=NEW.unit
         ) THEN RAISE(ABORT, 'control unit/version semantics mismatched') END;
       END""",
    """CREATE TRIGGER backfill_control_snapshot_v2_validate BEFORE INSERT
       ON backfill_control_snapshot_v2 BEGIN
         SELECT CASE WHEN NOT EXISTS(
           SELECT 1 FROM historical_control_observation_v2 observation
           JOIN backfill_run run ON run.run_id=NEW.run_id
           JOIN backfill_episode episode
             ON episode.episode_id=run.episode_id AND episode.version=run.episode_version
           JOIN json_each(episode.manifest_json,'$.controls') control
           WHERE observation.control_observation_id=NEW.control_observation_id
             AND observation.series_id=NEW.series_id
             AND observation.series_version=NEW.series_version
             AND observation.unit=NEW.unit
             AND observation.availability_at=NEW.availability_at
             AND observation.availability_at<=episode.availability_cutoff
             AND json_extract(control.value,'$.series_id')=NEW.series_id
             AND json_extract(control.value,'$.version')=NEW.series_version
             AND json_extract(control.value,'$.unit')=NEW.unit
         ) THEN RAISE(ABORT, 'control snapshot semantics or cutoff mismatched') END;
       END""",
    """CREATE TABLE candidate_acquired_document (
        package_id TEXT NOT NULL,
        candidate_event_id TEXT NOT NULL,
        source_ordinal INTEGER NOT NULL,
        document_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        content_text TEXT NOT NULL,
        public_availability_at TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        acquisition_provenance_json TEXT NOT NULL CHECK(
            json_valid(acquisition_provenance_json)
            AND json_type(acquisition_provenance_json)='object'
        ),
        PRIMARY KEY(package_id,candidate_event_id,source_ordinal),
        FOREIGN KEY(package_id,candidate_event_id,source_ordinal)
            REFERENCES candidate_source_edge(package_id,candidate_event_id,source_ordinal),
        FOREIGN KEY(document_id) REFERENCES items(id),
        CHECK(length(content_sha256)=64),
        CHECK(sha256(content_text)=content_sha256),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',public_availability_at)=public_availability_at),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',fetched_at)=fetched_at)
    )""",
    """CREATE TABLE candidate_evidence_promotion_v2 (
        package_id TEXT NOT NULL,
        candidate_event_id TEXT NOT NULL,
        source_ordinal INTEGER NOT NULL,
        observation_id TEXT NOT NULL UNIQUE,
        canonical_fact_id TEXT NOT NULL,
        entity_role TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        decision_json TEXT NOT NULL CHECK(
            json_valid(decision_json) AND json_type(decision_json)='object'
            AND json_extract(decision_json,'$.decision')='promote'
        ),
        PRIMARY KEY(package_id,candidate_event_id,source_ordinal,observation_id),
        FOREIGN KEY(package_id,candidate_event_id,source_ordinal)
            REFERENCES candidate_acquired_document(package_id,candidate_event_id,source_ordinal),
        FOREIGN KEY(observation_id) REFERENCES observation_v2(observation_id),
        FOREIGN KEY(canonical_fact_id) REFERENCES canonical_fact(canonical_fact_id),
        CHECK(entity_role IN ('primary','counterparty')),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',reviewed_at)=reviewed_at)
    )""",
    """CREATE TRIGGER candidate_promotion_v2_validate BEFORE INSERT
       ON candidate_evidence_promotion_v2 BEGIN
         SELECT CASE WHEN NOT EXISTS(
           SELECT 1 FROM candidate_event candidate
           JOIN candidate_acquired_document acquired
             ON acquired.package_id=candidate.package_id
            AND acquired.candidate_event_id=candidate.candidate_event_id
            AND acquired.source_ordinal=NEW.source_ordinal
           JOIN observation_v2 observation ON observation.observation_id=NEW.observation_id
           JOIN canonical_fact_assignment assignment
             ON assignment.event_id=observation.event_id
            AND assignment.canonical_fact_id=NEW.canonical_fact_id
            AND assignment.available_at<=NEW.reviewed_at
           WHERE candidate.package_id=NEW.package_id
             AND candidate.candidate_event_id=NEW.candidate_event_id
             AND observation.source_document_id=acquired.document_id
             AND observation.availability_at>=acquired.public_availability_at
             AND candidate.effective_date
                 BETWEEN observation.period_start AND observation.period_end
             AND ((NEW.entity_role='primary' AND observation.entity_id=candidate.primary_entity)
               OR (NEW.entity_role='counterparty'
                   AND observation.entity_id=candidate.counterparty_entity))
         ) THEN RAISE(ABORT, 'candidate promotion lacks acquired reviewed fact lineage') END;
       END""",
    "DROP TRIGGER backfill_coverage_cell_validate",
    """CREATE TRIGGER backfill_coverage_cell_validate BEFORE INSERT
       ON backfill_coverage_cell BEGIN
         SELECT CASE WHEN NEW.dimension='source' AND NEW.present != EXISTS(
           SELECT 1 FROM backfill_source_snapshot_v2 snapshot
           JOIN backfill_run run ON run.run_id=snapshot.run_id
           JOIN backfill_episode episode
             ON episode.episode_id=run.episode_id AND episode.version=run.episode_version
           JOIN observation_v2 observation
             ON observation.source_document_id=snapshot.document_id
            AND observation.entity_id=snapshot.entity_id
           JOIN canonical_fact_assignment assignment ON assignment.event_id=observation.event_id
           WHERE snapshot.run_id=NEW.run_id AND snapshot.entity_id=NEW.entity_id
             AND snapshot.source_plan_id=NEW.requirement_key
             AND observation.period_start<=NEW.period_end
             AND observation.period_end>=NEW.period_start
             AND observation.review_confidence IS NOT NULL
             AND assignment.available_at<=episode.availability_cutoff
         ) THEN RAISE(ABORT, 'source coverage cell is not derived from reviewed V2 evidence') END;
         SELECT CASE WHEN NEW.dimension='control' AND NEW.present != EXISTS(
           SELECT 1 FROM backfill_control_snapshot_v2 control
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
             AND value.value_numeric IS NOT NULL AND value.missingness_reason IS NULL
             AND value.fact_count>0
             AND value.reliability >= COALESCE((
               SELECT json_extract(feature.value,'$.minimum_reliability')
               FROM backfill_run run JOIN backfill_episode episode
                 ON episode.episode_id=run.episode_id AND episode.version=run.episode_version
               JOIN json_each(episode.manifest_json,'$.features') feature
               WHERE run.run_id=NEW.run_id
                 AND json_extract(feature.value,'$.feature_key')=NEW.requirement_key
                 AND json_extract(feature.value,'$.feature_version')=NEW.requirement_version
             ),0.0)
             AND EXISTS(SELECT 1 FROM feature_value_fact fact
                        WHERE fact.feature_value_id=value.feature_value_id)
         ) THEN RAISE(ABORT, 'feature coverage cell is not evidence-backed') END;
       END""",
    "DROP TRIGGER backfill_run_finalize_validate",
    """CREATE TRIGGER backfill_run_finalize_validate BEFORE INSERT
       ON backfill_run_finalization BEGIN
         SELECT CASE WHEN (SELECT manifest_checksum FROM backfill_run WHERE run_id=NEW.run_id)
           != (SELECT episode.manifest_checksum FROM backfill_run run JOIN backfill_episode episode
                 ON episode.episode_id=run.episode_id AND episode.version=run.episode_version
                WHERE run.run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill manifest identity mismatched') END;
         SELECT CASE WHEN (SELECT run_id FROM backfill_run WHERE run_id=NEW.run_id)
           != sha256((SELECT episode_id||'|'||episode_version||'|'||input_checksum
                      FROM backfill_run WHERE run_id=NEW.run_id))
           THEN RAISE(ABORT, 'backfill run identity mismatched') END;
         SELECT CASE WHEN (SELECT COUNT(*) FROM backfill_source_snapshot_v2 WHERE run_id=NEW.run_id)
           != (SELECT source_count FROM backfill_run WHERE run_id=NEW.run_id)
           OR (SELECT COUNT(*) FROM backfill_build_link WHERE run_id=NEW.run_id)
           != (SELECT build_count FROM backfill_run WHERE run_id=NEW.run_id)
           OR (SELECT COUNT(*) FROM backfill_control_snapshot_v2 WHERE run_id=NEW.run_id)
           != (SELECT control_count FROM backfill_run WHERE run_id=NEW.run_id)
           OR (SELECT COUNT(*) FROM backfill_coverage_cell WHERE run_id=NEW.run_id)
           != (SELECT coverage_cell_count FROM backfill_run WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill normalized counts do not match') END;
         SELECT CASE WHEN EXISTS(
           SELECT 1 FROM backfill_coverage_metric metric
           WHERE metric.run_id=NEW.run_id AND (
             metric.present_count != (SELECT COUNT(*) FROM backfill_coverage_cell cell
               WHERE cell.run_id=NEW.run_id AND cell.dimension=metric.dimension AND cell.present=1)
             OR metric.total_count != (SELECT COUNT(*) FROM backfill_coverage_cell cell
               WHERE cell.run_id=NEW.run_id AND cell.dimension=metric.dimension)
           )) THEN RAISE(ABORT, 'backfill coverage metrics are not derived') END;
         SELECT CASE WHEN (SELECT coverage_passed FROM backfill_run WHERE run_id=NEW.run_id)
           != (NOT EXISTS(SELECT 1 FROM backfill_coverage_metric metric
                 WHERE metric.run_id=NEW.run_id
                   AND 1.0*metric.present_count/metric.total_count < metric.threshold)
               AND EXISTS(SELECT 1 FROM backfill_coverage_metric WHERE run_id=NEW.run_id))
           THEN RAISE(ABORT, 'backfill coverage pass flag is forged') END;
         SELECT CASE WHEN (SELECT leakage_passed FROM backfill_run WHERE run_id=NEW.run_id)
           != NOT EXISTS(SELECT 1 FROM backfill_leakage_violation WHERE run_id=NEW.run_id)
           THEN RAISE(ABORT, 'backfill leakage pass flag is forged') END;
         SELECT CASE WHEN (SELECT coverage_json FROM backfill_run WHERE run_id=NEW.run_id)
           != (SELECT json_object(
                'cell_count',(SELECT COUNT(*) FROM backfill_coverage_cell WHERE run_id=NEW.run_id),
                'missing_cell_count',(SELECT COUNT(*) FROM backfill_coverage_cell
                                      WHERE run_id=NEW.run_id AND present=0),
                'passed',(SELECT coverage_passed FROM backfill_run WHERE run_id=NEW.run_id)) )
           THEN RAISE(ABORT, 'backfill coverage report is not canonical') END;
         SELECT CASE WHEN (SELECT leakage_json FROM backfill_run WHERE run_id=NEW.run_id)
           != json_object('passed',(
                SELECT leakage_passed FROM backfill_run WHERE run_id=NEW.run_id),
                          'violation_count',(SELECT COUNT(*) FROM backfill_leakage_violation
                                             WHERE run_id=NEW.run_id))
           THEN RAISE(ABORT, 'backfill leakage report is not canonical') END;
       END""",
    *tuple(
        f"""CREATE TRIGGER {table}_no_{action} BEFORE {action.upper()} ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"""
        for table in (
            "backfill_coverage_metric",
            "control_series_definition",
            "historical_control_observation_v2",
            "backfill_control_snapshot_v2",
            "candidate_acquired_document",
            "candidate_evidence_promotion_v2",
        )
        for action in ("update", "delete")
    ),
)
