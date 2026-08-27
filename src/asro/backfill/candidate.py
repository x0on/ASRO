from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from asro.backfill.manifest import EpisodeManifest


@dataclass(frozen=True)
class CandidateImportResult:
    package_id: str
    event_count: int
    entity_count: int
    source_edge_count: int
    eligible_event_count: int


def candidate_episode_support(
    connection: sqlite3.Connection,
    package_id: str,
    manifests: list[EpisodeManifest],
) -> list[dict[str, object]]:
    """Report discovery candidates separately from promoted, coverage-eligible evidence."""
    reports: list[dict[str, object]] = []
    for manifest in sorted(manifests, key=lambda item: item.episode_id):
        placeholders = ",".join("?" for _ in manifest.entities)
        parameters: tuple[object, ...] = (
            package_id,
            manifest.period_start.isoformat(),
            manifest.period_end.isoformat(),
            *manifest.entities,
            *manifest.entities,
        )
        candidate_count = int(
            connection.execute(
                f"""SELECT COUNT(*) FROM candidate_event
                    WHERE package_id=? AND eligible_as_of=1
                      AND effective_date BETWEEN ? AND ?
                      AND (primary_entity IN ({placeholders})
                           OR counterparty_entity IN ({placeholders}))""",  # noqa: S608
                parameters,
            ).fetchone()[0]
        )
        promoted_count = int(
            connection.execute(
                f"""SELECT COUNT(DISTINCT promotion.candidate_event_id)
                    FROM candidate_evidence_promotion_v2 promotion
                    JOIN candidate_event event
                      ON event.package_id=promotion.package_id
                     AND event.candidate_event_id=promotion.candidate_event_id
                    WHERE event.package_id=? AND event.eligible_as_of=1
                      AND event.effective_date BETWEEN ? AND ?
                      AND (event.primary_entity IN ({placeholders})
                           OR event.counterparty_entity IN ({placeholders}))""",  # noqa: S608
                parameters,
            ).fetchone()[0]
        )
        accepted = connection.execute(
            """SELECT run.run_id FROM finalized_backfill_run run
               WHERE run.episode_id=? AND run.episode_version=?
                 AND run.coverage_passed=1 AND run.leakage_passed=1
               ORDER BY run.run_id LIMIT 1""",
            (manifest.episode_id, manifest.version),
        ).fetchone()
        reports.append(
            {
                "episode_id": manifest.episode_id,
                "candidate_event_count": candidate_count,
                "promoted_event_count": promoted_count,
                "genuinely_supported": accepted is not None,
                "finalized_backfill_run_id": str(accepted[0]) if accepted else None,
                "coverage_contribution": 0,
            }
        )
    return reports


def ingest_candidate_package(
    connection: sqlite3.Connection, directory: Path
) -> CandidateImportResult:
    """Quarantine a research corpus without creating production items, events, or observations."""
    required = {
        "archive": directory / "asro-seed-dataset-v2.tar.gz",
        "events": directory / "seed_events.json",
        "entities": directory / "entities.json",
        "dedupe": directory / "dedupe_report.json",
    }
    for path in required.values():
        if not path.is_file():
            raise ValueError(f"candidate package is missing {path.name}")
    hashes = {key: _file_hash(path) for key, path in required.items()}
    events_document = _object(required["events"])
    entities_document = _object(required["entities"])
    events = _list_field(events_document, "events")
    entities = _list_field(entities_document, "entities")
    if int(events_document.get("event_count", -1)) != len(events):
        raise ValueError("candidate event count does not match its manifest")
    if int(entities_document.get("entity_count", -1)) != len(entities):
        raise ValueError("candidate entity count does not match its manifest")
    as_of = date.fromisoformat(str(events_document["as_of"]))
    package_id = hashes["archive"]
    result = CandidateImportResult(
        package_id=package_id,
        event_count=len(events),
        entity_count=len(entities),
        source_edge_count=sum(len(_sources(item)) for item in events),
        eligible_event_count=sum(_eligible(item, as_of) for item in events),
    )
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    existing = connection.execute(
        """SELECT archive_sha256,events_sha256,entities_sha256,dedupe_sha256,
                  research_as_of,schema_name,event_count,entity_count
           FROM candidate_package WHERE package_id=?""",
        (package_id,),
    ).fetchone()
    if existing is not None:
        _validate_existing_package(
            connection,
            package_id,
            existing,
            hashes,
            as_of,
            events_document,
            events,
            entities,
            files,
            directory,
        )
        return result
    entity_names = {str(item["canonical_name"]) for item in entities}
    referenced = set()
    for event in events:
        referenced.add(str(event["primary_entity"]))
        if event.get("counterparty_entity"):
            referenced.add(str(event["counterparty_entity"]))
    missing = sorted(referenced - entity_names)
    if missing:
        raise ValueError("candidate events reference entities absent from entities.json")
    try:
        connection.execute("BEGIN")
        connection.execute(
            "INSERT INTO candidate_package VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                package_id,
                hashes["archive"],
                hashes["events"],
                hashes["entities"],
                hashes["dedupe"],
                as_of.isoformat(),
                str(events_document.get("schema", "")),
                len(events),
                len(entities),
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.executemany(
            "INSERT INTO candidate_package_file VALUES(?,?,?,?)",
            [
                (
                    package_id,
                    path.relative_to(directory).as_posix(),
                    _file_hash(path),
                    path.stat().st_size,
                )
                for path in files
            ],
        )
        connection.executemany(
            "INSERT INTO candidate_entity VALUES(?,?,?,?)",
            [
                (
                    package_id,
                    str(item["canonical_name"]),
                    _canonical(item),
                    int(bool(item.get("stub") or item.get("is_stub"))),
                )
                for item in entities
            ],
        )
        for event in events:
            event_id = str(event["event_id"])
            eligible = _eligible(event, as_of)
            reasons = _quarantine_reasons(event, as_of, entity_names)
            connection.execute(
                "INSERT INTO candidate_event VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    package_id,
                    event_id,
                    event.get("event_group_id"),
                    str(event["effective_date"]),
                    str(event["event_type"]),
                    str(event["primary_entity"]),
                    event.get("counterparty_entity"),
                    eligible,
                    ",".join(reasons),
                    _canonical(event),
                ),
            )
            connection.executemany(
                "INSERT INTO candidate_source_edge VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        package_id,
                        event_id,
                        index,
                        str(source["url"]),
                        str(source.get("title", "")),
                        str(source.get("publisher", "")),
                        source.get("published_at"),
                        source.get("source_tier"),
                        source.get("source_type"),
                        str(source.get("excerpt", "")),
                        int(bool(source.get("is_primary"))),
                        None,
                        None,
                    )
                    for index, source in enumerate(_sources(event))
                ],
            )
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    return result


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _list_field(value: dict[str, Any], key: str) -> list[dict[str, Any]]:
    items = value.get(key)
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError(f"candidate package {key} must be an object array")
    return items


def _sources(event: dict[str, Any]) -> list[dict[str, Any]]:
    return _list_field(event, "sources")


def _eligible(event: dict[str, Any], as_of: date) -> int:
    return int(date.fromisoformat(str(event["effective_date"])[:10]) <= as_of)


def _quarantine_reasons(
    event: dict[str, Any], as_of: date, entity_names: set[str] | None = None
) -> list[str]:
    reasons = ["researcher_assertion_requires_review", "full_documents_not_acquired"]
    if not _eligible(event, as_of):
        reasons.append("post_as_of")
    if event.get("low_authority_only"):
        reasons.append("low_authority_only")
    if event.get("headline_generated"):
        reasons.append("generated_headline")
    if event.get("currency") == "headcount":
        reasons.append("invalid_currency")
    if event.get("amount") is None and event.get("currency") is not None:
        reasons.append("currency_without_amount")
    if entity_names is not None and any(
        str(item) not in entity_names for item in event.get("other_entities", [])
    ):
        reasons.append("unresolved_other_entity_reference")
    return sorted(reasons)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_existing_package(
    connection: sqlite3.Connection,
    package_id: str,
    stored: sqlite3.Row,
    hashes: dict[str, str],
    as_of: date,
    document: dict[str, Any],
    events: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    files: list[Path],
    directory: Path,
) -> None:
    expected_package = (
        hashes["archive"],
        hashes["events"],
        hashes["entities"],
        hashes["dedupe"],
        as_of.isoformat(),
        str(document.get("schema", "")),
        len(events),
        len(entities),
    )
    if tuple(stored) != expected_package:
        raise ValueError("candidate package file hashes or manifest changed")
    stored_files = {
        (str(row[0]), str(row[1]), int(row[2]))
        for row in connection.execute(
            "SELECT relative_path,sha256,byte_count FROM candidate_package_file WHERE package_id=?",
            (package_id,),
        )
    }
    expected_files = {
        (path.relative_to(directory).as_posix(), _file_hash(path), path.stat().st_size)
        for path in files
    }
    if stored_files != expected_files:
        raise ValueError("candidate package raw-file manifest changed")
    stored_entities = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT canonical_name,assertion_json FROM candidate_entity WHERE package_id=?",
            (package_id,),
        )
    }
    if stored_entities != {(str(item["canonical_name"]), _canonical(item)) for item in entities}:
        raise ValueError("candidate entity assertions changed")
    stored_events = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT candidate_event_id,assertion_json FROM candidate_event WHERE package_id=?",
            (package_id,),
        )
    }
    if stored_events != {(str(item["event_id"]), _canonical(item)) for item in events}:
        raise ValueError("candidate event assertions changed")
    if int(
        connection.execute(
            "SELECT COUNT(*) FROM candidate_source_edge WHERE package_id=?", (package_id,)
        ).fetchone()[0]
    ) != sum(len(_sources(item)) for item in events):
        raise ValueError("candidate source-edge lineage changed")
