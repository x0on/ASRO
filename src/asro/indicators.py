from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from math import isfinite
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from asro.dictionary.registry import VARIABLES
from asro.entities import canonicalize


def _normalize(value: float, scale: float) -> float:
    if value <= 0:
        return 0.0
    return 100.0 * (1.0 - 1.0 / (1.0 + value / scale))


# Observations older than this are stale and ignored; within it, only the newest value per
# (variable, entity) counts. The score therefore moves when a condition changes, not when
# more articles about the same condition accumulate.
WINDOW_DAYS = 90
MIN_DIMENSION_POINTS = 5
MIN_DIRECTIONAL_POINTS = 1
DIRECTIONAL_PRIOR_STRENGTH = 5
DIRECTIONAL_MAX_DEVIATION = 25.0
MAX_PLAUSIBLE_USD = 5_000_000_000_000.0
INDICATOR_VERSION = "reviewed-evidence-v3"


def _parse(ts: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(str(ts))
        except (TypeError, ValueError, OverflowError):
            return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


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
        event_at = _parse(obs.get("effective_date")) if obs.get("effective_date") else observed_at
        if (
            observed_at is None
            or observed_at > as_of
            or event_at is None
            or event_at < cutoff
            or event_at > as_of
        ):
            continue
        key = (str(obs.get("variable_key")), str(obs.get("entity") or ""))
        if key not in latest or event_at > latest[key][0]:
            latest[key] = (event_at, obs)
    return [obs for _, obs in latest.values()]


def compute_dimension_scores(
    observations: Iterable[dict[str, Any]],
    as_of: datetime | None = None,
    window_days: int = WINDOW_DAYS,
) -> dict[str, float | None]:
    as_of = as_of or datetime.now(UTC)
    rows = list(observations)
    buckets = _dimension_buckets(rows, as_of, window_days)

    dimensions = {v.dimension.value for v in VARIABLES.values()}
    scores: dict[str, float | None] = {}
    for dimension in dimensions:
        points = buckets.get(dimension, [])
        milestones = [point for point in points if point[2] == "confirmed_trigger"]
        if milestones:
            # Stage is an ordinal frontier, not an average across companies.
            # Another earlier-stage issuer must not reduce established reach.
            scores[dimension] = max(point[0] / point[3] for point in milestones)
            continue
        required = min((point[1] for point in points), default=MIN_DIMENSION_POINTS)
        if len(points) >= required:
            scores[dimension] = sum(point[0] for point in points) / sum(
                point[3] for point in points
            )
            continue
        # Qualitative direction is published separately; it is not a numeric severity.
        scores[dimension] = None
    return scores


def dimension_evidence_counts(
    observations: Iterable[dict[str, Any]],
    as_of: datetime | None = None,
    window_days: int = WINDOW_DAYS,
) -> dict[str, int]:
    """Evidence points that are recent, independent, and eligible for scoring."""
    buckets = _dimension_buckets(observations, as_of or datetime.now(UTC), window_days)
    return {dimension: len(values) for dimension, values in buckets.items()}


def dimension_evidence_basis(
    observations: Iterable[dict[str, Any]],
    as_of: datetime | None = None,
    window_days: int = WINDOW_DAYS,
) -> dict[str, str]:
    """Explain whether a score is an aggregate estimate or an authoritative trigger."""
    rows = list(observations)
    moment = as_of or datetime.now(UTC)
    buckets = _dimension_buckets(rows, moment, window_days)
    directional = _direction_buckets(rows, moment, window_days)
    result: dict[str, str] = {}
    for dimension in set(buckets) | set(directional):
        points = buckets.get(dimension, [])
        required = min((point[1] for point in points), default=MIN_DIMENSION_POINTS)
        if any(point[2] == "confirmed_trigger" for point in points):
            result[dimension] = "confirmed_trigger"
        elif len(points) >= required:
            result[dimension] = "aggregate"
        elif len(directional.get(dimension, [])) >= MIN_DIRECTIONAL_POINTS:
            result[dimension] = "directional_estimate"
        else:
            result[dimension] = "aggregate"
    return result


def dimension_directional_readings(
    observations: Iterable[dict[str, Any]],
    as_of: datetime | None = None,
    window_days: int = WINDOW_DAYS,
) -> dict[str, dict[str, int | str]]:
    """Direction from confirmed evidence, without promoting it to a numeric score."""
    readings = _direction_buckets(observations, as_of or datetime.now(UTC), window_days)

    result: dict[str, dict[str, int | str]] = {}
    for dimension, values in readings.items():
        balance = sum(values) / len(values)
        if balance > 0.2:
            direction = "higher_pressure"
        elif balance < -0.2:
            direction = "lower_pressure"
        else:
            direction = "mixed"
        result[dimension] = {"direction": direction, "evidence_count": len(values)}
    return result


def _direction_buckets(
    observations: Iterable[dict[str, Any]], as_of: datetime, window_days: int
) -> dict[str, list[float]]:
    readings: dict[str, list[float]] = defaultdict(list)
    for obs in latest_observations(observations, as_of, window_days):
        definition = VARIABLES.get(str(obs.get("variable_key")))
        if definition is None:
            continue
        confidence = float(obs.get("confidence") or 0.0)
        if confidence <= 0:
            continue
        polarity = str(obs.get("polarity") or "risk")
        readings[definition.dimension.value].append(
            -confidence if polarity == "safety" else confidence
        )
    return readings


def overall_evidence_direction(readings: dict[str, dict[str, int | str]]) -> str:
    """Summarize the balance of directional evidence without calling it a trend."""
    balance = 0
    for reading in readings.values():
        count = int(reading.get("evidence_count") or 0)
        direction = reading.get("direction")
        if direction == "higher_pressure":
            balance += count
        elif direction == "lower_pressure":
            balance -= count
    if balance > 0:
        return "higher_pressure"
    if balance < 0:
        return "lower_pressure"
    return "mixed" if readings else "unknown"


def _dimension_buckets(
    observations: Iterable[dict[str, Any]], as_of: datetime, window_days: int
) -> dict[str, list[tuple[float, int, str, float]]]:
    buckets: dict[str, list[tuple[float, int, str, float]]] = defaultdict(list)
    for obs in latest_observations(observations, as_of, window_days):
        key = obs.get("variable_key")
        definition = VARIABLES.get(str(key))
        if not definition:
            continue
        if obs.get("value") is None:
            continue
        raw = float(obs["value"])
        confidence = float(obs.get("confidence") or 0.0)
        if not isfinite(raw) or not 0 < confidence <= 1 or definition.weight <= 0:
            continue
        if definition.unit == "USD":
            if obs.get("unit") != "USD" or raw <= 0 or raw > MAX_PLAUSIBLE_USD:
                continue
            normalized = _normalize(raw, 10_000_000_000.0)
        elif definition.unit == "percent":
            if obs.get("unit") != "percent" or not 0 <= raw <= 100:
                continue
            normalized = min(100.0, max(0.0, raw))
        else:
            if obs.get("unit") != "score" or not 0 <= raw <= 5:
                continue
            normalized = raw * 20.0
        if definition.direction == "higher_is_safer":
            normalized = 100.0 - normalized
        buckets[definition.dimension.value].append(
            (
                normalized * confidence * definition.weight,
                definition.minimum_points,
                definition.evidence_basis,
                confidence * definition.weight,
            )
        )
    return buckets


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
        # Counter-evidence uses the same risk-oriented scale as every other
        # dimension: 50 is neutral and lower values are reassuring. Only the
        # reassuring distance below neutral should reduce the headline score.
        reassurance = max(0.0, 50.0 - counter)
        base = max(0.0, base - reassurance * COUNTER_EVIDENCE_WEIGHT)

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


def evidence_points(
    observations: Iterable[dict[str, Any]], as_of: datetime
) -> list[dict[str, Any]]:
    """Strongest supported risk and counterpoint per variable/entity within 90 days.

    Repeated reporting cannot add votes. A weaker additional risk point cannot
    replace a stronger one. Opposing evidence remains a separate contribution.
    """
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in observations:
        entity = row.get("entity")
        if not entity:
            try:
                companies = json.loads(str(row.get("source_companies") or "[]"))
            except (ValueError, TypeError):
                continue
            if not isinstance(companies, list) or len(companies) != 1:
                continue
            entity = companies[0]
        if not isinstance(entity, str) or not entity.strip():
            continue
        row = {**row, "entity": canonicalize(entity)}
        # A qualitative cash-flow/revenue report alone is not reassuring evidence.
        if row.get("polarity") == "safety" and row.get("unit") == "signal":
            continue
        if row.get("variable_key") == "free_cash_flow_strength":
            try:
                if float(row["value"]) < 0:
                    row = {**row, "polarity": "risk"}
            except (KeyError, TypeError, ValueError):
                continue
        definition = VARIABLES.get(str(row.get("variable_key")))
        if definition is None or row.get("polarity") not in {"risk", "safety"}:
            continue
        try:
            url = urlsplit(str(row.get("url") or ""))
            confidence = float(row.get("confidence") or 0)
        except (ValueError, TypeError):
            continue
        if url.scheme not in {"https", "http"} or not url.hostname:
            continue
        observed = _parse(row.get("observed_at"))
        effective = _parse(row.get("effective_date"))
        reviewed = _parse(row.get("reviewed_at"))
        if any(moment is None or moment > as_of for moment in (observed, reviewed)):
            continue
        if effective is None or not as_of - timedelta(days=WINDOW_DAYS) <= effective <= as_of:
            continue
        if not isfinite(confidence) or not 0 < confidence <= 1:
            continue
        # Qualitative events support direction, not an invented dollar amount.
        strength = 1.0
        if row.get("unit") == "score":
            try:
                raw = float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            if not isfinite(raw) or not 0 <= raw <= 5:
                continue
            strength = raw / 5
        if strength <= 0:
            continue
        key = (definition.key, str(row["entity"]), str(row["polarity"]))
        point = {
            **row,
            "dimension": definition.dimension.value,
            "support": confidence * strength * definition.weight,
        }
        previous = selected.get(key)
        if previous is None or (point["support"], str(point.get("event_id"))) > (
            previous["support"],
            str(previous.get("event_id")),
        ):
            selected[key] = point
    return [selected[key] for key in sorted(selected)]


def compute_evidence_reading(
    observations: Iterable[dict[str, Any]], as_of: datetime | None = None
) -> tuple[dict[str, float | None], ConvergenceResult, list[dict[str, Any]]]:
    """One signed-support estimator at every coverage level, with explicit unknowns.

    50 + 50*(risk-support - counter-support)/(5 + total-support).
    Confidence controls support, not an apparent discount to risk severity.
    The same calculation supplies the headline and each dimension; it never
    switches to a raw-value mean when a fifth point arrives.
    """
    points = evidence_points(observations, as_of or datetime.now(UTC))

    def reading(rows: list[dict[str, Any]]) -> float | None:
        if not rows:
            return None
        positive = sum(p["support"] for p in rows if p["polarity"] == "risk")
        negative = sum(p["support"] for p in rows if p["polarity"] == "safety")
        return round(float(50 + 50 * (positive - negative) / (5 + positive + negative)), 1)

    dimensions = {
        v.dimension.value: reading([p for p in points if p["dimension"] == v.dimension.value])
        for v in VARIABLES.values()
    }
    result = compute_convergence(dimensions)
    if result.known_dimensions >= MIN_KNOWN_DIMENSIONS:
        result.score = reading(points)
        assert result.score is not None
        result.label = (
            "DISPERSED"
            if result.score < 25
            else "FORMING"
            if result.score < 45
            else "BUILDING PRESSURE"
            if result.score < 65
            else "FRAGILE"
            if result.score < 80 or not result.gate_passed
            else "HIGH CONVERGENCE"
        )
    return dimensions, result, points
