from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import fmean

from asro.evidence.models import EconomicScope, ObservationV2
from asro.evidence.repository import EvidenceRepository
from asro.evidence.time import timestamp_text
from asro.features.models import (
    Aggregation,
    FactLineage,
    FeatureSpec,
    FeatureValue,
    MissingnessReason,
)


@dataclass(frozen=True)
class BuildResult:
    build_id: str
    checksum: str
    row_count: int


def _month_bounds(value: date) -> tuple[str, str]:
    start = value.replace(day=1)
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    return start.isoformat(), (next_month - date.resolution).isoformat()


def _month_start(value: str | date | datetime) -> date:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    elif isinstance(value, datetime):
        value = value.date()
    return value.replace(day=1)


def _months(start: date, end: date) -> list[date]:
    result = []
    current = start
    while current <= end:
        result.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return result


def _representative(observations: list[ObservationV2]) -> ObservationV2:
    return max(
        observations,
        key=lambda item: (
            item.review_confidence or 0.0,
            item.source_quality,
            item.extraction_confidence,
            item.availability_at,
            item.observation_id,
        ),
    )


def _reliability(observations: list[ObservationV2]) -> float:
    values = [
        fmean(
            value
            for value in (
                observation.source_quality,
                observation.extraction_confidence,
                observation.review_confidence,
            )
            if value is not None
        )
        for observation in observations
    ]
    return round(fmean(values), 6)


class FeatureStoreBuilder:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def build_entity_month(
        self,
        specs: list[FeatureSpec],
        availability_cutoff: str | date | datetime,
        expected_entities: list[str],
        code_commit: str,
        feature_set_version: str,
        period_start: str | date | datetime,
        period_end: str | date | datetime,
    ) -> BuildResult:
        if not specs or not expected_entities:
            raise ValueError("feature specs and expected entities are required")
        if len({(item.feature_key, item.feature_version) for item in specs}) != len(specs):
            raise ValueError("feature specs must be unique by key and version")
        canonical_specs = sorted(specs, key=lambda item: (item.feature_key, item.feature_version))
        requested_start = _month_start(period_start)
        requested_end = _month_start(period_end)
        if requested_end < requested_start:
            raise ValueError("period_end must not precede period_start")
        requested_months = _months(requested_start, requested_end)
        for spec in canonical_specs:
            registered = self._connection.execute(
                """SELECT definition_json FROM feature_definition
                   WHERE feature_key = ? AND feature_version = ?""",
                (spec.feature_key, spec.feature_version),
            ).fetchone()
            if registered is None:
                raise ValueError(f"unregistered feature: {spec.feature_key}@{spec.feature_version}")
            semantics = json.loads(str(registered["definition_json"]))
            if (
                semantics.get("aggregation") != spec.aggregation.value
                or semantics.get("unit") != spec.unit
                or semantics.get("expected_facts_per_period") != spec.expected_facts_per_period
            ):
                raise ValueError(
                    f"build spec does not match registered semantics: "
                    f"{spec.feature_key}@{spec.feature_version}"
                )
        cutoff = timestamp_text(availability_cutoff)
        observations = EvidenceRepository.as_of(self._connection, availability_cutoff)
        assignments = EvidenceRepository.canonical_assignments_as_of(
            self._connection, availability_cutoff
        )
        spec_by_key = {(spec.feature_key, spec.feature_version): spec for spec in canonical_specs}
        groups: dict[tuple[str, str, str, str], list[ObservationV2]] = defaultdict(list)
        for observation in observations:
            candidate = spec_by_key.get((observation.feature_key, observation.feature_version))
            if (
                candidate is None
                or observation.value_numeric is None
                or observation.economic_scope is not EconomicScope.ENTITY
                or observation.entity_id is None
                or observation.period_end is None
                or observation.unit != candidate.unit
            ):
                continue
            month = observation.period_end.date().replace(day=1).isoformat()
            if requested_start.isoformat() <= month <= requested_end.isoformat():
                groups[
                    (
                        observation.entity_id,
                        month,
                        observation.feature_key,
                        observation.feature_version,
                    )
                ].append(observation)

        rows: list[FeatureValue] = []
        for month_value in requested_months:
            month = month_value.isoformat()
            row_period_start, row_period_end = _month_bounds(month_value)
            for entity in sorted(set(expected_entities)):
                for spec in canonical_specs:
                    contributors = groups.get(
                        (entity, month, spec.feature_key, spec.feature_version), []
                    )
                    if not contributors:
                        rows.append(
                            FeatureValue(
                                feature_value_id="pending",
                                build_id="pending",
                                entity_id=entity,
                                period_start=row_period_start,
                                period_end=row_period_end,
                                feature_key=spec.feature_key,
                                feature_version=spec.feature_version,
                                missingness_reason=MissingnessReason.UNKNOWN,
                                coverage=0.0,
                                reliability=0.0,
                                fact_lineage=[],
                            )
                        )
                        continue
                    ordered = sorted(contributors, key=lambda item: item.observation_id)
                    by_fact: dict[str, list[ObservationV2]] = defaultdict(list)
                    for observation in ordered:
                        assignment = assignments.get(observation.event_id)
                        if assignment is None:
                            raise ValueError(
                                f"no canonical assignment at cutoff for {observation.event_id}"
                            )
                        by_fact[assignment[0]].append(observation)
                    representatives = sorted(
                        (_representative(items) for items in by_fact.values()),
                        key=lambda item: (item.availability_at, item.observation_id),
                    )
                    values = [
                        float(item.value_numeric)
                        for item in representatives
                        if item.value_numeric is not None
                    ]
                    if spec.aggregation is Aggregation.SUM:
                        value = sum(values)
                    elif spec.aggregation is Aggregation.MEAN:
                        value = fmean(values)
                    else:
                        value = values[-1]
                    rows.append(
                        FeatureValue(
                            feature_value_id="pending",
                            build_id="pending",
                            entity_id=entity,
                            period_start=row_period_start,
                            period_end=row_period_end,
                            feature_key=spec.feature_key,
                            feature_version=spec.feature_version,
                            value_numeric=value,
                            coverage=min(
                                1.0, len(representatives) / spec.expected_facts_per_period
                            ),
                            reliability=_reliability(ordered),
                            fact_lineage=[
                                FactLineage(
                                    canonical_fact_id=fact_id,
                                    canonical_assignment_id=assignments[
                                        _representative(items).event_id
                                    ][1],
                                    representative_observation_id=_representative(
                                        items
                                    ).observation_id,
                                    contributor_assignments={
                                        item.observation_id: assignments[item.event_id][1]
                                        for item in sorted(
                                            items, key=lambda candidate: candidate.observation_id
                                        )
                                    },
                                )
                                for fact_id, items in sorted(by_fact.items())
                            ],
                        )
                    )

        canonical_rows = [
            row.model_dump(mode="json", exclude={"build_id", "feature_value_id"}) for row in rows
        ]
        manifest = {
            "grain": "entity_month",
            "code_commit": code_commit,
            "availability_cutoff": cutoff,
            "feature_set_version": feature_set_version,
            "period_start": requested_start.isoformat(),
            "period_end": requested_end.isoformat(),
            "coverage_semantics": (
                "distinct_economic_facts / expected_facts_per_period, capped at 1"
            ),
            "specs": [spec.model_dump(mode="json") for spec in canonical_specs],
            "rows": canonical_rows,
        }
        serialized = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(serialized.encode()).hexdigest()
        build_id = hashlib.sha256(serialized.encode()).hexdigest()
        final_rows = []
        for row in rows:
            grain = "|".join(
                [
                    build_id,
                    row.entity_id,
                    row.period_start,
                    row.period_end,
                    row.feature_key,
                    row.feature_version,
                ]
            )
            final_rows.append(
                row.model_copy(
                    update={
                        "build_id": build_id,
                        "feature_value_id": hashlib.sha256(grain.encode()).hexdigest(),
                    }
                )
            )
        self._persist(
            build_id, code_commit, feature_set_version, cutoff, serialized, checksum, final_rows
        )
        return BuildResult(build_id=build_id, checksum=checksum, row_count=len(final_rows))

    def _persist(
        self,
        build_id: str,
        code_commit: str,
        feature_set_version: str,
        cutoff: str,
        manifest: str,
        checksum: str,
        rows: list[FeatureValue],
    ) -> None:
        period_start = min(row.period_start for row in rows)
        period_end = max(row.period_end for row in rows)
        existing = self._connection.execute(
            """SELECT build_id, code_commit, feature_set_version, availability_cutoff,
                      period_start, period_end, row_count, manifest_json, checksum
               FROM dataset_build WHERE build_id = ?""",
            (build_id,),
        ).fetchone()
        if existing is not None:
            expected_metadata = (
                build_id,
                code_commit,
                feature_set_version,
                cutoff,
                period_start,
                period_end,
                len(rows),
                manifest,
                checksum,
            )
            if tuple(existing) != expected_metadata:
                raise ValueError("build ID already exists with different metadata or content")
            self._validate_existing_build(build_id, rows)
            return
        try:
            self._connection.execute("BEGIN")
            self._connection.execute(
                """INSERT INTO dataset_build (
                    build_id, code_commit, feature_set_version, availability_cutoff,
                    period_start, period_end, row_count, manifest_json, checksum, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    build_id,
                    code_commit,
                    feature_set_version,
                    cutoff,
                    period_start,
                    period_end,
                    len(rows),
                    manifest,
                    checksum,
                    datetime.now(UTC).isoformat(),
                ),
            )
            for row in rows:
                values = row.model_dump(mode="json", exclude={"fact_lineage"})
                values["fact_count"] = len(row.fact_lineage)
                values["contributor_count"] = sum(
                    len(fact.contributor_assignments) for fact in row.fact_lineage
                )
                self._connection.execute(
                    """INSERT INTO feature_value (
                        feature_value_id, build_id, entity_id, period_start, period_end,
                        feature_key, feature_version, value_numeric, missingness_reason,
                        coverage, reliability, fact_count, contributor_count
                    ) VALUES (
                        :feature_value_id, :build_id, :entity_id, :period_start, :period_end,
                        :feature_key, :feature_version, :value_numeric, :missingness_reason,
                        :coverage, :reliability, :fact_count, :contributor_count
                    )""",
                    values,
                )
                self._connection.executemany(
                    """INSERT INTO feature_value_fact (
                        feature_value_id, canonical_fact_id, canonical_assignment_id,
                        representative_observation_id
                    ) VALUES (?, ?, ?, ?)""",
                    [
                        (
                            row.feature_value_id,
                            fact.canonical_fact_id,
                            fact.canonical_assignment_id,
                            fact.representative_observation_id,
                        )
                        for fact in row.fact_lineage
                    ],
                )
                self._connection.executemany(
                    """INSERT INTO feature_value_contributor (
                        feature_value_id, canonical_fact_id, canonical_assignment_id,
                        observation_id
                    ) VALUES (?, ?, ?, ?)""",
                    [
                        (
                            row.feature_value_id,
                            fact.canonical_fact_id,
                            assignment_id,
                            observation_id,
                        )
                        for fact in row.fact_lineage
                        for observation_id, assignment_id in fact.contributor_assignments.items()
                    ],
                )
            self._connection.execute(
                "INSERT INTO dataset_build_finalization(build_id, finalized_at) VALUES (?, ?)",
                (build_id, datetime.now(UTC).isoformat()),
            )
        except Exception:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _validate_existing_build(self, build_id: str, rows: list[FeatureValue]) -> None:
        finalized = self._connection.execute(
            "SELECT 1 FROM dataset_build_finalization WHERE build_id = ?", (build_id,)
        ).fetchone()
        if finalized is None:
            raise ValueError("matching build exists but is not finalized")
        stored_count = self._connection.execute(
            "SELECT COUNT(*) FROM feature_value WHERE build_id = ?", (build_id,)
        ).fetchone()[0]
        if int(stored_count) != len(rows):
            raise ValueError("finalized build has inconsistent feature rows")
        for expected in rows:
            stored = self._connection.execute(
                """SELECT feature_value_id, build_id, entity_id, period_start, period_end,
                          feature_key, feature_version, value_numeric, missingness_reason,
                          coverage, reliability, fact_count, contributor_count
                   FROM feature_value WHERE feature_value_id = ? AND build_id = ?""",
                (expected.feature_value_id, build_id),
            ).fetchone()
            expected_fact_count = len(expected.fact_lineage)
            expected_contributor_count = sum(
                len(fact.contributor_assignments) for fact in expected.fact_lineage
            )
            if stored is None or tuple(stored) != (
                expected.feature_value_id,
                expected.build_id,
                expected.entity_id,
                expected.period_start,
                expected.period_end,
                expected.feature_key,
                expected.feature_version,
                expected.value_numeric,
                expected.missingness_reason.value if expected.missingness_reason else None,
                expected.coverage,
                expected.reliability,
                expected_fact_count,
                expected_contributor_count,
            ):
                raise ValueError("finalized build feature content is inconsistent")
            facts = {
                tuple(item)
                for item in self._connection.execute(
                    """SELECT canonical_fact_id, canonical_assignment_id,
                              representative_observation_id
                       FROM feature_value_fact WHERE feature_value_id = ?""",
                    (expected.feature_value_id,),
                )
            }
            expected_facts = {
                (
                    fact.canonical_fact_id,
                    fact.canonical_assignment_id,
                    fact.representative_observation_id,
                )
                for fact in expected.fact_lineage
            }
            contributors = {
                tuple(item)
                for item in self._connection.execute(
                    """SELECT canonical_fact_id, canonical_assignment_id, observation_id
                       FROM feature_value_contributor WHERE feature_value_id = ?""",
                    (expected.feature_value_id,),
                )
            }
            expected_contributors = {
                (fact.canonical_fact_id, assignment_id, observation_id)
                for fact in expected.fact_lineage
                for observation_id, assignment_id in fact.contributor_assignments.items()
            }
            if facts != expected_facts or contributors != expected_contributors:
                raise ValueError("finalized build lineage is inconsistent")
