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

    @property
    def vintage(self) -> str:
        """Identity-bearing: two vintages of one period are different observations."""
        return self.provenance.get("vintage", "unknown")


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
        """INSERT OR IGNORE INTO historical_control_observation_v2(
               control_observation_id, series_id, series_version, period_start, period_end,
               observed_at, availability_at, value_numeric, unit, provenance_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        values,
    )
    if cursor.rowcount == 0:
        stored = connection.execute(
            """SELECT control_observation_id, series_id, series_version, period_start,
                      period_end, observed_at, availability_at, value_numeric, unit,
                      provenance_json
                 FROM historical_control_observation_v2 WHERE control_observation_id=?""",
            (observation.control_observation_id,),
        ).fetchone()
        if stored is None:
            # The row was rejected by the (series, version, period, vintage) uniqueness
            # rule rather than by its own id: an equivalent observation is already stored
            # under a different identifier.
            raise ValueError(
                "a control observation for this series, period and vintage already "
                "exists under a different identifier"
            )
        if tuple(stored) != values:
            raise ValueError("control observation identity has different semantics")
        return False
    return True
