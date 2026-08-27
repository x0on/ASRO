from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from asro.evidence.time import timestamp_text

_APPROVED_HOSTS = {"www.sec.gov", "data.sec.gov"}


def build_acceptance_queue(
    connection: sqlite3.Connection,
    manifest_path: Path,
    acquired_directory: Path,
) -> dict[str, object]:
    """Create an idempotent review queue without promoting evidence."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "candidate_acquisition_only":
        raise ValueError("queue manifest must remain candidate acquisition only")
    receipts_payload = json.loads(
        (acquired_directory / "acquisition-receipts.json").read_text(encoding="utf-8")
    )
    receipts = {str(item["id"]): item for item in receipts_payload["receipts"]}
    candidates: list[dict[str, Any]] = []
    for raw in manifest.get("documents", []):
        candidate = dict(raw)
        receipt = receipts.get(str(candidate.get("id")))
        if receipt is None:
            raise ValueError(f"missing acquired receipt: {candidate.get('id')}")
        source_url = str(receipt["final_url"])
        if urlparse(source_url).hostname not in _APPROVED_HOSTS:
            raise ValueError(f"unsupported authoritative host: {source_url}")
        content_path = acquired_directory / str(receipt["content_file"])
        content = content_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != receipt["content_sha256"]:
            raise ValueError(f"immutable document hash mismatch: {candidate['id']}")
        value = candidate.get("candidate_value_numeric")
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"candidate requires a finite numeric value: {candidate['id']}")
        passage = str(candidate.get("passage_text") or "").strip()
        locator = str(candidate.get("passage_locator") or "").strip()
        marker = str(candidate.get("passage_marker") or "").strip()
        if not passage or not locator or not marker or marker.encode() not in content:
            raise ValueError(f"candidate supporting passage is missing: {candidate['id']}")
        entity = str(candidate.get("entity") or "").strip()
        counterparty = str(candidate.get("counterparty") or "").strip()
        entity_role = str(candidate.get("entity_role") or "").strip()
        counterparty_role = str(candidate.get("counterparty_role") or "").strip()
        if (
            not all((entity, counterparty, entity_role, counterparty_role))
            or entity == counterparty
        ):
            raise ValueError(f"candidate roles are ambiguous: {candidate['id']}")
        event_at = str(candidate.get("event_at") or "")
        row_period_end = str(candidate.get("row_period_end") or "")
        availability = timestamp_text(receipt["public_availability_at"])
        row_cutoff = timestamp_text(f"{row_period_end}T23:59:59Z")
        rejection_reason = None
        if availability > row_cutoff:
            rejection_reason = "public availability is after the requested row cutoff"
        duplicate = connection.execute(
            """SELECT assignment.canonical_fact_id FROM observation_v2 observation
               JOIN canonical_fact_assignment assignment ON assignment.event_id=observation.event_id
               WHERE observation.entity_id=? AND observation.counterparty_entity_id=?
                 AND observation.feature_key=? AND observation.feature_version=?
                 AND observation.value_numeric=? AND substr(observation.event_at,1,10)=?
               ORDER BY assignment.available_at DESC LIMIT 1""",
            (
                entity,
                counterparty,
                candidate["feature_key"],
                candidate["feature_version"],
                float(value),
                event_at,
            ),
        ).fetchone()
        status = (
            "rejected" if rejection_reason else "duplicate_fact" if duplicate else "pending_review"
        )
        resolved = {
            "lead_id": str(candidate["id"]),
            "document_sha256": digest,
            "source_url": source_url,
            "entity_id": entity,
            "counterparty_entity_id": counterparty,
            "entity_role": entity_role,
            "counterparty_role": counterparty_role,
            "feature_key": str(candidate["feature_key"]),
            "feature_version": str(candidate["feature_version"]),
            "value_numeric": float(value),
            "unit": str(candidate["unit"]),
            "currency": candidate.get("currency"),
            "event_at": event_at,
            "public_availability_at": availability,
            "passage_locator": locator,
            "passage_text": passage,
            "status": status,
            "rejection_reason": rejection_reason,
            "canonical_fact_match": str(duplicate[0]) if duplicate else None,
        }
        candidates.append(resolved)
    candidates.sort(key=lambda item: item["lead_id"])
    canonical = json.dumps(candidates, sort_keys=True, separators=(",", ":"))
    manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    run_id = hashlib.sha256(canonical.encode()).hexdigest()
    existing = connection.execute(
        "SELECT manifest_json,item_count FROM acceptance_queue_run WHERE run_id=?", (run_id,)
    ).fetchone()
    if existing is not None:
        if tuple(existing) != (canonical, len(candidates)):
            raise ValueError("queue run identity collision")
        return _queue_report(connection, run_id)
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    connection.execute("BEGIN")
    try:
        connection.execute(
            "INSERT INTO acceptance_queue_run VALUES (?,?,?,?,?)",
            (run_id, manifest_sha256, canonical, len(candidates), now),
        )
        for candidate in candidates:
            queue_item_id = hashlib.sha256(f"{run_id}|{candidate['lead_id']}".encode()).hexdigest()
            connection.execute(
                """INSERT INTO acceptance_queue_item VALUES (
                   :queue_item_id,:run_id,:lead_id,:document_sha256,:source_url,
                   :entity_id,:counterparty_entity_id,:entity_role,:counterparty_role,
                   :feature_key,:feature_version,:value_numeric,:unit,:currency,:event_at,
                   :public_availability_at,:passage_locator,:passage_text,:status,
                   :rejection_reason,:canonical_fact_match,:candidate_json)""",
                {
                    **candidate,
                    "queue_item_id": queue_item_id,
                    "run_id": run_id,
                    "candidate_json": json.dumps(candidate, sort_keys=True, separators=(",", ":")),
                },
            )
    except Exception:
        connection.rollback()
        raise
    connection.commit()
    return _queue_report(connection, run_id)


def _queue_report(connection: sqlite3.Connection, run_id: str) -> dict[str, object]:
    rows = [
        dict(row)
        for row in connection.execute(
            """SELECT queue_item_id,lead_id,entity_id,counterparty_entity_id,
                      feature_key,feature_version,value_numeric,unit,currency,event_at,
                      public_availability_at,passage_locator,passage_text,status,
                      rejection_reason,canonical_fact_match,source_url,document_sha256
               FROM acceptance_queue_item WHERE run_id=? ORDER BY lead_id""",
            (run_id,),
        )
    ]
    return {
        "run_id": run_id,
        "item_count": len(rows),
        "status_counts": {
            status: sum(item["status"] == status for item in rows)
            for status in ("pending_review", "duplicate_fact", "rejected")
        },
        "items": rows,
        "auto_promoted": 0,
    }
