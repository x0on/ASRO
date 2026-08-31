"""Descriptive comparison of historical episodes.

This is deliberately transparent statistics: medians, median absolute deviations,
percentiles against pooled history, and level/velocity/breadth views. No model is fitted,
nothing is trained on the live heuristic score, and no episode month is labelled with an
outcome. The point is to see whether the episodes are distinguishable at all before
anyone considers calibrating against them.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from asro.benchmark.catalog import BENCHMARK_VARIABLES


@dataclass(frozen=True)
class MonthlySeries:
    episode_id: str
    stratum: str
    feature_key: str
    months: tuple[str, ...]
    values: tuple[float, ...]
    entity_counts: tuple[int, ...]
    expected_entities: int

    @property
    def level(self) -> float | None:
        return statistics.median(self.values) if self.values else None

    @property
    def velocity(self) -> float | None:
        """Median twelve-month change, so a trend is not confused with a level."""
        if len(self.values) < 13:
            return None
        deltas = [
            self.values[index] - self.values[index - 12] for index in range(12, len(self.values))
        ]
        return statistics.median(deltas) if deltas else None

    @property
    def breadth(self) -> float | None:
        """Share of the episode's entities carrying the measurement, averaged over months."""
        if not self.entity_counts or not self.expected_entities:
            return None
        return sum(self.entity_counts) / (len(self.entity_counts) * self.expected_entities)

    @property
    def confidence(self) -> float:
        """Coverage of the measurement, kept separate from its economic magnitude."""
        if not self.months:
            return 0.0
        return len(self.values) / len(self.months)


def episode_series(
    connection: sqlite3.Connection, episode_id: str, feature_keys: Sequence[str]
) -> list[MonthlySeries]:
    """Entity-median monthly series per feature for the episode's latest finalized build."""
    row = connection.execute(
        """SELECT run.run_id, episode.stratum, link.build_id, link.grain
             FROM backfill_run run
             JOIN backfill_episode episode
               ON episode.episode_id=run.episode_id AND episode.version=run.episode_version
             JOIN backfill_build_link link ON link.run_id=run.run_id
            WHERE run.episode_id=? AND link.grain='entity_month'
            ORDER BY run.created_at DESC LIMIT 1""",
        (episode_id,),
    ).fetchone()
    if row is None:
        return []
    stratum, build_id = str(row[1]), str(row[2])
    expected = int(
        connection.execute(
            "SELECT COUNT(DISTINCT entity_id) FROM feature_value WHERE build_id=?",
            (build_id,),
        ).fetchone()[0]
    )
    series: list[MonthlySeries] = []
    for feature_key in feature_keys:
        rows = connection.execute(
            """SELECT period_start,
                      GROUP_CONCAT(value_numeric) AS values_csv,
                      SUM(value_numeric IS NOT NULL) AS present
                 FROM feature_value
                WHERE build_id=? AND feature_key=?
                GROUP BY period_start ORDER BY period_start""",
            (build_id, feature_key),
        ).fetchall()
        months: list[str] = []
        values: list[float] = []
        counts: list[int] = []
        for period_start, values_csv, present in rows:
            months.append(str(period_start))
            counts.append(int(present or 0))
            if values_csv:
                parsed = [float(item) for item in str(values_csv).split(",") if item]
                if parsed:
                    values.append(statistics.median(parsed))
        series.append(
            MonthlySeries(
                episode_id=episode_id,
                stratum=stratum,
                feature_key=feature_key,
                months=tuple(months),
                values=tuple(values),
                entity_counts=tuple(counts),
                expected_entities=expected,
            )
        )
    return series


def robust_z(value: float, reference: Sequence[float]) -> float | None:
    """Deviation in median-absolute-deviation units; robust to the outliers a crisis creates."""
    if len(reference) < 4:
        return None
    median = statistics.median(reference)
    deviations = [abs(item - median) for item in reference]
    mad = statistics.median(deviations)
    if mad == 0:
        return None
    return (value - median) / (1.4826 * mad)


def percentile_of(value: float, reference: Sequence[float]) -> float | None:
    if not reference:
        return None
    below = sum(1 for item in reference if item < value)
    equal = sum(1 for item in reference if item == value)
    return (below + 0.5 * equal) / len(reference)


def compare_episodes(
    connection: sqlite3.Connection,
    episode_ids: Sequence[str],
    feature_keys: Sequence[str],
) -> dict[str, object]:
    """Level, velocity, breadth and pooled percentiles for every episode and feature."""
    collected: dict[str, list[MonthlySeries]] = {
        episode_id: episode_series(connection, episode_id, feature_keys)
        for episode_id in episode_ids
    }
    pooled: dict[str, list[float]] = {key: [] for key in feature_keys}
    for series_list in collected.values():
        for series in series_list:
            pooled[series.feature_key].extend(series.values)

    comparison: dict[str, object] = {}
    for episode_id, series_list in collected.items():
        features: dict[str, object] = {}
        for series in series_list:
            level = series.level
            variable = BENCHMARK_VARIABLES.get(series.feature_key)
            features[series.feature_key] = {
                "level": level,
                "velocity": series.velocity,
                "breadth": series.breadth,
                "confidence": round(series.confidence, 4),
                "months_with_value": len(series.values),
                "months_in_window": len(series.months),
                "pooled_percentile": (
                    percentile_of(level, pooled[series.feature_key]) if level is not None else None
                ),
                "pooled_robust_z": (
                    robust_z(level, pooled[series.feature_key]) if level is not None else None
                ),
                "direction": variable.direction.value if variable else None,
                "causal_role": variable.causal_role.value if variable else None,
            }
        comparison[episode_id] = {
            "stratum": series_list[0].stratum if series_list else None,
            "features": features,
        }
    return {
        "method": "median level, median 12-month change, entity breadth, pooled percentile",
        "note": (
            "Descriptive only. No episode month carries an outcome label and nothing here "
            "is fitted to the live heuristic score."
        ),
        "episodes": comparison,
    }


def false_positive_check(
    comparison: dict[str, object], feature_keys: Sequence[str]
) -> dict[str, object]:
    """Does a benign episode look like a crisis episode on these features?

    If benign booms sit at the same pooled percentiles as crisis episodes, the feature set
    cannot separate them, and any calibration built on it would raise false alarms. This
    reports that plainly rather than reporting only the flattering direction.
    """
    episodes = comparison.get("episodes", {})
    if not isinstance(episodes, dict):
        return {"error": "comparison payload is malformed"}
    by_stratum: dict[str, list[dict[str, object]]] = {}
    for episode_id, payload in episodes.items():
        if not isinstance(payload, dict):
            continue
        stratum = str(payload.get("stratum"))
        by_stratum.setdefault(stratum, []).append(
            {"episode_id": episode_id, "features": payload.get("features", {})}
        )

    def levels(stratum: str, feature_key: str) -> list[float]:
        out: list[float] = []
        for item in by_stratum.get(stratum, []):
            features = item["features"]
            if isinstance(features, dict):
                entry = features.get(feature_key)
                if isinstance(entry, dict) and entry.get("level") is not None:
                    out.append(float(entry["level"]))
        return out

    findings: list[dict[str, object]] = []
    for feature_key in feature_keys:
        crisis = levels("crisis", feature_key)
        benign = levels("benign", feature_key)
        if not crisis or not benign:
            findings.append(
                {
                    "feature_key": feature_key,
                    "separable": None,
                    "reason": "a stratum has no measured level for this feature",
                    "crisis_levels": crisis,
                    "benign_levels": benign,
                }
            )
            continue
        separable = min(crisis) > max(benign) or max(crisis) < min(benign)
        findings.append(
            {
                "feature_key": feature_key,
                "separable": separable,
                "crisis_range": [min(crisis), max(crisis)],
                "benign_range": [min(benign), max(benign)],
                "overlap": not separable,
            }
        )
    measured = [item for item in findings if item.get("separable") is not None]
    return {
        "question": "can these features tell a crisis episode from a benign boom?",
        "features_evaluated": len(measured),
        "features_separating": sum(1 for item in measured if item.get("separable")),
        "findings": findings,
        "caveat": (
            "Separation across a handful of episodes is not evidence of predictive power. "
            "With one accepted episode per stratum this cannot distinguish a real signal "
            "from a coincidence of two samples."
        ),
    }
