from __future__ import annotations

VERSION = 11
NAME = "stage3_final_integrity_closure"

STATEMENTS = (
    "DROP TRIGGER candidate_acquisition_validate",
    """CREATE TRIGGER candidate_acquisition_validate BEFORE INSERT
       ON candidate_acquired_document BEGIN
         SELECT CASE WHEN NEW.public_availability_at>NEW.fetched_at
           THEN RAISE(ABORT,'candidate document fetched before public availability') END;
         SELECT CASE WHEN NOT EXISTS(
           SELECT 1 FROM candidate_source_edge edge
           JOIN items item ON item.id=NEW.document_id
           JOIN documents document ON document.item_id=NEW.document_id
           WHERE edge.package_id=NEW.package_id
             AND edge.candidate_event_id=NEW.candidate_event_id
             AND edge.source_ordinal=NEW.source_ordinal
             AND document.fetch_status='ok'
             AND julianday(document.fetched_at)=julianday(NEW.fetched_at)
             AND sha256(document.text)=NEW.content_sha256
             AND document.text=NEW.content_text
             AND (item.url=edge.url OR EXISTS(
               SELECT 1 FROM candidate_source_redirect redirect
               WHERE redirect.package_id=edge.package_id
                 AND redirect.candidate_event_id=edge.candidate_event_id
                 AND redirect.source_ordinal=edge.source_ordinal
                 AND redirect.redirect_url=item.url
                 AND redirect.available_at<=NEW.fetched_at))
         ) THEN RAISE(ABORT,
           'candidate acquisition does not match immutable repository content') END;
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
           JOIN evidence_reviews review ON review.review_id=observation.review_id
             AND review.decision IN ('confirm','merge') AND review.reviewed_at<=NEW.reviewed_at
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
             AND NOT EXISTS(SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id=assignment.assignment_id
                 AND correction.available_at<=NEW.reviewed_at)
         ) THEN RAISE(ABORT,
           'candidate promotion lacks accepted reviewed acquired fact lineage') END;
       END""",
    "DROP TRIGGER backfill_source_cell_temporal_validate",
    "DROP TRIGGER backfill_coverage_cell_validate",
    """CREATE TRIGGER backfill_coverage_cell_validate BEFORE INSERT
       ON backfill_coverage_cell BEGIN
         SELECT CASE WHEN NEW.dimension='control' AND NEW.present != EXISTS(
           SELECT 1 FROM backfill_control_snapshot_v2 control
           WHERE control.run_id=NEW.run_id AND control.series_id=NEW.requirement_key
             AND control.series_version=NEW.requirement_version
             AND control.period_start=NEW.period_start AND control.period_end=NEW.period_end
         ) THEN RAISE(ABORT,'control coverage cell is not derived from evidence') END;
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
         ) THEN RAISE(ABORT,'feature coverage cell is not evidence-backed') END;
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
           JOIN evidence_reviews review ON review.review_id=observation.review_id
             AND review.decision IN ('confirm','merge')
             AND review.reviewed_at<=episode.availability_cutoff
           JOIN canonical_fact_assignment assignment ON assignment.event_id=observation.event_id
           WHERE snapshot.run_id=NEW.run_id AND snapshot.entity_id=NEW.entity_id
             AND snapshot.source_plan_id=NEW.requirement_key
             AND observation.period_start<=NEW.period_end
             AND observation.period_end>=NEW.period_start
             AND observation.availability_at<=episode.availability_cutoff
             AND observation.extracted_at<=episode.availability_cutoff
             AND assignment.available_at<=episode.availability_cutoff
             AND NOT EXISTS(SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id=assignment.assignment_id
                 AND correction.available_at<=episode.availability_cutoff)
         ) THEN RAISE(ABORT,'source coverage lacks accepted review available at cutoff') END;
       END""",
    """CREATE TRIGGER evidence_reviews_no_update BEFORE UPDATE ON evidence_reviews
       BEGIN SELECT RAISE(ABORT,'evidence reviews are append-only'); END""",
    """CREATE TRIGGER evidence_reviews_no_delete BEFORE DELETE ON evidence_reviews
       BEGIN SELECT RAISE(ABORT,'evidence reviews are append-only'); END""",
    "DROP TRIGGER collector_run_completion_validate",
    """CREATE TRIGGER collector_run_completion_validate BEFORE UPDATE ON collector_runs BEGIN
         SELECT CASE WHEN OLD.status!='running'
           THEN RAISE(ABORT,'terminal collector run is immutable') END;
         SELECT CASE WHEN NEW.collector!=OLD.collector OR NEW.started_at!=OLD.started_at
           OR NEW.repair_execution_id IS NOT OLD.repair_execution_id
           OR NEW.target_window_start IS NOT OLD.target_window_start
           OR NEW.target_window_end IS NOT OLD.target_window_end
           OR NEW.acquisition_start IS NOT OLD.acquisition_start
           OR NEW.acquisition_end IS NOT OLD.acquisition_end
           THEN RAISE(ABORT,'collector run identity is immutable') END;
         SELECT CASE WHEN NEW.status='running' OR NEW.completed_at IS NULL
           OR strftime('%Y-%m-%dT%H:%M:%S+00:00',NEW.completed_at)!=NEW.completed_at
           OR NEW.completed_at<NEW.started_at
           THEN RAISE(ABORT,'collector terminal transition is invalid') END;
         SELECT CASE WHEN EXISTS(SELECT 1 FROM repair_execution_collector link
           JOIN repair_execution_finalization final
             ON final.repair_execution_id=link.repair_execution_id
           WHERE link.collector_run_id=OLD.id)
           THEN RAISE(ABORT,'finalized repair collector is immutable') END;
       END""",
    """CREATE UNIQUE INDEX idx_workflow_collector_single_use
       ON workflow_run_collector(collector_run_id)""",
    """CREATE TRIGGER complete_collection_proof_validate BEFORE INSERT
       ON collection_window_assessment WHEN NEW.status='complete' BEGIN
         SELECT CASE WHEN (SELECT COUNT(*) FROM workflow_run_collector
           WHERE workflow_run_id=NEW.workflow_run_id) != 4
           OR (SELECT COUNT(DISTINCT run.collector)
           FROM workflow_run_collector link JOIN collector_runs run ON run.id=link.collector_run_id
           WHERE link.workflow_run_id=NEW.workflow_run_id
             AND run.collector IN ('google-news-rss','company-economic-news',
                                   'external-competitive-pressure','sec-edgar')) != 4
           OR EXISTS(SELECT 1 FROM workflow_run_collector link
             JOIN collector_runs run ON run.id=link.collector_run_id
             JOIN workflow_run_provenance workflow ON workflow.workflow_run_id=link.workflow_run_id
             WHERE link.workflow_run_id=NEW.workflow_run_id
               AND (run.collector NOT IN ('google-news-rss','company-economic-news',
                                          'external-competitive-pressure','sec-edgar')
                 OR run.status NOT IN ('ok','degraded')
                 OR run.repair_execution_id IS NOT NULL
                 OR run.started_at<workflow.started_at OR run.completed_at>workflow.completed_at))
           THEN RAISE(ABORT,'complete window lacks exact current collector proof') END;
       END""",
)
