from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from asro.evidence import (
    CanonicalFactAssignment,
    EconomicScope,
    EvidenceRepository,
    FactStatus,
    FeatureDefinitionV2,
    ObservationV2,
    SourceTier,
)
from asro.evidence.time import normalize_timestamp
from asro.migrations.runner import Migration, apply_migrations
from asro.models import EventType, FinancialEvent, SourceItem
from asro.scoring import score
from asro.storage import SqliteRepository


def _feature(**overrides: object) -> FeatureDefinitionV2:
    values: dict[str, object] = {
        "feature_key": "ai_related_debt",
        "feature_version": "1.0.0",
        "definition_json": '{"unit":"currency","role":"vulnerability"}',
        "released_at": "2026-01-01",
    }
    values.update(overrides)
    return FeatureDefinitionV2.model_validate(values)


def _source_and_event(repo: SqliteRepository, connection: sqlite3.Connection) -> str:
    item = score(
        SourceItem(
            title="Company reports financing terms",
            url="https://example.com/filing",
            source="Primary filing",
            published_at="2026-03-31",
        ),
        [],
    )
    assert repo.insert(connection, item)
    event = FinancialEvent(
        event_id="event-1",
        document_id=item.item_id,
        event_type=EventType.ISSUES_DEBT,
        source_entity="company-a",
        amount=500_000_000,
        currency="USD",
        effective_date="2026-03-30",
        confidence=0.91,
        evidence_text="The company issued $500 million of senior notes.",
        extractor="deterministic-v2",
    )
    assert repo.insert_event(connection, event)
    assert EvidenceRepository.register_canonical_fact(connection, "fact-debt-1")
    assert EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment(
            assignment_id="assignment-1",
            event_id="event-1",
            canonical_fact_id="fact-debt-1",
            available_at="2026-03-31T12:00:00Z",
            assigned_by="test",
            assignment_method="manual_review",
        ),
    )
    assert EvidenceRepository.register_feature(connection, _feature())
    return item.item_id


def _observation(source_id: str, **overrides: object) -> ObservationV2:
    values: dict[str, object] = {
        "observation_id": "obs-1",
        "event_id": "event-1",
        "source_document_id": source_id,
        "source_locator": "page 7, debt footnote",
        "evidence_text": "The company issued $500 million of senior notes.",
        "entity_id": "company-a",
        "entity_role": "issuer",
        "feature_key": "ai_related_debt",
        "feature_version": "1.0.0",
        "value_numeric": 500_000_000,
        "unit": "currency",
        "currency": "USD",
        "economic_scope": EconomicScope.ENTITY,
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
        "event_at": "2026-03-30",
        "published_at": "Tue, 31 Mar 2026 08:00:00 -0400",
        "availability_at": "2026-03-31T12:05:00Z",
        "extracted_at": "2026-03-31T12:06:00Z",
        "fact_status": FactStatus.DIRECT,
        "source_tier": SourceTier.PRIMARY,
        "source_quality": 0.98,
        "extraction_confidence": 0.91,
        "review_confidence": 0.95,
        "extractor_name": "deterministic",
        "extractor_version": "2.0.0",
    }
    values.update(overrides)
    return ObservationV2.model_validate(values)


def _direct_values(observation: ObservationV2) -> dict[str, object]:
    values: dict[str, object] = observation.model_dump(mode="json")
    values["derivation_inputs"] = json.dumps(values["derivation_inputs"])
    for field in (
        "period_start",
        "period_end",
        "event_at",
        "published_at",
        "availability_at",
        "extracted_at",
    ):
        value = values[field]
        if isinstance(value, str) and value.endswith("Z"):
            values[field] = f"{value[:-1]}+00:00"
    return values


def test_normalize_timestamp_requires_timezone_but_accepts_explicit_date_precision() -> None:
    assert normalize_timestamp("2026-03-31") == datetime(2026, 3, 31, tzinfo=UTC)
    assert normalize_timestamp(date(2026, 3, 31)) == datetime(2026, 3, 31, tzinfo=UTC)
    assert normalize_timestamp("Tue, 31 Mar 2026 08:00:00 -0400") == datetime(
        2026, 3, 31, 12, tzinfo=UTC
    )
    with pytest.raises(ValueError, match="explicit timezone"):
        normalize_timestamp(datetime(2026, 3, 31, 4))
    with pytest.raises(ValueError, match="explicit timezone"):
        normalize_timestamp("2026-03-31T04:00:00")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_observation_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValidationError):
        _observation("source", value_numeric=value)


def test_observation_enforces_exclusive_values_currency_and_temporal_semantics() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        _observation("source", value_text="also text")
    with pytest.raises(ValidationError, match="exactly one"):
        _observation("source", value_numeric=None)
    with pytest.raises(ValidationError, match="numeric observations require a unit"):
        _observation("source", unit=None, currency=None)
    with pytest.raises(ValidationError, match="must appear together"):
        _observation("source", currency=None)
    with pytest.raises(ValidationError, match="availability_at cannot precede"):
        _observation("source", availability_at="2026-03-31T11:59:00Z")
    with pytest.raises(ValidationError, match="period_end cannot precede"):
        _observation("source", period_end="2025-12-31")
    with pytest.raises(ValidationError, match="extracted_at cannot precede"):
        _observation("source", extracted_at="2026-03-31T12:04:59Z")
    with pytest.raises(ValidationError, match="period and economic scope"):
        _observation("source", economic_scope=None)
    with pytest.raises(ValidationError, match="period and economic scope"):
        _observation("source", period_start=None)


def test_time_precision_must_match_timestamp_presence_and_original_input() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        _observation("source", event_time_precision="second")
    with pytest.raises(ValidationError, match="does not match"):
        _observation("source", published_time_precision="date")
    with pytest.raises(ValidationError, match="requires event_at"):
        _observation("source", event_at=None, event_time_precision="date")


def test_classification_specific_provenance_is_required() -> None:
    with pytest.raises(ValidationError, match="method and derivation inputs"):
        _observation("source", fact_status=FactStatus.INFERRED)
    with pytest.raises(ValidationError, match="estimation model"):
        _observation(
            "source",
            fact_status=FactStatus.ESTIMATED,
            derivation_method="ratio",
            derivation_inputs=["obs-a", "obs-b"],
        )
    with pytest.raises(ValidationError, match="dispute reason"):
        _observation("source", fact_status=FactStatus.DISPUTED)
    with pytest.raises(ValidationError, match="valid JSON"):
        _feature(definition_json="not-json")
    with pytest.raises(ValidationError, match="JSON object"):
        _feature(definition_json="[]")


def test_v2_round_trip_preserves_provenance_confidences_and_precision(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        source_id = _source_and_event(repo, connection)
        observation = _observation(source_id)
        assert EvidenceRepository.insert(connection, observation)
        assert not EvidenceRepository.insert(connection, observation)
        connection.commit()
        stored = EvidenceRepository.get(connection, observation.observation_id)
        assert stored == observation
        assert stored is not None
        assert stored.source_quality == 0.98
        assert stored.extraction_confidence == 0.91
        assert stored.review_confidence == 0.95
        assert stored.event_time_precision == "date"
        assert stored.published_time_precision == "second"


def test_as_of_normalizes_equivalent_instants_offsets_datetime_and_date(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        source_id = _source_and_event(repo, connection)
        assert EvidenceRepository.insert(connection, _observation(source_id))
        connection.commit()
        cutoffs: list[str | date | datetime] = [
            "2026-03-31T12:05:00Z",
            "2026-03-31T14:05:00+02:00",
            "2026-03-31T07:05:00-05:00",
            datetime(2026, 3, 31, 8, 5, tzinfo=timezone(timedelta(hours=-4))),
            date(2026, 4, 1),
        ]
        for cutoff in cutoffs:
            assert [row.observation_id for row in EvidenceRepository.as_of(connection, cutoff)] == [
                "obs-1"
            ]


def test_corrections_are_append_only_and_as_of_queries_are_temporally_honest(
    tmp_path: Path,
) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        source_id = _source_and_event(repo, connection)
        original = _observation(source_id)
        correction = _observation(
            source_id,
            observation_id="obs-2",
            supersedes_observation_id=original.observation_id,
            value_numeric=480_000_000,
            published_at="2026-04-02T14:00:00Z",
            availability_at="2026-04-02T14:05:00Z",
            extracted_at="2026-04-02T14:06:00Z",
        )
        assert EvidenceRepository.insert(connection, original)
        assert EvidenceRepository.insert(connection, correction)
        connection.commit()
        before = EvidenceRepository.as_of(connection, "2026-04-01T00:00:00Z")
        after = EvidenceRepository.as_of(connection, "2026-04-03T00:00:00Z")
        assert [row.observation_id for row in before] == ["obs-1"]
        assert [row.observation_id for row in after] == ["obs-2"]
        assert EvidenceRepository.get(connection, "obs-1") == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "event-other"),
        ("source_document_id", "source-other"),
        ("entity_id", "company-b"),
        ("counterparty_entity_id", "bank-b"),
        ("entity_role", "borrower"),
        ("feature_key", "other_feature"),
        ("feature_version", "2.0.0"),
        ("period_start", "2026-02-01"),
        ("period_end", "2026-04-30"),
        ("unit", "USD"),
        ("currency", "EUR"),
        ("denominator_feature_key", "cash_flow"),
        ("economic_scope", "ecosystem"),
    ],
)
def test_correction_cannot_change_fact_identity(tmp_path: Path, field: str, value: str) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        source_id = _source_and_event(repo, connection)
        original = _observation(source_id)
        assert EvidenceRepository.insert(connection, original)
        changes: dict[str, object] = {
            "observation_id": "obs-2",
            "supersedes_observation_id": "obs-1",
            "published_at": "2026-04-02T14:00:00Z",
            "availability_at": "2026-04-02T14:05:00Z",
            "extracted_at": "2026-04-02T14:06:00Z",
            field: value,
        }
        if field == "unit":
            changes["currency"] = None
        if field == "feature_key":
            EvidenceRepository.register_feature(connection, _feature(feature_key="other_feature"))
        if field == "feature_version":
            EvidenceRepository.register_feature(connection, _feature(feature_version="2.0.0"))
        correction = _observation(source_id, **changes)
        with pytest.raises(ValueError, match="immutable identity"):
            EvidenceRepository.insert(connection, correction)


def test_correction_requires_monotonic_times_existing_parent_and_no_self_reference(
    tmp_path: Path,
) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        source_id = _source_and_event(repo, connection)
        original = _observation(source_id)
        assert EvidenceRepository.insert(connection, original)
        with pytest.raises(ValueError, match="supersede itself"):
            EvidenceRepository.insert(
                connection,
                _observation(source_id, supersedes_observation_id="obs-1"),
            )
        with pytest.raises(ValueError, match="does not exist"):
            EvidenceRepository.insert(
                connection,
                _observation(
                    source_id,
                    observation_id="obs-x",
                    supersedes_observation_id="missing",
                ),
            )
        with pytest.raises(ValueError, match="availability cannot precede"):
            EvidenceRepository.insert(
                connection,
                _observation(
                    source_id,
                    observation_id="obs-early",
                    supersedes_observation_id="obs-1",
                    availability_at="2026-03-31T12:04:00Z",
                ),
            )
        with pytest.raises(ValueError, match="extraction cannot precede"):
            EvidenceRepository.insert(
                connection,
                _observation(
                    source_id,
                    observation_id="obs-extract",
                    supersedes_observation_id="obs-1",
                    extracted_at="2026-03-31T12:05:30Z",
                ),
            )


def test_foreign_keys_reject_orphan_source_event_review_supersession_and_feature(
    tmp_path: Path,
) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        source_id = _source_and_event(repo, connection)
        base = _direct_values(_observation(source_id))
        columns = ", ".join(base)
        placeholders = ", ".join(f":{key}" for key in base)
        sql = f"INSERT INTO observation_v2 ({columns}) VALUES ({placeholders})"
        for field, value in [
            ("source_document_id", "missing-source"),
            ("event_id", "missing-event"),
            ("review_id", 9999),
            ("supersedes_observation_id", "missing-parent"),
            ("feature_version", "missing-version"),
        ]:
            invalid = dict(base)
            invalid[field] = value
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                connection.execute(sql, invalid)


def test_database_enforces_append_only_and_direct_value_constraints(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        source_id = _source_and_event(repo, connection)
        assert EvidenceRepository.insert(connection, _observation(source_id))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE observation_v2 SET value_numeric = 1 WHERE observation_id = 'obs-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM observation_v2 WHERE observation_id = 'obs-1'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE feature_definition SET definition_json = '{}'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM feature_definition")


def test_direct_sql_cannot_bypass_value_or_correction_rules(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        source_id = _source_and_event(repo, connection)
        original = _observation(source_id)
        assert EvidenceRepository.insert(connection, original)
        base = _direct_values(_observation(source_id, observation_id="direct"))
        columns = ", ".join(base)
        placeholders = ", ".join(f":{key}" for key in base)
        sql = f"INSERT INTO observation_v2 ({columns}) VALUES ({placeholders})"

        invalid_rows = []
        both = dict(base, observation_id="both", value_text="also text")
        invalid_rows.append(both)
        blank = dict(base, observation_id="blank", value_numeric=None, value_text="   ")
        invalid_rows.append(blank)
        infinite = dict(base, observation_id="infinite", value_numeric=float("inf"))
        invalid_rows.append(infinite)
        currency = dict(base, observation_id="currency", currency=None)
        invalid_rows.append(currency)
        inferred = dict(base, observation_id="inferred", fact_status="inferred")
        invalid_rows.append(inferred)
        extracted = dict(
            base,
            observation_id="extracted",
            extracted_at="2026-03-31T12:04:59+00:00",
        )
        invalid_rows.append(extracted)
        no_period = dict(base, observation_id="no-period", period_start=None)
        invalid_rows.append(no_period)
        no_scope = dict(base, observation_id="no-scope", economic_scope=None)
        invalid_rows.append(no_scope)
        event_precision = dict(
            base,
            observation_id="event-precision",
            event_time_precision=None,
        )
        invalid_rows.append(event_precision)
        published_precision = dict(
            base,
            observation_id="published-precision",
            published_time_precision="date",
        )
        invalid_rows.append(published_precision)
        availability_precision = dict(
            base,
            observation_id="availability-precision",
            availability_time_precision="date",
        )
        invalid_rows.append(availability_precision)
        malformed_inputs = dict(
            base,
            observation_id="malformed-inputs",
            derivation_inputs="not-json",
        )
        invalid_rows.append(malformed_inputs)
        for invalid in invalid_rows:
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
                connection.execute(sql, invalid)

        unrelated = dict(
            base,
            observation_id="unrelated",
            supersedes_observation_id="obs-1",
            entity_id="company-b",
            published_at="2026-04-02T14:00:00+00:00",
            availability_at="2026-04-02T14:05:00+00:00",
            extracted_at="2026-04-02T14:06:00+00:00",
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable identity"):
            connection.execute(sql, unrelated)

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(
                """INSERT INTO feature_definition VALUES (
                    'bad', '1', 'not-json', '2026-01-01T00:00:00+00:00', NULL
                )"""
            )


def test_feature_versions_are_registered_and_immutable(tmp_path: Path) -> None:
    repo = SqliteRepository(tmp_path / "monitor.db")
    with repo.connect() as connection:
        source_id = _source_and_event(repo, connection)
        assert not EvidenceRepository.register_feature(connection, _feature())
        with pytest.raises(ValueError, match="different semantics"):
            EvidenceRepository.register_feature(
                connection, _feature(definition_json='{"changed":true}')
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            EvidenceRepository.insert(
                connection, _observation(source_id, feature_version="not-registered")
            )


def test_genuine_pre_v2_database_is_migrated_without_losing_legacy_data(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY, discovered_at TEXT NOT NULL, published_at TEXT,
            source TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
            summary TEXT NOT NULL, score INTEGER NOT NULL, category TEXT NOT NULL,
            companies TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """INSERT INTO items VALUES (
            'legacy', '2025-01-01', NULL, 'SEC', 'Title', 'url', '',
            1, 'General AI capital', '[]'
        )"""
    )
    connection.commit()
    connection.close()

    with SqliteRepository(database).connect() as upgraded:
        assert upgraded.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
        assert [
            row[0]
            for row in upgraded.execute("SELECT version FROM schema_migrations ORDER BY version")
        ] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
        assert upgraded.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observation_v2'"
        ).fetchone()


def test_migration_failure_rolls_back_and_partial_schema_is_detected() -> None:
    connection = sqlite3.connect(":memory:")
    broken = Migration(
        version=99,
        name="broken",
        statements=("CREATE TABLE should_rollback(id INTEGER)", "NOT VALID SQL"),
    )
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(connection, (broken,))
    assert not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='should_rollback'"
    ).fetchone()
    assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=99").fetchone()

    partial = sqlite3.connect(":memory:")
    partial.execute("CREATE TABLE observation_v2(id TEXT)")
    with pytest.raises(RuntimeError, match="partially created"):
        apply_migrations(partial)

    obsolete = sqlite3.connect(":memory:")
    obsolete.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
    )
    obsolete.execute(
        "INSERT INTO schema_migrations VALUES (1, 'v2_evidence_foundation', CURRENT_TIMESTAMP)"
    )
    obsolete.execute("CREATE TABLE observation_v2(observation_id TEXT)")
    with pytest.raises(RuntimeError, match="obsolete or partially created"):
        apply_migrations(obsolete)
