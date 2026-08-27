from __future__ import annotations

VERSION = 10
NAME = "stage3_temporal_and_repair_proof"

STATEMENTS = (
    """CREATE TABLE candidate_source_redirect (
        package_id TEXT NOT NULL,
        candidate_event_id TEXT NOT NULL,
        source_ordinal INTEGER NOT NULL,
        redirect_url TEXT NOT NULL,
        available_at TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        PRIMARY KEY(package_id,candidate_event_id,source_ordinal,redirect_url),
        FOREIGN KEY(package_id,candidate_event_id,source_ordinal)
            REFERENCES candidate_source_edge(package_id,candidate_event_id,source_ordinal),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',available_at)=available_at),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',reviewed_at)=reviewed_at),
        CHECK(reviewed_at>=available_at)
    )""",
    """CREATE TRIGGER candidate_acquisition_validate BEFORE INSERT
       ON candidate_acquired_document BEGIN
         SELECT CASE WHEN NEW.public_availability_at>NEW.fetched_at
           THEN RAISE(ABORT,'candidate document fetched before public availability') END;
         SELECT CASE WHEN NOT EXISTS(
           SELECT 1 FROM candidate_source_edge edge JOIN items item ON item.id=NEW.document_id
           WHERE edge.package_id=NEW.package_id
             AND edge.candidate_event_id=NEW.candidate_event_id
             AND edge.source_ordinal=NEW.source_ordinal
             AND (item.url=edge.url OR EXISTS(
               SELECT 1 FROM candidate_source_redirect redirect
               WHERE redirect.package_id=edge.package_id
                 AND redirect.candidate_event_id=edge.candidate_event_id
                 AND redirect.source_ordinal=edge.source_ordinal
                 AND redirect.redirect_url=item.url
                 AND redirect.available_at<=NEW.fetched_at))
         ) THEN RAISE(ABORT,'acquired document URL does not match candidate source edge') END;
       END""",
    "DROP TRIGGER candidate_promotion_v2_validate",
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
             AND acquired.public_availability_at<=acquired.fetched_at
             AND acquired.fetched_at<=NEW.reviewed_at
             AND observation.availability_at>=acquired.public_availability_at
             AND observation.availability_at<=NEW.reviewed_at
             AND observation.extracted_at<=NEW.reviewed_at
             AND candidate.effective_date
                 BETWEEN observation.period_start AND observation.period_end
             AND ((NEW.entity_role='primary' AND observation.entity_id=candidate.primary_entity)
               OR (NEW.entity_role='counterparty'
                   AND observation.entity_id=candidate.counterparty_entity))
             AND NOT EXISTS(
               SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id=assignment.assignment_id
                 AND correction.available_at<=NEW.reviewed_at)
         ) THEN RAISE(ABORT,'candidate promotion lacks active acquired fact lineage') END;
       END""",
    """CREATE TRIGGER backfill_source_cell_temporal_validate BEFORE INSERT
       ON backfill_coverage_cell WHEN NEW.dimension='source' BEGIN
         SELECT CASE WHEN NEW.present != EXISTS(
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
             AND observation.availability_at<=episode.availability_cutoff
             AND observation.extracted_at<=episode.availability_cutoff
             AND (observation.review_id IS NULL OR EXISTS(
               SELECT 1 FROM evidence_reviews review
               WHERE review.review_id=observation.review_id
                 AND review.reviewed_at<=episode.availability_cutoff))
             AND assignment.available_at<=episode.availability_cutoff
             AND NOT EXISTS(
               SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id=assignment.assignment_id
                 AND correction.available_at<=episode.availability_cutoff)
         ) THEN RAISE(ABORT,'source coverage uses evidence unavailable at cutoff') END;
       END""",
    "ALTER TABLE collector_runs ADD COLUMN repair_execution_id TEXT",
    "ALTER TABLE collector_runs ADD COLUMN target_window_start TEXT",
    "ALTER TABLE collector_runs ADD COLUMN target_window_end TEXT",
    "ALTER TABLE collector_runs ADD COLUMN acquisition_start TEXT",
    "ALTER TABLE collector_runs ADD COLUMN acquisition_end TEXT",
    """CREATE TABLE repair_execution (
        repair_execution_id TEXT PRIMARY KEY,
        target_window_start TEXT NOT NULL,
        target_window_end TEXT NOT NULL,
        acquisition_start TEXT NOT NULL,
        acquisition_end TEXT NOT NULL,
        started_at TEXT NOT NULL,
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',target_window_start)=target_window_start),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',target_window_end)=target_window_end),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',acquisition_start)=acquisition_start),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',acquisition_end)=acquisition_end),
        CHECK(strftime('%Y-%m-%dT%H:%M:%S+00:00',started_at)=started_at),
        CHECK(target_window_end>target_window_start),
        CHECK(acquisition_start<=target_window_start AND acquisition_end>=target_window_end)
    )""",
    """CREATE TABLE repair_execution_collector (
        repair_execution_id TEXT NOT NULL,
        collector_run_id INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(repair_execution_id,collector_run_id),
        FOREIGN KEY(repair_execution_id) REFERENCES repair_execution(repair_execution_id),
        FOREIGN KEY(collector_run_id) REFERENCES collector_runs(id)
    )""",
    """CREATE TABLE repair_execution_finalization (
        repair_execution_id TEXT PRIMARY KEY,
        finalized_at TEXT NOT NULL,
        FOREIGN KEY(repair_execution_id) REFERENCES repair_execution(repair_execution_id)
    )""",
    """CREATE TRIGGER repair_execution_finalize_validate BEFORE INSERT
       ON repair_execution_finalization BEGIN
         SELECT CASE WHEN strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.finalized_at)!=NEW.finalized_at
           THEN RAISE(ABORT,'repair finalization timestamp is noncanonical') END;
         SELECT CASE WHEN EXISTS(
           SELECT 1 FROM repair_execution_collector link JOIN collector_runs run
             ON run.id=link.collector_run_id
           WHERE link.repair_execution_id=NEW.repair_execution_id
             AND (run.status!='ok' OR run.repair_execution_id!=NEW.repair_execution_id
               OR run.target_window_start!=(SELECT target_window_start FROM repair_execution
                                            WHERE repair_execution_id=NEW.repair_execution_id)
               OR run.target_window_end!=(SELECT target_window_end FROM repair_execution
                                          WHERE repair_execution_id=NEW.repair_execution_id)
               OR run.acquisition_start!=(SELECT acquisition_start FROM repair_execution
                                          WHERE repair_execution_id=NEW.repair_execution_id)
               OR run.acquisition_end!=(SELECT acquisition_end FROM repair_execution
                                        WHERE repair_execution_id=NEW.repair_execution_id))
         ) THEN RAISE(ABORT,'repair collector provenance is inconsistent') END;
         SELECT CASE WHEN (SELECT COUNT(DISTINCT run.collector) FROM repair_execution_collector link
           JOIN collector_runs run ON run.id=link.collector_run_id
           WHERE link.repair_execution_id=NEW.repair_execution_id
             AND run.collector IN ('google-news-history','sec-edgar-history')) != 2
           THEN RAISE(ABORT,'repair required collector set is incomplete') END;
         SELECT CASE WHEN EXISTS(
           SELECT 1 FROM repair_execution_collector link JOIN collector_runs run
             ON run.id=link.collector_run_id
           WHERE link.repair_execution_id=NEW.repair_execution_id
             AND run.collector NOT IN ('google-news-history','sec-edgar-history'))
           THEN RAISE(ABORT,'repair includes an unexpected collector') END;
       END""",
    """CREATE TRIGGER collector_run_repair_validate BEFORE INSERT ON collector_runs
       WHEN NEW.repair_execution_id IS NOT NULL BEGIN
         SELECT CASE WHEN strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.started_at)!=NEW.started_at
           OR NEW.target_window_start IS NULL OR NEW.target_window_end IS NULL
           OR NEW.acquisition_start IS NULL OR NEW.acquisition_end IS NULL
           OR NOT EXISTS(SELECT 1 FROM repair_execution repair
             WHERE repair.repair_execution_id=NEW.repair_execution_id
               AND repair.target_window_start=NEW.target_window_start
               AND repair.target_window_end=NEW.target_window_end
               AND repair.acquisition_start=NEW.acquisition_start
               AND repair.acquisition_end=NEW.acquisition_end)
           THEN RAISE(ABORT,'collector run repair provenance is invalid') END;
       END""",
    """CREATE TRIGGER collector_run_completion_validate BEFORE UPDATE ON collector_runs BEGIN
         SELECT CASE WHEN NEW.collector!=OLD.collector OR NEW.started_at!=OLD.started_at
           OR NEW.repair_execution_id IS NOT OLD.repair_execution_id
           OR NEW.target_window_start IS NOT OLD.target_window_start
           OR NEW.target_window_end IS NOT OLD.target_window_end
           OR NEW.acquisition_start IS NOT OLD.acquisition_start
           OR NEW.acquisition_end IS NOT OLD.acquisition_end
           THEN RAISE(ABORT,'collector run identity is immutable') END;
         SELECT CASE WHEN NEW.status!='running' AND (
           NEW.completed_at IS NULL
           OR strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.completed_at)!=NEW.completed_at
           OR NEW.completed_at<NEW.started_at)
           THEN RAISE(ABORT,'collector completion timestamp is noncanonical or reversed') END;
       END""",
    """CREATE TABLE workflow_run_collector (
        workflow_run_id TEXT NOT NULL,
        collector_run_id INTEGER NOT NULL,
        PRIMARY KEY(workflow_run_id,collector_run_id),
        FOREIGN KEY(workflow_run_id) REFERENCES workflow_run_provenance(workflow_run_id),
        FOREIGN KEY(collector_run_id) REFERENCES collector_runs(id)
    )""",
    """CREATE TRIGGER workflow_run_provenance_validate BEFORE INSERT
       ON workflow_run_provenance BEGIN
         SELECT CASE WHEN strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.scheduled_for)!=NEW.scheduled_for
           OR strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.started_at)!=NEW.started_at
           OR strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.completed_at)!=NEW.completed_at
           OR NEW.completed_at<NEW.started_at
           THEN RAISE(ABORT,'workflow timestamps are noncanonical or reversed') END;
         SELECT CASE WHEN (NEW.conclusion='success')!=(NEW.failure_stage IS NULL)
           THEN RAISE(ABORT,'workflow conclusion and failure stage disagree') END;
       END""",
    """CREATE TRIGGER collection_assessment_validate BEFORE INSERT
       ON collection_window_assessment BEGIN
         SELECT CASE WHEN strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.window_start)!=NEW.window_start
           OR strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.window_end)!=NEW.window_end
           OR strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.recorded_at)!=NEW.recorded_at
           THEN RAISE(ABORT,'collection window timestamps are noncanonical') END;
         SELECT CASE WHEN NEW.supersedes_assessment_id IS NOT NULL AND NOT EXISTS(
           SELECT 1 FROM collection_window_assessment parent
           WHERE parent.assessment_id=NEW.supersedes_assessment_id
             AND parent.window_start=NEW.window_start AND parent.window_end=NEW.window_end
             AND parent.status='collection_failed' AND NEW.status='repaired')
           THEN RAISE(ABORT,'invalid collection-window transition') END;
         SELECT CASE WHEN NEW.supersedes_assessment_id IS NULL AND NOT EXISTS(
           SELECT 1 FROM workflow_run_provenance workflow
           WHERE workflow.workflow_run_id=NEW.workflow_run_id
             AND ((NEW.status='complete' AND workflow.conclusion='success')
               OR (NEW.status='collection_failed' AND workflow.conclusion!='success'
                   AND workflow.failure_stage='collection')
               OR (NEW.status='publish_failed' AND workflow.conclusion!='success'
                   AND workflow.failure_stage='publish')
               OR (NEW.status='deployment_failed' AND workflow.conclusion!='success'
                   AND workflow.failure_stage='deployment')))
           THEN RAISE(ABORT,'assessment status disagrees with workflow') END;
         SELECT CASE WHEN NEW.status='repaired' AND NOT EXISTS(
           SELECT 1 FROM workflow_run_collector workflow_link
           JOIN collector_runs run ON run.id=workflow_link.collector_run_id
           JOIN repair_execution_finalization finalized
             ON finalized.repair_execution_id=run.repair_execution_id
           JOIN repair_execution repair ON repair.repair_execution_id=run.repair_execution_id
           WHERE workflow_link.workflow_run_id=NEW.workflow_run_id
             AND repair.target_window_start=NEW.window_start
             AND repair.target_window_end=NEW.window_end)
           THEN RAISE(ABORT,'repaired window lacks finalized collector proof') END;
         SELECT CASE WHEN (
           (SELECT COUNT(*) FROM json_each(NEW.collector_runs_json)) !=
             (SELECT COUNT(*) FROM workflow_run_collector
              WHERE workflow_run_id=NEW.workflow_run_id)
           OR EXISTS(SELECT 1 FROM json_each(NEW.collector_runs_json) claimed
              WHERE NOT EXISTS(SELECT 1 FROM workflow_run_collector linked
                WHERE linked.workflow_run_id=NEW.workflow_run_id
                  AND linked.collector_run_id=claimed.value))
           OR EXISTS(SELECT 1 FROM workflow_run_collector linked
              WHERE linked.workflow_run_id=NEW.workflow_run_id
                AND NOT EXISTS(SELECT 1 FROM json_each(NEW.collector_runs_json) claimed
                  WHERE claimed.value=linked.collector_run_id)))
           THEN RAISE(ABORT,'collector report does not match normalized links') END;
       END""",
    """CREATE TRIGGER alert_resolution_validate BEFORE INSERT
       ON operational_alert_resolution BEGIN
         SELECT CASE WHEN strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.resolved_at)!=NEW.resolved_at
           THEN RAISE(ABORT,'alert resolution timestamp is noncanonical') END;
         SELECT CASE WHEN NOT EXISTS(
           SELECT 1 FROM operational_alert alert JOIN collection_window_assessment repair
             ON repair.assessment_id=NEW.assessment_id
           WHERE alert.alert_id=NEW.alert_id AND repair.status='repaired'
             AND alert.window_start=repair.window_start AND alert.window_end=repair.window_end)
           THEN RAISE(ABORT,'alert resolution does not match a valid repair') END;
       END""",
    *tuple(
        f"""CREATE TRIGGER {table}_no_{action} BEFORE {action_upper} ON {table}
            BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END"""
        for table in (
            "candidate_source_redirect",
            "repair_execution",
            "repair_execution_collector",
            "repair_execution_finalization",
            "workflow_run_collector",
        )
        for action, action_upper in (("update", "UPDATE"), ("delete", "DELETE"))
    ),
)
