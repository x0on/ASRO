from datetime import UTC, datetime

from asro.indicators import compute_convergence, compute_dimension_scores


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
        "confidence": 1.0,
        "observed_at": observed_at,
    }


def test_only_latest_value_per_variable_and_entity_counts() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    old_high = _obs(50_000_000_000, "2026-08-01T00:00:00+00:00")
    new_low = _obs(1_000_000_000, "2026-08-20T00:00:00+00:00")

    only_new = compute_dimension_scores([new_low], as_of=as_of)["fragility"]
    superseded = compute_dimension_scores([old_high, new_low], as_of=as_of)["fragility"]
    repeated = compute_dimension_scores([new_low] * 20, as_of=as_of)["fragility"]

    assert superseded == only_new  # the older value no longer contributes
    assert repeated == only_new  # twenty copies do not move the needle


def test_stale_observations_are_ignored() -> None:
    as_of = datetime(2026, 8, 21, tzinfo=UTC)
    stale = _obs(50_000_000_000, "2026-01-01T00:00:00+00:00")
    assert compute_dimension_scores([stale], as_of=as_of)["fragility"] is None
