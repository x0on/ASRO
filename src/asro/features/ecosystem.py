from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from statistics import fmean

from asro.evidence.models import ObservationV2
from asro.features.build import BuildResult, _representative
from asro.features.models import (
    Aggregation,
    EcosystemFeatureSpec,
    EcosystemFeatureValue,
    FactLineage,
    MissingnessReason,
)


class EcosystemFeatureStoreBuilder:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def build_months(
        self,
        source_entity_build_id: str,
        specs: list[EcosystemFeatureSpec],
        code_commit: str,
        feature_set_version: str,
    ) -> BuildResult:
        if not specs:
            raise ValueError("ecosystem feature specs are required")
        canonical_specs = sorted(
            specs,
            key=lambda item: (
                item.feature_key,
                item.feature_version,
                item.source_feature_key,
                item.source_feature_version,
            ),
        )
        if len({(item.feature_key, item.feature_version) for item in canonical_specs}) != len(
            canonical_specs
        ):
            raise ValueError("ecosystem output specs must be unique by key and version")
        source_build = self._connection.execute(
            """SELECT build.build_id, build.availability_cutoff, build.period_start,
                      build.period_end, build.checksum
               FROM dataset_build build
               JOIN dataset_build_finalization finalized ON finalized.build_id = build.build_id
               WHERE build.build_id = ?""",
            (source_entity_build_id,),
        ).fetchone()
        if source_build is None:
            raise ValueError("source entity build must exist and be finalized")
        for spec in canonical_specs:
            self._validate_spec(source_entity_build_id, spec)

        source_rows = self._load_source_rows(source_entity_build_id, canonical_specs)
        periods = sorted(
            {
                (str(row["period_start"]), str(row["period_end"]))
                for rows in source_rows.values()
                for row in rows
            }
        )
        if not periods:
            raise ValueError("source entity build contains no requested feature rows")
        rows: list[EcosystemFeatureValue] = []
        for period_start, period_end in periods:
            for spec in canonical_specs:
                entities = source_rows.get(
                    (
                        period_start,
                        period_end,
                        spec.source_feature_key,
                        spec.source_feature_version,
                    ),
                    [],
                )
                rows.append(self._aggregate_cell(period_start, period_end, spec, entities))

        canonical_rows = [
            row.model_dump(mode="json", exclude={"build_id", "ecosystem_feature_value_id"})
            for row in rows
        ]
        manifest = {
            "grain": "ecosystem_month",
            "source_entity_build_id": source_entity_build_id,
            "source_entity_build_checksum": str(source_build["checksum"]),
            "code_commit": code_commit,
            "feature_set_version": feature_set_version,
            "availability_cutoff": str(source_build["availability_cutoff"]),
            "period_start": str(source_build["period_start"]),
            "period_end": str(source_build["period_end"]),
            "coverage_semantics": "mean source entity-cell coverage, including missing cells",
            "specs": [spec.model_dump(mode="json") for spec in canonical_specs],
            "rows": canonical_rows,
        }
        serialized = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(serialized.encode()).hexdigest()
        build_id = checksum
        final_rows = []
        for row in rows:
            grain = "|".join(
                [
                    build_id,
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
                        "ecosystem_feature_value_id": hashlib.sha256(grain.encode()).hexdigest(),
                    }
                )
            )
        self._persist(
            build_id,
            source_entity_build_id,
            code_commit,
            feature_set_version,
            str(source_build["availability_cutoff"]),
            str(source_build["period_start"]),
            str(source_build["period_end"]),
            serialized,
            checksum,
            final_rows,
        )
        return BuildResult(build_id=build_id, checksum=checksum, row_count=len(final_rows))

    def _validate_spec(self, source_build_id: str, spec: EcosystemFeatureSpec) -> None:
        source = self._connection.execute(
            """SELECT 1 FROM finalized_entity_feature_value WHERE build_id = ?
               AND feature_key = ? AND feature_version = ? LIMIT 1""",
            (source_build_id, spec.source_feature_key, spec.source_feature_version),
        ).fetchone()
        if source is None:
            raise ValueError(
                f"source feature is absent from entity build: "
                f"{spec.source_feature_key}@{spec.source_feature_version}"
            )
        definition = self._connection.execute(
            """SELECT definition_json FROM feature_definition
               WHERE feature_key = ? AND feature_version = ?""",
            (spec.feature_key, spec.feature_version),
        ).fetchone()
        if definition is None:
            raise ValueError(
                f"unregistered ecosystem feature: {spec.feature_key}@{spec.feature_version}"
            )
        semantics = json.loads(str(definition[0]))
        if (
            semantics.get("grain") != "ecosystem_month"
            or semantics.get("aggregation") != spec.aggregation.value
            or semantics.get("unit") != spec.unit
        ):
            raise ValueError(
                f"ecosystem spec does not match registered semantics: "
                f"{spec.feature_key}@{spec.feature_version}"
            )

    def _load_source_rows(
        self, source_build_id: str, specs: list[EcosystemFeatureSpec]
    ) -> dict[tuple[str, str, str, str], list[sqlite3.Row]]:
        requested = {(item.source_feature_key, item.source_feature_version) for item in specs}
        groups: dict[tuple[str, str, str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in self._connection.execute(
            """SELECT * FROM finalized_entity_feature_value
               WHERE build_id = ? ORDER BY feature_value_id""",
            (source_build_id,),
        ):
            key = (str(row["feature_key"]), str(row["feature_version"]))
            if key in requested:
                groups[
                    (
                        str(row["period_start"]),
                        str(row["period_end"]),
                        key[0],
                        key[1],
                    )
                ].append(row)
        return groups

    def _aggregate_cell(
        self,
        period_start: str,
        period_end: str,
        spec: EcosystemFeatureSpec,
        entity_rows: list[sqlite3.Row],
    ) -> EcosystemFeatureValue:
        source_ids = sorted(str(row["feature_value_id"]) for row in entity_rows)
        coverage = (
            round(fmean(float(row["coverage"]) for row in entity_rows), 6) if entity_rows else 0.0
        )
        reliability = (
            round(fmean(float(row["reliability"]) for row in entity_rows), 6)
            if entity_rows
            else 0.0
        )
        by_fact: dict[str, list[tuple[ObservationV2, str]]] = defaultdict(list)
        for row in entity_rows:
            for fact in self._connection.execute(
                """SELECT canonical_fact_id, canonical_assignment_id,
                          representative_observation_id
                   FROM feature_value_fact WHERE feature_value_id = ?""",
                (row["feature_value_id"],),
            ):
                observation_row = self._connection.execute(
                    "SELECT * FROM observation_v2 WHERE observation_id = ?",
                    (fact["representative_observation_id"],),
                ).fetchone()
                observation = ObservationV2.model_validate(
                    dict(observation_row), context={"from_storage": True}
                )
                by_fact[str(fact["canonical_fact_id"])].append(
                    (observation, str(fact["canonical_assignment_id"]))
                )
        if not by_fact:
            return EcosystemFeatureValue(
                ecosystem_feature_value_id="pending",
                build_id="pending",
                period_start=period_start,
                period_end=period_end,
                source_feature_key=spec.source_feature_key,
                source_feature_version=spec.source_feature_version,
                feature_key=spec.feature_key,
                feature_version=spec.feature_version,
                missingness_reason=MissingnessReason.UNKNOWN,
                coverage=coverage,
                reliability=reliability,
                source_feature_value_ids=source_ids,
                fact_lineage=[],
            )
        representatives = {
            fact_id: _representative([item[0] for item in candidates])
            for fact_id, candidates in by_fact.items()
        }
        ordered = sorted(
            representatives.values(), key=lambda item: (item.availability_at, item.observation_id)
        )
        values = [float(item.value_numeric) for item in ordered if item.value_numeric is not None]
        if spec.aggregation is Aggregation.SUM:
            value = sum(values)
        elif spec.aggregation is Aggregation.MEAN:
            value = fmean(values)
        else:
            value = values[-1]
        lineage = []
        for fact_id, representative in sorted(representatives.items()):
            candidates = by_fact[fact_id]
            assignment_by_observation = {
                item.observation_id: assignment for item, assignment in candidates
            }
            lineage.append(
                FactLineage(
                    canonical_fact_id=fact_id,
                    canonical_assignment_id=assignment_by_observation[
                        representative.observation_id
                    ],
                    representative_observation_id=representative.observation_id,
                    contributor_assignments=assignment_by_observation,
                )
            )
        return EcosystemFeatureValue(
            ecosystem_feature_value_id="pending",
            build_id="pending",
            period_start=period_start,
            period_end=period_end,
            source_feature_key=spec.source_feature_key,
            source_feature_version=spec.source_feature_version,
            feature_key=spec.feature_key,
            feature_version=spec.feature_version,
            value_numeric=value,
            coverage=coverage,
            reliability=reliability,
            source_feature_value_ids=source_ids,
            fact_lineage=lineage,
        )

    def _persist(
        self,
        build_id: str,
        source_build_id: str,
        code_commit: str,
        feature_set_version: str,
        cutoff: str,
        period_start: str,
        period_end: str,
        manifest: str,
        checksum: str,
        rows: list[EcosystemFeatureValue],
    ) -> None:
        expected_metadata = (
            build_id,
            source_build_id,
            code_commit,
            feature_set_version,
            cutoff,
            period_start,
            period_end,
            len(rows),
            manifest,
            checksum,
        )
        existing = self._connection.execute(
            """SELECT build_id, source_entity_build_id, code_commit, feature_set_version,
                      availability_cutoff, period_start, period_end, row_count,
                      manifest_json, checksum FROM ecosystem_dataset_build WHERE build_id = ?""",
            (build_id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != expected_metadata:
                raise ValueError("ecosystem build ID exists with different metadata")
            self._validate_existing(build_id, rows)
            return
        try:
            self._connection.execute("BEGIN")
            self._connection.execute(
                """INSERT INTO ecosystem_dataset_build VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*expected_metadata, datetime.now(UTC).isoformat()),
            )
            for row in rows:
                values = row.model_dump(
                    mode="json", exclude={"source_feature_value_ids", "fact_lineage"}
                )
                values["entity_contributor_count"] = len(row.source_feature_value_ids)
                values["fact_count"] = len(row.fact_lineage)
                self._connection.execute(
                    """INSERT INTO ecosystem_feature_value VALUES (
                        :ecosystem_feature_value_id, :build_id, :period_start, :period_end,
                        :source_feature_key, :source_feature_version, :feature_key,
                        :feature_version, :value_numeric, :missingness_reason, :coverage,
                        :reliability, :entity_contributor_count, :fact_count)""",
                    values,
                )
                self._connection.executemany(
                    "INSERT INTO ecosystem_feature_value_entity_contributor VALUES (?, ?)",
                    [
                        (row.ecosystem_feature_value_id, item)
                        for item in row.source_feature_value_ids
                    ],
                )
                self._connection.executemany(
                    "INSERT INTO ecosystem_feature_value_fact VALUES (?, ?, ?, ?)",
                    [
                        (
                            row.ecosystem_feature_value_id,
                            fact.canonical_fact_id,
                            fact.canonical_assignment_id,
                            fact.representative_observation_id,
                        )
                        for fact in row.fact_lineage
                    ],
                )
            self._connection.execute(
                "INSERT INTO ecosystem_dataset_build_finalization VALUES (?, ?)",
                (build_id, datetime.now(UTC).isoformat()),
            )
        except Exception:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _validate_existing(self, build_id: str, rows: list[EcosystemFeatureValue]) -> None:
        if (
            self._connection.execute(
                "SELECT 1 FROM ecosystem_dataset_build_finalization WHERE build_id = ?", (build_id,)
            ).fetchone()
            is None
        ):
            raise ValueError("matching ecosystem build is not finalized")
        for expected in rows:
            stored = self._connection.execute(
                """SELECT ecosystem_feature_value_id, build_id, period_start, period_end,
                          source_feature_key, source_feature_version, feature_key,
                          feature_version, value_numeric, missingness_reason, coverage,
                          reliability, entity_contributor_count, fact_count
                   FROM ecosystem_feature_value
                   WHERE ecosystem_feature_value_id = ? AND build_id = ?""",
                (expected.ecosystem_feature_value_id, build_id),
            ).fetchone()
            expected_tuple = (
                expected.ecosystem_feature_value_id,
                expected.build_id,
                expected.period_start,
                expected.period_end,
                expected.source_feature_key,
                expected.source_feature_version,
                expected.feature_key,
                expected.feature_version,
                expected.value_numeric,
                expected.missingness_reason.value if expected.missingness_reason else None,
                expected.coverage,
                expected.reliability,
                len(expected.source_feature_value_ids),
                len(expected.fact_lineage),
            )
            if stored is None or tuple(stored) != expected_tuple:
                raise ValueError("persisted ecosystem feature content is inconsistent")
            contributors = {
                str(item[0])
                for item in self._connection.execute(
                    """SELECT source_feature_value_id
                       FROM ecosystem_feature_value_entity_contributor
                       WHERE ecosystem_feature_value_id = ?""",
                    (expected.ecosystem_feature_value_id,),
                )
            }
            facts = {
                tuple(item)
                for item in self._connection.execute(
                    """SELECT canonical_fact_id, canonical_assignment_id,
                              representative_observation_id
                       FROM ecosystem_feature_value_fact
                       WHERE ecosystem_feature_value_id = ?""",
                    (expected.ecosystem_feature_value_id,),
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
            if contributors != set(expected.source_feature_value_ids) or facts != expected_facts:
                raise ValueError("persisted ecosystem lineage is inconsistent")
