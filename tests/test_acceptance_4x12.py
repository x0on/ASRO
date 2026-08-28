from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from asro.backfill.manifest import EpisodeManifest

ROOT = Path(__file__).parents[1]


def test_4x12_manifest_is_exact_and_controls_are_non_modeling_vintages() -> None:
    manifest = EpisodeManifest.from_toml(
        ROOT / "data/acceptance/current_ai_cycle_4x12_2025_slice.toml"
    )
    audit = json.loads(
        (ROOT / "data/acceptance/current_ai_4x12_control_vintage_audit.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest.period_start == date(2025, 1, 1)
    assert manifest.period_end == date(2025, 12, 31)
    assert manifest.entities == ["Alphabet", "Amazon", "Meta", "Microsoft"]
    assert manifest.feature_set_version == "current-ai-feature-family-12m-1.0.0"
    assert {(item.feature_key, item.feature_version) for item in manifest.features} == {
        ("ai_related_debt", "1.0.0"),
        ("ai_compute_contract_value_flow", "1.0.0"),
        ("ai_contingent_credit_support_stock", "1.0.0"),
    }
    assert {item.version for item in manifest.controls} == {"1.0.0-current-vintage"}
    assert audit["append_only_correction"] is True
    assert audit["modeling_allowed"] is False
    assert audit["required_modeling_version"] == "1.0.0-alfred-vintage"


def test_h1_queue_is_bounded_and_rejects_late_mixed_scope_lead() -> None:
    inventory = json.loads(
        (ROOT / "data/acceptance/current_ai_4x12_h1_queue_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    report = json.loads(
        (ROOT / "data/acceptance/current_ai_4x12_h1_search_report.json").read_text(encoding="utf-8")
    )

    assert inventory["selection"]["lead_cap"] == 25
    assert inventory["selection"]["selected_leads"] == len(inventory["documents"]) == 1
    lead = inventory["documents"][0]
    assert date.fromisoformat(lead["public_availability_at"][:10]) > date.fromisoformat(
        lead["row_period_end"]
    )
    assert report["qualifying_timely_leads"] == 0
    assert report["accepted_facts"] == 0
    assert report["modeling_allowed"] is False


def test_finalized_4x12_matrix_preserves_4x6_and_explicit_h1_unknowns() -> None:
    with sqlite3.connect(ROOT / "data/monitor.db") as connection:
        expanded = connection.execute(
            """SELECT build_id FROM dataset_build
               WHERE feature_set_version='current-ai-feature-family-12m-1.0.0'"""
        ).fetchone()
        assert expanded is not None

        expanded_counts = connection.execute(
            """SELECT COUNT(*), COUNT(value_numeric),
                      SUM(CASE WHEN period_start < '2025-07-01' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN period_start < '2025-07-01'
                                    AND value_numeric IS NOT NULL THEN 1 ELSE 0 END)
               FROM finalized_entity_feature_value WHERE build_id=?""",
            (expanded[0],),
        ).fetchone()
        preserved_counts = connection.execute(
            """SELECT COUNT(*), COUNT(value_numeric)
               FROM finalized_entity_feature_value value
               JOIN dataset_build build USING (build_id)
               WHERE build.feature_set_version='current-ai-feature-family-1.0.0'
               GROUP BY value.build_id"""
        ).fetchall()

    assert expanded_counts == (144, 12, 72, 0)
    assert (72, 12) in preserved_counts


def test_public_snapshot_labels_expanded_and_preserved_slices() -> None:
    snapshot = json.loads((ROOT / "site/data/snapshot.json").read_text(encoding="utf-8"))
    scope = snapshot["feature_family_scope"]

    assert scope["window"] == "2025-01 through 2025-12"
    assert scope["required_cells"] == 144
    assert scope["accepted_numeric_cells"] == 12
    assert scope["preserved_4x6_status"] == "finalized and unchanged"
    assert scope["modeling_allowed"] is False
    assert len(snapshot["feature_family"]) == 144
