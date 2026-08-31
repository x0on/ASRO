from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime

from asro.backfill.manifest import EpisodeManifest, SourcePlan
from asro.evidence.time import normalize_timestamp
from asro.features.quality import BuildQualityReport, audit_finalized_build


@dataclass(frozen=True)
class BackfillResult:
    run_id: str
    input_checksum: str
    source_count: int
    coverage_passed: bool
    leakage_passed: bool


@dataclass(frozen=True)
class _SourceSnapshot:
    document_id: str
    source_plan_id: str
    content_sha256: str
    published_at: str | None
    discovered_at: str
    fetched_at: str
    availability_at: str
    content_type: str | None
    fetch_status: str
    entity_id: str
    availability_basis: str
    url: str
    title: str
    source_name: str
    content_text: str


@dataclass(frozen=True)
class _ControlSnapshot:
    control_observation_id: str
    series_id: str
    series_version: str
    period_start: str
    period_end: str
    observed_at: str
    availability_at: str
    value_numeric: float
    unit: str
    provenance_json: str


class BackfillRunner:
    """Freeze existing historical evidence without running or delaying live collectors."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def run(
        self,
        manifest: EpisodeManifest,
        entity_build_id: str | None = None,
        ecosystem_build_id: str | None = None,
    ) -> BackfillResult:
        self._register_control_definitions(manifest)
        self._register_episode(manifest)
        snapshots = self._source_snapshots(manifest)
        controls = self._control_snapshots(manifest)
        build_reports = self._build_reports(entity_build_id, ecosystem_build_id)
        self._validate_build_compatibility(manifest, entity_build_id, ecosystem_build_id)
        cells = self._coverage_cells(manifest, snapshots, controls, entity_build_id)
        coverage, metrics = self._coverage_report(manifest, cells)
        leakage, violations = self._leakage_report(manifest, snapshots, build_reports)
        coverage_json = _canonical_json(coverage)
        leakage_json = _canonical_json(leakage)
        coverage_checksum = _checksum(coverage_json)
        leakage_checksum = _checksum(leakage_json)
        links = [
            {
                "grain": report.grain,
                "build_id": report.build_id,
                "build_checksum": report.checksum,
            }
            for report in sorted(build_reports, key=lambda item: item.grain)
        ]
        input_manifest = {
            "manifest_checksum": manifest.checksum(),
            "sources": [snapshot.__dict__ for snapshot in snapshots],
            "controls": [snapshot.__dict__ for snapshot in controls],
            "builds": links,
        }
        input_checksum = _checksum(_canonical_json(input_manifest))
        run_id = _checksum(f"{manifest.episode_id}|{manifest.version}|{input_checksum}")
        result = BackfillResult(
            run_id=run_id,
            input_checksum=input_checksum,
            source_count=len(snapshots),
            coverage_passed=bool(coverage["passed"]),
            leakage_passed=bool(leakage["passed"]),
        )
        existing = self._connection.execute(
            "SELECT * FROM finalized_backfill_run WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing is not None:
            self._validate_existing(
                existing,
                result,
                manifest,
                coverage_json,
                coverage_checksum,
                leakage_json,
                leakage_checksum,
                snapshots,
                controls,
                cells,
                metrics,
                violations,
                links,
            )
            return result
        self._persist(
            result,
            manifest,
            coverage_json,
            coverage_checksum,
            leakage_json,
            leakage_checksum,
            snapshots,
            controls,
            cells,
            metrics,
            violations,
            links,
        )
        return result

    def _register_control_definitions(self, manifest: EpisodeManifest) -> None:
        registered_at = datetime.now(UTC).isoformat(timespec="seconds")
        for control in manifest.controls:
            schema = _canonical_json(control.provenance_schema)
            existing = self._connection.execute(
                """SELECT unit,provenance_schema_json FROM control_series_definition
                   WHERE series_id=? AND series_version=?""",
                (control.series_id, control.version),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (control.unit, schema):
                    raise ValueError("control series version has different semantics")
                continue
            self._connection.execute(
                "INSERT INTO control_series_definition VALUES(?,?,?,?,?)",
                (control.series_id, control.version, control.unit, schema, registered_at),
            )
        self._connection.commit()

    def _register_episode(self, manifest: EpisodeManifest) -> None:
        existing = self._connection.execute(
            """SELECT manifest_json, manifest_checksum FROM backfill_episode
               WHERE episode_id = ? AND version = ?""",
            (manifest.episode_id, manifest.version),
        ).fetchone()
        canonical = manifest.canonical_json()
        checksum = manifest.checksum()
        if existing is not None:
            if tuple(existing) != (canonical, checksum):
                raise ValueError("episode version is already registered with different semantics")
            return
        self._connection.execute(
            """INSERT INTO backfill_episode (
               episode_id, version, stratum, period_start, period_end,
               availability_cutoff, manifest_json, manifest_checksum, registered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                manifest.episode_id,
                manifest.version,
                manifest.stratum.value,
                manifest.period_start.isoformat(),
                manifest.period_end.isoformat(),
                manifest.availability_cutoff.isoformat(),
                canonical,
                checksum,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._connection.commit()

    def _source_snapshots(self, manifest: EpisodeManifest) -> list[_SourceSnapshot]:
        snapshots: list[_SourceSnapshot] = []
        for row in self._connection.execute(
            """SELECT item.id, item.source, item.title, item.url, item.companies,
                      item.published_at, item.discovered_at,
                      document.fetched_at, document.content_type, document.fetch_status,
                      document.text
               FROM items item JOIN documents document ON document.item_id = item.id
               ORDER BY item.id"""
        ):
            plan = _match_source_plan(str(row["source"]), manifest.source_plan)
            if plan is None:
                continue
            published = str(row["published_at"]) if row["published_at"] else None
            observed_time = normalize_timestamp(published or str(row["discovered_at"]))
            fetched_at = normalize_timestamp(str(row["fetched_at"]))
            if not self._document_in_episode(str(row["id"]), manifest):
                continue
            availability = observed_time
            basis = "published_at" if published else "first_observed_at"
            if availability > manifest.availability_cutoff:
                continue
            entities = self._document_entities(str(row["id"]), manifest)
            text = str(row["text"])
            for entity_id in entities:
                snapshots.append(
                    _SourceSnapshot(
                        document_id=str(row["id"]),
                        source_plan_id=plan.source_id,
                        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        published_at=published,
                        discovered_at=str(row["discovered_at"]),
                        fetched_at=fetched_at.isoformat(),
                        availability_at=availability.isoformat(),
                        content_type=str(row["content_type"]) if row["content_type"] else None,
                        fetch_status=str(row["fetch_status"]),
                        entity_id=entity_id,
                        availability_basis=basis,
                        url=str(row["url"]),
                        title=str(row["title"]),
                        source_name=str(row["source"]),
                        content_text=text,
                    )
                )
        return sorted(snapshots, key=lambda item: (item.document_id, item.entity_id))

    def _document_in_episode(self, document_id: str, manifest: EpisodeManifest) -> bool:
        bounds = (manifest.period_start.isoformat(), manifest.period_end.isoformat())
        observation = self._connection.execute(
            """SELECT 1 FROM observation_v2 WHERE source_document_id=?
               AND period_start<=? AND period_end>=?
               AND review_confidence IS NOT NULL
               AND EXISTS(SELECT 1 FROM canonical_fact_assignment assignment
                          WHERE assignment.event_id=observation_v2.event_id)""",
            (document_id, bounds[1], bounds[0]),
        ).fetchone()
        return observation is not None

    def _document_entities(self, document_id: str, manifest: EpisodeManifest) -> list[str]:
        declared = set(manifest.entities)
        candidates: set[str] = set()
        for row in self._connection.execute(
            """SELECT entity_id FROM observation_v2 WHERE source_document_id = ?
               AND review_confidence IS NOT NULL
               AND EXISTS(SELECT 1 FROM canonical_fact_assignment assignment
                          WHERE assignment.event_id=observation_v2.event_id)""",
            (document_id,),
        ):
            if row[0] is not None:
                candidates.add(str(row[0]))
        return sorted(candidates & declared)

    def _control_snapshots(self, manifest: EpisodeManifest) -> list[_ControlSnapshot]:
        controls: list[_ControlSnapshot] = []
        for plan in manifest.controls:
            rows = self._connection.execute(
                """SELECT * FROM historical_control_observation_v2
                   WHERE series_id=? AND series_version=? AND unit=?
                     AND period_start>=? AND period_end<=? AND availability_at<=?
                   ORDER BY period_start, control_observation_id""",
                (
                    plan.series_id,
                    plan.version,
                    plan.unit,
                    manifest.period_start.isoformat(),
                    manifest.period_end.isoformat(),
                    manifest.availability_cutoff.isoformat(),
                ),
            )
            controls.extend(_ControlSnapshot(**dict(row)) for row in rows)
        return controls

    def _build_reports(
        self, entity_build_id: str | None, ecosystem_build_id: str | None
    ) -> list[BuildQualityReport]:
        reports = []
        if entity_build_id:
            reports.append(audit_finalized_build(self._connection, entity_build_id, "entity_month"))
        if ecosystem_build_id:
            reports.append(
                audit_finalized_build(self._connection, ecosystem_build_id, "ecosystem_month")
            )
        return reports

    def _validate_build_compatibility(
        self,
        manifest: EpisodeManifest,
        entity_build_id: str | None,
        ecosystem_build_id: str | None,
    ) -> None:
        if manifest.features and entity_build_id is None:
            raise ValueError("an entity build is required by the episode feature requirements")
        if entity_build_id is not None:
            build = self._connection.execute(
                "SELECT * FROM dataset_build WHERE build_id=?", (entity_build_id,)
            ).fetchone()
            if build is None or str(build["feature_set_version"]) != manifest.feature_set_version:
                raise ValueError("entity build feature-set version does not match the episode")
            if (str(build["period_start"]), str(build["period_end"])) != (
                manifest.period_start.isoformat(),
                manifest.period_end.isoformat(),
            ):
                raise ValueError("entity build does not cover the exact episode window")
            entities = {
                str(row[0])
                for row in self._connection.execute(
                    "SELECT DISTINCT entity_id FROM feature_value WHERE build_id=?",
                    (entity_build_id,),
                )
            }
            if entities != set(manifest.entities):
                raise ValueError("entity build entity set does not match the episode")
            features = {
                (str(row[0]), str(row[1]))
                for row in self._connection.execute(
                    """SELECT DISTINCT feature_key, feature_version
                       FROM feature_value WHERE build_id=?""",
                    (entity_build_id,),
                )
            }
            expected_features = {
                (item.feature_key, item.feature_version) for item in manifest.features
            }
            if expected_features and features != expected_features:
                raise ValueError("entity build feature semantics do not match the episode")
            expected_rows = (
                len(manifest.entities)
                * len(_months(manifest.period_start, manifest.period_end))
                * len(expected_features)
            )
            if expected_features and int(build["row_count"]) != expected_rows:
                raise ValueError("entity build does not contain the complete expected grid")
            versions = {
                str(row[0])
                for row in self._connection.execute(
                    """SELECT DISTINCT observation.extractor_version FROM feature_value value
                       JOIN feature_value_contributor contributor
                         ON contributor.feature_value_id=value.feature_value_id
                       JOIN observation_v2 observation
                         ON observation.observation_id=contributor.observation_id
                       WHERE value.build_id=?""",
                    (entity_build_id,),
                )
            }
            if versions and versions != {manifest.extractor_version}:
                raise ValueError("entity build extractor provenance does not match the episode")
            if manifest.schema_version != "v2":
                raise ValueError("episode schema version is unsupported by the linked build")
        if ecosystem_build_id is not None:
            row = self._connection.execute(
                "SELECT source_entity_build_id FROM ecosystem_dataset_build WHERE build_id=?",
                (ecosystem_build_id,),
            ).fetchone()
            if row is None or entity_build_id is None or str(row[0]) != entity_build_id:
                raise ValueError("ecosystem build does not derive from the linked entity build")

    def _coverage_cells(
        self,
        manifest: EpisodeManifest,
        snapshots: list[_SourceSnapshot],
        controls: list[_ControlSnapshot],
        entity_build_id: str | None,
    ) -> list[dict[str, object]]:
        cells: list[dict[str, object]] = []
        months = _months(manifest.period_start, manifest.period_end)
        for entity in manifest.entities:
            for start, end in months:
                for plan in manifest.source_plan:
                    if not plan.required:
                        continue
                    present = any(
                        item.entity_id == entity
                        and item.source_plan_id == plan.source_id
                        and self._source_supports_month(
                            item.document_id,
                            entity,
                            start,
                            end,
                            manifest.availability_cutoff.isoformat(),
                        )
                        for item in snapshots
                    )
                    cells.append(
                        _cell(
                            entity,
                            date.fromisoformat(start),
                            date.fromisoformat(end),
                            "source",
                            plan.source_id,
                            "1",
                            present,
                            "no_entity_month_source",
                        )
                    )
                for feature in manifest.features:
                    if not feature.required:
                        continue
                    present = (
                        entity_build_id is not None
                        and self._connection.execute(
                            """SELECT 1 FROM finalized_entity_feature_value
                           WHERE build_id=? AND entity_id=? AND period_start=? AND period_end=?
                             AND feature_key=? AND feature_version=?
                             AND value_numeric IS NOT NULL AND missingness_reason IS NULL
                             AND reliability>=? AND fact_count>0
                             AND EXISTS(SELECT 1 FROM feature_value_fact fact
                                        WHERE fact.feature_value_id=
                                              finalized_entity_feature_value.feature_value_id)""",
                            (
                                entity_build_id,
                                entity,
                                start,
                                end,
                                feature.feature_key,
                                feature.feature_version,
                                feature.minimum_reliability,
                            ),
                        ).fetchone()
                        is not None
                    )
                    cells.append(
                        _cell(
                            entity,
                            date.fromisoformat(start),
                            date.fromisoformat(end),
                            "feature",
                            feature.feature_key,
                            feature.feature_version,
                            present,
                            "missing_entity_month_feature",
                        )
                    )
        for start, end in months:
            for control in manifest.controls:
                if not control.required:
                    continue
                present = any(
                    item.series_id == control.series_id
                    and item.series_version == control.version
                    and item.period_start == start
                    and item.period_end == end
                    for item in controls
                )
                cells.append(
                    _cell(
                        "__episode__",
                        date.fromisoformat(start),
                        date.fromisoformat(end),
                        "control",
                        control.series_id,
                        control.version,
                        present,
                        "missing_control_month",
                    )
                )
        return sorted(cells, key=lambda item: tuple(str(value) for value in item.values()))

    def _source_supports_month(
        self, document_id: str, entity_id: str, start: str, end: str, cutoff: str
    ) -> bool:
        """Whether a document carries reviewed, canonically assigned evidence for a month.

        Leakage is governed by `availability_at` and by the canonical assignment's
        `available_at`: both answer "was this knowable then". `extracted_at` and
        `reviewed_at` answer a different question, namely when this observatory did its
        own work, and for a historical backfill that is necessarily today. Requiring them
        to precede the cutoff would make every retrospective episode permanently
        uncoverable while protecting against nothing, so they are recorded and audited but
        not used as an as-of filter here. The leakage report still rejects any observation
        whose availability postdates the cutoff.
        """
        return (
            self._connection.execute(
                """SELECT 1 FROM observation_v2
                   JOIN evidence_reviews review ON review.review_id=observation_v2.review_id
                   WHERE source_document_id=? AND entity_id=?
                   AND period_start<=? AND period_end>=?
                   AND availability_at<=?
                   AND review.decision IN ('confirm','merge')""",
                (document_id, entity_id, end, start, cutoff),
            ).fetchone()
            is not None
            and self._connection.execute(
                """SELECT 1 FROM observation_v2 observation
                   JOIN evidence_reviews review ON review.review_id=observation.review_id
                   JOIN canonical_fact_assignment assignment
                     ON assignment.event_id=observation.event_id
                   WHERE observation.source_document_id=? AND observation.entity_id=?
                     AND observation.period_start<=? AND observation.period_end>=?
                     AND observation.availability_at<=?
                     AND review.decision IN ('confirm','merge')
                     AND assignment.available_at<=?
                     AND NOT EXISTS(
                       SELECT 1 FROM canonical_fact_assignment correction
                       WHERE correction.supersedes_assignment_id=assignment.assignment_id
                         AND correction.available_at<=?)""",
                (document_id, entity_id, end, start, cutoff, cutoff, cutoff),
            ).fetchone()
            is not None
        )

    def _coverage_report(
        self,
        manifest: EpisodeManifest,
        cells: list[dict[str, object]],
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        by_dimension: dict[str, list[bool]] = {}
        for cell in cells:
            by_dimension.setdefault(str(cell["dimension"]), []).append(bool(cell["present"]))
        ratios = {key: sum(values) / len(values) for key, values in by_dimension.items()}
        gate = manifest.coverage_gate
        thresholds = {
            "feature": gate.minimum_entity_month_feature_coverage,
            "source": gate.minimum_entity_source_coverage,
            "control": gate.minimum_control_month_coverage,
        }
        metrics = [
            {
                "dimension": key,
                "present_count": sum(values),
                "total_count": len(values),
                "threshold": thresholds[key],
            }
            for key, values in sorted(by_dimension.items())
        ]
        passed = bool(metrics) and all(
            ratios.get(key, 1.0) >= value for key, value in thresholds.items()
        )
        return {
            "cell_count": len(cells),
            "missing_cell_count": sum(not bool(item["present"]) for item in cells),
            "passed": int(passed),
        }, metrics

    def _leakage_report(
        self,
        manifest: EpisodeManifest,
        snapshots: list[_SourceSnapshot],
        builds: list[BuildQualityReport],
    ) -> tuple[dict[str, object], list[dict[str, str]]]:
        violations: list[dict[str, str]] = []
        for snapshot in snapshots:
            if normalize_timestamp(snapshot.availability_at) > manifest.availability_cutoff:
                violations.append({"type": "source_after_cutoff", "identity": snapshot.document_id})
        for build in builds:
            if normalize_timestamp(build.availability_cutoff) > manifest.availability_cutoff:
                violations.append({"type": "build_after_cutoff", "identity": build.build_id})
            if (
                datetime.fromisoformat(build.period_start).date() < manifest.period_start
                or datetime.fromisoformat(build.period_end).date() > manifest.period_end
            ):
                violations.append({"type": "build_outside_episode", "identity": build.build_id})
            late_count = self._late_observation_count(build, manifest)
            if late_count:
                violations.append(
                    {
                        "type": "observation_after_cutoff",
                        "identity": build.build_id,
                        "count": str(late_count),
                    }
                )
        return {"passed": int(not violations), "violation_count": len(violations)}, violations

    def _late_observation_count(self, build: BuildQualityReport, manifest: EpisodeManifest) -> int:
        cutoff = manifest.availability_cutoff.isoformat()
        if build.grain == "entity_month":
            query = """SELECT COUNT(DISTINCT observation.observation_id)
                       FROM feature_value value
                       JOIN feature_value_contributor contributor
                         ON contributor.feature_value_id = value.feature_value_id
                       JOIN observation_v2 observation
                         ON observation.observation_id = contributor.observation_id
                       WHERE value.build_id = ? AND observation.availability_at > ?"""
        else:
            query = """SELECT COUNT(DISTINCT observation.observation_id)
                       FROM ecosystem_feature_value value
                       JOIN ecosystem_feature_value_entity_contributor ecosystem_contributor
                         ON ecosystem_contributor.ecosystem_feature_value_id =
                            value.ecosystem_feature_value_id
                       JOIN feature_value_contributor contributor
                         ON contributor.feature_value_id =
                            ecosystem_contributor.source_feature_value_id
                       JOIN observation_v2 observation
                         ON observation.observation_id = contributor.observation_id
                       WHERE value.build_id = ? AND observation.availability_at > ?"""
        return int(self._connection.execute(query, (build.build_id, cutoff)).fetchone()[0])

    def _persist(
        self,
        result: BackfillResult,
        manifest: EpisodeManifest,
        coverage_json: str,
        coverage_checksum: str,
        leakage_json: str,
        leakage_checksum: str,
        snapshots: list[_SourceSnapshot],
        controls: list[_ControlSnapshot],
        cells: list[dict[str, object]],
        metrics: list[dict[str, object]],
        violations: list[dict[str, str]],
        links: list[dict[str, str]],
    ) -> None:
        try:
            self._connection.execute("BEGIN")
            self._connection.execute(
                """INSERT INTO backfill_run(
                   run_id, episode_id, episode_version, manifest_checksum, input_checksum,
                   coverage_json, coverage_checksum, leakage_json, leakage_checksum,
                   coverage_passed, leakage_passed, source_count, build_count, created_at,
                   control_count, coverage_cell_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result.run_id,
                    manifest.episode_id,
                    manifest.version,
                    manifest.checksum(),
                    result.input_checksum,
                    coverage_json,
                    coverage_checksum,
                    leakage_json,
                    leakage_checksum,
                    int(result.coverage_passed),
                    int(result.leakage_passed),
                    result.source_count,
                    len(links),
                    datetime.now(UTC).isoformat(),
                    len(controls),
                    len(cells),
                ),
            )
            self._connection.executemany(
                """INSERT INTO backfill_source_snapshot_v2(
                   run_id, document_id, source_plan_id, content_sha256, published_at,
                   discovered_at, fetched_at, availability_at, content_type, fetch_status,
                   entity_id, availability_basis, url, title, source_name, content_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(result.run_id, *snapshot.__dict__.values()) for snapshot in snapshots],
            )
            self._connection.executemany(
                """INSERT INTO backfill_control_snapshot_v2 VALUES(
                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(result.run_id, *control.__dict__.values()) for control in controls],
            )
            self._connection.executemany(
                "INSERT INTO backfill_build_link VALUES (?, ?, ?, ?)",
                [
                    (result.run_id, link["grain"], link["build_id"], link["build_checksum"])
                    for link in links
                ],
            )
            self._connection.executemany(
                """INSERT INTO backfill_coverage_cell VALUES(
                   ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(result.run_id, *cell.values()) for cell in cells],
            )
            self._connection.executemany(
                "INSERT INTO backfill_coverage_metric VALUES(?,?,?,?,?)",
                [(result.run_id, *metric.values()) for metric in metrics],
            )
            self._connection.executemany(
                "INSERT INTO backfill_leakage_violation VALUES (?, ?, ?, ?)",
                [
                    (result.run_id, item["type"], item["identity"], _canonical_json(item))
                    for item in violations
                ],
            )
            self._connection.execute(
                "INSERT INTO backfill_run_finalization VALUES (?, ?)",
                (result.run_id, datetime.now(UTC).isoformat()),
            )
        except Exception:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _validate_existing(
        self,
        existing: sqlite3.Row,
        result: BackfillResult,
        manifest: EpisodeManifest,
        coverage_json: str,
        coverage_checksum: str,
        leakage_json: str,
        leakage_checksum: str,
        snapshots: list[_SourceSnapshot],
        controls: list[_ControlSnapshot],
        cells: list[dict[str, object]],
        metrics: list[dict[str, object]],
        violations: list[dict[str, str]],
        links: list[dict[str, str]],
    ) -> None:
        expected = (
            result.run_id,
            manifest.episode_id,
            manifest.version,
            manifest.checksum(),
            result.input_checksum,
            coverage_json,
            coverage_checksum,
            leakage_json,
            leakage_checksum,
            int(result.coverage_passed),
            int(result.leakage_passed),
            result.source_count,
            len(links),
            existing["created_at"],
            len(controls),
            len(cells),
        )
        if tuple(existing) != expected:
            raise ValueError("backfill run identity exists with different content")
        stored_sources = {
            tuple(row)
            for row in self._connection.execute(
                """SELECT document_id, source_plan_id, content_sha256, published_at,
                          discovered_at, fetched_at, availability_at, content_type, fetch_status,
                          entity_id, availability_basis, url, title, source_name, content_text
                   FROM backfill_source_snapshot_v2 WHERE run_id = ?""",
                (result.run_id,),
            )
        }
        expected_sources = {tuple(snapshot.__dict__.values()) for snapshot in snapshots}
        stored_links = {
            tuple(row)[1:]
            for row in self._connection.execute(
                "SELECT * FROM backfill_build_link WHERE run_id = ?", (result.run_id,)
            )
        }
        expected_links = {
            (item["grain"], item["build_id"], item["build_checksum"]) for item in links
        }
        stored_controls = {
            tuple(row)[1:]
            for row in self._connection.execute(
                "SELECT * FROM backfill_control_snapshot_v2 WHERE run_id=?", (result.run_id,)
            )
        }
        expected_controls = {tuple(item.__dict__.values()) for item in controls}
        stored_cells = {
            tuple(row)[1:]
            for row in self._connection.execute(
                "SELECT * FROM backfill_coverage_cell WHERE run_id=?", (result.run_id,)
            )
        }
        expected_cells = {tuple(item.values()) for item in cells}
        stored_metrics = {
            tuple(row)[1:]
            for row in self._connection.execute(
                "SELECT * FROM backfill_coverage_metric WHERE run_id=?", (result.run_id,)
            )
        }
        expected_metrics = {tuple(item.values()) for item in metrics}
        stored_violations = {
            tuple(row)[1:]
            for row in self._connection.execute(
                "SELECT * FROM backfill_leakage_violation WHERE run_id=?", (result.run_id,)
            )
        }
        expected_violations = {
            (item["type"], item["identity"], _canonical_json(item)) for item in violations
        }
        if (
            stored_sources != expected_sources
            or stored_links != expected_links
            or stored_controls != expected_controls
            or stored_cells != expected_cells
            or stored_metrics != expected_metrics
            or stored_violations != expected_violations
        ):
            raise ValueError("backfill run provenance is inconsistent")


def _match_source_plan(source: str, plans: list[SourcePlan]) -> SourcePlan | None:
    folded = source.casefold()
    matches = [plan for plan in plans if plan.source_pattern.casefold() in folded]
    return sorted(matches, key=lambda item: item.source_id)[0] if matches else None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _checksum(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _months(start: date, end: date) -> list[tuple[str, str]]:
    cursor = start.replace(day=1)
    result: list[tuple[str, str]] = []
    while cursor <= end:
        following = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        month_end = min(end, following.fromordinal(following.toordinal() - 1))
        result.append((max(start, cursor).isoformat(), month_end.isoformat()))
        cursor = following
    return result


def _cell(
    entity: str,
    start: date,
    end: date,
    dimension: str,
    key: str,
    version: str,
    present: bool,
    reason: str,
) -> dict[str, object]:
    return {
        "entity_id": entity,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "dimension": dimension,
        "requirement_key": key,
        "requirement_version": version,
        "present": int(present),
        "missingness_reason": None if present else reason,
    }
