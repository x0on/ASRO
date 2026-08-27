from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from asro.backfill.acceptance_queue import build_acceptance_queue
from asro.evidence import (
    CanonicalFactAssignment,
    EconomicScope,
    EvidenceRepository,
    FactStatus,
    FeatureDefinitionV2,
    ObservationV2,
    SourceTier,
)
from asro.models import EventType, FinancialEvent, SourceItem
from asro.scoring import score
from asro.storage import SqliteRepository


def _queue_files(tmp_path: Path, **updates: object) -> tuple[Path, Path]:
    acquired = tmp_path / "acquired"
    acquired.mkdir()
    content = b"SEC filing states total contract value is $5.5 billion for AI workloads."
    digest = hashlib.sha256(content).hexdigest()
    (acquired / f"{digest}.bin").write_bytes(content)
    receipt = {
        "id": "lead-one",
        "final_url": "https://www.sec.gov/Archives/test.htm",
        "public_availability_at": "2025-11-03T00:00:00Z",
        "content_sha256": digest,
        "content_file": f"{digest}.bin",
    }
    (acquired / "acquisition-receipts.json").write_text(
        json.dumps({"receipts": [receipt]}), encoding="utf-8"
    )
    document = {
        "id": "lead-one",
        "url": receipt["final_url"],
        "entity": "Amazon",
        "counterparty": "Cipher",
        "entity_role": "customer",
        "counterparty_role": "provider",
        "feature_key": "ai_compute_contract_value_flow",
        "feature_version": "1.0.0",
        "candidate_value_numeric": 5_500_000_000,
        "unit": "currency",
        "currency": "USD",
        "event_at": "2025-11-03",
        "row_period_end": "2025-11-30",
        "passage_locator": "Exhibit 99.1",
        "passage_marker": "$5.5 billion",
        "passage_text": "Total contract value is $5.5 billion for AI workloads.",
    }
    document.update(updates)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "status": "candidate_acquisition_only",
                "documents": [document],
                "controls": [],
            }
        ),
        encoding="utf-8",
    )
    return manifest, acquired


def test_queue_is_deterministic_idempotent_and_never_promotes(tmp_path: Path) -> None:
    manifest, acquired = _queue_files(tmp_path)
    with SqliteRepository(tmp_path / "queue.db").connect() as connection:
        first = build_acceptance_queue(connection, manifest, acquired)
        second = build_acceptance_queue(connection, manifest, acquired)
        assert second == first
        assert first["status_counts"] == {
            "pending_review": 1,
            "duplicate_fact": 0,
            "rejected": 0,
        }
        assert first["auto_promoted"] == 0
        assert connection.execute("SELECT COUNT(*) FROM observation_v2").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM acceptance_queue_run").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"passage_marker": "not in document"}, "supporting passage"),
        ({"candidate_value_numeric": None}, "finite numeric value"),
        ({"counterparty": "Amazon"}, "roles are ambiguous"),
    ],
)
def test_queue_rejects_incomplete_candidate_fields(
    tmp_path: Path, updates: dict[str, object], message: str
) -> None:
    manifest, acquired = _queue_files(tmp_path, **updates)
    with (
        SqliteRepository(tmp_path / "queue.db").connect() as connection,
        pytest.raises(ValueError, match=message),
    ):
        build_acceptance_queue(connection, manifest, acquired)


def test_queue_rejects_unsupported_host(tmp_path: Path) -> None:
    manifest, acquired = _queue_files(tmp_path)
    receipts = json.loads((acquired / "acquisition-receipts.json").read_text())
    receipts["receipts"][0]["final_url"] = "https://example.com/claim"
    (acquired / "acquisition-receipts.json").write_text(json.dumps(receipts))
    with (
        SqliteRepository(tmp_path / "queue.db").connect() as connection,
        pytest.raises(ValueError, match="unsupported authoritative host"),
    ):
        build_acceptance_queue(connection, manifest, acquired)


def test_queue_marks_post_cutoff_evidence_rejected(tmp_path: Path) -> None:
    manifest, acquired = _queue_files(tmp_path, row_period_end="2025-10-31")
    with SqliteRepository(tmp_path / "queue.db").connect() as connection:
        report = build_acceptance_queue(connection, manifest, acquired)
        assert report["status_counts"]["rejected"] == 1
        assert report["items"][0]["rejection_reason"] == (
            "public availability is after the requested row cutoff"
        )


def test_queue_marks_existing_canonical_fact_duplicate(tmp_path: Path) -> None:
    manifest, acquired = _queue_files(tmp_path)
    repository = SqliteRepository(tmp_path / "queue.db")
    with repository.connect() as connection:
        assert EvidenceRepository.register_feature(
            connection,
            FeatureDefinitionV2.model_validate(
                {
                    "feature_key": "ai_compute_contract_value_flow",
                    "feature_version": "1.0.0",
                    "definition_json": json.dumps(
                        {
                            "aggregation": "sum",
                            "unit": "currency",
                            "expected_facts_per_period": 1,
                        }
                    ),
                    "released_at": "2025-01-01",
                }
            ),
        )
        item = score(
            SourceItem(
                title="SEC filing",
                url="https://www.sec.gov/Archives/test.htm",
                source="SEC",
                published_at="2025-11-03",
            ),
            ["Amazon", "Cipher"],
        )
        assert repository.insert(connection, item)
        assert repository.insert_event(
            connection,
            FinancialEvent.model_validate(
                {
                    "event_id": "event-existing",
                    "document_id": item.item_id,
                    "event_type": EventType.CAPEX_COMMITMENT,
                    "source_entity": "Amazon",
                    "target_entity": "Cipher",
                    "amount": 5_500_000_000,
                    "currency": "USD",
                    "effective_date": "2025-11-03",
                    "confidence": 1.0,
                    "evidence_text": "Existing fact",
                    "extractor": "test",
                }
            ),
        )
        assert EvidenceRepository.register_canonical_fact(connection, "fact-existing")
        assert EvidenceRepository.assign_canonical_fact(
            connection,
            CanonicalFactAssignment.model_validate(
                {
                    "assignment_id": "assignment-existing",
                    "event_id": "event-existing",
                    "canonical_fact_id": "fact-existing",
                    "available_at": "2025-11-03T00:00:00Z",
                    "assigned_by": "test",
                    "assignment_method": "manual",
                }
            ),
        )
        assert EvidenceRepository.insert(
            connection,
            ObservationV2.model_validate(
                {
                    "observation_id": "observation-existing",
                    "event_id": "event-existing",
                    "source_document_id": item.item_id,
                    "source_locator": "filing",
                    "evidence_text": "Existing fact",
                    "entity_id": "Amazon",
                    "counterparty_entity_id": "Cipher",
                    "entity_role": "customer",
                    "feature_key": "ai_compute_contract_value_flow",
                    "feature_version": "1.0.0",
                    "value_numeric": 5_500_000_000,
                    "unit": "currency",
                    "currency": "USD",
                    "economic_scope": EconomicScope.ENTITY,
                    "period_start": "2025-11-01",
                    "period_end": "2025-11-30",
                    "event_at": "2025-11-03",
                    "published_at": "2025-11-03",
                    "availability_at": "2025-11-03",
                    "extracted_at": "2025-11-03T01:00:00Z",
                    "fact_status": FactStatus.DIRECT,
                    "source_tier": SourceTier.PRIMARY,
                    "source_quality": 1.0,
                    "extraction_confidence": 1.0,
                    "review_confidence": 1.0,
                    "extractor_name": "test",
                    "extractor_version": "1",
                }
            ),
        )
        connection.commit()
        report = build_acceptance_queue(connection, manifest, acquired)
        assert report["status_counts"]["duplicate_fact"] == 1
        assert report["items"][0]["canonical_fact_match"] == "fact-existing"
