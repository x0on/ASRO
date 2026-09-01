"""Make a control observation's vintage part of its identity.

A series, a period and a value were previously enough to identify a control observation.
That silently assumed one version of history: ingesting the genuine 2018-01-31 vintage of
unemployment into a database already holding today's revision collided, because both rows
claimed the same identity while carrying different numbers. Both are legitimate — the
revision is what the series says now, the vintage is what it said then — and both must be
keepable side by side.

`vintage` is added as a *generated* column read straight out of the provenance already
stored on every row. Nothing is rewritten, which matters because the table is append-only:
existing latest-revision rows keep their bytes, and the column cannot drift away from the
evidence that justifies it because it is not separately stored. Uniqueness then becomes
(series, version, period, vintage), so a vintage lands beside a revision instead of
colliding with it.
"""

from __future__ import annotations

VERSION = 18
NAME = "control_vintage_identity"

STATEMENTS: tuple[str, ...] = (
    """ALTER TABLE historical_control_observation_v2
       ADD COLUMN vintage TEXT
       GENERATED ALWAYS AS (
           COALESCE(json_extract(provenance_json, '$.vintage'), 'unknown')
       ) VIRTUAL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_control_observation_vintage_identity
         ON historical_control_observation_v2(
             series_id, series_version, period_start, period_end, vintage
         )""",
)
