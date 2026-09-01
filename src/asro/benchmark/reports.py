"""Regenerate every benchmark report from one database, in one pass.

The reports have to agree with each other. Refreshing readiness while leaving coverage,
leakage and the vintage report describing an older build produces a set of files that
contradict themselves, and the contradiction would appear exactly when it matters most:
after a successful vintage acquisition changes the verdict.

So there is one entry point, it reads a single connection, and it writes all of them.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from asro.benchmark.analysis import compare_episodes, false_positive_check
from asro.benchmark.catalog import BENCHMARK_VARIABLES
from asro.benchmark.controls_ingest import UNAVAILABLE_CONTROL_SERIES
from asro.benchmark.episodes import ROSTERS, EpisodeRoster
from asro.benchmark.readiness import (
    CalibrationReadiness,
    EpisodeAcceptance,
    episode_acceptances,
    evaluate_readiness,
    load_documented_insufficiency,
)
from asro.benchmark.vintages import revised_series_for

REPORT_NAMES: tuple[str, ...] = (
    "readiness.json",
    "revision_and_vintage.json",
    "coverage.json",
    "leakage.json",
    "missingness.json",
    "episode_comparison.json",
    "false_positive_analysis.json",
    "acquisition_receipts.json",
)


def _write(directory: Path, name: str, payload: object) -> None:
    (directory / name).write_text(
        json.dumps(payload, indent=1, default=str) + "\n", encoding="utf-8"
    )


def _coverage(
    connection: sqlite3.Connection,
    rosters: tuple[EpisodeRoster, ...],
    run_ids: Sequence[str],
) -> dict[str, Any]:
    placeholders = _placeholders(run_ids)
    episodes: dict[str, Any] = {}
    for row in connection.execute(
        f"""SELECT episode_id, coverage_passed, leakage_passed, source_count,
                   control_count, coverage_cell_count FROM backfill_run
             WHERE run_id IN ({placeholders}) ORDER BY 1""",  # noqa: S608
        tuple(run_ids),
    ):
        episodes[str(row[0])] = {
            "coverage_passed": bool(row[1]),
            "leakage_passed": bool(row[2]),
            "source_count": row[3],
            "control_count": row[4],
            "coverage_cells": row[5],
            "metrics": {},
        }
    for row in connection.execute(
        f"""SELECT run.episode_id, m.dimension, m.present_count, m.total_count, m.threshold
              FROM backfill_coverage_metric m
              JOIN backfill_run run ON run.run_id = m.run_id
             WHERE m.run_id IN ({placeholders})""",  # noqa: S608
        tuple(run_ids),
    ):
        ratio = row[2] / row[3] if row[3] else None
        episodes[str(row[0])]["metrics"][str(row[1])] = {
            "present": row[2],
            "total": row[3],
            "ratio": round(ratio, 4) if ratio is not None else None,
            "threshold": row[4],
            "meets_threshold": bool(ratio is not None and ratio >= row[4]),
        }
    for roster in rosters:
        entry = episodes.setdefault(
            roster.episode_id,
            {"measurable": False, "reason": roster.unmeasurable_reason},
        )
        revised = revised_series_for(roster)
        entry.update(
            {
                "window": f"{roster.period_start}..{roster.period_end}",
                "stratum": roster.stratum,
                "entities": [plan.entity_id for plan in roster.entities],
                "features": list(roster.features),
                "controls": list(roster.controls),
                "vintage_required_for": list(revised),
                "vintage_date_needed": (
                    roster.availability_cutoff.isoformat() if revised else None
                ),
            }
        )
        if roster.substitutions:
            entry["substitutions"] = roster.substitutions
    return {"episodes": episodes}


def _vintage(
    connection: sqlite3.Connection,
    rosters: tuple[EpisodeRoster, ...],
    acceptances: Sequence[EpisodeAcceptance],
) -> dict[str, Any]:
    """Control vintages as the reported runs froze them, plus the store they were drawn from.

    `control_series` is what the gate actually judged: the snapshots pinned to these run
    IDs. The shared store also holds rows from superseded runs and from acquisitions no
    accepted run reads, so it is reported separately under `store_inventory` rather than
    being mixed in -- a superseded latest-revision row must never read as selected.
    """
    run_ids = [item.run_id for item in acceptances]
    accepted_run_ids = [item.run_id for item in acceptances if item.accepted]
    placeholders = _placeholders(run_ids)
    series: dict[str, list[dict[str, object]]] = {}
    for row in connection.execute(
        f"""SELECT snapshot.series_id,
                   COALESCE(json_extract(snapshot.provenance_json, '$.vintage'), 'unknown'),
                   json_extract(snapshot.provenance_json, '$.fred_series_id'),
                   snapshot.run_id,
                   COUNT(*)
              FROM backfill_control_snapshot_v2 AS snapshot
             WHERE snapshot.run_id IN ({placeholders})
             GROUP BY 1, 2, 3, 4 ORDER BY 1, 2""",  # noqa: S608
        tuple(run_ids),
    ):
        series.setdefault(str(row[0]), []).append(
            {
                "vintage": row[1],
                "fred_series_id": row[2],
                "run_id": row[3],
                "in_accepted_run": str(row[3]) in set(accepted_run_ids),
                "observations": row[4],
            }
        )
    inventory: dict[str, list[dict[str, object]]] = {}
    for row in connection.execute(
        """SELECT series_id, vintage, json_extract(provenance_json, '$.fred_series_id'),
                  COUNT(*)
             FROM historical_control_observation_v2 GROUP BY 1, 2, 3 ORDER BY 1, 2"""
    ):
        inventory.setdefault(str(row[0]), []).append(
            {"vintage": row[1], "fred_series_id": row[2], "observations": row[3]}
        )
    return {
        "vintage_rule": ("earliest XBRL filing at or before the episode availability cutoff"),
        "control_vintage_markings": {
            "as_published": "never materially revised",
            "point_in_time:<date>": (
                "FRED API with realtime_start=realtime_end=<date>; the date must not "
                "postdate the episode cutoff"
            ),
            "latest_revision": "today's revised value; blocks calibration",
        },
        "acquisition_command": "asro acquire-vintages",
        "per_episode_vintage_dates": {
            roster.episode_id: roster.availability_cutoff.isoformat()
            for roster in rosters
            if revised_series_for(roster)
        },
        "reported_run_ids": list(run_ids),
        "accepted_run_ids": list(accepted_run_ids),
        "control_series": series,
        "control_series_note": (
            "control_series is scoped to reported_run_ids: the snapshots the reported "
            "runs froze, one entry per run. Only entries with in_accepted_run can block "
            "calibration, which is why a revised vintage may appear here without "
            "appearing in the gate's revised_only_control_series. store_inventory is the "
            "whole shared store, including vintages no reported run selected."
        ),
        "store_inventory": inventory,
        "unavailable_control_series": UNAVAILABLE_CONTROL_SERIES,
    }


def _receipts(connection: sqlite3.Connection) -> dict[str, Any]:
    series = [
        {
            "series_id": row[0],
            "fred_series_id": row[1],
            "vintage": row[2],
            "source_url": row[3],
            "content_sha256": row[4],
            "monthly_observations": row[5],
        }
        for row in connection.execute(
            """SELECT series_id, json_extract(provenance_json, '$.fred_series_id'),
                      vintage, json_extract(provenance_json, '$.source_url'),
                      json_extract(provenance_json, '$.content_sha256'), COUNT(*)
                 FROM historical_control_observation_v2
                GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 3"""
        )
    ]
    filings = [
        {
            "document_id": row[0],
            "title": row[1],
            "url": row[2],
            "filed": row[3],
            "entities": json.loads(str(row[4])),
            "observations": row[5],
        }
        for row in connection.execute(
            """SELECT item.id, item.title, item.url, item.published_at, item.companies,
                      COUNT(observation.observation_id)
                 FROM items item
                 JOIN observation_v2 observation
                   ON observation.source_document_id = item.id
                WHERE item.source = 'SEC EDGAR filing'
                GROUP BY 1, 2, 3, 4, 5 ORDER BY item.published_at"""
        )
    ]
    return {
        "control_series_acquired": series,
        "sec_filings_acquired_count": len(filings),
        "sec_filings_acquired": filings,
    }


def _placeholders(run_ids: Sequence[str]) -> str:
    return ",".join("?" for _ in run_ids) or "''"


def write_benchmark_reports(
    connection: sqlite3.Connection,
    output_directory: Path,
    *,
    insufficiency_path: Path,
    rosters: tuple[EpisodeRoster, ...] = ROSTERS,
    episode_ids: Sequence[str] | None = None,
) -> CalibrationReadiness:
    """Write every benchmark report from this connection and return the readiness result.

    Every report is restricted to one shared set of run IDs: exactly the latest run per
    episode, as `episode_acceptances` selects them. A rebuild leaves an older run behind,
    and a report that queried all of `backfill_run` would describe both -- so readiness
    could call an episode accepted while coverage or leakage still reported the run it
    replaced. The set is computed once here and passed down, rather than each report
    re-deriving it.
    """
    output_directory.mkdir(parents=True, exist_ok=True)
    acceptances = episode_acceptances(connection, episode_ids)
    run_ids = [item.run_id for item in acceptances]
    run_placeholders = _placeholders(run_ids)
    readiness = evaluate_readiness(
        connection,
        documented_insufficiency=load_documented_insufficiency(insufficiency_path),
        episode_ids=episode_ids,
    )
    status = readiness.as_public_status()
    status["observed_variable_keys"] = list(readiness.observed_variable_keys)
    status["catalog_variable_count"] = len(BENCHMARK_VARIABLES)
    status["episode_runs_considered"] = [
        {
            "episode_id": item.episode_id,
            "stratum": item.stratum,
            "accepted": item.accepted,
            "run_id": item.run_id,
        }
        for item in acceptances
    ]
    status["reported_run_ids"] = list(run_ids)
    _write(output_directory, "readiness.json", status)
    _write(output_directory, "coverage.json", _coverage(connection, rosters, run_ids))
    _write(
        output_directory, "revision_and_vintage.json", _vintage(connection, rosters, acceptances)
    )
    _write(
        output_directory,
        "leakage.json",
        {
            "violations": [
                {"episode_id": row[0], "violation_type": row[1], "identity": row[2]}
                for row in connection.execute(
                    f"""SELECT run.episode_id, v.violation_type, v.identity
                          FROM backfill_leakage_violation v
                          JOIN backfill_run run ON run.run_id = v.run_id
                         WHERE v.run_id IN ({run_placeholders})""",  # noqa: S608
                    tuple(run_ids),
                )
            ],
            "availability_before_publication": connection.execute(
                "SELECT COUNT(*) FROM observation_v2 WHERE availability_at < published_at"
            ).fetchone()[0],
            "all_episodes_leakage_passed": all(
                bool(row[0])
                for row in connection.execute(
                    f"SELECT leakage_passed FROM backfill_run "  # noqa: S608
                    f"WHERE run_id IN ({run_placeholders})",
                    tuple(run_ids),
                )
            ),
        },
    )
    missing: dict[str, list[dict[str, object]]] = {}
    for row in connection.execute(
        f"""SELECT run.episode_id, cell.dimension, cell.requirement_key, cell.entity_id,
                   COUNT(*)
              FROM backfill_coverage_cell cell
              JOIN backfill_run run ON run.run_id = cell.run_id
             WHERE cell.present = 0 AND cell.run_id IN ({run_placeholders})
             GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC""",  # noqa: S608
        tuple(run_ids),
    ):
        missing.setdefault(str(row[0]), []).append(
            {
                "dimension": row[1],
                "requirement": row[2],
                "entity": row[3],
                "missing_cells": row[4],
            }
        )
    _write(
        output_directory,
        "missingness.json",
        {
            "note": "missing cells are recorded as unknown, never zero",
            "by_episode": missing,
        },
    )
    features = sorted({key for roster in rosters for key in roster.features})
    comparison = compare_episodes(
        connection, [roster.episode_id for roster in rosters], features, run_ids=run_ids
    )
    _write(output_directory, "episode_comparison.json", comparison)
    _write(
        output_directory,
        "false_positive_analysis.json",
        false_positive_check(comparison, features),
    )
    _write(output_directory, "acquisition_receipts.json", _receipts(connection))
    return readiness
