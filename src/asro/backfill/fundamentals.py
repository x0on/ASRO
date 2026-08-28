from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

_ENTITIES = ("Alphabet", "Amazon", "Meta", "Microsoft")
_TARGET_ENDS = ("2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30")
_FEATURE_KEY = "company_liquid_assets_stock"
_FEATURE_VERSION = "1.0.0"
_DIRECT_TAG = "CashCashEquivalentsAndShortTermInvestments"
_COMPONENT_TAGS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "MarketableSecuritiesCurrent",
)


@dataclass(frozen=True)
class FundamentalsPoint:
    entity: str
    period_end: str
    value: float
    accession: str
    form: str
    frame: str | None
    filed: str
    accepted_at: str
    primary_document: str
    taxonomy_tags: tuple[str, ...]
    derivation: str
    amendment: bool


def select_liquid_asset_points(
    entity: str, companyfacts: dict[str, Any], submissions: dict[str, Any]
) -> tuple[list[FundamentalsPoint], list[dict[str, object]]]:
    """Select comparable point facts; never interpret duration/YTD facts as a stock."""
    if entity not in _ENTITIES:
        raise ValueError(f"unsupported fundamentals entity: {entity}")
    filing_index = _filing_index(submissions)
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    selected: list[FundamentalsPoint] = []
    rejected: list[dict[str, object]] = []
    for period_end in _TARGET_ENDS:
        direct = _point_candidates(facts, _DIRECT_TAG, period_end)
        if direct:
            candidate = _earliest_candidate(direct)
            selected.append(
                _point_from_candidate(
                    entity,
                    candidate,
                    filing_index,
                    (f"us-gaap:{_DIRECT_TAG}",),
                    "direct_standardized_combined_tag",
                )
            )
            rejected.extend(_later_changes(entity, period_end, direct, candidate))
            continue
        components = [_point_candidates(facts, tag, period_end) for tag in _COMPONENT_TAGS]
        matches = _matching_components(components)
        if not matches:
            rejected.append(
                {
                    "entity": entity,
                    "period_end": period_end,
                    "reason": "no comparable same-accession cash-plus-current-securities point",
                }
            )
            continue
        left, right = min(matches, key=lambda pair: (pair[0]["filed"], pair[0]["accn"]))
        combined = dict(left)
        combined["val"] = float(left["val"]) + float(right["val"])
        selected.append(
            _point_from_candidate(
                entity,
                combined,
                filing_index,
                tuple(f"us-gaap:{tag}" for tag in _COMPONENT_TAGS),
                "sum_same_accession_cash_and_current_marketable_securities",
            )
        )
    return selected, rejected


def promote_company_fundamentals(
    connection: sqlite3.Connection,
    *,
    acquired_directory: Path,
    code_commit: str,
) -> dict[str, object]:
    """Review one comparable total-company context feature and build an exact 4x12 grid."""
    receipts_payload = json.loads(
        (acquired_directory / "acquisition-receipts.json").read_text(encoding="utf-8")
    )
    receipts = {str(item["id"]): dict(item) for item in receipts_payload["receipts"]}
    review_time = datetime.now(UTC).replace(microsecond=0).isoformat()
    repository = SqliteRepository(Path("unused"))
    all_points: list[FundamentalsPoint] = []
    rejected: list[dict[str, object]] = []
    source_hashes: dict[str, dict[str, str]] = {}
    for entity in _ENTITIES:
        slug = entity.lower()
        facts_receipt = receipts[f"{slug}-companyfacts-2026-08-28"]
        submissions_receipt = receipts[f"{slug}-submissions-2026-08-28"]
        facts_text = _verified_content(acquired_directory, facts_receipt)
        submissions_text = _verified_content(acquired_directory, submissions_receipt)
        companyfacts = json.loads(facts_text)
        submissions = json.loads(submissions_text)
        points, entity_rejections = select_liquid_asset_points(entity, companyfacts, submissions)
        if len(points) != len(_TARGET_ENDS):
            raise ValueError(f"incomplete comparable liquid-assets point set for {entity}")
        all_points.extend(points)
        rejected.extend(entity_rejections)
        document_id = _persist_snapshot(connection, repository, entity, facts_receipt, facts_text)
        source_hashes[entity] = {
            "companyfacts_sha256": str(facts_receipt["content_sha256"]),
            "companyfacts_url": str(facts_receipt["final_url"]),
            "companyfacts_fetched_at": str(facts_receipt["fetched_at"]),
            "submissions_sha256": str(submissions_receipt["content_sha256"]),
            "submissions_url": str(submissions_receipt["final_url"]),
            "submissions_fetched_at": str(submissions_receipt["fetched_at"]),
            "document_id": document_id,
        }

    _register_fundamentals_features(connection)
    promoted: list[dict[str, object]] = []
    accepted_event_ids: list[str] = []
    for point in all_points:
        source = source_hashes[point.entity]
        event_id = _stable_id("fundamentals-event", point.entity, point.period_end)
        accepted_event_ids.append(event_id)
        observation_id = _stable_id("fundamentals-observation", point.entity, point.period_end)
        fact_id = _stable_id("fundamentals-fact", point.entity, point.period_end)
        assignment_id = _stable_id("fundamentals-assignment", point.entity, point.period_end)
        evidence = (
            f"SEC Companyfacts reports total-company cash plus current marketable/short-term "
            f"securities of USD {point.value:.0f} at {point.period_end}; "
            f"taxonomy path: {' + '.join(point.taxonomy_tags)}."
        )
        repository.insert_event(
            connection,
            FinancialEvent.model_validate(
                {
                    "event_id": event_id,
                    "document_id": source["document_id"],
                    "event_type": EventType.BALANCE_SHEET_REPORT,
                    "source_entity": point.entity,
                    "amount": point.value,
                    "currency": "USD",
                    "instrument": "total-company liquid assets; not AI-attributed",
                    "effective_date": point.period_end,
                    "confidence": 1.0,
                    "evidence_text": evidence,
                    "extractor": "sec-companyfacts-reviewed-1.0.0",
                    "processed_at": review_time,
                }
            ),
        )
        review_id = _accepted_review(connection, event_id, review_time, point)
        EvidenceRepository.register_canonical_fact(connection, fact_id)
        EvidenceRepository.assign_canonical_fact(
            connection,
            CanonicalFactAssignment.model_validate(
                {
                    "assignment_id": assignment_id,
                    "event_id": event_id,
                    "canonical_fact_id": fact_id,
                    "available_at": review_time,
                    "reviewer_id": review_id,
                    "assigned_by": "human-fundamentals-review",
                    "assignment_method": "sec-companyfacts-taxonomy-and-period-review",
                    "provenance": {
                        **source,
                        "accession": point.accession,
                        "form": point.form,
                        "frame": point.frame,
                        "taxonomy_tags": point.taxonomy_tags,
                        "derivation": point.derivation,
                        "amendment": point.amendment,
                        "filing_acceptance_at": point.accepted_at,
                    },
                    "created_at": review_time,
                }
            ),
        )
        EvidenceRepository.insert(
            connection,
            ObservationV2.model_validate(
                {
                    "observation_id": observation_id,
                    "event_id": event_id,
                    "source_document_id": source["document_id"],
                    "source_locator": (
                        f"SEC accession {point.accession}; {point.primary_document}; "
                        f"frame={point.frame or 'unframed'}; tags={'+'.join(point.taxonomy_tags)}"
                    ),
                    "evidence_text": evidence,
                    "entity_id": point.entity,
                    "entity_role": "reporting_company",
                    "feature_key": _FEATURE_KEY,
                    "feature_version": _FEATURE_VERSION,
                    "value_numeric": point.value,
                    "unit": "currency",
                    "currency": "USD",
                    "economic_scope": EconomicScope.ENTITY,
                    "period_start": point.period_end,
                    "period_end": point.period_end,
                    "event_at": point.period_end,
                    "published_at": point.accepted_at,
                    "availability_at": point.accepted_at,
                    "extracted_at": review_time,
                    "fact_status": (
                        FactStatus.DIRECT
                        if point.derivation == "direct_standardized_combined_tag"
                        else FactStatus.INFERRED
                    ),
                    "source_tier": SourceTier.PRIMARY,
                    "source_quality": 1.0,
                    "extraction_confidence": 1.0,
                    "review_confidence": 1.0,
                    "extractor_name": "sec-companyfacts-reviewed",
                    "extractor_version": "1.0.0",
                    "review_id": review_id,
                    "derivation_method": (
                        None
                        if point.derivation == "direct_standardized_combined_tag"
                        else point.derivation
                    ),
                    "derivation_inputs": (
                        []
                        if point.derivation == "direct_standardized_combined_tag"
                        else list(point.taxonomy_tags)
                    ),
                }
            ),
        )
        promoted.append(
            {
                "entity": point.entity,
                "period_end": point.period_end,
                "value_numeric": point.value,
                "available_at": point.accepted_at,
                "accession": point.accession,
                "taxonomy_tags": list(point.taxonomy_tags),
                "derivation": point.derivation,
            }
        )
    connection.commit()
    placeholders = ",".join("?" for _ in accepted_event_ids)
    build_cutoff = str(
        connection.execute(
            f"""SELECT MAX(available_at) FROM canonical_fact_assignment
                WHERE event_id IN ({placeholders})""",  # noqa: S608
            accepted_event_ids,
        ).fetchone()[0]
    )
    spec = FeatureSpec(
        feature_key=_FEATURE_KEY,
        feature_version=_FEATURE_VERSION,
        aggregation=Aggregation.AS_OF_LATEST,
        unit="currency",
        expected_facts_per_period=1,
        max_age_months=3,
    )
    build = FeatureStoreBuilder(connection).build_entity_month(
        [spec],
        build_cutoff,
        list(_ENTITIES),
        code_commit,
        "company-fundamentals-context-1.0.0",
        "2025-01-01",
        "2025-12-31",
    )
    ecosystem = EcosystemFeatureStoreBuilder(connection).build_months(
        build.build_id,
        [
            EcosystemFeatureSpec(
                source_feature_key=_FEATURE_KEY,
                source_feature_version=_FEATURE_VERSION,
                feature_key=f"ecosystem_{_FEATURE_KEY}",
                feature_version=_FEATURE_VERSION,
                aggregation=Aggregation.SUM,
                unit="currency",
            )
        ],
        code_commit,
        "company-fundamentals-context-1.0.0",
    )
    numeric = int(
        connection.execute(
            "SELECT SUM(value_numeric IS NOT NULL) FROM feature_value WHERE build_id=?",
            (build.build_id,),
        ).fetchone()[0]
    )
    return {
        "status": "accepted_context_not_modeling",
        "feature_set_version": "company-fundamentals-context-1.0.0",
        "feature": f"{_FEATURE_KEY}@{_FEATURE_VERSION}",
        "scope": "total-company context; not AI-attributed",
        "entity_build_id": build.build_id,
        "ecosystem_build_id": ecosystem.build_id,
        "distinct_accepted_facts": len(promoted),
        "required_cells": 48,
        "accepted_numeric_cells": numeric,
        "unknown_cells": 48 - numeric,
        "promoted_points": promoted,
        "source_snapshots": source_hashes,
        "rejected_or_deferred": rejected,
        "deferred_features": {
            "total_debt": "taxonomy/scope paths are not yet uniformly validated",
            "purchase_obligations": "tag and scope are not consistently comparable",
            "quarterly_capex_and_operating_cash_flow": (
                "deferred until quarter-only derivations are validated against YTD periods"
            ),
        },
        "carry_semantics": "as-of latest, maximum age three months, filing acceptance cutoff",
        "build_cutoff": build_cutoff,
        "modeling_allowed": False,
    }


def _point_candidates(facts: dict[str, Any], tag: str, period_end: str) -> list[dict[str, Any]]:
    fact = facts.get(tag)
    if not isinstance(fact, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for unit, rows in fact.get("units", {}).items():
        if unit != "USD":
            continue
        for raw in rows:
            row = dict(raw)
            if (
                row.get("end") == period_end
                and row.get("form") in {"10-Q", "10-K", "10-Q/A", "10-K/A"}
                and "start" not in row
                and str(row.get("filed", ""))[:4] in {"2025", "2026"}
            ):
                row["unit"] = unit
                candidates.append(row)
    return candidates


def _earliest_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    unique = {(row["accn"], row["filed"], float(row["val"])): row for row in candidates}
    return min(unique.values(), key=lambda row: (row["filed"], row["accn"]))


def _matching_components(
    groups: list[list[dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if len(groups) != 2:
        return []
    return [
        (left, right)
        for left in groups[0]
        for right in groups[1]
        if left["accn"] == right["accn"]
        and left["filed"] == right["filed"]
        and left["end"] == right["end"]
        and left["unit"] == right["unit"] == "USD"
    ]


def _filing_index(submissions: dict[str, Any]) -> dict[str, dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    return {
        str(accession): {key: values[index] for key, values in recent.items()}
        for index, accession in enumerate(accessions)
    }


def _point_from_candidate(
    entity: str,
    candidate: dict[str, Any],
    filings: dict[str, dict[str, Any]],
    taxonomy_tags: tuple[str, ...],
    derivation: str,
) -> FundamentalsPoint:
    accession = str(candidate["accn"])
    filing = filings.get(accession)
    if filing is None:
        raise ValueError(f"submission metadata is missing for accession {accession}")
    accepted_at = str(filing.get("acceptanceDateTime") or "")
    if not accepted_at.endswith("Z"):
        raise ValueError(f"filing acceptance timestamp is not canonical UTC: {accession}")
    form = str(filing["form"])
    if form != str(candidate["form"]) or str(filing["filingDate"]) != str(candidate["filed"]):
        raise ValueError(f"Companyfacts and submissions filing metadata disagree: {accession}")
    return FundamentalsPoint(
        entity=entity,
        period_end=str(candidate["end"]),
        value=float(candidate["val"]),
        accession=accession,
        form=form,
        frame=str(candidate["frame"]) if candidate.get("frame") else None,
        filed=str(candidate["filed"]),
        accepted_at=accepted_at,
        primary_document=str(filing["primaryDocument"]),
        taxonomy_tags=taxonomy_tags,
        derivation=derivation,
        amendment=form.endswith("/A"),
    )


def _later_changes(
    entity: str,
    period_end: str,
    candidates: list[dict[str, Any]],
    selected: dict[str, Any],
) -> list[dict[str, object]]:
    return [
        {
            "entity": entity,
            "period_end": period_end,
            "reason": "later filing value differs; requires append-only restatement review",
            "selected_accession": selected["accn"],
            "later_accession": row["accn"],
        }
        for row in candidates
        if row["filed"] > selected["filed"] and float(row["val"]) != float(selected["val"])
    ]


def _verified_content(directory: Path, receipt: dict[str, Any]) -> str:
    content = (directory / str(receipt["content_file"])).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != receipt["content_sha256"]:
        raise ValueError(f"fundamentals snapshot hash mismatch: {receipt['id']}")
    return content.decode("utf-8")


def _persist_snapshot(
    connection: sqlite3.Connection,
    repository: SqliteRepository,
    entity: str,
    receipt: dict[str, Any],
    content: str,
) -> str:
    item = score(
        SourceItem.model_validate(
            {
                "title": f"{entity} SEC Companyfacts immutable snapshot 2026-08-28",
                "url": receipt["final_url"],
                "source": "SEC Companyfacts",
                "summary": "Authoritative total-company XBRL facts; not AI-attributed.",
                "published_at": None,
                "discovered_at": receipt["fetched_at"],
            }
        ),
        [entity],
    )
    repository.insert(connection, item)
    existing = connection.execute(
        "SELECT text,fetched_at FROM documents WHERE item_id=?", (item.item_id,)
    ).fetchone()
    if existing is not None:
        if hashlib.sha256(str(existing[0]).encode()).hexdigest() != receipt["content_sha256"]:
            raise ValueError(f"immutable Companyfacts snapshot changed for {entity}")
        if str(existing[1]) != str(receipt["fetched_at"]):
            raise ValueError(f"Companyfacts fetch provenance changed for {entity}")
    else:
        repository.upsert_document(
            connection,
            item.item_id,
            str(receipt["fetched_at"]),
            "application/json",
            "ok",
            content,
        )
    return item.item_id


def _register_fundamentals_features(connection: sqlite3.Connection) -> None:
    definitions = {
        _FEATURE_KEY: {
            "aggregation": "as_of_latest",
            "unit": "currency",
            "grain": "entity_month_as_of",
            "economic_scope": "total_company_not_ai_attributed",
            "expected_facts_per_period": 1,
            "max_age_months": 3,
            "accepted_taxonomy_paths": [
                [f"us-gaap:{_DIRECT_TAG}"],
                [f"us-gaap:{tag}" for tag in _COMPONENT_TAGS],
            ],
            "period_semantics": "instant only; duration and YTD contexts rejected",
            "restatement_semantics": "append-only review required",
        },
        f"ecosystem_{_FEATURE_KEY}": {
            "aggregation": "sum",
            "unit": "currency",
            "grain": "ecosystem_month",
            "economic_scope": "sum_of_distinct_total_company_context_points",
            "expected_facts_per_period": 1,
            "max_age_months": None,
        },
    }
    for key, semantics in definitions.items():
        EvidenceRepository.register_feature(
            connection,
            FeatureDefinitionV2.model_validate(
                {
                    "feature_key": key,
                    "feature_version": _FEATURE_VERSION,
                    "definition_json": json.dumps(semantics, sort_keys=True, separators=(",", ":")),
                    "released_at": "2026-08-28T00:00:00Z",
                }
            ),
        )


def _accepted_review(
    connection: sqlite3.Connection,
    event_id: str,
    review_time: str,
    point: FundamentalsPoint,
) -> int:
    existing = connection.execute(
        """SELECT review_id FROM evidence_reviews
           WHERE fingerprint=? AND decision='confirm' AND model='human-fundamentals-review'""",
        (event_id,),
    ).fetchone()
    if existing is not None:
        return int(existing[0])
    cursor = connection.execute(
        """INSERT INTO evidence_reviews(
           fingerprint,decision,canonical_fingerprint,confidence,reasoning,model,reviewed_at
           ) VALUES(?, 'confirm', NULL, 1.0, ?, 'human-fundamentals-review', ?)""",
        (
            event_id,
            (
                f"Accepted SEC point fact after unit, instant-period, accession, taxonomy, "
                f"and filing-acceptance review; derivation={point.derivation}."
            ),
            review_time,
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _stable_id(prefix: str, entity: str, period_end: str) -> str:
    digest = hashlib.sha256(
        f"{prefix}|{entity}|{period_end}|{_FEATURE_VERSION}".encode()
    ).hexdigest()
    return f"{prefix}-{digest[:32]}"
