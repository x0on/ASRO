from datetime import UTC, datetime

import pytest

from asro.indicators import compute_evidence_reading

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def point(variable="ai_related_debt", entity="A", **changes):
    return {
        "event_id": variable + str(entity),
        "variable_key": variable,
        "entity": entity,
        "confidence": 0.8,
        "polarity": "risk",
        "unit": "signal",
        "value": 1,
        "url": "https://example.com/filing",
        "observed_at": NOW.isoformat(),
        "reviewed_at": NOW.isoformat(),
        "effective_date": "2026-09-01",
        **changes,
    }


def baseline():
    return [point(), point("ai_capital_commitments"), point("vendor_financing")]


def test_live_news_produces_reading_without_fake_numeric_amounts():
    dims, reading, points = compute_evidence_reading(baseline(), NOW)
    assert reading.score is not None and reading.score > 50
    assert dims["fragility"] > 50
    assert dims["stress"] is None
    assert len(points) == 3


def test_duplicate_and_weaker_repeated_risk_do_not_change_score():
    rows = baseline()
    before = compute_evidence_reading(rows, NOW)
    assert compute_evidence_reading(rows + rows, NOW) == before
    weaker = point(event_id="another-report", confidence=0.1)
    assert compute_evidence_reading(rows + [weaker], NOW) == before


def test_new_risk_in_new_or_existing_category_cannot_lower_headline():
    rows = baseline()
    before = compute_evidence_reading(rows, NOW)[1].score
    for new in (point(entity="B"), point("refinancing_stress", unit="score", value=1)):
        assert compute_evidence_reading(rows + [new], NOW)[1].score >= before


def test_source_company_resolves_missing_entity_without_inventing_independence():
    rows = [point(entity=None, source_companies='["CoreWeave"]')]
    duplicate = {**rows[0], "event_id": "second-excerpt"}
    assert len(compute_evidence_reading(rows + [duplicate], NOW)[2]) == 1
    assert not compute_evidence_reading([point(entity=None)], NOW)[2]


def test_fifth_point_uses_same_formula_as_fourth():
    rows = baseline() + [point(entity="B")]
    before = compute_evidence_reading(rows, NOW)
    after = compute_evidence_reading(rows + [point(entity="C")], NOW)
    assert after[1].score > before[1].score
    assert after[0]["fragility"] > before[0]["fragility"]


def test_greater_severity_never_lowers_score():
    scores = [
        compute_evidence_reading(
            baseline() + [point("refinancing_stress", unit="score", value=value)], NOW
        )[1].score
        for value in (1, 2, 3, 4, 5)
    ]
    assert scores == sorted(scores)
    invalid = point("refinancing_stress", unit="score", value=10)
    assert compute_evidence_reading(baseline() + [invalid], NOW)[0]["stress"] is None


def test_counter_evidence_is_preserved_and_can_lower_reading():
    rows = baseline()
    before = compute_evidence_reading(rows, NOW)[1].score
    counter = point("free_cash_flow_strength", polarity="safety", unit="USD", value=1e9)
    assert compute_evidence_reading(rows + [counter], NOW)[1].score < before


def test_cash_burn_cannot_be_reassurance_even_in_legacy_rows():
    rows = baseline()
    before = compute_evidence_reading(rows, NOW)[1].score
    burn = point("free_cash_flow_strength", polarity="safety", unit="USD", value=-1e9)
    assert compute_evidence_reading(rows + [burn], NOW)[1].score > before


@pytest.mark.parametrize(
    "text",
    [
        "Credit Facility: $11.7 billion of credit facilities, "
        "of which $1.3 billion was outstanding.",
        "Entered a $10 billion revolving credit agreement.",
        "Outstanding balances associated with letters of credit were $533 million.",
        "A $17.5 billion delayed draw term loan credit facility.",
    ],
)
def test_borrowing_capacity_is_not_scored_as_outstanding_debt(text):
    row = point(unit="USD", value=11.7e9, evidence_text=text)
    assert not compute_evidence_reading([row], NOW)[2]
    debt = point(unit="USD", value=1e9, evidence_text="Issued $1 billion in senior notes.")
    assert len(compute_evidence_reading([debt], NOW)[2]) == 1


def test_post_review_capture_and_site_share_cutoff(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from asro.cli import app
    from asro.site import build_static_site
    from asro.storage import SqliteRepository

    database = tmp_path / "state.db"
    monkeypatch.setenv("ASRO_DATABASE_PATH", str(database))
    result = CliRunner().invoke(app, ["capture-reading"])
    assert result.exit_code == 0, result.output
    with SqliteRepository(database).connect() as connection:
        snapshot = dict(SqliteRepository.recent_snapshots(connection)[0])
    output = build_static_site(tmp_path / "site", database)
    payload = json.loads((output / "data/snapshot.json").read_text())
    assert payload["signal"]["reading_as_of"] == snapshot["captured_at"]
    assert payload["signal"]["score"] == snapshot["score"]
    assert payload["dimensions"] == json.loads(snapshot["dimensions"])


def test_workflow_captures_after_review_before_packaging():
    from pathlib import Path

    workflow = Path(".github/workflows/monitor.yml").read_text()
    assert workflow.index("Daily evidence review") < workflow.index("asro capture-reading")
    assert workflow.index("asro capture-reading") < workflow.index("Package candidate immutable")


def test_published_direction_keeps_both_opposing_source_points(tmp_path, monkeypatch):
    import json

    from asro.site import build_static_site
    from asro.storage import SqliteRepository

    timestamp = datetime.now(UTC).isoformat()
    rows = [
        point(confidence=0.9, event_id="risk"),
        point(confidence=0.2, polarity="safety", unit="USD", event_id="counter"),
    ]
    for row in rows:
        row.update(observed_at=timestamp, reviewed_at=timestamp, effective_date=timestamp)
    monkeypatch.setattr(SqliteRepository, "recent_observations", lambda *a, **k: rows)
    output = build_static_site(tmp_path / "site", tmp_path / "state.db")
    payload = json.loads((output / "data/snapshot.json").read_text())
    assert payload["dimensions"]["fragility"] > 50
    assert payload["dimension_direction"]["fragility"] == {
        "direction": "higher_pressure",
        "evidence_count": 2,
    }
    assert len(payload["dimension_evidence_items"]["fragility"]) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"url": None},
        {"url": "javascript:alert(1)"},
        {"reviewed_at": None},
        {"reviewed_at": "2027-01-01"},
        {"effective_date": "2025-01-01"},
        {"effective_date": "bad date"},
        {"confidence": float("nan")},
    ],
)
def test_unusable_evidence_never_creates_zero_or_numeric_reading(change):
    dims, reading, points = compute_evidence_reading([point(**change)], NOW)
    assert all(value is None for value in dims.values())
    assert reading.score is None
    assert not points
