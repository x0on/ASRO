from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from asro.backfill.fundamentals import select_liquid_asset_points

ROOT = Path(__file__).parents[1]
ENDS = ("2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30")


def _submissions(accessions: list[str], forms: list[str] | None = None) -> dict[str, Any]:
    count = len(accessions)
    return {
        "filings": {
            "recent": {
                "accessionNumber": accessions,
                "filingDate": ["2025-02-01"] * count,
                "acceptanceDateTime": ["2025-02-01T20:00:00.000Z"] * count,
                "form": forms or ["10-K"] * count,
                "primaryDocument": [f"filing-{index}.htm" for index in range(count)],
            }
        }
    }


def _facts(rows: list[dict[str, object]], unit: str = "USD") -> dict[str, Any]:
    return {
        "facts": {
            "us-gaap": {"CashCashEquivalentsAndShortTermInvestments": {"units": {unit: rows}}}
        }
    }


def test_stock_selector_rejects_ytd_duration_facts() -> None:
    rows = [
        {
            "start": "2025-01-01",
            "end": end,
            "val": 10,
            "form": "10-Q",
            "filed": "2025-05-01",
            "accn": f"accn-{index}",
        }
        for index, end in enumerate(ENDS)
    ]
    selected, rejected = select_liquid_asset_points(
        "Alphabet", _facts(rows), _submissions([f"accn-{index}" for index in range(4)])
    )

    assert selected == []
    assert len(rejected) == 4


def test_stock_selector_rejects_non_usd_units() -> None:
    rows = [
        {
            "end": end,
            "val": 10,
            "form": "10-Q",
            "filed": "2025-05-01",
            "accn": f"accn-{index}",
        }
        for index, end in enumerate(ENDS)
    ]
    selected, rejected = select_liquid_asset_points(
        "Alphabet",
        _facts(rows, "shares"),
        _submissions([f"accn-{index}" for index in range(4)]),
    )

    assert selected == []
    assert len(rejected) == 4


def test_restatement_is_not_silently_substituted() -> None:
    rows = [
        {
            "end": end,
            "val": 10,
            "form": "10-Q",
            "filed": "2025-02-01",
            "accn": f"original-{index}",
        }
        for index, end in enumerate(ENDS)
    ]
    rows.append(
        {
            "end": ENDS[0],
            "val": 12,
            "form": "10-K/A",
            "filed": "2025-03-01",
            "accn": "amended-0",
        }
    )
    accessions = [f"original-{index}" for index in range(4)] + ["amended-0"]
    forms = ["10-Q"] * 4 + ["10-K/A"]

    selected, rejected = select_liquid_asset_points(
        "Alphabet", _facts(rows), _submissions(accessions, forms)
    )

    assert selected[0].value == 10
    assert selected[0].amendment is False
    assert rejected == [
        {
            "entity": "Alphabet",
            "period_end": "2024-12-31",
            "reason": "later filing value differs; requires append-only restatement review",
            "selected_accession": "original-0",
            "later_accession": "amended-0",
        }
    ]


def test_fundamentals_matrix_blocks_later_filing_and_expires_carry() -> None:
    with sqlite3.connect(ROOT / "data/monitor.db") as connection:
        connection.row_factory = sqlite3.Row
        build_id = connection.execute(
            """SELECT build.build_id FROM dataset_build build
               JOIN dataset_build_finalization finalized USING(build_id)
               WHERE feature_set_version='company-fundamentals-context-1.0.0'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()[0]
        rows = {
            (row["entity_id"], row["period_start"]): row
            for row in connection.execute(
                """SELECT entity_id,period_start,value_numeric,missingness_reason,fact_count
                   FROM finalized_entity_feature_value WHERE build_id=?""",
                (build_id,),
            )
        }

    assert rows[("Alphabet", "2025-03-01")]["value_numeric"] == 95_657_000_000
    assert rows[("Alphabet", "2025-04-01")]["value_numeric"] == 95_328_000_000
    assert rows[("Amazon", "2025-04-01")]["value_numeric"] is None
    assert rows[("Amazon", "2025-04-01")]["missingness_reason"] == "unknown"


def test_fundamentals_fact_lineage_deduplicates_carried_cells() -> None:
    with sqlite3.connect(ROOT / "data/monitor.db") as connection:
        build_id = connection.execute(
            """SELECT build_id FROM dataset_build
               WHERE feature_set_version='company-fundamentals-context-1.0.0'
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()[0]
        counts = connection.execute(
            """SELECT COUNT(*),COUNT(value_numeric),MAX(fact_count)
               FROM finalized_entity_feature_value WHERE build_id=?""",
            (build_id,),
        ).fetchone()
        distinct_facts = connection.execute(
            """SELECT COUNT(DISTINCT fact.canonical_fact_id)
               FROM finalized_entity_feature_value value
               JOIN feature_value_fact fact USING(feature_value_id)
               WHERE value.build_id=?""",
            (build_id,),
        ).fetchone()[0]

    assert counts == (48, 44, 1)
    assert distinct_facts == 16


def test_snapshot_bodies_match_acquisition_receipts() -> None:
    receipts = json.loads(
        (
            ROOT / "data/acceptance/acquired/company-fundamentals-4x12/acquisition-receipts.json"
        ).read_text(encoding="utf-8")
    )["receipts"]
    expected = {
        item["id"].split("-companyfacts", 1)[0]: item["content_sha256"]
        for item in receipts
        if "-companyfacts-" in item["id"]
    }
    with sqlite3.connect(ROOT / "data/monitor.db") as connection:
        rows = connection.execute(
            """SELECT item.title,document.text FROM documents document
               JOIN items item ON item.id=document.item_id
               WHERE item.title LIKE '%SEC Companyfacts immutable snapshot 2026-08-28'"""
        ).fetchall()
    actual = {
        title.split(" SEC Companyfacts", 1)[0].lower(): hashlib.sha256(text.encode()).hexdigest()
        for title, text in rows
    }

    assert actual == expected


def test_fundamentals_public_payload_is_separate_and_non_modeling() -> None:
    snapshot = json.loads((ROOT / "site/data/snapshot.json").read_text(encoding="utf-8"))
    scope = snapshot["fundamentals_scope"]

    assert scope["label"] == "total-company context; not AI-attributed"
    assert scope["modeling_allowed"] is False
    assert scope["required_cells"] == 48
    assert scope["accepted_numeric_cells"] == 44
    assert len(snapshot["fundamentals"]) == 48
