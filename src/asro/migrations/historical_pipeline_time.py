"""Separate knowability time from pipeline time in backfill source coverage.

`backfill_source_cell_temporal_validate` previously required an observation's
`extracted_at` and its review's `reviewed_at` to precede the episode's availability
cutoff. Those two timestamps describe when this observatory did its own work. For a
retrospective episode that work necessarily happens now, so the condition could never be
satisfied for any historical window and every historical episode was permanently
uncoverable regardless of the evidence behind it.

The leakage guarantee does not depend on them. What must precede the cutoff is what was
*knowable* then:

* `observation.availability_at` - when the source document was public;
* `assignment.available_at` - when the canonical fact was established;
* the absence of a superseding assignment available by the cutoff.

All three remain enforced here, and `BackfillRunner._leakage_report` still rejects any
source or observation whose availability postdates the cutoff. `extracted_at` and
`reviewed_at` are still recorded on every row and are still ordered against
`availability_at` by the `observation_v2` check constraints; they are simply no longer
treated as as-of filters, because they answer a different question.
"""

from __future__ import annotations

VERSION = 17
NAME = "historical_pipeline_time_separation"

STATEMENTS: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS backfill_source_cell_temporal_validate",
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
           JOIN canonical_fact_assignment assignment ON assignment.event_id=observation.event_id
           WHERE snapshot.run_id=NEW.run_id AND snapshot.entity_id=NEW.entity_id
             AND snapshot.source_plan_id=NEW.requirement_key
             AND observation.period_start<=NEW.period_end
             AND observation.period_end>=NEW.period_start
             AND observation.availability_at<=episode.availability_cutoff
             AND assignment.available_at<=episode.availability_cutoff
             AND NOT EXISTS(SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id=assignment.assignment_id
                 AND correction.available_at<=episode.availability_cutoff)
         ) THEN RAISE(ABORT,'source coverage lacks accepted review available at cutoff') END;
       END""",
)
