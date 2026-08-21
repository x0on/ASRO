from datetime import UTC, datetime

from asro.indicators import (
    compute_convergence,
    compute_dimension_scores,
    dimension_directional_readings,
    dimension_evidence_basis,
    dimension_evidence_counts,
    overall_evidence_direction,
)


def test_unknown_is_not_zero():
    scores = compute_dimension_scores([])
    assert all(value is None for value in scores.values())


def test_convergence_requires_multiple_dimensions():
    result = compute_convergence(
        {
            "capital": 80.0,
            "circularity": None,
            "monetization": None,
            "cannibalization": None,
            "fragility": None,
            "transmission": None,
            "stress": None,
            "external_pressure": None,
            "counter_evidence": None,
        }
    )
    assert result.score is None
    assert result.label == "INSUFFICIENT EVIDENCE"
    assert result.direction == "unknown"


def test_counter_evidence_reduces_convergence():
    base = {
        "capital": 70.0,
        "circularity": 70.0,
        "monetization": 70.0,
        "cannibalization": 70.0,
        "fragility": 70.0,
        "transmission": 70.0,
        "stress": 70.0,
        "external_pressure": 70.0,
        "counter_evidence": None,
    }
    a = compute_convergence(base).score
    base["counter_evidence"] = 20.0
    b = compute_convergence(base).score
    assert b < a


def test_neutral_counter_evidence_does_not_reduce_convergence() -> None:
    dimensions = _all(70.0)
    without_counter = compute_convergence(dimensions).score
    dimensions["counter_evidence"] = 50.0

    assert compute_convergence(dimensions).score == without_counter


def _all(value: float | None) -> dict[str, float | None]:
    return {
        "capital": value,
        "circularity": value,
        "monetization": value,
        "cannibalization": value,
        "fragility": value,
        "transmission": value,
        "stress": value,
        "external_pressure": value,
        "counter_evidence": None,
    }


def test_high_convergence_requires_both_documented_groups() -> None:
    # Strong everywhere except the propagation group: never the highest state.
    dims = _all(90.0)
    dims.update(fragility=10.0, stress=10.0, transmission=10.0)
    result = compute_convergence(dims)
    assert result.gate_passed is False
    assert result.label != "HIGH CONVERGENCE"

    # Strong everywhere except the economics group: never the highest state.
    dims = _all(90.0)
    dims.update(monetization=10.0, capital=10.0, circularity=10.0)
    result = compute_convergence(dims)
    assert result.gate_passed is False
    assert result.label != "HIGH CONVERGENCE"

    # Material evidence in one dimension of each group unlocks it.
    result = compute_convergence(_all(90.0))
    assert result.gate_passed is True
    assert result.label == "HIGH CONVERGENCE"


def _obs(value: float, observed_at: str, entity: str = "Nvidia") -> dict[str, object]:
    return {
        "variable_key": "ai_related_debt",
        "entity": entity,
        "value": value,
        "unit": "USD",
        "confidence": 1.0,
        "observed_at": observed_at,
    }


def test_only_latest_value_per_variable_and_entity_counts() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    old_high = _obs(50_000_000_000, "2026-08-01T00:00:00+00:00")
    new_low = _obs(1_000_000_000, "2026-08-20T00:00:00+00:00")

    companions = [
        _obs(2_000_000_000, "2026-08-20T00:00:00+00:00", "Microsoft"),
        _obs(3_000_000_000, "2026-08-20T00:00:00+00:00", "OpenAI"),
    ]
    only_new = compute_dimension_scores([new_low, *companions], as_of=as_of)["fragility"]
    superseded = compute_dimension_scores([old_high, new_low, *companions], as_of=as_of)[
        "fragility"
    ]
    repeated = compute_dimension_scores([new_low] * 20 + companions, as_of=as_of)["fragility"]

    assert superseded == only_new  # the older value no longer contributes
    assert repeated == only_new  # twenty copies do not move the needle


def test_stale_observations_are_ignored() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    stale = _obs(50_000_000_000, "2026-01-01T00:00:00+00:00")
    assert compute_dimension_scores([stale], as_of=as_of)["fragility"] is None


def test_unquantified_money_creates_a_shrunk_directional_estimate() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    observations = [
        {
            "variable_key": "ai_external_revenue",
            "entity": entity,
            "value": 1.0,
            "unit": "signal",
            "confidence": 0.72,
            "polarity": "safety",
            "observed_at": "2026-08-20T00:00:00+00:00",
        }
        for entity in ("OpenAI", "Anthropic", "Nvidia")
    ]

    assert compute_dimension_scores(observations, as_of=as_of)["monetization"] == 40.6
    assert dimension_evidence_counts(observations, as_of=as_of) == {}


def test_thin_directional_evidence_is_shrunk_until_five_numeric_points_exist() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    two = [
        _obs(10_000_000_000, "2026-08-20T00:00:00+00:00", "Nvidia"),
        _obs(20_000_000_000, "2026-08-20T00:00:00+00:00", "Microsoft"),
    ]
    five = [
        *two,
        _obs(30_000_000_000, "2026-08-20T00:00:00+00:00", "OpenAI"),
        _obs(40_000_000_000, "2026-08-20T00:00:00+00:00", "Amazon"),
        _obs(50_000_000_000, "2026-08-20T00:00:00+00:00", "Alphabet"),
    ]

    assert compute_dimension_scores(two, as_of=as_of)["fragility"] == 57.1
    assert compute_dimension_scores(five, as_of=as_of)["fragility"] is not None
    assert dimension_evidence_counts(five, as_of=as_of)["fragility"] == 5


def test_authoritative_market_stage_does_not_require_five_estimates() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    observation = {
        "variable_key": "public_market_transmission_stage",
        "entity": "SpaceX",
        "value": 2.0,
        "unit": "score",
        "confidence": 0.99,
        "observed_at": "2026-08-20T00:00:00+00:00",
    }

    scores = compute_dimension_scores([observation], as_of=as_of)
    assert scores["transmission"] == 39.6
    assert dimension_evidence_basis([observation], as_of=as_of) == {
        "transmission": "confirmed_trigger"
    }


def test_qualitative_evidence_sets_direction_and_a_conservative_estimate() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    observations = [
        {
            "variable_key": "refinancing_stress",
            "entity": entity,
            "value": 1.0,
            "unit": "signal",
            "confidence": 0.9,
            "polarity": "risk",
            "observed_at": "2026-08-20T00:00:00+00:00",
        }
        for entity in ("SpaceX", "CoreWeave")
    ]

    assert compute_dimension_scores(observations, as_of=as_of)["stress"] == 57.1
    assert dimension_directional_readings(observations, as_of=as_of)["stress"] == {
        "direction": "higher_pressure",
        "evidence_count": 2,
    }


def test_one_confirmed_directional_event_publishes_a_heavily_shrunk_estimate() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    observation = {
        "variable_key": "model_price_pressure",
        "entity": "OpenAI",
        "value": 1.0,
        "unit": "signal",
        "confidence": 0.99,
        "polarity": "risk",
        "observed_at": "2026-08-20T00:00:00+00:00",
    }

    assert compute_dimension_scores([observation], as_of=as_of)["cannibalization"] == 54.2
    assert dimension_evidence_basis([observation], as_of=as_of) == {
        "cannibalization": "directional_estimate"
    }


def test_overall_direction_uses_the_balance_of_confirmed_evidence() -> None:
    readings = {
        "capital": {"direction": "higher_pressure", "evidence_count": 6},
        "monetization": {"direction": "lower_pressure", "evidence_count": 2},
    }
    assert overall_evidence_direction(readings) == "higher_pressure"
