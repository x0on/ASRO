from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from asro.dictionary.registry import VARIABLES


def _normalize(value: float, scale: float) -> float:
    if value <= 0:
        return 0.0
    return 100.0 * (1.0 - 1.0 / (1.0 + value / scale))


# Observations older than this are stale and ignored; within it, only the newest value per
# (variable, entity) counts. The score therefore moves when a condition changes, not when
# more articles about the same condition accumulate.
WINDOW_DAYS = 90


def _parse(ts: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def latest_observations(
    observations: Iterable[dict[str, Any]],
    as_of: datetime,
    window_days: int = WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Newest observation per (variable, entity) observed within the window."""
    cutoff = as_of - timedelta(days=window_days)
    latest: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for obs in observations:
        observed_at = _parse(obs.get("observed_at"))
        if observed_at is None or observed_at < cutoff or observed_at > as_of:
            continue
        key = (str(obs.get("variable_key")), str(obs.get("entity") or ""))
        if key not in latest or observed_at > latest[key][0]:
            latest[key] = (observed_at, obs)
    return [obs for _, obs in latest.values()]


def compute_dimension_scores(
    observations: Iterable[dict[str, Any]],
    as_of: datetime | None = None,
    window_days: int = WINDOW_DAYS,
) -> dict[str, float | None]:
    as_of = as_of or datetime.now(UTC)
    buckets: dict[str, list[float]] = defaultdict(list)
    for obs in latest_observations(observations, as_of, window_days):
        key = obs.get("variable_key")
        definition = VARIABLES.get(str(key))
        if not definition:
            continue
        raw = float(obs.get("value") or 0.0)
        confidence = float(obs.get("confidence") or 0.0)
        if definition.unit == "USD":
            normalized = _normalize(raw, 10_000_000_000.0)
        elif definition.unit == "percent":
            normalized = min(100.0, max(0.0, raw))
        else:
            normalized = min(100.0, max(0.0, raw * 20.0 if raw <= 5 else raw))
        if definition.direction == "higher_is_safer":
            normalized = 100.0 - normalized
        buckets[definition.dimension.value].append(normalized * confidence * definition.weight)

    dimensions = {v.dimension.value for v in VARIABLES.values()}
    return {
        dimension: None
        if not buckets.get(dimension)
        else min(100.0, sum(buckets[dimension]) / len(buckets[dimension]))
        for dimension in dimensions
    }


# Warning-gate policy, from docs/DATA_DICTIONARY.md "Interaction triggers":
# the highest state needs independent evidence in at least three dimensions,
# including one from each of these two groups. "Material" evidence means >= MATERIAL.
PROPAGATION_GROUP = ("fragility", "stress", "transmission")
ECONOMICS_GROUP = ("monetization", "capital", "circularity")
RISK_DIMENSIONS = (
    "capital",
    "circularity",
    "monetization",
    "cannibalization",
    "fragility",
    "transmission",
    "stress",
    "external_pressure",
)
MIN_KNOWN_DIMENSIONS = 3
MATERIAL = 55.0
COUNTER_EVIDENCE_WEIGHT = 0.25


class ConvergenceResult(BaseModel):
    """The public headline. `score` is None whenever evidence is insufficient."""

    score: float | None
    label: str
    # Trend over time is not measured yet; never report a direction we did not compute.
    direction: Literal["unknown"] = "unknown"
    known_dimensions: int
    gate_passed: bool
    gate_reason: str


def _material(dimensions: dict[str, float | None], group: tuple[str, ...]) -> list[str]:
    return [d for d in group if (v := dimensions.get(d)) is not None and v >= MATERIAL]


def compute_convergence(dimensions: dict[str, float | None]) -> ConvergenceResult:
    known = [v for d in RISK_DIMENSIONS if (v := dimensions.get(d)) is not None]
    if len(known) < MIN_KNOWN_DIMENSIONS:
        return ConvergenceResult(
            score=None,
            label="INSUFFICIENT EVIDENCE",
            known_dimensions=len(known),
            gate_passed=False,
            gate_reason=f"{len(known)} of {MIN_KNOWN_DIMENSIONS} required dimensions measured",
        )

    base = sum(known) / len(known)
    counter = dimensions.get("counter_evidence")
    if counter is not None:
        base = max(0.0, base - (100.0 - counter) * COUNTER_EVIDENCE_WEIGHT)

    propagation = _material(dimensions, PROPAGATION_GROUP)
    economics = _material(dimensions, ECONOMICS_GROUP)
    gate_passed = bool(propagation and economics)
    if gate_passed:
        gate_reason = f"material evidence in {propagation[0]} and {economics[0]}"
    elif not propagation:
        gate_reason = "no material evidence in fragility/stress/transmission"
    else:
        gate_reason = "no material evidence in monetization/capital/circularity"

    if base < 25:
        label = "DISPERSED"
    elif base < 45:
        label = "FORMING"
    elif base < 65:
        label = "BUILDING PRESSURE"
    elif base < 80 or not gate_passed:
        label = "FRAGILE"
    else:
        label = "HIGH CONVERGENCE"

    return ConvergenceResult(
        score=round(base, 1),
        label=label,
        known_dimensions=len(known),
        gate_passed=gate_passed,
        gate_reason=gate_reason,
    )
