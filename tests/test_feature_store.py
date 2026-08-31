from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from asro.backfill import (
    BackfillRunner,
    ControlObservation,
    ControlPlan,
    EpisodeManifest,
    FeatureRequirement,
    candidate_episode_support,
    ingest_candidate_package,
    register_control_observation,
)
from asro.backfill.manifest import CoverageGate, EpisodeStratum, SourcePlan
from asro.cli import app
from asro.evidence import (
    CanonicalFactAssignment,
    EconomicScope,
    EvidenceRepository,
    FactStatus,
    FeatureDefinitionV2,
    ObservationV2,
    SourceTier,
)
from asro.features import (
    Aggregation,
    EcosystemFeatureSpec,
    EcosystemFeatureStoreBuilder,
    FeatureSpec,
    FeatureStoreBuilder,
)
from asro.features.quality import audit_finalized_build
from asro.migrations import runner as migration_runner
from asro.models import EventType, FinancialEvent, SourceItem
from asro.operations import (
    WorkflowRunRecord,
    alert_missing_daily_windows,
    alert_missing_hourly_windows,
    missing_daily_windows,
    missing_hourly_windows,
    record_window_repair,
    record_workflow_run,
)
from asro.scoring import score
from asro.storage import SqliteRepository


def _seed_event(
    repository: SqliteRepository,
    connection: sqlite3.Connection,
    suffix: str,
    amount: float,
) -> tuple[str, str]:
    item = score(
        SourceItem(
            title=f"Debt filing {suffix}",
            url=f"https://example.com/{suffix}",
            source="Primary filing",
            published_at="2026-03-31",
        ),
        [],
    )
    assert repository.insert(connection, item)
    event_id = f"event-{suffix}"
    assert repository.insert_event(
        connection,
        FinancialEvent(
            event_id=event_id,
            document_id=item.item_id,
            event_type=EventType.ISSUES_DEBT,
            source_entity="company-a",
            amount=amount,
            currency="USD",
            effective_date="2026-03-31",
            confidence=0.9,
            evidence_text=f"Company A issued {amount} dollars of debt.",
            extractor="test-v2",
        ),
    )
    return item.item_id, event_id


def _observation(
    observation_id: str,
    source_id: str,
    event_id: str,
    amount: float,
    availability_at: str,
) -> ObservationV2:
    return ObservationV2(
        observation_id=observation_id,
        event_id=event_id,
        source_document_id=source_id,
        source_locator="debt footnote",
        evidence_text=f"Company A issued {amount} dollars of debt.",
        entity_id="company-a",
        entity_role="issuer",
        feature_key="ai_related_debt",
        feature_version="1.0.0",
        value_numeric=amount,
        unit="currency",
        currency="USD",
        economic_scope=EconomicScope.ENTITY,
        period_start="2026-03-01",
        period_end="2026-03-31",
        event_at="2026-03-31",
        published_at="2026-03-31T12:00:00Z",
        availability_at=availability_at,
        extracted_at=availability_at,
        fact_status=FactStatus.DIRECT,
        source_tier=SourceTier.PRIMARY,
        source_quality=0.98,
        extraction_confidence=0.9,
        review_confidence=0.95,
        extractor_name="test",
        extractor_version="2.0.0",
    )


def _prepare(
    tmp_path: Path, review_decisions: tuple[str, str] = ("confirm", "confirm")
) -> tuple[sqlite3.Connection, FeatureSpec]:
    repository = SqliteRepository(tmp_path / "monitor.db")
    connection = repository.connect()
    definition = FeatureDefinitionV2(
        feature_key="ai_related_debt",
        feature_version="1.0.0",
        definition_json=json.dumps(
            {
                "aggregation": "sum",
                "unit": "currency",
                "grain": "entity_month",
                "role": "vulnerability",
                "expected_facts_per_period": 2,
            },
            sort_keys=True,
        ),
        released_at="2026-01-01",
    )
    assert EvidenceRepository.register_feature(connection, definition)
    first_source, first_event = _seed_event(repository, connection, "one", 500_000_000)
    second_source, second_event = _seed_event(repository, connection, "two", 200_000_000)
    assert EvidenceRepository.register_canonical_fact(connection, first_event)
    assert EvidenceRepository.register_canonical_fact(connection, second_event)
    for event_id in (first_event, second_event):
        assert EvidenceRepository.assign_canonical_fact(
            connection,
            CanonicalFactAssignment(
                assignment_id=f"assignment-{event_id}",
                event_id=event_id,
                canonical_fact_id=event_id,
                available_at="2026-03-31T12:00:00Z",
                assigned_by="test",
                assignment_method="manual_review",
            ),
        )
    review_ids: list[int] = []
    for ordinal, (event_id, decision) in enumerate(
        zip((first_event, second_event), review_decisions, strict=True), start=1
    ):
        cursor = connection.execute(
            """INSERT INTO evidence_reviews(
               fingerprint,decision,canonical_fingerprint,confidence,reasoning,model,reviewed_at
               ) VALUES(?, ?, NULL, 0.95, 'test evidence review', 'test-reviewer', ?)""",
            (event_id, decision, f"2026-04-0{ordinal}T13:00:00+00:00"),
        )
        assert cursor.lastrowid is not None
        review_ids.append(int(cursor.lastrowid))
    assert EvidenceRepository.insert(
        connection,
        _observation(
            "obs-one",
            first_source,
            first_event,
            500_000_000,
            "2026-03-31T12:05:00Z",
        ).model_copy(update={"review_id": review_ids[0]}),
    )
    assert EvidenceRepository.insert(
        connection,
        _observation(
            "obs-two",
            second_source,
            second_event,
            200_000_000,
            "2026-04-02T12:05:00Z",
        ).model_copy(update={"review_id": review_ids[1]}),
    )
    connection.commit()
    return connection, FeatureSpec(
        feature_key="ai_related_debt",
        feature_version="1.0.0",
        aggregation=Aggregation.SUM,
        unit="currency",
        expected_facts_per_period=2,
    )


def _register_second_version(
    connection: sqlite3.Connection, source_id: str, event_id: str
) -> FeatureSpec:
    definition = FeatureDefinitionV2(
        feature_key="ai_related_debt",
        feature_version="2.0.0",
        definition_json=json.dumps(
            {
                "aggregation": "sum",
                "unit": "currency",
                "grain": "entity_month",
                "expected_facts_per_period": 1,
            },
            sort_keys=True,
        ),
        released_at="2026-02-01",
    )
    assert EvidenceRepository.register_feature(connection, definition)
    observation = _observation(
        "obs-version-two", source_id, event_id, 125_000_000, "2026-03-31T13:00:00Z"
    ).model_copy(update={"feature_version": "2.0.0"})
    assert EvidenceRepository.insert(connection, observation)
    connection.commit()
    return FeatureSpec(
        feature_key="ai_related_debt",
        feature_version="2.0.0",
        aggregation=Aggregation.SUM,
        unit="currency",
        expected_facts_per_period=1,
    )


def _ecosystem_spec(connection: sqlite3.Connection) -> EcosystemFeatureSpec:
    assert EvidenceRepository.register_feature(
        connection,
        FeatureDefinitionV2(
            feature_key="ecosystem_ai_debt",
            feature_version="1.0.0",
            definition_json=json.dumps(
                {
                    "aggregation": "sum",
                    "unit": "currency",
                    "grain": "ecosystem_month",
                },
                sort_keys=True,
            ),
            released_at="2026-01-01",
        ),
    )
    connection.commit()
    return EcosystemFeatureSpec(
        source_feature_key="ai_related_debt",
        source_feature_version="1.0.0",
        feature_key="ecosystem_ai_debt",
        feature_version="1.0.0",
        aggregation=Aggregation.SUM,
        unit="currency",
    )


def _insert_raw_ecosystem_build(
    connection: sqlite3.Connection, build_id: str, source_build_id: str
) -> None:
    connection.execute(
        """INSERT INTO ecosystem_dataset_build VALUES (
           ?, ?, 'sql', 'set', '2026-04-03T00:00:00+00:00',
           '2026-03-01', '2026-03-31', 1, '{}', ?,
           '2026-04-03T00:00:00+00:00')""",
        (build_id, source_build_id, f"checksum-{build_id}"),
    )


def _episode_manifest(**overrides: object) -> EpisodeManifest:
    values: dict[str, object] = {
        "episode_id": "test-episode",
        "version": "1.0.0",
        "title": "Test historical episode",
        "stratum": EpisodeStratum.BENIGN,
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "availability_cutoff": "2026-04-03T00:00:00Z",
        "entities": ["company-a"],
        "controls": [],
        "features": [],
        "source_plan": [
            SourcePlan(
                source_id="primary-filings",
                source_pattern="Primary filing",
                tier="primary",
            )
        ],
        "schema_version": "v2",
        "extractor_version": "2.0.0",
        "feature_set_version": "entity-set",
        "coverage_gate": CoverageGate(),
    }
    values.update(overrides)
    return EpisodeManifest.model_validate(values)


def test_entity_month_build_is_temporally_honest_and_has_explicit_missingness(
    tmp_path: Path,
) -> None:
    connection, spec = _prepare(tmp_path)
    with connection:
        builder = FeatureStoreBuilder(connection)
        early = builder.build_entity_month(
            [spec],
            "2026-04-01T00:00:00Z",
            ["company-b", "company-a"],
            code_commit="abc123",
            feature_set_version="1.0.0",
            period_start="2026-03-01",
            period_end="2026-03-31",
        )
        assert early.row_count == 2
        rows = connection.execute(
            """SELECT value.entity_id, value.value_numeric, value.missingness_reason,
                      COUNT(contributor.observation_id)
               FROM feature_value value
               LEFT JOIN feature_value_contributor contributor
                 ON contributor.feature_value_id = value.feature_value_id
               WHERE value.build_id = ?
               GROUP BY value.feature_value_id ORDER BY value.entity_id""",
            (early.build_id,),
        ).fetchall()
        assert tuple(rows[0]) == ("company-a", 500_000_000, None, 1)
        assert tuple(rows[1]) == ("company-b", None, "unknown", 0)
        contributor = connection.execute(
            "SELECT observation_id FROM feature_value_contributor"
        ).fetchone()
        assert contributor[0] == "obs-one"

        late = builder.build_entity_month(
            [spec],
            "2026-04-03T00:00:00Z",
            ["company-a", "company-b"],
            code_commit="abc123",
            feature_set_version="1.0.0",
            period_start="2026-03-01",
            period_end="2026-03-31",
        )
        assert late.build_id != early.build_id
        late_value = connection.execute(
            """SELECT value_numeric FROM feature_value
               WHERE build_id = ? AND entity_id = 'company-a'""",
            (late.build_id,),
        ).fetchone()[0]
        assert late_value == 700_000_000
    connection.close()


def test_requested_window_includes_entirely_evidence_free_month(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    result = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-04-30",
    )
    rows = connection.execute(
        """SELECT period_start, value_numeric, missingness_reason FROM feature_value
           WHERE build_id = ? ORDER BY period_start""",
        (result.build_id,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("2026-03-01", 700_000_000, None),
        ("2026-04-01", None, "unknown"),
    ]
    connection.close()


def test_as_of_feature_respects_publication_cutoff_and_staleness(tmp_path: Path) -> None:
    connection, _flow_spec = _prepare(tmp_path)
    assert EvidenceRepository.register_feature(
        connection,
        FeatureDefinitionV2(
            feature_key="ai_infrastructure_debt_stock",
            feature_version="1.0.0",
            definition_json=json.dumps(
                {
                    "aggregation": "as_of_latest",
                    "unit": "currency",
                    "grain": "entity_month_as_of",
                    "expected_facts_per_period": 1,
                    "max_age_months": 3,
                },
                sort_keys=True,
            ),
            released_at="2026-01-01",
        ),
    )
    source_id, event_id = connection.execute(
        "SELECT document_id,event_id FROM financial_events WHERE event_id='event-one'"
    ).fetchone()
    stock_values = _observation(
        "stock-one", source_id, event_id, 900_000_000, "2026-03-31T12:00:00Z"
    ).model_dump()
    stock_values.update(
        {
            "feature_key": "ai_infrastructure_debt_stock",
            "feature_version": "1.0.0",
            "period_start": "2026-01-31",
            "period_end": "2026-01-31",
            "event_at": "2026-01-31",
            "published_at": "2026-02-15T12:00:00Z",
            "availability_at": "2026-02-15T12:00:00Z",
            "extracted_at": "2026-02-15T12:00:00Z",
        }
    )
    stock = ObservationV2.model_validate(stock_values)
    assert EvidenceRepository.insert(connection, stock)
    connection.commit()
    spec = FeatureSpec(
        feature_key="ai_infrastructure_debt_stock",
        feature_version="1.0.0",
        aggregation=Aggregation.AS_OF_LATEST,
        unit="currency",
        expected_facts_per_period=1,
        max_age_months=3,
    )
    result = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-05-31T23:59:59Z",
        ["company-a"],
        code_commit="as-of-test",
        feature_set_version="as-of-1",
        period_start="2026-01-01",
        period_end="2026-05-31",
    )
    rows = connection.execute(
        """SELECT period_start,value_numeric,missingness_reason
           FROM feature_value WHERE build_id=? ORDER BY period_start""",
        (result.build_id,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("2026-01-01", None, "unknown"),
        ("2026-02-01", 900_000_000, None),
        ("2026-03-01", 900_000_000, None),
        ("2026-04-01", 900_000_000, None),
        ("2026-05-01", None, "unknown"),
    ]
    assert EvidenceRepository.register_feature(
        connection,
        FeatureDefinitionV2(
            feature_key="ecosystem_ai_infrastructure_debt_stock",
            feature_version="1.0.0",
            definition_json=json.dumps(
                {"aggregation": "sum", "unit": "currency", "grain": "ecosystem_month"},
                sort_keys=True,
            ),
            released_at="2026-01-01",
        ),
    )
    connection.commit()
    ecosystem = EcosystemFeatureStoreBuilder(connection).build_months(
        result.build_id,
        [
            EcosystemFeatureSpec(
                source_feature_key="ai_infrastructure_debt_stock",
                source_feature_version="1.0.0",
                feature_key="ecosystem_ai_infrastructure_debt_stock",
                feature_version="1.0.0",
                aggregation=Aggregation.SUM,
                unit="currency",
            )
        ],
        code_commit="as-of-test",
        feature_set_version="as-of-1",
    )
    ecosystem_rows = connection.execute(
        """SELECT period_start,value_numeric,missingness_reason
           FROM ecosystem_feature_value WHERE build_id=? ORDER BY period_start""",
        (ecosystem.build_id,),
    ).fetchall()
    assert [tuple(row) for row in ecosystem_rows] == [
        ("2026-01-01", None, "unknown"),
        ("2026-02-01", 900_000_000, None),
        ("2026-03-01", 900_000_000, None),
        ("2026-04-01", 900_000_000, None),
        ("2026-05-01", None, "unknown"),
    ]
    lineage = connection.execute(
        """SELECT COUNT(*),COUNT(DISTINCT fact.canonical_fact_id)
           FROM ecosystem_feature_value value
           JOIN ecosystem_feature_value_fact fact
             ON fact.ecosystem_feature_value_id=value.ecosystem_feature_value_id
           WHERE value.build_id=?""",
        (ecosystem.build_id,),
    ).fetchone()
    assert tuple(lineage) == (3, 1)
    connection.close()


def test_feature_spec_rejects_flow_stock_semantic_confusion() -> None:
    with pytest.raises(ValueError, match="require max_age_months"):
        FeatureSpec(
            feature_key="stock",
            feature_version="1",
            aggregation=Aggregation.AS_OF_LATEST,
            unit="currency",
            expected_facts_per_period=1,
        )
    with pytest.raises(ValueError, match="only valid for as-of"):
        FeatureSpec(
            feature_key="flow",
            feature_version="1",
            aggregation=Aggregation.SUM,
            unit="currency",
            expected_facts_per_period=1,
            max_age_months=1,
        )


def test_as_of_feature_rejects_multiple_canonical_point_facts(tmp_path: Path) -> None:
    connection, _flow_spec = _prepare(tmp_path)
    assert EvidenceRepository.register_feature(
        connection,
        FeatureDefinitionV2(
            feature_key="ai_credit_support_stock",
            feature_version="1.0.0",
            definition_json=json.dumps(
                {
                    "aggregation": "as_of_latest",
                    "unit": "currency",
                    "grain": "entity_month_as_of",
                    "expected_facts_per_period": 1,
                    "max_age_months": 2,
                },
                sort_keys=True,
            ),
            released_at="2026-01-01",
        ),
    )
    for ordinal, event_id in enumerate(("event-one", "event-two"), start=1):
        source_id = connection.execute(
            "SELECT document_id FROM financial_events WHERE event_id=?", (event_id,)
        ).fetchone()[0]
        values = _observation(
            f"support-{ordinal}", source_id, event_id, float(ordinal), "2026-03-31T12:00:00Z"
        ).model_dump()
        values.update(
            {
                "feature_key": "ai_credit_support_stock",
                "feature_version": "1.0.0",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
                "event_time_precision": "second",
            }
        )
        assert EvidenceRepository.insert(connection, ObservationV2.model_validate(values))
    connection.commit()
    with pytest.raises(ValueError, match="exactly one canonical point fact"):
        FeatureStoreBuilder(connection).build_entity_month(
            [
                FeatureSpec(
                    feature_key="ai_credit_support_stock",
                    feature_version="1.0.0",
                    aggregation=Aggregation.AS_OF_LATEST,
                    unit="currency",
                    expected_facts_per_period=1,
                    max_age_months=2,
                )
            ],
            "2026-03-31T23:59:59Z",
            ["company-a"],
            code_commit="as-of-double-count-test",
            feature_set_version="as-of-1",
            period_start="2026-03-01",
            period_end="2026-03-31",
        )
    connection.close()


def test_feature_identity_distinguishes_versions_and_feature_sets(tmp_path: Path) -> None:
    connection, first_spec = _prepare(tmp_path)
    source_id, event_id = connection.execute(
        "SELECT document_id, event_id FROM financial_events ORDER BY event_id LIMIT 1"
    ).fetchone()
    second_spec = _register_second_version(connection, source_id, event_id)
    builder = FeatureStoreBuilder(connection)
    first = builder.build_entity_month(
        [first_spec, second_spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="abc123",
        feature_set_version="set-a",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    assert (
        connection.execute(
            "SELECT COUNT(DISTINCT feature_version) FROM feature_value WHERE build_id = ?",
            (first.build_id,),
        ).fetchone()[0]
        == 2
    )
    second = builder.build_entity_month(
        [first_spec, second_spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="abc123",
        feature_set_version="set-b",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    assert (first.build_id, first.checksum) != (second.build_id, second.checksum)
    connection.close()


def test_reordered_feature_specs_have_identical_build_identity(tmp_path: Path) -> None:
    connection, first_spec = _prepare(tmp_path)
    source_id, event_id = connection.execute(
        "SELECT document_id, event_id FROM financial_events ORDER BY event_id LIMIT 1"
    ).fetchone()
    second_spec = _register_second_version(connection, source_id, event_id)
    builder = FeatureStoreBuilder(connection)
    kwargs = dict(
        availability_cutoff="2026-04-03T00:00:00Z",
        expected_entities=["company-a"],
        code_commit="abc123",
        feature_set_version="set-a",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    assert builder.build_entity_month([first_spec, second_spec], **kwargs) == (
        builder.build_entity_month([second_spec, first_spec], **kwargs)
    )
    manifest = json.loads(
        connection.execute("SELECT manifest_json FROM dataset_build").fetchone()[0]
    )
    assert manifest["code_commit"] == "abc123"
    assert all("feature_value_id" not in row for row in manifest["rows"])
    connection.close()


def test_expanded_build_has_new_build_scoped_row_ids_without_collisions(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    builder = FeatureStoreBuilder(connection)
    kwargs = dict(
        specs=[spec],
        availability_cutoff="2026-04-01T00:00:00Z",
        code_commit="abc123",
        feature_set_version="same-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    smaller = builder.build_entity_month(expected_entities=["company-a"], **kwargs)
    expanded = builder.build_entity_month(expected_entities=["company-a", "company-b"], **kwargs)
    assert smaller.build_id != expanded.build_id
    ids = connection.execute(
        "SELECT feature_value_id FROM feature_value ORDER BY feature_value_id"
    ).fetchall()
    assert len(ids) == len({row[0] for row in ids}) == 3
    company_a_ids = connection.execute(
        """SELECT feature_value_id FROM feature_value
           WHERE entity_id = 'company-a' ORDER BY build_id"""
    ).fetchall()
    assert company_a_ids[0][0] != company_a_ids[1][0]
    connection.close()


def test_distinct_documents_and_events_for_one_fact_are_counted_once(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    repository = SqliteRepository(tmp_path / "monitor.db")
    source_id, event_id = _seed_event(repository, connection, "duplicate-mention", 500_000_000)
    assert EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment(
            assignment_id="assignment-duplicate-mention",
            event_id=event_id,
            canonical_fact_id="event-one",
            available_at="2026-03-31T12:30:00Z",
            assigned_by="test",
            assignment_method="manual_review",
        ),
    )
    connection.commit()
    assert EvidenceRepository.insert(
        connection,
        _observation("obs-one-duplicate", source_id, event_id, 500_000_000, "2026-03-31T13:00:00Z"),
    )
    connection.commit()
    result = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    row = connection.execute(
        """SELECT feature_value_id, value_numeric, coverage
           FROM feature_value WHERE build_id = ?""",
        (result.build_id,),
    ).fetchone()
    assert tuple(row[1:]) == (500_000_000, 0.5)
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM feature_value_fact WHERE feature_value_id = ?", (row[0],)
        ).fetchone()[0]
        == 1
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM feature_value_contributor WHERE feature_value_id = ?", (row[0],)
        ).fetchone()[0]
        == 2
    )
    connection.close()


def test_canonical_merge_is_temporal_and_can_be_corrected_append_only(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    assert EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment(
            assignment_id="assignment-event-two-merge",
            event_id="event-two",
            canonical_fact_id="event-one",
            available_at="2026-04-04T00:00:00Z",
            supersedes_assignment_id="assignment-event-two",
            assigned_by="reviewer",
            assignment_method="cross_document_match",
            provenance={"review": "merge"},
        ),
    )
    connection.commit()
    builder = FeatureStoreBuilder(connection)
    common = dict(
        specs=[spec],
        expected_entities=["company-a"],
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    historical = builder.build_entity_month(availability_cutoff="2026-04-03T00:00:00Z", **common)
    merged = builder.build_entity_month(availability_cutoff="2026-04-05T00:00:00Z", **common)
    historical_value = connection.execute(
        "SELECT value_numeric FROM feature_value WHERE build_id = ?", (historical.build_id,)
    ).fetchone()[0]
    merged_fact_count = connection.execute(
        "SELECT fact_count FROM feature_value WHERE build_id = ?", (merged.build_id,)
    ).fetchone()[0]
    assert historical_value == 700_000_000
    assert merged_fact_count == 1

    assert EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment(
            assignment_id="assignment-event-two-correction",
            event_id="event-two",
            canonical_fact_id="event-two",
            available_at="2026-04-06T00:00:00Z",
            supersedes_assignment_id="assignment-event-two-merge",
            reviewer_id=None,
            assigned_by="reviewer",
            assignment_method="correction",
            provenance={"reason": "false merge"},
        ),
    )
    connection.commit()
    corrected = builder.build_entity_month(availability_cutoff="2026-04-07T00:00:00Z", **common)
    assert (
        connection.execute(
            "SELECT fact_count FROM feature_value WHERE build_id = ?", (corrected.build_id,)
        ).fetchone()[0]
        == 2
    )
    connection.close()


def test_canonical_assignment_rejects_competing_roots_and_detects_ambiguity(
    tmp_path: Path,
) -> None:
    connection, _ = _prepare(tmp_path)
    competing = CanonicalFactAssignment(
        assignment_id="competing-root",
        event_id="event-one",
        canonical_fact_id="event-two",
        available_at="2026-03-31T12:30:00Z",
        created_at="2026-03-31T12:31:00Z",
        assigned_by="test",
        assignment_method="manual_review",
    )
    with pytest.raises(ValueError, match="already has.*root"):
        EvidenceRepository.assign_canonical_fact(connection, competing)
    values = competing.model_dump(mode="json", exclude={"provenance"})
    values["available_at"] = competing.available_at.isoformat()
    values["created_at"] = competing.created_at.isoformat()
    values["provenance_json"] = "{}"
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        connection.execute(
            """INSERT INTO canonical_fact_assignment VALUES (
               :assignment_id, :event_id, :canonical_fact_id, :available_at, NULL, NULL,
               :assigned_by, :assignment_method, :provenance_json, :created_at)""",
            values,
        )
    connection.execute("DROP INDEX idx_canonical_assignment_root")
    connection.execute(
        """INSERT INTO canonical_fact_assignment VALUES (
           :assignment_id, :event_id, :canonical_fact_id, :available_at, NULL, NULL,
           :assigned_by, :assignment_method, :provenance_json, :created_at)""",
        values,
    )
    with pytest.raises(RuntimeError, match="ambiguous canonical assignments"):
        EvidenceRepository.canonical_assignments_as_of(connection, "2026-04-01T00:00:00Z")
    connection.close()


def test_assignment_timestamps_are_canonical_and_offset_cutoffs_are_equivalent(
    tmp_path: Path,
) -> None:
    connection, _ = _prepare(tmp_path)
    utc = EvidenceRepository.canonical_assignments_as_of(connection, "2026-04-01T00:00:00Z")
    offset = EvidenceRepository.canonical_assignments_as_of(connection, "2026-03-31T20:00:00-04:00")
    assert offset == utc
    stored = connection.execute(
        """SELECT available_at, created_at FROM canonical_fact_assignment
           WHERE assignment_id = 'assignment-event-one'"""
    ).fetchone()
    assert stored[0].endswith("+00:00") and stored[1].endswith("+00:00")
    repository = SqliteRepository(tmp_path / "monitor.db")
    _, timestamp_event = _seed_event(repository, connection, "timestamp-direct", 1.0)
    assert EvidenceRepository.register_canonical_fact(connection, timestamp_event)
    for available_at, created_at in (
        ("2026-03-31 12:00:00", "2026-03-31T12:01:00+00:00"),
        ("2026-03-31T08:00:00-04:00", "2026-03-31T12:01:00+00:00"),
        ("2026-03-31T12:02:00+00:00", "2026-03-31T12:01:00+00:00"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(
                """INSERT INTO canonical_fact_assignment VALUES (
                   ?, ?, ?, ?, NULL, NULL,
                   'sql', 'correction', '{}', ?)""",
                (
                    f"bad-time-{available_at}",
                    timestamp_event,
                    timestamp_event,
                    available_at,
                    created_at,
                ),
            )
    connection.close()


def test_direct_sql_lineage_cannot_claim_missing_fact_or_observation(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    result = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    feature_value_id = connection.execute(
        "SELECT feature_value_id FROM feature_value WHERE build_id = ?", (result.build_id,)
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="lineage|FOREIGN KEY|finalized"):
        connection.execute(
            """INSERT INTO feature_value_fact
               (feature_value_id, canonical_fact_id, canonical_assignment_id,
                representative_observation_id)
               VALUES (?, 'missing-fact', 'assignment-event-one', 'obs-one')""",
            (feature_value_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="contributor|FOREIGN KEY|finalized"):
        connection.execute(
            """INSERT INTO feature_value_contributor
               (feature_value_id, canonical_fact_id, canonical_assignment_id, observation_id)
               VALUES (?, 'event-one', 'assignment-event-one', 'missing-observation')""",
            (feature_value_id,),
        )
    connection.close()


def test_direct_sql_as_of_lineage_enforces_same_month_row_cutoff(tmp_path: Path) -> None:
    connection, _ = _prepare(tmp_path)
    assert EvidenceRepository.register_feature(
        connection,
        FeatureDefinitionV2(
            feature_key="ai_credit_support_stock",
            feature_version="1.0.0",
            definition_json=json.dumps(
                {
                    "aggregation": "as_of_latest",
                    "unit": "currency",
                    "grain": "entity_month_as_of",
                    "expected_facts_per_period": 1,
                    "max_age_months": 2,
                },
                sort_keys=True,
            ),
            released_at="2026-01-01",
        ),
    )
    source_id = connection.execute(
        "SELECT document_id FROM financial_events WHERE event_id='event-one'"
    ).fetchone()[0]
    for observation_id, available_at in (
        ("support-timely", "2026-03-31T12:00:00Z"),
        ("support-future", "2026-04-15T12:00:00Z"),
    ):
        values = _observation(
            observation_id, source_id, "event-one", 3.0, available_at
        ).model_dump()
        values.update(
            {
                "feature_key": "ai_credit_support_stock",
                "feature_version": "1.0.0",
                "published_at": available_at,
                "event_time_precision": "second",
            }
        )
        assert EvidenceRepository.insert(
            connection,
            ObservationV2.model_validate(values, context={"from_storage": True}),
        )
    for suffix in ("timely", "future-fact", "future-contributor"):
        connection.execute(
            """INSERT INTO dataset_build VALUES (?, 'sql', 'as-of-direct',
               '2026-05-01T00:00:00+00:00', '2026-03-01', '2026-03-31', 1,
               '{}', ?, '2026-05-01T00:00:00+00:00')""",
            (f"as-of-build-{suffix}", f"as-of-checksum-{suffix}"),
        )
        connection.execute(
            """INSERT INTO feature_value VALUES (?, ?, 'company-a', '2026-03-01',
               '2026-03-31', 'ai_credit_support_stock', '1.0.0', 3.0, NULL,
               1.0, 1.0, 1, 1)""",
            (f"as-of-value-{suffix}", f"as-of-build-{suffix}"),
        )
    connection.execute(
        """INSERT INTO feature_value_fact VALUES (
           'as-of-value-timely', 'event-one', 'assignment-event-one', 'support-timely')"""
    )
    connection.execute(
        """INSERT INTO feature_value_contributor VALUES (
           'as-of-value-timely', 'event-one', 'assignment-event-one', 'support-timely')"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="fact lineage does not match"):
        connection.execute(
            """INSERT INTO feature_value_fact VALUES (
               'as-of-value-future-fact', 'event-one', 'assignment-event-one',
               'support-future')"""
        )
    connection.execute(
        """INSERT INTO feature_value_fact VALUES (
           'as-of-value-future-contributor', 'event-one', 'assignment-event-one',
           'support-timely')"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="contributor does not match"):
        connection.execute(
            """INSERT INTO feature_value_contributor VALUES (
               'as-of-value-future-contributor', 'event-one', 'assignment-event-one',
               'support-future')"""
        )
    connection.close()


@pytest.mark.parametrize("numeric", [True, False])
def test_build_finalization_rejects_cells_inconsistent_with_fact_lineage(
    tmp_path: Path, numeric: bool
) -> None:
    connection, _ = _prepare(tmp_path)
    build_id = f"invalid-build-{numeric}"
    connection.execute(
        """INSERT INTO dataset_build VALUES (?, 'sql', 'test',
           '2026-04-01T00:00:00+00:00', '2026-03-01', '2026-03-31', 1,
           '{}', ?, '2026-04-01T00:00:00+00:00')""",
        (build_id, f"checksum-{numeric}"),
    )
    connection.execute(
        """INSERT INTO feature_value VALUES (?, ?, 'company-a', '2026-03-01',
           '2026-03-31', 'ai_related_debt', '1.0.0', ?, ?, 0.0, 0.0, ?, ?)""",
        (
            f"invalid-value-{numeric}",
            build_id,
            1.0 if numeric else None,
            None if numeric else "unknown",
            1 if numeric else 0,
            1 if numeric else 0,
        ),
    )
    if not numeric:
        connection.execute(
            """INSERT INTO feature_value_fact VALUES (
               'invalid-value-False', 'event-one', 'assignment-event-one', 'obs-one')"""
        )
        connection.execute(
            """INSERT INTO feature_value_contributor VALUES (
               'invalid-value-False', 'event-one', 'assignment-event-one', 'obs-one')"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="lineage is incomplete"):
        connection.execute(
            "INSERT INTO dataset_build_finalization VALUES (?, '2026-04-01')", (build_id,)
        )
    connection.close()


def test_direct_sql_rejects_contributors_outside_the_feature_cell(tmp_path: Path) -> None:
    connection, _ = _prepare(tmp_path)
    source_id = connection.execute(
        "SELECT document_id FROM financial_events WHERE event_id = 'event-one'"
    ).fetchone()[0]
    second_spec = _register_second_version(connection, source_id, "event-one")
    assert second_spec.feature_version == "2.0.0"
    variants = [
        _observation(
            "obs-wrong-entity", source_id, "event-one", 1.0, "2026-03-31T13:00:00Z"
        ).model_copy(update={"entity_id": "company-b"}),
        ObservationV2.model_validate(
            {
                **_observation(
                    "obs-wrong-month",
                    source_id,
                    "event-one",
                    1.0,
                    "2026-03-31T13:01:00Z",
                ).model_dump(),
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
            },
            context={"from_storage": True},
        ),
        _observation(
            "obs-wrong-scope", source_id, "event-one", 1.0, "2026-03-31T13:02:00Z"
        ).model_copy(update={"economic_scope": EconomicScope.ECOSYSTEM}),
    ]
    for observation in variants:
        assert EvidenceRepository.insert(connection, observation)
    connection.commit()
    connection.execute(
        """INSERT INTO dataset_build VALUES (
           'cell-build', 'sql', 'set', '2026-05-01T00:00:00+00:00',
           '2026-03-01', '2026-03-31', 1, '{}', 'cell-checksum',
           '2026-05-01T00:00:00+00:00')"""
    )
    connection.execute(
        """INSERT INTO feature_value VALUES (
           'cell-value', 'cell-build', 'company-a', '2026-03-01', '2026-03-31',
           'ai_related_debt', '1.0.0', 1.0, NULL, 1.0, 1.0, 1, 1)"""
    )
    connection.execute(
        """INSERT INTO feature_value_fact VALUES (
           'cell-value', 'event-one', 'assignment-event-one', 'obs-one')"""
    )
    for observation_id in (
        "obs-wrong-entity",
        "obs-wrong-month",
        "obs-wrong-scope",
        "obs-version-two",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="does not match feature cell"):
            connection.execute(
                """INSERT INTO feature_value_contributor VALUES (
                   'cell-value', 'event-one', 'assignment-event-one', ?)""",
                (observation_id,),
            )
    connection.close()


def test_identical_feature_build_is_reproducible_and_idempotent(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    builder = FeatureStoreBuilder(connection)
    arguments = ([spec], "2026-04-03T00:00:00Z", ["company-b", "company-a"])
    first = builder.build_entity_month(
        *arguments,
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    second = builder.build_entity_month(
        *arguments,
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    assert second == first
    assert connection.execute("SELECT COUNT(*) FROM dataset_build").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM feature_value").fetchone()[0] == 2
    connection.close()


def test_idempotent_build_rejects_matching_but_unfinalized_build(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    builder = FeatureStoreBuilder(connection)
    arguments = dict(
        specs=[spec],
        availability_cutoff="2026-04-03T00:00:00Z",
        expected_entities=["company-a"],
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    result = builder.build_entity_month(**arguments)
    connection.execute("DROP TRIGGER dataset_build_finalization_no_delete")
    connection.execute(
        "DELETE FROM dataset_build_finalization WHERE build_id = ?", (result.build_id,)
    )
    connection.commit()
    with pytest.raises(ValueError, match="not finalized"):
        builder.build_entity_month(**arguments)
    connection.close()


def test_idempotent_build_rejects_finalized_row_with_tampered_grain(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    builder = FeatureStoreBuilder(connection)
    arguments = dict(
        specs=[spec],
        availability_cutoff="2026-04-03T00:00:00Z",
        expected_entities=["company-b"],
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    result = builder.build_entity_month(**arguments)
    connection.execute("DROP TRIGGER feature_value_no_update")
    connection.execute(
        """UPDATE feature_value SET entity_id = 'tampered-entity'
           WHERE build_id = ?""",
        (result.build_id,),
    )
    connection.commit()
    with pytest.raises(ValueError, match="feature content is inconsistent"):
        builder.build_entity_month(**arguments)
    connection.close()


def test_idempotent_build_rejects_tampered_manifest_or_build_metadata(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    builder = FeatureStoreBuilder(connection)
    arguments = dict(
        specs=[spec],
        availability_cutoff="2026-04-03T00:00:00Z",
        expected_entities=["company-a"],
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    result = builder.build_entity_month(**arguments)
    connection.execute("DROP TRIGGER dataset_build_no_update")
    connection.execute(
        """UPDATE dataset_build
           SET manifest_json = '{"tampered":true}', code_commit = 'wrong-commit'
           WHERE build_id = ?""",
        (result.build_id,),
    )
    connection.commit()
    with pytest.raises(ValueError, match="different metadata or content"):
        builder.build_entity_month(**arguments)
    connection.close()


def test_build_refuses_unregistered_or_semantically_different_spec(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    builder = FeatureStoreBuilder(connection)
    with pytest.raises(ValueError, match="does not match registered semantics"):
        builder.build_entity_month(
            [spec.model_copy(update={"aggregation": Aggregation.MEAN})],
            "2026-04-03T00:00:00Z",
            ["company-a"],
            code_commit="abc123",
            feature_set_version="1.0.0",
            period_start="2026-03-01",
            period_end="2026-03-31",
        )
    with pytest.raises(ValueError, match="unregistered feature"):
        builder.build_entity_month(
            [spec.model_copy(update={"feature_version": "2.0.0"})],
            "2026-04-03T00:00:00Z",
            ["company-a"],
            code_commit="abc123",
            feature_set_version="1.0.0",
            period_start="2026-03-01",
            period_end="2026-03-31",
        )
    connection.close()


def test_feature_store_is_append_only_at_database_layer(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    result = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="abc123",
        feature_set_version="1.0.0",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    for statement in (
        "UPDATE feature_value SET value_numeric = 1",
        "DELETE FROM feature_value",
        "UPDATE feature_value_contributor SET observation_id = 'x'",
        "DELETE FROM feature_value_contributor",
        "UPDATE feature_value_fact SET representative_observation_id = 'x'",
        "DELETE FROM feature_value_fact",
        "UPDATE dataset_build SET checksum = 'x'",
        f"DELETE FROM dataset_build WHERE build_id = '{result.build_id}'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(statement)
    connection.close()


def test_ecosystem_build_rolls_up_finalized_entity_rows_with_missing_months(
    tmp_path: Path,
) -> None:
    connection, entity_spec = _prepare(tmp_path)
    ecosystem_spec = _ecosystem_spec(connection)
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-03T00:00:00Z",
        ["company-a", "company-b"],
        code_commit="entity-commit",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-04-30",
    )
    builder = EcosystemFeatureStoreBuilder(connection)
    result = builder.build_months(
        entity_build.build_id,
        [ecosystem_spec],
        code_commit="ecosystem-commit",
        feature_set_version="ecosystem-set",
    )
    rows = connection.execute(
        """SELECT period_start, value_numeric, missingness_reason, coverage,
                  entity_contributor_count, fact_count
           FROM ecosystem_feature_value WHERE build_id = ? ORDER BY period_start""",
        (result.build_id,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("2026-03-01", 700_000_000, None, 0.5, 2, 2),
        ("2026-04-01", None, "unknown", 0.0, 2, 0),
    ]
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM ecosystem_dataset_build_finalization WHERE build_id = ?",
            (result.build_id,),
        ).fetchone()[0]
        == 1
    )
    assert (
        builder.build_months(
            entity_build.build_id,
            [ecosystem_spec],
            code_commit="ecosystem-commit",
            feature_set_version="ecosystem-set",
        )
        == result
    )
    connection.close()


def test_ecosystem_sum_deduplicates_one_fact_reported_by_two_entities(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    ecosystem_spec = _ecosystem_spec(connection)
    repository = SqliteRepository(tmp_path / "monitor.db")
    source_id, event_id = _seed_event(repository, connection, "dual-role", 500_000_000)
    assert EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment(
            assignment_id="assignment-dual-role",
            event_id=event_id,
            canonical_fact_id="event-one",
            available_at="2026-03-31T12:20:00Z",
            assigned_by="reviewer",
            assignment_method="cross_document_match",
        ),
    )
    assert EvidenceRepository.insert(
        connection,
        _observation(
            "obs-dual-role",
            source_id,
            event_id,
            500_000_000,
            "2026-03-31T12:30:00Z",
        ).model_copy(update={"entity_id": "company-b"}),
    )
    connection.commit()
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a", "company-b"],
        code_commit="entity-commit",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    result = EcosystemFeatureStoreBuilder(connection).build_months(
        entity_build.build_id,
        [ecosystem_spec],
        code_commit="ecosystem-commit",
        feature_set_version="ecosystem-set",
    )
    row = connection.execute(
        """SELECT ecosystem_feature_value_id, value_numeric,
                  entity_contributor_count, fact_count
           FROM ecosystem_feature_value WHERE build_id = ?""",
        (result.build_id,),
    ).fetchone()
    assert tuple(row[1:]) == (500_000_000, 2, 1)
    assert (
        connection.execute(
            """SELECT COUNT(*) FROM ecosystem_feature_value_entity_contributor
           WHERE ecosystem_feature_value_id = ?""",
            (row[0],),
        ).fetchone()[0]
        == 2
    )
    connection.close()


def test_ecosystem_manifest_is_stable_under_spec_reordering(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    first = _ecosystem_spec(connection)
    assert EvidenceRepository.register_feature(
        connection,
        FeatureDefinitionV2(
            feature_key="ecosystem_ai_debt_secondary",
            feature_version="1.0.0",
            definition_json=json.dumps(
                {"aggregation": "sum", "unit": "currency", "grain": "ecosystem_month"},
                sort_keys=True,
            ),
            released_at="2026-01-01",
        ),
    )
    connection.commit()
    second = first.model_copy(update={"feature_key": "ecosystem_ai_debt_secondary"})
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    builder = EcosystemFeatureStoreBuilder(connection)
    kwargs = dict(
        source_entity_build_id=entity_build.build_id,
        code_commit="ecosystem",
        feature_set_version="ecosystem-set",
    )
    assert builder.build_months(specs=[first, second], **kwargs) == builder.build_months(
        specs=[second, first], **kwargs
    )
    connection.close()


def test_ecosystem_build_rejects_unfinalized_source_and_bad_semantics(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    ecosystem_spec = _ecosystem_spec(connection)
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="entity-commit",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    builder = EcosystemFeatureStoreBuilder(connection)
    with pytest.raises(ValueError, match="does not match registered semantics"):
        builder.build_months(
            entity_build.build_id,
            [ecosystem_spec.model_copy(update={"aggregation": Aggregation.MEAN})],
            code_commit="ecosystem-commit",
            feature_set_version="ecosystem-set",
        )
    connection.execute("DROP TRIGGER dataset_build_finalization_no_delete")
    connection.execute(
        "DELETE FROM dataset_build_finalization WHERE build_id = ?", (entity_build.build_id,)
    )
    connection.commit()
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM finalized_entity_feature_value WHERE build_id = ?",
            (entity_build.build_id,),
        ).fetchone()[0]
        == 0
    )
    with pytest.raises(ValueError, match="must exist and be finalized"):
        builder.build_months(
            entity_build.build_id,
            [ecosystem_spec],
            code_commit="ecosystem-commit",
            feature_set_version="ecosystem-set",
        )
    connection.close()


def test_ecosystem_store_database_constraints_and_append_only_rules(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    ecosystem_spec = _ecosystem_spec(connection)
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    result = EcosystemFeatureStoreBuilder(connection).build_months(
        entity_build.build_id,
        [ecosystem_spec],
        code_commit="ecosystem",
        feature_set_version="ecosystem-set",
    )
    for statement in (
        "UPDATE ecosystem_feature_value SET value_numeric = 1",
        "DELETE FROM ecosystem_feature_value",
        "UPDATE ecosystem_dataset_build SET code_commit = 'x'",
        "DELETE FROM ecosystem_feature_value_fact",
        "DELETE FROM ecosystem_feature_value_entity_contributor",
        f"DELETE FROM ecosystem_dataset_build WHERE build_id = '{result.build_id}'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(statement)
    connection.close()


@pytest.mark.parametrize("numeric", [True, False])
def test_ecosystem_finalization_rejects_inconsistent_missingness_and_facts(
    tmp_path: Path, numeric: bool
) -> None:
    connection, entity_spec = _prepare(tmp_path)
    _ecosystem_spec(connection)
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    build_id = f"invalid-ecosystem-{numeric}"
    value_id = f"invalid-ecosystem-value-{numeric}"
    connection.execute(
        """INSERT INTO ecosystem_dataset_build VALUES (
           ?, ?, 'sql', 'set', '2026-04-01T00:00:00+00:00',
           '2026-03-01', '2026-03-31', 1, '{}', ?,
           '2026-04-01T00:00:00+00:00')""",
        (build_id, entity_build.build_id, f"invalid-ecosystem-checksum-{numeric}"),
    )
    connection.execute(
        """INSERT INTO ecosystem_feature_value VALUES (
           ?, ?, '2026-03-01', '2026-03-31', 'ai_related_debt', '1.0.0',
           'ecosystem_ai_debt', '1.0.0', ?, ?, 1.0, 1.0, 1, ?)""",
        (
            value_id,
            build_id,
            1.0 if numeric else None,
            None if numeric else "unknown",
            1 if numeric else 0,
        ),
    )
    source_value_id = connection.execute(
        "SELECT feature_value_id FROM feature_value WHERE build_id = ?", (entity_build.build_id,)
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO ecosystem_feature_value_entity_contributor VALUES (?, ?)",
        (value_id, source_value_id),
    )
    if not numeric:
        connection.execute(
            """INSERT INTO ecosystem_feature_value_fact VALUES (
               ?, 'event-one', 'assignment-event-one', 'obs-one')""",
            (value_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="lineage is incomplete"):
        connection.execute(
            "INSERT INTO ecosystem_dataset_build_finalization VALUES (?, '2026-04-01')",
            (build_id,),
        )
    connection.close()


def test_ecosystem_finalization_requires_every_source_entity_cell(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    _ecosystem_spec(connection)
    source_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a", "company-b"],
        code_commit="entity",
        feature_set_version="set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    _insert_raw_ecosystem_build(connection, "omitted-entity-build", source_build.build_id)
    connection.execute(
        """INSERT INTO ecosystem_feature_value VALUES (
           'omitted-entity-value', 'omitted-entity-build', '2026-03-01', '2026-03-31',
           'ai_related_debt', '1.0.0', 'ecosystem_ai_debt', '1.0.0',
           500000000, NULL, 0.5, 0.5, 1, 1)"""
    )
    source_value = connection.execute(
        """SELECT feature_value_id FROM feature_value
           WHERE build_id = ? AND entity_id = 'company-a'""",
        (source_build.build_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO ecosystem_feature_value_entity_contributor VALUES (?, ?)",
        ("omitted-entity-value", source_value),
    )
    fact = connection.execute(
        """SELECT canonical_fact_id, canonical_assignment_id, representative_observation_id
           FROM feature_value_fact WHERE feature_value_id = ?""",
        (source_value,),
    ).fetchone()
    connection.execute(
        "INSERT INTO ecosystem_feature_value_fact VALUES (?, ?, ?, ?)",
        ("omitted-entity-value", *tuple(fact)),
    )
    with pytest.raises(sqlite3.IntegrityError, match="lineage is incomplete"):
        connection.execute(
            """INSERT INTO ecosystem_dataset_build_finalization
               VALUES ('omitted-entity-build', '2026-04-03')"""
        )
    connection.close()


def test_ecosystem_finalization_requires_complete_canonical_fact_union(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    _ecosystem_spec(connection)
    source_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    _insert_raw_ecosystem_build(connection, "omitted-fact-build", source_build.build_id)
    connection.execute(
        """INSERT INTO ecosystem_feature_value VALUES (
           'omitted-fact-value', 'omitted-fact-build', '2026-03-01', '2026-03-31',
           'ai_related_debt', '1.0.0', 'ecosystem_ai_debt', '1.0.0',
           500000000, NULL, 1.0, 1.0, 1, 1)"""
    )
    source_value = connection.execute(
        "SELECT feature_value_id FROM feature_value WHERE build_id = ?",
        (source_build.build_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO ecosystem_feature_value_entity_contributor VALUES (?, ?)",
        ("omitted-fact-value", source_value),
    )
    fact = connection.execute(
        """SELECT canonical_fact_id, canonical_assignment_id, representative_observation_id
           FROM feature_value_fact WHERE feature_value_id = ? ORDER BY canonical_fact_id LIMIT 1""",
        (source_value,),
    ).fetchone()
    connection.execute(
        "INSERT INTO ecosystem_feature_value_fact VALUES (?, ?, ?, ?)",
        ("omitted-fact-value", *tuple(fact)),
    )
    with pytest.raises(sqlite3.IntegrityError, match="lineage is incomplete"):
        connection.execute(
            """INSERT INTO ecosystem_dataset_build_finalization
               VALUES ('omitted-fact-build', '2026-04-03')"""
        )
    connection.close()


def test_ecosystem_fact_must_exist_in_a_linked_source_entity_cell(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    _ecosystem_spec(connection)
    repository = SqliteRepository(tmp_path / "monitor.db")
    source_id, event_id = _seed_event(repository, connection, "outside-source-build", 1.0)
    assert EvidenceRepository.register_canonical_fact(connection, event_id)
    assert EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment(
            assignment_id="assignment-outside-source-build",
            event_id=event_id,
            canonical_fact_id=event_id,
            available_at="2026-03-31T12:10:00Z",
            assigned_by="test",
            assignment_method="manual_review",
        ),
    )
    assert EvidenceRepository.insert(
        connection,
        _observation(
            "obs-outside-source-build",
            source_id,
            event_id,
            1.0,
            "2026-03-31T12:20:00Z",
        ).model_copy(update={"entity_id": "company-outside"}),
    )
    connection.commit()
    source_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    _insert_raw_ecosystem_build(connection, "unlinked-fact-build", source_build.build_id)
    connection.execute(
        """INSERT INTO ecosystem_feature_value VALUES (
           'unlinked-fact-value', 'unlinked-fact-build', '2026-03-01', '2026-03-31',
           'ai_related_debt', '1.0.0', 'ecosystem_ai_debt', '1.0.0',
           1, NULL, 1.0, 1.0, 1, 1)"""
    )
    source_value = connection.execute(
        "SELECT feature_value_id FROM feature_value WHERE build_id = ?",
        (source_build.build_id,),
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO ecosystem_feature_value_entity_contributor VALUES (?, ?)",
        ("unlinked-fact-value", source_value),
    )
    with pytest.raises(sqlite3.IntegrityError, match="does not match ecosystem cell"):
        connection.execute(
            """INSERT INTO ecosystem_feature_value_fact VALUES (
               'unlinked-fact-value', ?, 'assignment-outside-source-build',
               'obs-outside-source-build')""",
            (event_id,),
        )
    connection.close()


@pytest.mark.parametrize("value", [float("inf"), float("-inf")])
def test_ecosystem_value_rejects_infinite_direct_sql(tmp_path: Path, value: float) -> None:
    connection, entity_spec = _prepare(tmp_path)
    _ecosystem_spec(connection)
    source_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    _insert_raw_ecosystem_build(connection, f"infinite-{value}", source_build.build_id)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        connection.execute(
            """INSERT INTO ecosystem_feature_value VALUES (
               ?, ?, '2026-03-01', '2026-03-31', 'ai_related_debt', '1.0.0',
               'ecosystem_ai_debt', '1.0.0', ?, NULL, 1.0, 1.0, 1, 1)""",
            (f"infinite-value-{value}", f"infinite-{value}", value),
        )
    connection.close()


def test_quality_reports_are_deterministic_finalized_and_provenance_rich(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    ecosystem_spec = _ecosystem_spec(connection)
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-03T00:00:00Z",
        ["company-a", "company-b"],
        code_commit="entity",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-04-30",
    )
    entity_report = audit_finalized_build(connection, entity_build.build_id)
    assert entity_report.grain == "entity_month"
    assert (entity_report.row_count, entity_report.numeric_count, entity_report.missing_count) == (
        4,
        1,
        3,
    )
    assert entity_report.distinct_canonical_facts == 2
    assert entity_report.distinct_observations == 2
    assert entity_report.distinct_source_documents == 2
    assert entity_report.features[0].source_tiers == {"primary": 2}
    assert entity_report.features[0].fact_statuses == {"direct": 2}
    assert (
        entity_report.canonical_json()
        == audit_finalized_build(connection, entity_build.build_id, "entity_month").canonical_json()
    )

    ecosystem_build = EcosystemFeatureStoreBuilder(connection).build_months(
        entity_build.build_id,
        [ecosystem_spec],
        code_commit="ecosystem",
        feature_set_version="ecosystem-set",
    )
    ecosystem_report = audit_finalized_build(
        connection, ecosystem_build.build_id, "ecosystem_month"
    )
    assert ecosystem_report.grain == "ecosystem_month"
    assert ecosystem_report.row_count == 2
    assert ecosystem_report.features[0].missingness == {"unknown": 1}
    assert ecosystem_report.distinct_observations == 2
    connection.close()


def test_quality_report_rejects_unfinalized_or_wrong_grain(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    result = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-01T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    with pytest.raises(ValueError, match="exactly one finalized build"):
        audit_finalized_build(connection, result.build_id, "ecosystem_month")
    connection.execute("DROP TRIGGER dataset_build_finalization_no_delete")
    connection.execute(
        "DELETE FROM dataset_build_finalization WHERE build_id = ?", (result.build_id,)
    )
    connection.commit()
    with pytest.raises(ValueError, match="exactly one finalized build"):
        audit_finalized_build(connection, result.build_id)
    connection.close()


def test_feature_build_and_audit_cli_use_json_configs_and_finalized_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "cli.db"
    repository = SqliteRepository(database)
    connection = repository.connect()
    definition = FeatureDefinitionV2(
        feature_key="ai_related_debt",
        feature_version="1.0.0",
        definition_json=json.dumps(
            {
                "aggregation": "sum",
                "unit": "currency",
                "grain": "entity_month",
                "expected_facts_per_period": 1,
            },
            sort_keys=True,
        ),
        released_at="2026-01-01",
    )
    assert EvidenceRepository.register_feature(connection, definition)
    source_id, event_id = _seed_event(repository, connection, "cli", 10.0)
    assert EvidenceRepository.register_canonical_fact(connection, event_id)
    assert EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment(
            assignment_id="assignment-cli",
            event_id=event_id,
            canonical_fact_id=event_id,
            available_at="2026-03-31T12:00:00Z",
            assigned_by="test",
            assignment_method="manual_review",
        ),
    )
    assert EvidenceRepository.insert(
        connection,
        _observation("obs-cli", source_id, event_id, 10.0, "2026-03-31T12:10:00Z"),
    )
    connection.commit()
    connection.close()
    config = tmp_path / "feature-build.json"
    config.write_text(
        json.dumps(
            {
                "grain": "entity_month",
                "specs": [
                    {
                        "feature_key": "ai_related_debt",
                        "feature_version": "1.0.0",
                        "aggregation": "sum",
                        "unit": "currency",
                        "expected_facts_per_period": 1,
                    }
                ],
                "availability_cutoff": "2026-04-01T00:00:00Z",
                "expected_entities": ["company-a"],
                "code_commit": "cli-commit",
                "feature_set_version": "cli-set",
                "period_start": "2026-03-01",
                "period_end": "2026-03-31",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ASRO_DATABASE_PATH", str(database))
    runner = CliRunner()
    built = runner.invoke(app, ["feature-build", "--config", str(config)])
    assert built.exit_code == 0, built.output
    build_payload = json.loads(built.stdout)
    audited = runner.invoke(app, ["feature-audit", "--build-id", build_payload["build_id"]])
    assert audited.exit_code == 0, audited.output
    audit_payload = json.loads(audited.stdout)
    assert audit_payload["grain"] == "entity_month"
    assert audit_payload["row_count"] == 1
    assert audit_payload["distinct_observations"] == 1


def test_episode_catalog_contains_crisis_benign_and_current_strata() -> None:
    episode_dir = Path(__file__).parents[1] / "src" / "asro" / "backfill" / "episodes"
    manifests = [EpisodeManifest.from_toml(path) for path in sorted(episode_dir.glob("*.toml"))]
    assert len(manifests) == 7
    assert {item.stratum for item in manifests} == {
        EpisodeStratum.CRISIS,
        EpisodeStratum.BENIGN,
        EpisodeStratum.CURRENT,
    }
    assert len({item.checksum() for item in manifests}) == 7


def test_backfill_freezes_sources_builds_coverage_and_leakage_idempotently(
    tmp_path: Path,
) -> None:
    connection, entity_spec = _prepare(tmp_path)
    ecosystem_spec = _ecosystem_spec(connection)
    repository = SqliteRepository(tmp_path / "monitor.db")
    document_ids = [
        row[0] for row in connection.execute("SELECT document_id FROM financial_events")
    ]
    for index, document_id in enumerate(document_ids):
        repository.upsert_document(
            connection,
            document_id,
            f"2026-03-31T12:0{index}:00+00:00",
            "text/html",
            "ok",
            f"immutable primary filing {index}",
        )
    connection.commit()
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    ecosystem_build = EcosystemFeatureStoreBuilder(connection).build_months(
        entity_build.build_id,
        [ecosystem_spec],
        code_commit="ecosystem",
        feature_set_version="ecosystem-set",
    )
    runner = BackfillRunner(connection)
    manifest = _episode_manifest()
    first = runner.run(manifest, entity_build.build_id, ecosystem_build.build_id)
    second = runner.run(manifest, entity_build.build_id, ecosystem_build.build_id)
    assert second == first
    assert first.source_count == 2
    assert first.coverage_passed and first.leakage_passed
    source_rows = connection.execute(
        """SELECT content_sha256, fetched_at, availability_at
           FROM backfill_source_snapshot_v2 WHERE run_id = ? ORDER BY document_id""",
        (first.run_id,),
    ).fetchall()
    assert len(source_rows) == 2
    assert all(len(row[0]) == 64 and row[2] <= "2026-04-03" for row in source_rows)
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM backfill_build_link WHERE run_id = ?", (first.run_id,)
        ).fetchone()[0]
        == 2
    )
    coverage = json.loads(
        connection.execute(
            "SELECT coverage_json FROM backfill_run WHERE run_id = ?", (first.run_id,)
        ).fetchone()[0]
    )
    assert coverage["missing_cell_count"] == 0
    assert tuple(
        connection.execute(
            """SELECT present_count,total_count,threshold
               FROM backfill_coverage_metric WHERE run_id=? AND dimension='source'""",
            (first.run_id,),
        ).fetchone()
    ) == (1, 1, 1.0)
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM finalized_backfill_run WHERE run_id = ?", (first.run_id,)
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(sqlite3.IntegrityError, match="backfill run is finalized"):
        connection.execute(
            """INSERT INTO backfill_source_snapshot_v2(
                   run_id, document_id, source_plan_id, content_sha256, published_at,
                   discovered_at, fetched_at, availability_at, content_type, fetch_status,
                   entity_id, availability_basis, url, title, source_name, content_text
               ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                first.run_id,
                document_ids[0],
                "primary-filings",
                "0" * 64,
                "2026-03-01T00:00:00Z",
                "2026-03-01T00:00:00Z",
                "2026-03-01T00:00:00Z",
                "text/plain",
                "ok",
                "company-a",
                "first_observed_at",
                "https://example.com/late",
                "Late insert",
                "Primary filing",
                "tampered",
            ),
        )
    connection.close()


def test_backfill_finalization_rejects_claimed_inputs_without_rows(tmp_path: Path) -> None:
    connection, _ = _prepare(tmp_path)
    manifest = _episode_manifest()
    runner = BackfillRunner(connection)
    runner._register_episode(manifest)  # noqa: SLF001 - direct-SQL invariant setup
    connection.execute(
        """INSERT INTO backfill_run(
               run_id, episode_id, episode_version, manifest_checksum, input_checksum,
               coverage_json, coverage_checksum, leakage_json, leakage_checksum,
               coverage_passed, leakage_passed, source_count, build_count, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "incomplete-run",
            manifest.episode_id,
            manifest.version,
            manifest.checksum(),
            "input-checksum",
            "{}",
            "coverage-checksum",
            "{}",
            "leakage-checksum",
            0,
            0,
            1,
            0,
            "2026-04-03T00:00:00Z",
        ),
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM finalized_backfill_run WHERE run_id = 'incomplete-run'"
        ).fetchone()[0]
        == 0
    )
    with pytest.raises(sqlite3.IntegrityError, match="backfill run identity mismatched"):
        connection.execute(
            """INSERT INTO backfill_run_finalization(run_id, finalized_at)
               VALUES ('incomplete-run', '2026-04-03T00:00:00Z')"""
        )
    connection.close()


def test_backfill_new_source_content_creates_new_run_without_rewriting_snapshot(
    tmp_path: Path,
) -> None:
    connection, _ = _prepare(tmp_path)
    repository = SqliteRepository(tmp_path / "monitor.db")
    document_id = connection.execute("SELECT document_id FROM financial_events LIMIT 1").fetchone()[
        0
    ]
    repository.upsert_document(
        connection,
        document_id,
        "2026-03-31T12:00:00+00:00",
        "text/plain",
        "ok",
        "first immutable body",
    )
    connection.commit()
    runner = BackfillRunner(connection)
    manifest = _episode_manifest(
        coverage_gate=CoverageGate(
            minimum_entity_source_coverage=1.0,
        )
    )
    first = runner.run(manifest)
    repository.upsert_document(
        connection,
        document_id,
        "2026-03-31T12:00:00+00:00",
        "text/plain",
        "ok",
        "corrected body creates a new input identity",
    )
    connection.commit()
    second = runner.run(manifest)
    assert second.run_id != first.run_id
    hashes = connection.execute(
        """SELECT content_sha256 FROM backfill_source_snapshot_v2
           WHERE document_id = ? ORDER BY run_id""",
        (document_id,),
    ).fetchall()
    assert len({row[0] for row in hashes}) == 2
    connection.close()


def test_backfill_reports_coverage_failure_and_temporal_leakage(tmp_path: Path) -> None:
    connection, entity_spec = _prepare(tmp_path)
    source_build = FeatureStoreBuilder(connection).build_entity_month(
        [entity_spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="entity",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    manifest = _episode_manifest(
        availability_cutoff="2026-04-01T00:00:00Z",
        source_plan=[
            SourcePlan(
                source_id="absent-primary",
                source_pattern="source-that-does-not-exist",
                tier="primary",
            )
        ],
    )
    result = BackfillRunner(connection).run(manifest, entity_build_id=source_build.build_id)
    assert not result.coverage_passed
    assert not result.leakage_passed
    leakage = json.loads(
        connection.execute(
            "SELECT leakage_json FROM backfill_run WHERE run_id = ?", (result.run_id,)
        ).fetchone()[0]
    )
    assert leakage["violation_count"] == 2
    assert {
        row[0]
        for row in connection.execute(
            "SELECT violation_type FROM backfill_leakage_violation WHERE run_id=?",
            (result.run_id,),
        )
    } == {
        "build_after_cutoff",
        "observation_after_cutoff",
    }
    connection.close()


def test_archival_fetch_uses_public_availability_and_freezes_reconstructable_content(
    tmp_path: Path,
) -> None:
    connection, _ = _prepare(tmp_path)
    repository = SqliteRepository(tmp_path / "monitor.db")
    document_id = connection.execute("SELECT document_id FROM financial_events LIMIT 1").fetchone()[
        0
    ]
    repository.upsert_document(
        connection,
        document_id,
        "2030-01-01T00:00:00Z",
        "text/plain",
        "ok",
        "archival filing bytes",
    )
    connection.commit()
    result = BackfillRunner(connection).run(_episode_manifest())
    row = connection.execute(
        """SELECT availability_at, availability_basis, fetched_at, content_text,
                  content_sha256, url, title, source_name
           FROM backfill_source_snapshot_v2 WHERE run_id=?""",
        (result.run_id,),
    ).fetchone()
    assert row is not None
    assert row[0].startswith("2026-03-31") and row[1] == "published_at"
    assert row[2].startswith("2030-01-01")
    assert hashlib.sha256(row[3].encode()).hexdigest() == row[4]
    connection.execute(
        "UPDATE documents SET text='mutated live row' WHERE item_id=?", (document_id,)
    )
    connection.execute("DELETE FROM documents WHERE item_id=?", (document_id,))
    frozen = connection.execute(
        "SELECT content_text FROM backfill_source_snapshot_v2 WHERE run_id=?",
        (result.run_id,),
    ).fetchone()[0]
    assert frozen == "archival filing bytes"
    connection.close()


def test_unrelated_source_and_post_cutoff_publication_do_not_satisfy_entity_coverage(
    tmp_path: Path,
) -> None:
    connection, _ = _prepare(tmp_path)
    repository = SqliteRepository(tmp_path / "monitor.db")
    unrelated = score(
        SourceItem(
            title="Unrelated filing",
            url="https://example.com/unrelated",
            source="Primary filing",
            published_at="2026-03-15",
        ),
        [],
    )
    assert repository.insert(connection, unrelated)
    repository.upsert_document(
        connection, unrelated.item_id, "2026-03-16T00:00:00Z", "text/plain", "ok", "other"
    )
    for (document_id,) in connection.execute("SELECT document_id FROM financial_events"):
        repository.upsert_document(
            connection,
            document_id,
            "2026-03-20T00:00:00Z",
            "text/plain",
            "ok",
            "fetched before cutoff but published after cutoff",
        )
    connection.execute(
        """UPDATE items SET published_at='2026-04-10'
           WHERE id IN (SELECT document_id FROM financial_events)"""
    )
    connection.commit()
    result = BackfillRunner(connection).run(_episode_manifest())
    assert result.source_count == 0
    assert not result.coverage_passed
    connection.close()


def test_post_cutoff_observation_cannot_support_precutoff_source_cell(tmp_path: Path) -> None:
    connection, _ = _prepare(tmp_path)
    manifest = _episode_manifest(
        version="observation-cutoff",
        availability_cutoff="2026-03-31T12:03:00Z",
    )
    result = BackfillRunner(connection).run(manifest)
    source_cells = connection.execute(
        "SELECT present FROM backfill_coverage_cell WHERE run_id=? AND dimension='source'",
        (result.run_id,),
    ).fetchall()
    assert source_cells and {int(row[0]) for row in source_cells} == {0}
    assert not result.coverage_passed
    with pytest.raises(sqlite3.IntegrityError, match="accepted review available at cutoff"):
        connection.execute(
            """INSERT INTO backfill_coverage_cell VALUES(
               ?, 'company-a', '2026-03-01', '2026-03-31',
               'source', 'primary-filings', '', 1, '')""",
            (result.run_id,),
        )
    connection.close()


def test_confidence_without_review_available_at_cutoff_is_not_source_coverage(
    tmp_path: Path,
) -> None:
    connection, _ = _prepare(tmp_path)
    result = BackfillRunner(connection).run(
        _episode_manifest(
            version="review-cutoff",
            availability_cutoff="2026-03-31T23:00:00Z",
        )
    )
    assert (
        connection.execute(
            "SELECT present FROM backfill_coverage_cell WHERE run_id=? AND dimension='source'",
            (result.run_id,),
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_rejected_review_with_pre_cutoff_assignment_completes_as_missing_source(
    tmp_path: Path,
) -> None:
    connection, _ = _prepare(tmp_path, review_decisions=("flag", "flag"))
    result = BackfillRunner(connection).run(_episode_manifest(version="rejected-review"))
    assert (
        connection.execute(
            "SELECT present FROM backfill_coverage_cell WHERE run_id=? AND dimension='source'",
            (result.run_id,),
        ).fetchone()[0]
        == 0
    )
    assert not result.coverage_passed
    assert connection.execute(
        "SELECT 1 FROM backfill_run_finalization WHERE run_id=?", (result.run_id,)
    ).fetchone()
    connection.close()


def test_required_control_is_versioned_and_gated_by_month(tmp_path: Path) -> None:
    connection, _ = _prepare(tmp_path)
    manifest = _episode_manifest(
        controls=[ControlPlan(series_id="policy_rate", version="2026-v1", unit="percent")],
        source_plan=[
            SourcePlan(
                source_id="primary-filings",
                source_pattern="Primary filing",
                tier="primary",
                required=False,
            )
        ],
    )
    missing = BackfillRunner(connection).run(manifest)
    assert not missing.coverage_passed
    manifest = manifest.model_copy(update={"version": "1.0.1"})
    assert register_control_observation(
        connection,
        ControlObservation(
            control_observation_id="rate-2026-03",
            series_id="policy_rate",
            series_version="2026-v1",
            period_start="2026-03-01",
            period_end="2026-03-31",
            observed_at="2026-03-31T00:00:00Z",
            availability_at="2026-04-01T00:00:00Z",
            value_numeric=4.5,
            unit="percent",
            provenance={
                "publisher": "Federal Reserve",
                "source_url": "https://federalreserve.gov/rates",
                "vintage": "2026-04-01",
            },
        ),
    )
    connection.commit()
    accepted = BackfillRunner(connection).run(manifest)
    assert accepted.coverage_passed
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM backfill_control_snapshot_v2 WHERE run_id=?", (accepted.run_id,)
        ).fetchone()[0]
        == 1
    )
    connection.close()


@pytest.mark.parametrize(
    "observed_at,availability_at,unit,provenance",
    [
        (
            "2026-03-31T00:00:00Z",
            "2026-04-01T00:00:00+00:00",
            "percent",
            '{"publisher":"Fed","source_url":"u","vintage":"v"}',
        ),
        (
            "2026-04-02T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "percent",
            '{"publisher":"Fed","source_url":"u","vintage":"v"}',
        ),
        (
            "2026-03-31T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "basis_points",
            '{"publisher":"Fed","source_url":"u","vintage":"v"}',
        ),
        (
            "2026-03-31T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "percent",
            '{"publisher":"Fed"}',
        ),
    ],
)
def test_control_direct_sql_rejects_noncanonical_or_semantically_invalid_rows(
    tmp_path: Path, observed_at: str, availability_at: str, unit: str, provenance: str
) -> None:
    connection, _ = _prepare(tmp_path)
    BackfillRunner(connection)._register_control_definitions(  # noqa: SLF001
        _episode_manifest(
            controls=[ControlPlan(series_id="policy_rate", version="v1", unit="percent")]
        )
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO historical_control_observation_v2 VALUES(
               'bad-control','policy_rate','v1','2026-03-01','2026-03-31',
               ?,?,4.5,?,?)""",
            (observed_at, availability_at, unit, provenance),
        )
    connection.close()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"entities": ["company-a", "company-b"]}, "entity set"),
        ({"period_start": "2026-02-01"}, "exact episode window"),
        ({"feature_set_version": "wrong-set"}, "feature-set version"),
        ({"schema_version": "v1"}, "schema version"),
        ({"extractor_version": "wrong-extractor"}, "extractor provenance"),
    ],
)
def test_backfill_rejects_incompatible_build_claims(
    tmp_path: Path, override: dict[str, object], message: str
) -> None:
    connection, spec = _prepare(tmp_path)
    build = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="compat",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    manifest = _episode_manifest(
        features=[FeatureRequirement(feature_key="ai_related_debt", feature_version="1.0.0")],
        **override,
    )
    with pytest.raises(ValueError, match=message):
        BackfillRunner(connection).run(manifest, entity_build_id=build.build_id)
    connection.close()


def test_backfill_rejects_ecosystem_from_different_entity_build(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    ecosystem_spec = _ecosystem_spec(connection)
    first = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="first",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    second = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-03T00:00:00Z",
        ["company-a"],
        code_commit="second",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    ecosystem = EcosystemFeatureStoreBuilder(connection).build_months(
        second.build_id, [ecosystem_spec], code_commit="eco", feature_set_version="eco-set"
    )
    manifest = _episode_manifest(
        features=[FeatureRequirement(feature_key="ai_related_debt", feature_version="1.0.0")]
    )
    with pytest.raises(ValueError, match="does not derive"):
        BackfillRunner(connection).run(manifest, first.build_id, ecosystem.build_id)
    connection.close()


def test_direct_sql_cannot_finalize_forged_pass_flags(tmp_path: Path) -> None:
    connection, _ = _prepare(tmp_path)
    manifest = _episode_manifest()
    BackfillRunner(connection)._register_episode(manifest)  # noqa: SLF001
    coverage = '{"cell_count":0,"passed":true}'
    leakage = '{"passed":true,"violation_count":0}'
    input_checksum = "forged-input"
    run_id = hashlib.sha256(
        f"{manifest.episode_id}|{manifest.version}|{input_checksum}".encode()
    ).hexdigest()
    connection.execute(
        """INSERT INTO backfill_run(
           run_id, episode_id, episode_version, manifest_checksum, input_checksum,
           coverage_json, coverage_checksum, leakage_json, leakage_checksum,
           coverage_passed, leakage_passed, source_count, build_count, created_at,
           control_count, coverage_cell_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, 0, ?, 0, 0)""",
        (
            run_id,
            manifest.episode_id,
            manifest.version,
            manifest.checksum(),
            input_checksum,
            coverage,
            hashlib.sha256(coverage.encode()).hexdigest(),
            leakage,
            hashlib.sha256(leakage.encode()).hexdigest(),
            "2026-04-03T00:00:00Z",
        ),
    )
    with pytest.raises(sqlite3.IntegrityError, match="coverage pass flag is forged"):
        connection.execute(
            "INSERT INTO backfill_run_finalization VALUES(?,'2026-04-03T00:00:00Z')",
            (run_id,),
        )
    connection.close()


def test_explicit_unknown_feature_rows_are_not_coverage(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    build = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-03T00:00:00Z",
        ["company-b"],
        code_commit="unknown",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    manifest = _episode_manifest(
        entities=["company-b"],
        features=[FeatureRequirement(feature_key="ai_related_debt", feature_version="1.0.0")],
        source_plan=[
            SourcePlan(source_id="optional", source_pattern="none", tier="primary", required=False)
        ],
    )
    result = BackfillRunner(connection).run(manifest, entity_build_id=build.build_id)
    assert not result.coverage_passed
    assert (
        connection.execute(
            "SELECT present FROM backfill_coverage_cell WHERE run_id=? AND dimension='feature'",
            (result.run_id,),
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_feature_coverage_threshold_boundary_matches_database(tmp_path: Path) -> None:
    connection, spec = _prepare(tmp_path)
    build = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        "2026-04-03T00:00:00Z",
        ["company-a", "company-b"],
        code_commit="threshold",
        feature_set_version="entity-set",
        period_start="2026-03-01",
        period_end="2026-03-31",
    )
    common = {
        "entities": ["company-a", "company-b"],
        "features": [FeatureRequirement(feature_key="ai_related_debt", feature_version="1.0.0")],
        "source_plan": [
            SourcePlan(source_id="optional", source_pattern="none", tier="primary", required=False)
        ],
    }
    passing = _episode_manifest(
        version="threshold-pass",
        coverage_gate=CoverageGate(minimum_entity_month_feature_coverage=0.5),
        **common,
    )
    failing = _episode_manifest(
        version="threshold-fail",
        coverage_gate=CoverageGate(minimum_entity_month_feature_coverage=0.500001),
        **common,
    )
    assert BackfillRunner(connection).run(passing, entity_build_id=build.build_id).coverage_passed
    assert (
        not BackfillRunner(connection).run(failing, entity_build_id=build.build_id).coverage_passed
    )
    connection.close()


def test_candidate_package_is_hashed_quarantined_and_contributes_no_coverage(
    tmp_path: Path,
) -> None:
    package = tmp_path / "candidate"
    package.mkdir()
    (package / "asro-seed-dataset-v2.tar.gz").write_bytes(b"immutable archive")
    (package / "dedupe_report.json").write_text("{}", encoding="utf-8")
    (package / "entities.json").write_text(
        json.dumps(
            {
                "entity_count": 1,
                "entities": [{"canonical_name": "company-a", "stub": False}],
            }
        ),
        encoding="utf-8",
    )
    (package / "seed_events.json").write_text(
        json.dumps(
            {
                "schema": "candidate-test",
                "as_of": "2026-04-03",
                "event_count": 2,
                "events": [
                    {
                        "event_id": "candidate-one",
                        "effective_date": "2026-03-15",
                        "event_type": "UNSUPPORTED_RESEARCH_TYPE",
                        "primary_entity": "company-a",
                        "sources": [
                            {
                                "url": "https://www.sec.gov/one",
                                "title": "Filing",
                                "publisher": "SEC",
                                "published_at": "2026-03-16",
                                "source_tier": "A",
                                "source_type": "filing",
                                "excerpt": "candidate excerpt only",
                                "is_primary": True,
                            },
                            {
                                "url": "https://example.com/two",
                                "title": "Second source",
                                "publisher": "Reporter",
                                "published_at": "2026-03-17",
                                "source_tier": "C",
                                "source_type": "news",
                                "excerpt": "second edge",
                                "is_primary": False,
                            },
                        ],
                    },
                    {
                        "event_id": "candidate-late",
                        "effective_date": "2026-04-04",
                        "event_type": "LATE",
                        "primary_entity": "company-a",
                        "sources": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    connection, _ = _prepare(tmp_path)
    result = ingest_candidate_package(connection, package)
    assert result.event_count == 2
    assert result.source_edge_count == 2
    assert result.eligible_event_count == 1
    assert connection.execute("SELECT COUNT(*) FROM candidate_source_edge").fetchone()[0] == 2
    assert (
        connection.execute(
            """SELECT quarantine_reason FROM candidate_event
               WHERE candidate_event_id='candidate-late'"""
        )
        .fetchone()[0]
        .find("post_as_of")
        >= 0
    )
    assert connection.execute("SELECT COUNT(*) FROM observation_v2").fetchone()[0] == 2
    support = candidate_episode_support(connection, result.package_id, [_episode_manifest()])
    assert support == [
        {
            "episode_id": "test-episode",
            "candidate_event_count": 1,
            "promoted_event_count": 0,
            "genuinely_supported": False,
            "finalized_backfill_run_id": None,
            "coverage_contribution": 0,
        }
    ]
    changed = json.loads((package / "seed_events.json").read_text(encoding="utf-8"))
    changed["events"][0]["event_type"] = "CHANGED_ASSERTION_SAME_COUNT"
    (package / "seed_events.json").write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="file hashes or manifest changed"):
        ingest_candidate_package(connection, package)
    connection.close()


def test_unrelated_same_entity_observation_cannot_promote_candidate_source(tmp_path: Path) -> None:
    package = tmp_path / "promotion-candidate"
    package.mkdir()
    (package / "asro-seed-dataset-v2.tar.gz").write_bytes(b"promotion archive")
    (package / "dedupe_report.json").write_text("{}", encoding="utf-8")
    (package / "entities.json").write_text(
        '{"entity_count":1,"entities":[{"canonical_name":"company-a"}]}', encoding="utf-8"
    )
    (package / "seed_events.json").write_text(
        """{"schema":"candidate-test","as_of":"2026-04-03","event_count":1,
        "events":[{"event_id":"candidate","effective_date":"2026-03-31",
        "event_type":"ASSERTED","primary_entity":"company-a","sources":[
        {"url":"https://example.com/two","title":"Two","publisher":"SEC",
        "published_at":"2026-03-31","excerpt":"excerpt","is_primary":true}]}]}""",
        encoding="utf-8",
    )
    connection, _ = _prepare(tmp_path)
    result = ingest_candidate_package(connection, package)
    repository = SqliteRepository(tmp_path / "monitor.db")
    documents = [row[0] for row in connection.execute("SELECT document_id FROM financial_events")]
    repository.upsert_document(
        connection, documents[0], "2026-03-31T12:00:00Z", "text/plain", "ok", "full one"
    )
    repository.upsert_document(
        connection, documents[1], "2026-03-31T12:00:00Z", "text/plain", "ok", "full two"
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable repository content"):
        connection.execute(
            """INSERT INTO candidate_acquired_document VALUES(
               ?, 'candidate', 0, ?, ?, 'full one', '2026-03-31T00:00:00+00:00',
               '2026-03-31T12:00:00+00:00', '{"method":"authoritative_fetch"}')""",
            (result.package_id, documents[0], hashlib.sha256(b"full one").hexdigest()),
        )
    with pytest.raises(sqlite3.IntegrityError, match="fetched before public availability"):
        connection.execute(
            """INSERT INTO candidate_acquired_document VALUES(
               ?, 'candidate', 0, ?, ?, 'full two', '2026-04-01T00:00:00+00:00',
               '2026-03-31T12:00:00+00:00', '{"method":"authoritative_fetch"}')""",
            (result.package_id, documents[1], hashlib.sha256(b"full two").hexdigest()),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable repository content"):
        connection.execute(
            """INSERT INTO candidate_acquired_document VALUES(
               ?, 'candidate', 0, ?, ?, 'fabricated body', '2026-03-31T00:00:00+00:00',
               '2026-03-31T12:00:00+00:00', '{"method":"authoritative_fetch"}')""",
            (
                result.package_id,
                documents[1],
                hashlib.sha256(b"fabricated body").hexdigest(),
            ),
        )
    connection.execute(
        """INSERT INTO candidate_acquired_document VALUES(
           ?, 'candidate', 0, ?, ?, 'full two', '2026-03-31T00:00:00+00:00',
           '2026-03-31T12:00:00+00:00', '{"method":"authoritative_fetch"}')""",
        (result.package_id, documents[1], hashlib.sha256(b"full two").hexdigest()),
    )
    with pytest.raises(sqlite3.IntegrityError, match="accepted reviewed acquired fact lineage"):
        connection.execute(
            """INSERT INTO candidate_evidence_promotion_v2 VALUES(
               ?, 'candidate', 0, 'obs-one', 'event-one', 'primary',
               '2026-04-03T00:00:00+00:00', 'reviewer', '{"decision":"promote"}')""",
            (result.package_id,),
        )
    connection.close()


def test_later_green_run_does_not_close_failed_window_and_repair_is_idempotent(
    tmp_path: Path,
) -> None:
    connection, _ = _prepare(tmp_path)
    failed = WorkflowRunRecord(
        workflow_run_id="32781036876",
        run_number=107,
        run_attempt=1,
        workflow_name="Hourly monitor",
        head_sha="c6986ab190971d9d5196b0395a30a7a96fc180c0",
        event_name="schedule",
        scheduled_for="2026-08-24T21:43:45Z",
        started_at="2026-08-24T21:43:49Z",
        completed_at="2026-08-24T21:44:34Z",
        conclusion="failure",
        failure_stage="collection",
        steps=[{"name": "Collect and extract", "conclusion": "failure"}],
        window_start="2026-08-24T20:17:00Z",
        window_end="2026-08-24T21:17:00Z",
        collector_runs=[],
    )
    later = WorkflowRunRecord(
        workflow_run_id="32786057614",
        run_number=108,
        run_attempt=1,
        workflow_name="Hourly monitor",
        head_sha=failed.head_sha,
        event_name="schedule",
        scheduled_for="2026-08-24T22:44:03Z",
        started_at="2026-08-24T22:44:03Z",
        completed_at="2026-08-24T22:45:08Z",
        conclusion="success",
        failure_stage=None,
        steps=[{"name": "Collect and extract", "conclusion": "success"}],
        window_start="2026-08-24T21:17:00Z",
        window_end="2026-08-24T22:17:00Z",
        collector_runs=[],
    )
    normal_run_ids = []
    for collector in (
        "google-news-rss",
        "company-economic-news",
        "external-competitive-pressure",
        "sec-edgar",
    ):
        run_id = SqliteRepository.start_collector_run(
            connection, collector, "2026-08-24T22:44:04+00:00"
        )
        SqliteRepository.finish_collector_run(
            connection, run_id, "2026-08-24T22:45:00+00:00", "ok", 1, 1
        )
        normal_run_ids.append(run_id)
    later = WorkflowRunRecord(**{**later.__dict__, "collector_runs": normal_run_ids})
    record_workflow_run(connection, failed)
    with pytest.raises(sqlite3.IntegrityError, match="exact current collector proof"):
        record_workflow_run(
            connection,
            WorkflowRunRecord(
                **{
                    **later.__dict__,
                    "workflow_run_id": "complete-without-collectors",
                    "collector_runs": [],
                }
            ),
        )
    duplicate_id = SqliteRepository.start_collector_run(
        connection, "google-news-rss", "2026-08-24T22:44:05+00:00"
    )
    SqliteRepository.finish_collector_run(
        connection, duplicate_id, "2026-08-24T22:45:01+00:00", "ok", 1, 1
    )
    with pytest.raises(sqlite3.IntegrityError, match="exact current collector proof"):
        record_workflow_run(
            connection,
            WorkflowRunRecord(
                **{
                    **later.__dict__,
                    "workflow_run_id": "complete-with-duplicate-collector",
                    "collector_runs": [*normal_run_ids, duplicate_id],
                }
            ),
        )
    record_workflow_run(connection, later)
    assert missing_hourly_windows(connection, "2026-08-24T20:17:00Z", "2026-08-24T22:17:00Z") == [
        ("2026-08-24T20:17:00+00:00", "2026-08-24T21:17:00+00:00")
    ]
    assert (
        len(
            alert_missing_hourly_windows(connection, "2026-08-24T20:17:00Z", "2026-08-24T22:17:00Z")
        )
        == 1
    )
    repair = WorkflowRunRecord(
        **{
            **later.__dict__,
            "workflow_run_id": "repair-107",
            "run_number": 0,
            "workflow_name": "interval-repair",
            "event_name": "workflow_dispatch",
            "window_start": failed.window_start,
            "window_end": failed.window_end,
        }
    )
    target_start = "2026-08-24T20:17:00+00:00"
    target_end = "2026-08-24T21:17:00+00:00"
    acquisition_start = "2026-08-24T00:00:00+00:00"
    acquisition_end = "2026-08-25T00:00:00+00:00"
    repair_execution_id = "repair-execution-107"
    connection.execute(
        "INSERT INTO repair_execution VALUES(?,?,?,?,?,?)",
        (
            repair_execution_id,
            target_start,
            target_end,
            acquisition_start,
            acquisition_end,
            "2026-08-24T22:44:03+00:00",
        ),
    )
    repair_run_ids = []
    for collector in ("google-news-history", "sec-edgar-history"):
        run_id = SqliteRepository.start_collector_run(
            connection,
            collector,
            "2026-08-24T22:44:03+00:00",
            repair_execution_id,
            target_start,
            target_end,
            acquisition_start,
            acquisition_end,
        )
        SqliteRepository.finish_collector_run(
            connection, run_id, "2026-08-24T22:45:00+00:00", "ok", 0, 0
        )
        connection.execute(
            "INSERT INTO repair_execution_collector VALUES(?,?)",
            (repair_execution_id, run_id),
        )
        repair_run_ids.append(run_id)
    connection.execute(
        "INSERT INTO repair_execution_finalization VALUES(?,?)",
        (repair_execution_id, "2026-08-24T22:45:01+00:00"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="terminal collector run is immutable"):
        connection.execute(
            "UPDATE collector_runs SET status='error' WHERE id=?", (repair_run_ids[0],)
        )
    failed_terminal = SqliteRepository.start_collector_run(
        connection, "test-terminal", "2026-08-24T22:44:03+00:00"
    )
    SqliteRepository.finish_collector_run(
        connection, failed_terminal, "2026-08-24T22:45:00+00:00", "error", 0, 0, "failed"
    )
    with pytest.raises(sqlite3.IntegrityError, match="terminal collector run is immutable"):
        connection.execute(
            "UPDATE collector_runs SET status='ok', error=NULL WHERE id=?", (failed_terminal,)
        )
    repair = WorkflowRunRecord(
        **{
            **repair.__dict__,
            "collector_runs": repair_run_ids,
            "repair_execution_id": repair_execution_id,
        }
    )
    first = record_window_repair(connection, repair)
    assert record_window_repair(connection, repair) == first
    assert not missing_hourly_windows(connection, "2026-08-24T20:17:00Z", "2026-08-24T22:17:00Z")
    connection.close()


def test_daily_window_audit_uses_1017_utc_and_24_hour_cadence(tmp_path: Path) -> None:
    connection, _ = _prepare(tmp_path)
    assert missing_daily_windows(connection, "2026-08-27T00:00:00Z", "2026-08-29T10:17:00Z") == [
        ("2026-08-27T10:17:00+00:00", "2026-08-28T10:17:00+00:00"),
        ("2026-08-28T10:17:00+00:00", "2026-08-29T10:17:00+00:00"),
    ]
    alert_ids = alert_missing_daily_windows(
        connection, "2026-08-27T00:00:00Z", "2026-08-29T10:17:00Z"
    )
    assert len(alert_ids) == 2
    details = [
        json.loads(row[0])
        for row in connection.execute(
            "SELECT detail_json FROM operational_alert ORDER BY window_start"
        )
    ]
    assert details == [
        {"expected_cadence_minutes": 1440},
        {"expected_cadence_minutes": 1440},
    ]
    connection.close()


def test_historical_backfill_cli_writes_deterministic_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "historical-cli.db"
    with SqliteRepository(database).connect():
        pass
    manifest = _episode_manifest(
        coverage_gate=CoverageGate(
            minimum_entity_source_coverage=0.0,
        )
    )
    manifest_path = tmp_path / "episode.toml"
    manifest_path.write_text(
        """episode_id = "cli-episode"
version = "1.0.0"
title = "CLI episode"
stratum = "benign"
period_start = 2026-03-01
period_end = 2026-03-31
availability_cutoff = 2026-04-03T00:00:00Z
entities = ["company-a"]
controls = []
schema_version = "v2"
extractor_version = "2.0.0"
feature_set_version = "1.0.0"
[[source_plan]]
source_id = "primary"
source_pattern = "SEC"
tier = "primary"
required = false
[coverage_gate]
minimum_entity_month_feature_coverage = 1.0
minimum_entity_source_coverage = 0.0
minimum_control_month_coverage = 1.0
""",
        encoding="utf-8",
    )
    assert manifest.coverage_gate.minimum_entity_source_coverage == 0.0
    output = tmp_path / "backfill-report.json"
    monkeypatch.setenv("ASRO_DATABASE_PATH", str(database))
    invoked = CliRunner().invoke(
        app,
        ["historical-backfill", "--manifest", str(manifest_path), "--output", str(output)],
    )
    assert invoked.exit_code == 0, invoked.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert not payload["coverage_passed"]
    assert payload["leakage_passed"]


@pytest.mark.parametrize("starting_version", [1, 2])
def test_forward_upgrade_preserves_existing_observations_and_feature_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, starting_version: int
) -> None:
    database = tmp_path / f"upgrade-{starting_version}.db"
    all_migrations = migration_runner.MIGRATIONS
    monkeypatch.setattr(migration_runner, "MIGRATIONS", all_migrations[:starting_version])
    repository = SqliteRepository(database)
    connection = repository.connect()
    definition = FeatureDefinitionV2(
        feature_key="ai_related_debt",
        feature_version="1.0.0",
        definition_json=json.dumps(
            {"aggregation": "sum", "unit": "currency", "expected_facts_per_period": 1}
        ),
        released_at="2026-01-01",
    )
    assert EvidenceRepository.register_feature(connection, definition)
    source_id, event_id = _seed_event(repository, connection, "upgrade", 10.0)
    assert EvidenceRepository.insert(
        connection,
        _observation("obs-upgrade", source_id, event_id, 10.0, "2026-03-31T13:00:00Z"),
    )
    if starting_version == 2:
        assert EvidenceRepository.insert(
            connection,
            _observation(
                "aaa-low-quality",
                source_id,
                event_id,
                10.0,
                "2026-03-31T13:01:00Z",
            ).model_copy(
                update={
                    "source_quality": 0.1,
                    "extraction_confidence": 0.1,
                    "review_confidence": 0.1,
                }
            ),
        )
        connection.execute(
            """INSERT INTO dataset_build VALUES (
               'old-build', 'old', 'old-set', '2026-04-01T00:00:00+00:00',
               '2026-03-01', '2026-03-31', 1, '{}', 'old-checksum',
               '2026-04-01T00:00:00+00:00')"""
        )
        connection.execute(
            """INSERT INTO feature_value VALUES (
               'old-value', 'old-build', 'company-a', '2026-03-01', '2026-03-31',
               'ai_related_debt', '1.0.0', 10.0, NULL, 1.0, 1.0, 1, 2)"""
        )
        connection.execute(
            "INSERT INTO feature_value_contributor VALUES ('old-value', 'obs-upgrade')"
        )
        connection.execute(
            "INSERT INTO feature_value_contributor VALUES ('old-value', 'aaa-low-quality')"
        )
    connection.commit()
    connection.close()

    monkeypatch.setattr(migration_runner, "MIGRATIONS", all_migrations)
    with repository.connect() as upgraded:
        assert [
            row[0]
            for row in upgraded.execute("SELECT version FROM schema_migrations ORDER BY version")
        ] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
        assert upgraded.execute("SELECT COUNT(*) FROM observation_v2").fetchone()[0] == (
            2 if starting_version == 2 else 1
        )
        if starting_version == 2:
            assert upgraded.execute("SELECT COUNT(*) FROM feature_value_fact").fetchone()[0] == 1
            assert (
                upgraded.execute(
                    """SELECT representative_observation_id FROM feature_value_fact
                   WHERE feature_value_id = 'old-value'"""
                ).fetchone()[0]
                == "obs-upgrade"
            )
            assert not upgraded.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("feature_value_contributor_legacy",),
            ).fetchone()
            assert (
                upgraded.execute(
                    "SELECT COUNT(*) FROM dataset_build_finalization WHERE build_id = 'old-build'"
                ).fetchone()[0]
                == 1
            )
