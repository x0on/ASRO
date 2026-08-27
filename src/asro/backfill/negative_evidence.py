from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_FORMS = {"8-K", "10-Q", "10-K", "424B2", "424B3", "424B5", "FWP", "S-3", "S-3ASR"}
_MONTHS = ["2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]


def enumerate_negative_evidence_universe(
    inventory_path: Path, receipt_path: Path, acquisition_directory: Path
) -> dict[str, object]:
    """Enumerate SEC accessions for review without assigning negative feature values."""
    inventory = _object(json.loads(inventory_path.read_text(encoding="utf-8")), "inventory")
    receipt = _object(json.loads(receipt_path.read_text(encoding="utf-8")), "receipt")
    entries = _objects(inventory.get("documents"), "inventory documents")
    receipts = {
        str(row["id"]): row for row in _objects(receipt.get("receipts"), "acquisition receipts")
    }
    cells: list[dict[str, object]] = []
    entities: list[dict[str, object]] = []
    for entry in entries:
        entry_id = str(entry["id"])
        acquired = receipts.get(entry_id)
        if acquired is None:
            raise ValueError(f"missing acquisition receipt: {entry_id}")
        content_path = acquisition_directory / str(acquired["content_file"])
        submissions = _object(json.loads(content_path.read_text(encoding="utf-8")), entry_id)
        recent = _object(_object(submissions.get("filings"), "filings").get("recent"), "recent")
        filings = _filings(recent)
        relevant = [row for row in filings if row["form"] in _FORMS]
        entity = str(entry["entity"])
        entities.append(
            {
                "entity": entity,
                "cik": str(entry["cik"]),
                "submission_sha256": acquired["content_sha256"],
                "fetched_at": acquired["fetched_at"],
                "recent_filing_count": len(filings),
                "relevant_filing_count": len(relevant),
            }
        )
        for month in _MONTHS:
            during_month = [row for row in relevant if str(row["filing_date"]).startswith(month)]
            subsequent_reports = sorted(
                (
                    row
                    for row in relevant
                    if row["form"] in {"10-Q", "10-K"} and str(row["filing_date"]) > f"{month}-31"
                ),
                key=lambda row: (row["filing_date"], row["accession"]),
            )
            cells.append(
                {
                    "entity": entity,
                    "month": month,
                    "in_month_accessions": during_month,
                    "first_subsequent_report": subsequent_reports[0]
                    if subsequent_reports
                    else None,
                    "decision": "missing_pending_full_filing_review",
                    "numeric_zero_created": False,
                }
            )
    return {
        "protocol": "ai_related_debt_negative_evidence_v1",
        "classification": "enumeration_only_not_accepted_coverage",
        "entities": entities,
        "cell_count": len(cells),
        "zero_cells_created": 0,
        "cells": cells,
    }


def _filings(recent: Mapping[str, Any]) -> list[dict[str, str]]:
    required = ("accessionNumber", "filingDate", "form", "primaryDocument")
    columns: dict[str, list[Any]] = {}
    for key in required:
        value = recent.get(key)
        if not isinstance(value, list):
            raise ValueError(f"SEC submissions missing list: {key}")
        columns[key] = value
    lengths = {len(value) for value in columns.values()}
    if len(lengths) != 1:
        raise ValueError("SEC submissions columns have inconsistent lengths")
    return [
        {
            "accession": str(columns["accessionNumber"][index]),
            "filing_date": str(columns["filingDate"][index]),
            "form": str(columns["form"][index]),
            "primary_document": str(columns["primaryDocument"][index]),
        }
        for index in range(next(iter(lengths), 0))
    ]


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _objects(value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    return value
