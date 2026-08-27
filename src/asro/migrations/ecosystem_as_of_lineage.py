"""Allow ecosystem lineage only through exact finalized source-cell membership."""

VERSION = 15
NAME = "ecosystem_as_of_source_lineage"

STATEMENTS = (
    "DROP TRIGGER ecosystem_fact_validate",
    """CREATE TRIGGER ecosystem_fact_validate BEFORE INSERT
       ON ecosystem_feature_value_fact BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM ecosystem_feature_value ecosystem
           JOIN ecosystem_dataset_build build ON build.build_id = ecosystem.build_id
           JOIN observation_v2 observation
             ON observation.observation_id = NEW.representative_observation_id
           JOIN canonical_fact_assignment assignment
             ON assignment.assignment_id = NEW.canonical_assignment_id
           WHERE ecosystem.ecosystem_feature_value_id = NEW.ecosystem_feature_value_id
             AND assignment.canonical_fact_id = NEW.canonical_fact_id
             AND assignment.event_id = observation.event_id
             AND assignment.available_at <= build.availability_cutoff
             AND observation.availability_at <= build.availability_cutoff
             AND observation.feature_key = ecosystem.source_feature_key
             AND observation.feature_version = ecosystem.source_feature_version
             AND observation.economic_scope = 'entity'
             AND NOT EXISTS (
               SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id = assignment.assignment_id
                 AND correction.available_at <= build.availability_cutoff
             )
             AND EXISTS (
               SELECT 1 FROM ecosystem_feature_value_entity_contributor contributor
               JOIN feature_value_fact source_fact
                 ON source_fact.feature_value_id = contributor.source_feature_value_id
               WHERE contributor.ecosystem_feature_value_id =
                     NEW.ecosystem_feature_value_id
                 AND source_fact.canonical_fact_id = NEW.canonical_fact_id
                 AND source_fact.canonical_assignment_id = NEW.canonical_assignment_id
                 AND source_fact.representative_observation_id =
                     NEW.representative_observation_id
             )
         ) THEN RAISE(ABORT, 'fact does not match ecosystem cell') END;
       END""",
)
