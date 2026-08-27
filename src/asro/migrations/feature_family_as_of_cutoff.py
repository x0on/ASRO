"""Close the same-period availability bypass in as-of feature lineage."""

VERSION = 14
NAME = "feature_family_as_of_row_cutoff"

_PERIOD_MATCH = """(
  (
    json_extract(definition.definition_json, '$.aggregation') != 'as_of_latest'
    AND substr(observation.period_end, 1, 10) BETWEEN value.period_start AND value.period_end
  )
  OR (
    json_extract(definition.definition_json, '$.aggregation') = 'as_of_latest'
    AND substr(observation.period_end, 1, 10) <= value.period_end
    AND observation.availability_at <= value.period_end || 'T23:59:59.999999+00:00'
    AND (
      (CAST(substr(value.period_end, 1, 4) AS INTEGER)
       - CAST(substr(observation.period_end, 1, 4) AS INTEGER)) * 12
      + CAST(substr(value.period_end, 6, 2) AS INTEGER)
      - CAST(substr(observation.period_end, 6, 2) AS INTEGER)
    ) <= json_extract(definition.definition_json, '$.max_age_months')
  )
)"""

STATEMENTS = (
    "DROP TRIGGER feature_value_fact_validate",
    "DROP TRIGGER feature_value_contributor_validate",
    f"""CREATE TRIGGER feature_value_fact_validate BEFORE INSERT ON feature_value_fact
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM feature_value value
           JOIN dataset_build build ON build.build_id = value.build_id
           JOIN feature_definition definition
             ON definition.feature_key = value.feature_key
            AND definition.feature_version = value.feature_version
           JOIN observation_v2 observation
             ON observation.observation_id = NEW.representative_observation_id
           JOIN canonical_fact_assignment assignment
             ON assignment.assignment_id = NEW.canonical_assignment_id
           WHERE value.feature_value_id = NEW.feature_value_id
             AND assignment.canonical_fact_id = NEW.canonical_fact_id
             AND assignment.event_id = observation.event_id
             AND assignment.available_at <= build.availability_cutoff
             AND observation.availability_at <= build.availability_cutoff
             AND observation.entity_id = value.entity_id
             AND observation.feature_key = value.feature_key
             AND observation.feature_version = value.feature_version
             AND observation.economic_scope = 'entity'
             AND {_PERIOD_MATCH}
             AND NOT EXISTS (
               SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id = assignment.assignment_id
                 AND correction.available_at <= build.availability_cutoff
             )
         ) THEN RAISE(ABORT, 'fact lineage does not match feature cell') END;
       END""",
    f"""CREATE TRIGGER feature_value_contributor_validate
       BEFORE INSERT ON feature_value_contributor
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM feature_value value
           JOIN dataset_build build ON build.build_id = value.build_id
           JOIN feature_definition definition
             ON definition.feature_key = value.feature_key
            AND definition.feature_version = value.feature_version
           JOIN observation_v2 observation ON observation.observation_id = NEW.observation_id
           JOIN canonical_fact_assignment assignment
             ON assignment.assignment_id = NEW.canonical_assignment_id
           WHERE value.feature_value_id = NEW.feature_value_id
             AND assignment.canonical_fact_id = NEW.canonical_fact_id
             AND assignment.event_id = observation.event_id
             AND assignment.available_at <= build.availability_cutoff
             AND observation.availability_at <= build.availability_cutoff
             AND observation.entity_id = value.entity_id
             AND observation.feature_key = value.feature_key
             AND observation.feature_version = value.feature_version
             AND observation.economic_scope = 'entity'
             AND {_PERIOD_MATCH}
             AND NOT EXISTS (
               SELECT 1 FROM canonical_fact_assignment correction
               WHERE correction.supersedes_assignment_id = assignment.assignment_id
                 AND correction.available_at <= build.availability_cutoff
             )
         ) THEN RAISE(ABORT, 'contributor does not match feature cell') END;
       END""",
)
