from __future__ import annotations

import calendar
import csv
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asro.backfill.candidate import ingest_candidate_package
from asro.backfill.controls import ControlObservation, register_control_observation
from asro.backfill.manifest import EpisodeManifest
from asro.backfill.runner import BackfillRunner
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
from asro.models import EventType, FinancialEvent, SourceItem
from asro.scoring import score
from asro.storage import SqliteRepository

_SEC_URL = "https://www.sec.gov/Archives/edgar/data/1326801/000119312525261217/d762778d424b2.htm"
_CANDIDATE_EVENT_ID = "evt-meta-30bn-bond-20251030-01"
_EVIDENCE = (
    "Meta Platforms, Inc. offered $30,000,000,000 aggregate principal amount "
    "of senior notes in six tranches."
)


def build_current_ai_acceptance_slice(
    connection: sqlite3.Connection,
    *,
    candidate_directory: Path,
    sec_document: Path,
    control_files: dict[str, Path],
    manifest_path: Path,
    code_commit: str,
) -> dict[str, object]:
    """Build the bounded, reviewed Meta October-2025 acceptance slice."""
    manifest = EpisodeManifest.from_toml(manifest_path)
    package = ingest_candidate_package(connection, candidate_directory)
    content = sec_document.read_text(encoding="utf-8")
    fetched_at = _file_time(sec_document)
    review_time = datetime.now(UTC).replace(microsecond=0).isoformat()
    if review_time >= manifest.availability_cutoff.isoformat():
        raise ValueError("slice review occurred after its declared availability cutoff")

    item = score(
        SourceItem.model_validate(
            {
                "title": "Meta Platforms $30 billion senior notes prospectus supplement",
                "url": _SEC_URL,
                "source": "SEC",
                "summary": _EVIDENCE,
                "published_at": "2025-10-30",
                "discovered_at": fetched_at,
            }
        ),
        ["Meta"],
    )
    repository = SqliteRepository(Path("unused"))
    repository.insert(connection, item)
    repository.upsert_document(connection, item.item_id, fetched_at, "text/html", "ok", content)
    event_id = "accepted-meta-30b-notes-2025-10-30"
    repository.insert_event(
        connection,
        FinancialEvent.model_validate(
            {
                "event_id": event_id,
                "document_id": item.item_id,
                "event_type": EventType.ISSUES_DEBT,
                "source_entity": "Meta",
                "amount": 30_000_000_000,
                "currency": "USD",
                "instrument": "six senior-note tranches",
                "effective_date": "2025-10-30",
                "confidence": 1.0,
                "evidence_text": _EVIDENCE,
                "extractor": "asro-v2-manual-acceptance",
                "processed_at": review_time,
            }
        ),
    )
    _register_features(connection)
    review_id = _accepted_review(connection, event_id, review_time)
    fact_id = "fact-meta-30b-senior-notes-2025-10-30"
    EvidenceRepository.register_canonical_fact(connection, fact_id)
    assignment_id = "assignment-meta-30b-senior-notes-2025-10-30"
    EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment.model_validate(
            {
                "assignment_id": assignment_id,
                "event_id": event_id,
                "canonical_fact_id": fact_id,
                "available_at": review_time,
                "reviewer_id": review_id,
                "assigned_by": "human-acceptance-review",
                "assignment_method": "full-document-manual-review",
                "provenance": {
                    "candidate_event_id": _CANDIDATE_EVENT_ID,
                    "source_url": _SEC_URL,
                },
                "created_at": review_time,
            }
        ),
    )
    observation_id = "observation-meta-30b-senior-notes-2025-10-30"
    EvidenceRepository.insert(
        connection,
        ObservationV2.model_validate(
            {
                "observation_id": observation_id,
                "event_id": event_id,
                "source_document_id": item.item_id,
                "source_locator": "$30,000,000,000 cover and Description of Notes",
                "evidence_text": _EVIDENCE,
                "entity_id": "Meta",
                "entity_role": "issuer",
                "feature_key": "ai_related_debt",
                "feature_version": "1.0.0",
                "value_numeric": 30_000_000_000,
                "unit": "currency",
                "currency": "USD",
                "economic_scope": EconomicScope.ENTITY,
                "period_start": "2025-10-01",
                "period_end": "2025-10-31",
                "event_at": "2025-10-30",
                "published_at": "2025-10-30",
                "availability_at": "2025-10-30",
                "extracted_at": review_time,
                "fact_status": FactStatus.DIRECT,
                "source_tier": SourceTier.PRIMARY,
                "source_quality": 1.0,
                "extraction_confidence": 1.0,
                "review_confidence": 1.0,
                "extractor_name": "manual-full-document-review",
                "extractor_version": "2.0.0",
                "review_id": review_id,
            }
        ),
    )
    _promote_candidate(
        connection,
        package.package_id,
        item.item_id,
        content,
        fetched_at,
        observation_id,
        fact_id,
        review_time,
    )
    _register_control_definitions(connection, manifest, review_time)
    _register_controls(connection, control_files, fetched_at)
    connection.commit()

    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [
            FeatureSpec(
                feature_key="ai_related_debt",
                feature_version="1.0.0",
                aggregation=Aggregation.SUM,
                unit="currency",
                expected_facts_per_period=1,
            )
        ],
        manifest.availability_cutoff,
        manifest.entities,
        code_commit,
        manifest.feature_set_version,
        manifest.period_start,
        manifest.period_end,
    )
    ecosystem_build = EcosystemFeatureStoreBuilder(connection).build_months(
        entity_build.build_id,
        [
            EcosystemFeatureSpec(
                source_feature_key="ai_related_debt",
                source_feature_version="1.0.0",
                feature_key="ecosystem_ai_related_debt",
                feature_version="1.0.0",
                aggregation=Aggregation.SUM,
                unit="currency",
            )
        ],
        code_commit,
        manifest.feature_set_version,
    )
    result = BackfillRunner(connection).run(
        manifest, entity_build.build_id, ecosystem_build.build_id
    )
    cells = [
        dict(row)
        for row in connection.execute(
            """SELECT entity_id,period_start,period_end,dimension,requirement_key,
                      requirement_version,present,missingness_reason
               FROM backfill_coverage_cell WHERE run_id=?
               ORDER BY dimension,entity_id,period_start,requirement_key""",
            (result.run_id,),
        )
    ]
    return {
        "episode_id": manifest.episode_id,
        "episode_version": manifest.version,
        "scope": {"entities": manifest.entities, "months": ["2025-10"], "features": 1},
        "source_document": {
            "url": _SEC_URL,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "fetched_at": fetched_at,
            "public_availability_at": "2025-10-30T00:00:00+00:00",
        },
        "entity_build_id": entity_build.build_id,
        "ecosystem_build_id": ecosystem_build.build_id,
        "backfill_run_id": result.run_id,
        "coverage_passed": result.coverage_passed,
        "leakage_passed": result.leakage_passed,
        "accepted_cells": [cell for cell in cells if cell["present"]],
        "missing_cells": [cell for cell in cells if not cell["present"]],
        "full_episode_remaining": {
            "accepted_entity_month_feature_cells": 1,
            "required_entity_month_feature_cells": 330,
            "remaining_entity_month_feature_cells": 329,
            "all_other_episodes_accepted": False,
        },
    }


def build_current_ai_4x6_matrix(
    connection: sqlite3.Connection,
    *,
    control_files: dict[str, Path],
    manifest_path: Path,
    code_commit: str,
) -> dict[str, object]:
    """Build the exact four-entity, six-month matrix without filling evidence gaps."""
    manifest = EpisodeManifest.from_toml(manifest_path)
    expected_entities = ["Alphabet", "Amazon", "Meta", "Microsoft"]
    if manifest.entities != expected_entities:
        raise ValueError("4x6 slice requires the approved exact entity set")
    months = ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]
    if manifest.period_start.isoformat() != "2025-07-01" or manifest.period_end.isoformat() != (
        "2025-12-31"
    ):
        raise ValueError("4x6 slice requires the approved exact month window")
    accepted = connection.execute(
        """SELECT 1 FROM observation_v2
           WHERE entity_id='Meta' AND feature_key='ai_related_debt'
             AND feature_version='1.0.0'
             AND substr(period_start,1,10)='2025-10-01'
             AND substr(period_end,1,10)='2025-10-31' AND review_id IS NOT NULL"""
    ).fetchone()
    if accepted is None:
        raise ValueError("reviewed Meta October evidence must be persisted before building 4x6")
    registered_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    _register_features(connection)
    _register_control_definitions(connection, manifest, registered_at)
    _register_controls_for_months(connection, control_files, months)
    connection.commit()
    feature_spec = FeatureSpec(
        feature_key="ai_related_debt",
        feature_version="1.0.0",
        aggregation=Aggregation.SUM,
        unit="currency",
        expected_facts_per_period=1,
    )
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        [feature_spec],
        manifest.availability_cutoff,
        manifest.entities,
        code_commit,
        manifest.feature_set_version,
        manifest.period_start,
        manifest.period_end,
    )
    ecosystem_build = EcosystemFeatureStoreBuilder(connection).build_months(
        entity_build.build_id,
        [
            EcosystemFeatureSpec(
                source_feature_key="ai_related_debt",
                source_feature_version="1.0.0",
                feature_key="ecosystem_ai_related_debt",
                feature_version="1.0.0",
                aggregation=Aggregation.SUM,
                unit="currency",
            )
        ],
        code_commit,
        manifest.feature_set_version,
    )
    result = BackfillRunner(connection).run(
        manifest, entity_build.build_id, ecosystem_build.build_id
    )
    rows = list(
        connection.execute(
            """SELECT entity_id,period_start,dimension,requirement_key,present,
                      missingness_reason
               FROM backfill_coverage_cell WHERE run_id=?
               ORDER BY dimension,entity_id,period_start,requirement_key""",
            (result.run_id,),
        )
    )
    dimensions: dict[str, dict[str, int]] = {}
    for dimension in ("feature", "source", "control"):
        selected = [row for row in rows if str(row["dimension"]) == dimension]
        dimensions[dimension] = {
            "accepted": sum(int(row["present"]) for row in selected),
            "missing": sum(not bool(row["present"]) for row in selected),
            "required": len(selected),
        }
    return {
        "episode_id": manifest.episode_id,
        "episode_version": manifest.version,
        "entities": manifest.entities,
        "months": months,
        "entity_build_id": entity_build.build_id,
        "ecosystem_build_id": ecosystem_build.build_id,
        "backfill_run_id": result.run_id,
        "coverage_passed": result.coverage_passed,
        "leakage_passed": result.leakage_passed,
        "cells": dimensions,
        "accepted_cells": [dict(row) for row in rows if row["present"]],
        "missing_cells": [dict(row) for row in rows if not row["present"]],
    }


def _register_features(connection: sqlite3.Connection) -> None:
    definitions = (
        (
            "ai_related_debt",
            {
                "aggregation": "sum",
                "unit": "currency",
                "grain": "entity_month",
                "expected_facts_per_period": 1,
            },
        ),
        (
            "ecosystem_ai_related_debt",
            {"aggregation": "sum", "unit": "currency", "grain": "ecosystem_month"},
        ),
    )
    for key, semantics in definitions:
        EvidenceRepository.register_feature(
            connection,
            FeatureDefinitionV2.model_validate(
                {
                    "feature_key": key,
                    "feature_version": "1.0.0",
                    "definition_json": json.dumps(semantics, sort_keys=True, separators=(",", ":")),
                    "released_at": "2025-01-01",
                }
            ),
        )


_FEATURE_FAMILY_FACTS = (
    {
        "receipt_id": "terawulf-fluidstack-google-2025-08",
        "entity": "Alphabet",
        "counterparty": "TeraWulf",
        "feature_key": "ai_contingent_credit_support_stock",
        "amount": 3_200_000_000,
        "event_date": "2025-08-18",
        "event_type": EventType.GUARANTEES,
        "locator": "Exhibit 99.1: Google Increases Backstop to $3.2 Billion",
        "evidence_marker": "$3.2 billion",
        "evidence": (
            "With this incremental commitment, Google's total backstop increases to "
            "approximately $3.2 billion."
        ),
    },
    {
        "receipt_id": "coreweave-meta-2025-09-8k",
        "entity": "Meta",
        "counterparty": "CoreWeave",
        "feature_key": "ai_compute_contract_value_flow",
        "amount": 14_200_000_000,
        "event_date": "2025-09-25",
        "event_type": EventType.CAPEX_COMMITMENT,
        "locator": "Item 1.01 Material Definitive Agreement",
        "evidence_marker": "$14.2 billion",
        "evidence": (
            "Meta has initially committed to pay the Company up to approximately $14.2 "
            "billion through December 14, 2031 under the Order Form."
        ),
    },
    {
        "receipt_id": "nebius-microsoft-2025-09-6k",
        "entity": "Microsoft",
        "counterparty": "Nebius",
        "feature_key": "ai_compute_contract_value_flow",
        "amount": 17_400_000_000,
        "event_date": "2025-09-08",
        "event_type": EventType.CAPEX_COMMITMENT,
        "locator": "Commercial Agreement with Microsoft",
        "evidence_marker": "$17.4 billion",
        "evidence": (
            "Subject to deployment and availability of the GPU Services, the total contract "
            "value is about $17.4 billion through 2031."
        ),
    },
    {
        "receipt_id": "iren-microsoft-2025-11-8k",
        "entity": "Microsoft",
        "counterparty": "IREN",
        "feature_key": "ai_compute_contract_value_flow",
        "amount": 9_700_000_000,
        "event_date": "2025-11-03",
        "event_type": EventType.CAPEX_COMMITMENT,
        "locator": "Item 1.01 Microsoft Agreement",
        "evidence_marker": "$9.7 billion",
        "evidence": "The contract value is approximately $9.7 billion through 2031.",
    },
    {
        "receipt_id": "nebius-meta-2025-11-6k",
        "entity": "Meta",
        "counterparty": "Nebius",
        "feature_key": "ai_compute_contract_value_flow",
        "amount": 2_900_000_000,
        "event_date": "2025-11-01",
        "event_type": EventType.CAPEX_COMMITMENT,
        "locator": "Commercial Agreement with Meta",
        "evidence_marker": "$2.9 billion",
        "evidence": (
            "The Order has a total contract value of approximately $2.9 billion for two "
            "dedicated GPU infrastructure capacity clusters over a five-year term."
        ),
    },
    {
        "receipt_id": "cipher-amazon-2025-11-8k-exhibit",
        "entity": "Amazon",
        "counterparty": "Cipher Mining",
        "feature_key": "ai_compute_contract_value_flow",
        "amount": 5_500_000_000,
        "event_date": "2025-11-03",
        "event_type": EventType.CAPEX_COMMITMENT,
        "locator": "Exhibit 99.1: Third Quarter 2025 Business Update",
        "evidence_marker": "$5.5 billion",
        "evidence": (
            "Cipher announced an approximately $5.5 billion, 15-year lease agreement with "
            "Amazon Web Services to provide turnkey space and power for AI workloads."
        ),
    },
    {
        "receipt_id": "alphabet-q3-total-backstop-2025-09",
        "entity": "Alphabet",
        "counterparty": "Third-party data center lessors",
        "feature_key": "ai_contingent_credit_support_stock",
        "amount": 6_529_000_000,
        "event_date": "2025-09-30",
        "event_type": EventType.GUARANTEES,
        "locator": "Form 10-Q, Note 3: Financial Instruments, credit derivatives table",
        "evidence_marker": "6,529",
        "evidence": (
            "The $6.529 billion notional amount for credit derivatives represents "
            "Alphabet's maximum potential backstop payments related to certain third-party "
            "data center leases."
        ),
    },
    {
        "receipt_id": "meta-october-cloud-capacity-total-2025",
        "entity": "Meta",
        "counterparty": "Third-party cloud providers",
        "feature_key": "ai_compute_contract_value_flow",
        "amount": 40_000_000_000,
        "event_date": "2025-10-31",
        "event_type": EventType.CAPEX_COMMITMENT,
        "locator": "Form 10-Q, Contractual Commitments",
        "evidence_marker": "40 billion",
        "evidence": (
            "In October 2025, Meta entered into multi-year third-party cloud capacity "
            "arrangements for an aggregate amount of approximately $40 billion."
        ),
    },
)


def promote_current_ai_feature_family(
    connection: sqlite3.Connection,
    *,
    acquired_directory: Path,
    additional_acquired_directories: tuple[Path, ...] = (),
    code_commit: str,
) -> dict[str, object]:
    """Promote a bounded primary-source batch and build the exact 4x6 matrices."""
    receipts: dict[str, dict[str, Any]] = {}
    for directory in (acquired_directory, *additional_acquired_directories):
        receipts_payload = json.loads(
            (directory / "acquisition-receipts.json").read_text(encoding="utf-8")
        )
        for raw in receipts_payload["receipts"]:
            receipt_record = dict(raw)
            receipt_record["_directory"] = directory
            receipts[str(receipt_record["id"])] = receipt_record
    review_time = datetime.now(UTC).replace(microsecond=0).isoformat()
    repository = SqliteRepository(Path("unused"))
    definitions = (
        (
            "ai_compute_contract_value_flow",
            {
                "aggregation": "sum",
                "unit": "currency",
                "grain": "entity_month",
                "expected_facts_per_period": 1,
                "meaning": "newly disclosed total contract value; not remaining obligation",
            },
        ),
        (
            "ai_contingent_credit_support_stock",
            {
                "aggregation": "as_of_latest",
                "unit": "currency",
                "grain": "entity_month_as_of",
                "expected_facts_per_period": 1,
                "max_age_months": 3,
                "meaning": "latest disclosed total guarantee or financing backstop",
            },
        ),
    )
    for key, semantics in definitions:
        EvidenceRepository.register_feature(
            connection,
            FeatureDefinitionV2.model_validate(
                {
                    "feature_key": key,
                    "feature_version": "1.0.0",
                    "definition_json": json.dumps(semantics, sort_keys=True, separators=(",", ":")),
                    "released_at": "2025-01-01",
                }
            ),
        )
        EvidenceRepository.register_feature(
            connection,
            FeatureDefinitionV2.model_validate(
                {
                    "feature_key": f"ecosystem_{key}",
                    "feature_version": "1.0.0",
                    "definition_json": json.dumps(
                        {
                            "aggregation": "sum",
                            "unit": "currency",
                            "grain": "ecosystem_month",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "released_at": "2025-01-01",
                }
            ),
        )
    _register_features(connection)
    promoted: list[dict[str, object]] = []
    for fact in _FEATURE_FAMILY_FACTS:
        receipt = receipts[str(fact["receipt_id"])]
        content_path = Path(str(receipt["_directory"])) / str(receipt["content_file"])
        content_bytes = content_path.read_bytes()
        digest = hashlib.sha256(content_bytes).hexdigest()
        if digest != receipt["content_sha256"]:
            raise ValueError(f"immutable receipt hash mismatch: {fact['receipt_id']}")
        content = content_bytes.decode("utf-8")
        if str(fact["evidence_marker"]) not in content:
            raise ValueError(f"reviewed evidence text not found: {fact['receipt_id']}")
        item = score(
            SourceItem(
                title=f"Primary filing: {fact['receipt_id']}",
                url=receipt["final_url"],
                source="SEC",
                summary=str(fact["evidence"]),
                published_at=str(receipt["public_availability_at"])[:10],
                discovered_at=receipt["fetched_at"],
            ),
            [str(fact["entity"]), str(fact["counterparty"])],
        )
        repository.insert(connection, item)
        repository.upsert_document(
            connection,
            item.item_id,
            receipt["fetched_at"],
            receipt["content_type"],
            "ok",
            content,
        )
        slug = str(fact["receipt_id"])
        event_id = f"accepted-{slug}"
        repository.insert_event(
            connection,
            FinancialEvent.model_validate(
                {
                    "event_id": event_id,
                    "document_id": item.item_id,
                    "event_type": fact["event_type"],
                    "source_entity": str(fact["entity"]),
                    "target_entity": str(fact["counterparty"]),
                    "amount": fact["amount"],
                    "currency": "USD",
                    "instrument": str(fact["feature_key"]),
                    "effective_date": str(fact["event_date"]),
                    "confidence": 1.0,
                    "evidence_text": str(fact["evidence"]),
                    "extractor": "asro-v2-manual-acceptance",
                    "processed_at": review_time,
                }
            ),
        )
        review_id = _accepted_review(connection, event_id, review_time)
        canonical_fact_id = f"fact-{slug}"
        EvidenceRepository.register_canonical_fact(connection, canonical_fact_id)
        assignment_id = f"assignment-{slug}"
        EvidenceRepository.assign_canonical_fact(
            connection,
            CanonicalFactAssignment.model_validate(
                {
                    "assignment_id": assignment_id,
                    "event_id": event_id,
                    "canonical_fact_id": canonical_fact_id,
                    "available_at": review_time,
                    "reviewer_id": review_id,
                    "assigned_by": "human-acceptance-review",
                    "assignment_method": "full-document-manual-review",
                    "provenance": {
                        "source_url": receipt["final_url"],
                        "content_sha256": digest,
                    },
                    "created_at": review_time,
                }
            ),
        )
        event_date = str(fact["event_date"])
        month_start = f"{event_date[:7]}-01"
        month_end = (
            f"{event_date[:7]}-{calendar.monthrange(int(event_date[:4]), int(event_date[5:7]))[1]}"
        )
        observation_id = f"observation-{slug}"
        EvidenceRepository.insert(
            connection,
            ObservationV2.model_validate(
                {
                    "observation_id": observation_id,
                    "event_id": event_id,
                    "source_document_id": item.item_id,
                    "source_locator": str(fact["locator"]),
                    "evidence_text": str(fact["evidence"]),
                    "entity_id": str(fact["entity"]),
                    "counterparty_entity_id": str(fact["counterparty"]),
                    "entity_role": (
                        "customer"
                        if fact["feature_key"] != "ai_contingent_credit_support_stock"
                        else "guarantor"
                    ),
                    "feature_key": str(fact["feature_key"]),
                    "feature_version": "1.0.0",
                    "value_numeric": fact["amount"],
                    "unit": "currency",
                    "currency": "USD",
                    "economic_scope": EconomicScope.ENTITY,
                    "period_start": month_start,
                    "period_end": month_end,
                    "event_at": event_date,
                    "published_at": receipt["public_availability_at"],
                    "availability_at": receipt["public_availability_at"],
                    "extracted_at": review_time,
                    "fact_status": FactStatus.DIRECT,
                    "source_tier": SourceTier.PRIMARY,
                    "source_quality": 1.0,
                    "extraction_confidence": 1.0,
                    "review_confidence": 1.0,
                    "extractor_name": "manual-full-document-review",
                    "extractor_version": "2.0.0",
                    "review_id": review_id,
                }
            ),
        )
        promoted.append({"observation_id": observation_id, "content_sha256": digest})
    connection.commit()
    accepted_event_ids = [f"accepted-{fact['receipt_id']}" for fact in _FEATURE_FAMILY_FACTS]
    placeholders = ",".join("?" for _ in accepted_event_ids)
    build_cutoff = str(
        connection.execute(
            f"""SELECT MAX(available_at) FROM canonical_fact_assignment
                WHERE event_id IN ({placeholders})""",  # noqa: S608
            accepted_event_ids,
        ).fetchone()[0]
    )
    specs = [
        FeatureSpec(
            feature_key="ai_related_debt",
            feature_version="1.0.0",
            aggregation=Aggregation.SUM,
            unit="currency",
            expected_facts_per_period=1,
        ),
        FeatureSpec(
            feature_key="ai_compute_contract_value_flow",
            feature_version="1.0.0",
            aggregation=Aggregation.SUM,
            unit="currency",
            expected_facts_per_period=1,
        ),
        FeatureSpec(
            feature_key="ai_contingent_credit_support_stock",
            feature_version="1.0.0",
            aggregation=Aggregation.AS_OF_LATEST,
            unit="currency",
            expected_facts_per_period=1,
            max_age_months=3,
        ),
    ]
    entity_build = FeatureStoreBuilder(connection).build_entity_month(
        specs,
        build_cutoff,
        ["Alphabet", "Amazon", "Meta", "Microsoft"],
        code_commit,
        "current-ai-feature-family-1.0.0",
        "2025-07-01",
        "2025-12-31",
    )
    ecosystem_build = EcosystemFeatureStoreBuilder(connection).build_months(
        entity_build.build_id,
        [
            EcosystemFeatureSpec(
                source_feature_key=spec.feature_key,
                source_feature_version=spec.feature_version,
                feature_key=f"ecosystem_{spec.feature_key}",
                feature_version="1.0.0",
                aggregation=Aggregation.SUM,
                unit="currency",
            )
            for spec in specs
        ],
        code_commit,
        "current-ai-feature-family-1.0.0",
    )
    counts = {
        row[0]: {"accepted": int(row[1]), "missing": 24 - int(row[1]), "required": 24}
        for row in connection.execute(
            """SELECT feature_key, SUM(value_numeric IS NOT NULL) FROM feature_value
               WHERE build_id=? GROUP BY feature_key ORDER BY feature_key""",
            (entity_build.build_id,),
        )
    }
    entity_counts = {
        row[0]: {"accepted": int(row[1]), "unknown": 18 - int(row[1]), "required": 18}
        for row in connection.execute(
            """SELECT entity_id,SUM(value_numeric IS NOT NULL) FROM feature_value
               WHERE build_id=? GROUP BY entity_id ORDER BY entity_id""",
            (entity_build.build_id,),
        )
    }
    numeric_cell_count = sum(int(item["accepted"]) for item in counts.values())
    distinct_fact_count = int(
        connection.execute(
            """SELECT COUNT(DISTINCT fact.canonical_fact_id)
               FROM feature_value value JOIN feature_value_fact fact
                 ON fact.feature_value_id=value.feature_value_id
               WHERE value.build_id=?""",
            (entity_build.build_id,),
        ).fetchone()[0]
    )
    return {
        "status": "partial_evidence_acceptance",
        "entity_build_id": entity_build.build_id,
        "ecosystem_build_id": ecosystem_build.build_id,
        "promoted": promoted,
        "feature_cells": counts,
        "entity_cells": entity_counts,
        "accepted_numeric_cells": numeric_cell_count,
        "distinct_accepted_facts": distinct_fact_count,
        "modeling_allowed": False,
        "build_cutoff": build_cutoff,
    }


def _accepted_review(connection: sqlite3.Connection, event_id: str, reviewed_at: str) -> int:
    existing = connection.execute(
        """SELECT review_id FROM evidence_reviews
           WHERE fingerprint=? AND decision='confirm' AND model='human-acceptance-review'
           ORDER BY review_id LIMIT 1""",
        (event_id,),
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    cursor = connection.execute(
        """INSERT INTO evidence_reviews(
           fingerprint,decision,canonical_fingerprint,confidence,reasoning,model,reviewed_at
           ) VALUES(?, 'confirm', NULL, 1.0, ?, 'human-acceptance-review', ?)""",
        (
            event_id,
            "Full authoritative document explicitly states the reviewed value, roles, and timing.",
            reviewed_at,
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _promote_candidate(
    connection: sqlite3.Connection,
    package_id: str,
    document_id: str,
    content: str,
    fetched_at: str,
    observation_id: str,
    fact_id: str,
    reviewed_at: str,
) -> None:
    ordinal = int(
        connection.execute(
            """SELECT source_ordinal FROM candidate_source_edge
               WHERE package_id=? AND candidate_event_id=? AND url=?
               ORDER BY source_ordinal LIMIT 1""",
            (package_id, _CANDIDATE_EVENT_ID, _SEC_URL),
        ).fetchone()[0]
    )
    digest = hashlib.sha256(content.encode()).hexdigest()
    acquired = connection.execute(
        """SELECT 1 FROM candidate_acquired_document
           WHERE package_id=? AND candidate_event_id=? AND source_ordinal=?""",
        (package_id, _CANDIDATE_EVENT_ID, ordinal),
    ).fetchone()
    if acquired is None:
        connection.execute(
            "INSERT INTO candidate_acquired_document VALUES(?,?,?,?,?,?,?,?,?)",
            (
                package_id,
                _CANDIDATE_EVENT_ID,
                ordinal,
                document_id,
                digest,
                content,
                "2025-10-30T00:00:00+00:00",
                fetched_at,
                json.dumps({"method": "authoritative_sec_fetch", "url": _SEC_URL}, sort_keys=True),
            ),
        )
    promoted = connection.execute(
        """SELECT 1 FROM candidate_evidence_promotion_v2
           WHERE package_id=? AND candidate_event_id=? AND source_ordinal=?""",
        (package_id, _CANDIDATE_EVENT_ID, ordinal),
    ).fetchone()
    if promoted is None:
        connection.execute(
            "INSERT INTO candidate_evidence_promotion_v2 VALUES(?,?,?,?,?,?,?,?,?)",
            (
                package_id,
                _CANDIDATE_EVENT_ID,
                ordinal,
                observation_id,
                fact_id,
                "primary",
                reviewed_at,
                "human-acceptance-review",
                json.dumps(
                    {
                        "decision": "promote",
                        "basis": "full_authoritative_document_review",
                    },
                    sort_keys=True,
                ),
            ),
        )


def _register_controls(
    connection: sqlite3.Connection, files: dict[str, Path], fetched_at: str
) -> None:
    specifications = (
        ("policy_rate", "FEDFUNDS", "percent", 1.0),
        ("credit_spread", "BAMLC0A0CM", "basis_points", 100.0),
        ("semiconductor_cycle", "IPG3344S", "index", 1.0),
    )
    for series_id, fred_id, unit, multiplier in specifications:
        value = _csv_value(files[fred_id], "2025-10-01") * multiplier
        register_control_observation(
            connection,
            ControlObservation(
                control_observation_id=f"{series_id}-2025-10-current-vintage",
                series_id=series_id,
                series_version="1.0.0",
                period_start="2025-10-01",
                period_end="2025-10-31",
                observed_at="2025-10-31T00:00:00Z",
                availability_at=fetched_at,
                value_numeric=value,
                unit=unit,
                provenance={
                    "publisher": "Federal Reserve Bank of St. Louis (FRED)",
                    "source_url": f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fred_id}",
                    "vintage": f"current-vintage-retrieved-{fetched_at}",
                },
            ),
        )


def _register_controls_for_months(
    connection: sqlite3.Connection, files: dict[str, Path], months: list[str]
) -> None:
    specifications = (
        ("policy_rate", "FEDFUNDS", "percent", 1.0),
        ("credit_spread", "BAMLC0A0CM", "basis_points", 100.0),
        ("semiconductor_cycle", "IPG3344S", "index", 1.0),
    )
    for series_id, fred_id, unit, multiplier in specifications:
        source_file = files[fred_id]
        fetched_at = _file_time(source_file)
        digest = hashlib.sha256(source_file.read_bytes()).hexdigest()
        for month in months:
            observation_date, raw_value = _first_csv_value_in_month(source_file, month)
            year, month_number = (int(part) for part in month.split("-"))
            period_start = f"{month}-01"
            period_end = f"{month}-{calendar.monthrange(year, month_number)[1]:02d}"
            register_control_observation(
                connection,
                ControlObservation(
                    control_observation_id=(f"{series_id}-{month}-current-vintage-{digest[:12]}"),
                    series_id=series_id,
                    series_version="1.0.0",
                    period_start=period_start,
                    period_end=period_end,
                    observed_at=f"{observation_date}T00:00:00Z",
                    availability_at=fetched_at,
                    value_numeric=raw_value * multiplier,
                    unit=unit,
                    provenance={
                        "publisher": "Federal Reserve Bank of St. Louis (FRED)",
                        "source_url": (
                            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fred_id}"
                        ),
                        "source_observation_date": observation_date,
                        "content_sha256": digest,
                        "vintage": f"current-vintage-retrieved-{fetched_at}",
                    },
                ),
            )


def _register_control_definitions(
    connection: sqlite3.Connection, manifest: EpisodeManifest, registered_at: str
) -> None:
    for control in manifest.controls:
        connection.execute(
            "INSERT OR IGNORE INTO control_series_definition VALUES(?,?,?,?,?)",
            (
                control.series_id,
                control.version,
                control.unit,
                json.dumps(control.provenance_schema, sort_keys=True, separators=(",", ":")),
                registered_at,
            ),
        )


def _csv_value(path: Path, observation_date: str) -> float:
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["observation_date"] == observation_date:
                return float(next(value for key, value in row.items() if key != "observation_date"))
    raise ValueError(f"missing {observation_date} in {path.name}")


def _first_csv_value_in_month(path: Path, month: str) -> tuple[str, float]:
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            observation_date = row["observation_date"]
            if observation_date.startswith(f"{month}-"):
                value = next(value for key, value in row.items() if key != "observation_date")
                if value not in {"", "."}:
                    return observation_date, float(value)
    raise ValueError(f"missing {month} in {path.name}")


def _file_time(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(microsecond=0).isoformat()
