from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from statistics import fmean

from pydantic import BaseModel, ConfigDict


class FeatureQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_key: str
    feature_version: str
    row_count: int
    numeric_count: int
    missing_count: int
    missingness: dict[str, int]
    mean_coverage: float
    minimum_coverage: float
    mean_reliability: float
    minimum_reliability: float
    distinct_canonical_facts: int
    distinct_observations: int
    distinct_source_documents: int
    source_tiers: dict[str, int]
    fact_statuses: dict[str, int]


class BuildQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    build_id: str
    grain: str
    checksum: str
    code_commit: str
    feature_set_version: str
    availability_cutoff: str
    period_start: str
    period_end: str
    row_count: int
    numeric_count: int
    missing_count: int
    mean_coverage: float
    minimum_coverage: float
    mean_reliability: float
    minimum_reliability: float
    distinct_canonical_facts: int
    distinct_observations: int
    distinct_source_documents: int
    features: list[FeatureQuality]

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def audit_finalized_build(
    connection: sqlite3.Connection, build_id: str, grain: str = "auto"
) -> BuildQualityReport:
    if grain not in {"auto", "entity_month", "ecosystem_month"}:
        raise ValueError("grain must be auto, entity_month, or ecosystem_month")
    entity = connection.execute(
        """SELECT build.* FROM dataset_build build
           JOIN dataset_build_finalization finalized ON finalized.build_id = build.build_id
           WHERE build.build_id = ?""",
        (build_id,),
    ).fetchone()
    ecosystem = connection.execute(
        """SELECT build.* FROM ecosystem_dataset_build build
           JOIN ecosystem_dataset_build_finalization finalized
             ON finalized.build_id = build.build_id
           WHERE build.build_id = ?""",
        (build_id,),
    ).fetchone()
    if grain == "entity_month":
        ecosystem = None
    elif grain == "ecosystem_month":
        entity = None
    if (entity is None) == (ecosystem is None):
        raise ValueError("exactly one finalized build must match the requested identity and grain")
    row = entity or ecosystem
    resolved_grain = "entity_month" if entity is not None else "ecosystem_month"
    values = _quality_rows(connection, build_id, resolved_grain)
    if len(values) != int(row["row_count"]):
        raise RuntimeError("finalized build row count is inconsistent")
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for value in values:
        grouped[(str(value["feature_key"]), str(value["feature_version"]))].append(value)
    feature_reports = [
        _feature_quality(connection, resolved_grain, items) for _, items in sorted(grouped.items())
    ]
    coverages = [float(item["coverage"]) for item in values]
    reliabilities = [float(item["reliability"]) for item in values]
    all_facts: set[str] = set()
    all_observations: set[str] = set()
    all_documents: set[str] = set()
    for feature in feature_reports:
        fact_ids, observation_ids, document_ids, _, _ = _provenance_for_feature(
            connection,
            resolved_grain,
            [
                str(value[_value_id_column(resolved_grain)])
                for value in grouped[(feature.feature_key, feature.feature_version)]
            ],
        )
        all_facts.update(fact_ids)
        all_observations.update(observation_ids)
        all_documents.update(document_ids)
    return BuildQualityReport(
        build_id=str(row["build_id"]),
        grain=resolved_grain,
        checksum=str(row["checksum"]),
        code_commit=str(row["code_commit"]),
        feature_set_version=str(row["feature_set_version"]),
        availability_cutoff=str(row["availability_cutoff"]),
        period_start=str(row["period_start"]),
        period_end=str(row["period_end"]),
        row_count=len(values),
        numeric_count=sum(item["value_numeric"] is not None for item in values),
        missing_count=sum(item["value_numeric"] is None for item in values),
        mean_coverage=_mean(coverages),
        minimum_coverage=min(coverages, default=0.0),
        mean_reliability=_mean(reliabilities),
        minimum_reliability=min(reliabilities, default=0.0),
        distinct_canonical_facts=len(all_facts),
        distinct_observations=len(all_observations),
        distinct_source_documents=len(all_documents),
        features=feature_reports,
    )


def _quality_rows(connection: sqlite3.Connection, build_id: str, grain: str) -> list[sqlite3.Row]:
    table = (
        "finalized_entity_feature_value"
        if grain == "entity_month"
        else "finalized_ecosystem_feature_value"
    )
    return list(
        connection.execute(
            f"""SELECT * FROM {table} WHERE build_id = ?
                ORDER BY period_start, feature_key, feature_version""",
            (build_id,),
        )
    )


def _feature_quality(
    connection: sqlite3.Connection, grain: str, rows: list[sqlite3.Row]
) -> FeatureQuality:
    ids = [str(item[_value_id_column(grain)]) for item in rows]
    facts, observations, documents, tiers, statuses = _provenance_for_feature(
        connection, grain, ids
    )
    missingness = Counter(
        str(item["missingness_reason"]) for item in rows if item["missingness_reason"] is not None
    )
    coverages = [float(item["coverage"]) for item in rows]
    reliabilities = [float(item["reliability"]) for item in rows]
    return FeatureQuality(
        feature_key=str(rows[0]["feature_key"]),
        feature_version=str(rows[0]["feature_version"]),
        row_count=len(rows),
        numeric_count=sum(item["value_numeric"] is not None for item in rows),
        missing_count=sum(item["value_numeric"] is None for item in rows),
        missingness=dict(sorted(missingness.items())),
        mean_coverage=_mean(coverages),
        minimum_coverage=min(coverages, default=0.0),
        mean_reliability=_mean(reliabilities),
        minimum_reliability=min(reliabilities, default=0.0),
        distinct_canonical_facts=len(facts),
        distinct_observations=len(observations),
        distinct_source_documents=len(documents),
        source_tiers=dict(sorted(tiers.items())),
        fact_statuses=dict(sorted(statuses.items())),
    )


def _provenance_for_feature(
    connection: sqlite3.Connection, grain: str, value_ids: list[str]
) -> tuple[set[str], set[str], set[str], Counter[str], Counter[str]]:
    facts: set[str] = set()
    observations: set[str] = set()
    documents: set[str] = set()
    tiers: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    for value_id in value_ids:
        if grain == "entity_month":
            fact_rows = connection.execute(
                "SELECT canonical_fact_id FROM feature_value_fact WHERE feature_value_id = ?",
                (value_id,),
            )
            observation_rows = connection.execute(
                """SELECT observation.* FROM feature_value_contributor contributor
                   JOIN observation_v2 observation
                     ON observation.observation_id = contributor.observation_id
                   WHERE contributor.feature_value_id = ?""",
                (value_id,),
            )
        else:
            fact_rows = connection.execute(
                """SELECT canonical_fact_id FROM ecosystem_feature_value_fact
                   WHERE ecosystem_feature_value_id = ?""",
                (value_id,),
            )
            observation_rows = connection.execute(
                """SELECT DISTINCT observation.*
                   FROM ecosystem_feature_value_entity_contributor ecosystem_contributor
                   JOIN feature_value_contributor contributor
                     ON contributor.feature_value_id =
                        ecosystem_contributor.source_feature_value_id
                   JOIN observation_v2 observation
                     ON observation.observation_id = contributor.observation_id
                   WHERE ecosystem_contributor.ecosystem_feature_value_id = ?""",
                (value_id,),
            )
        facts.update(str(item[0]) for item in fact_rows)
        for observation in observation_rows:
            observation_id = str(observation["observation_id"])
            if observation_id in observations:
                continue
            observations.add(observation_id)
            documents.add(str(observation["source_document_id"]))
            tiers[str(observation["source_tier"])] += 1
            statuses[str(observation["fact_status"])] += 1
    return facts, observations, documents, tiers, statuses


def _value_id_column(grain: str) -> str:
    return "feature_value_id" if grain == "entity_month" else "ecosystem_feature_value_id"


def _mean(values: list[float]) -> float:
    return round(fmean(values), 6) if values else 0.0
