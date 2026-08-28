from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def test_batch4_review_is_bounded_complete_and_temporally_fail_closed() -> None:
    root = Path(__file__).parents[1]
    inventory = json.loads(
        (root / "data/acceptance/current_ai_acceptance_queue_batch4_inventory.json").read_text(
            encoding="utf-8"
        )
    )
    review = json.loads(
        (root / "data/acceptance/current_ai_acceptance_queue_batch4_review.json").read_text(
            encoding="utf-8"
        )
    )

    documents = {item["id"]: item for item in inventory["documents"]}
    decisions = {item["lead_id"]: item for item in review["decisions"]}
    assert 0 < len(documents) <= inventory["selection"]["lead_cap"] == 25
    assert documents.keys() == decisions.keys()
    assert review["auto_promoted_count"] == 0
    assert {key for key, value in decisions.items() if value["decision"] == "accepted"} == {
        "alphabet-q3-total-backstop-2025-09",
        "meta-october-cloud-capacity-total-2025",
    }
    for lead_id, decision in decisions.items():
        document = documents[lead_id]
        availability = date.fromisoformat(document["public_availability_at"][:10])
        row_cutoff = date.fromisoformat(document["row_period_end"])
        if decision["decision"] == "accepted":
            assert availability <= row_cutoff
        if decision["decision"] == "rejected_by_queue":
            assert availability > row_cutoff
