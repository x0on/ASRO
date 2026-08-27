from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from asro.evidence.time import normalize_timestamp


@dataclass(frozen=True)
class ControlObservation:
    control_observation_id: str
    series_id: str
    series_version: str
    period_start: str
    period_end: str
    observed_at: str
    availability_at: str
    value_numeric: float
    unit: str
    provenance: dict[str, str]


def register_control_observation(
    connection: sqlite3.Connection, observation: ControlObservation
) -> bool:
    values = (
        observation.control_observation_id,
        observation.series_id,
        observation.series_version,
        observation.period_start,
        observation.period_end,
        normalize_timestamp(observation.observed_at).isoformat(timespec="seconds"),
        normalize_timestamp(observation.availability_at).isoformat(timespec="seconds"),
        observation.value_numeric,
        observation.unit,
        json.dumps(observation.provenance, sort_keys=True, separators=(",", ":")),
    )
    cursor = connection.execute(
        "INSERT OR IGNORE INTO historical_control_observation_v2 VALUES(?,?,?,?,?,?,?,?,?,?)",
        values,
    )
    if cursor.rowcount == 0:
        stored = connection.execute(
            "SELECT * FROM historical_control_observation_v2 WHERE control_observation_id=?",
            (observation.control_observation_id,),
        ).fetchone()
        if tuple(stored) != values:
            raise ValueError("control observation identity has different semantics")
        return False
    return True
