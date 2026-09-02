from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from asro.indicators import INDICATOR_VERSION, compute_dimension_scores
from asro.measurement import event_to_observation
from asro.models import EventType, FinancialEvent
from asro.site import comparable_history, public_market_alerts
from asro.storage import SqliteRepository

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def rows(value=1.0, confidence=1.0, count=5):
    return [
        dict(
            variable_key="external_capability_pressure",
            entity=str(i),
            value=value,
            unit="score",
            confidence=confidence,
            observed_at=NOW.isoformat(),
            effective_date="2026-09-01",
        )
        for i in range(count)
    ]


def test_threshold_does_not_switch_from_fake_numeric_severity():
    assert compute_dimension_scores(rows(count=4), as_of=NOW)["external_pressure"] is None
    assert compute_dimension_scores(rows(count=5), as_of=NOW)["external_pressure"] == 20


def test_scale_is_monotone_and_invalid_values_are_unknown():
    values = [
        compute_dimension_scores(rows(value=v), as_of=NOW)["external_pressure"]
        for v in (0, 1, 2, 3, 4, 5)
    ]
    assert values == sorted(values)
    assert values[-1] == 100
    for invalid in (5.1, 10, -1, float("nan"), None):
        assert compute_dimension_scores(rows(value=invalid), as_of=NOW)["external_pressure"] is None


def test_confidence_and_variable_weight_do_not_discount_severity():
    for confidence in (1.0, 0.1):
        assert compute_dimension_scores(rows(confidence=confidence), as_of=NOW)[
            "external_pressure"
        ] == pytest.approx(20)


def test_reingestion_does_not_make_old_event_fresh_and_bad_dates_fail_closed():
    for date in ("Fri, 02 Jan 2026 00:00:00 GMT", "invalid", "2027-01-01"):
        evidence = [dict(row, effective_date=date) for row in rows()]
        assert compute_dimension_scores(evidence, as_of=NOW)["external_pressure"] is None
    evidence = [dict(row, effective_date="Tue, 01 Sep 2026 00:00:00 GMT") for row in rows()]
    assert compute_dimension_scores(evidence, as_of=NOW)["external_pressure"] == 20


def test_ipo_filing_maps_to_early_stage_and_normalized_date():
    event = FinancialEvent(
        event_id="filing",
        document_id="source",
        event_type=EventType.FILES_FOR_IPO,
        source_entity="Anthropic",
        confidence=1,
        evidence_text="Anthropic confidentially files for IPO",
        extractor="test",
        effective_date="Mon, 01 Jun 2026 00:00:00 GMT",
    )
    observation = event_to_observation(event)
    assert observation is not None
    assert observation.variable_key == "public_market_transmission_stage"
    assert observation.value == 0.5
    assert observation.effective_date == "2026-06-01T00:00:00+00:00"
    with pytest.raises(ValidationError):
        type(observation).model_validate({**observation.model_dump(), "value": 10})


def test_alerts_are_reviewed_milestones_not_dependent_on_score():
    events = [
        dict(
            event_id=str(i),
            source_entity="Anthropic",
            event_type="FILES_FOR_IPO",
            review_status=status,
            url="https://example.com/source",
        )
        for i, status in enumerate(("confirmed", "flagged", "provisional"))
    ]
    alerts = public_market_alerts(events)
    assert len(alerts) == 1
    assert alerts[0]["event_id"] == "0"
    assert "potential" in alerts[0]["stage"]
    assert alerts[0]["url"] == events[0]["url"]
    template = Path("src/asro/templates/index.html").read_text()
    assert "renderPublicMarketAlerts();" in template
    assert "data.public_market_alerts" in template


def test_history_cannot_bridge_coverage_or_method_change():
    history = [
        dict(
            captured_at=f"2026-09-0{i}",
            indicator_version=INDICATOR_VERSION,
            dimensions={"capital": 50 if i != 2 else None},
        )
        for i in (3, 2, 1)
    ]
    assert len(comparable_history(history, {"capital": 50})) == 1
    history[0]["indicator_version"] = "legacy-v1"
    assert comparable_history(history, {"capital": 50}) == []


def test_new_snapshot_is_versioned(tmp_path):
    repository = SqliteRepository(tmp_path / "state.db")
    with repository.connect() as connection:
        repository.insert_snapshot(connection, NOW.isoformat(), None, "INSUFFICIENT EVIDENCE", {})
        assert repository.recent_snapshots(connection)[0]["indicator_version"] == INDICATOR_VERSION


def test_earlier_filing_does_not_reduce_existing_transmission_stage():
    existing = dict(rows()[0], variable_key="public_market_transmission_stage", value=2)
    filing = dict(existing, entity="new-issuer", value=0.5)
    assert compute_dimension_scores([existing, filing], as_of=NOW)["transmission"] == 40


def test_backer_is_not_promoted_to_ipo_issuer():
    event = FinancialEvent(
        event_id="backer",
        document_id="source",
        event_type=EventType.FILES_FOR_IPO,
        source_entity="Nvidia",
        confidence=1,
        evidence_text="SB Energy files for IPO with Nvidia backing",
        extractor="test",
    )
    assert event_to_observation(event) is None
    alerts = public_market_alerts([{**event.model_dump(), "review_status": "confirmed"}])
    assert alerts[0]["company"] == "Issuer attribution unresolved"
