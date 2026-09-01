"""Acquire point-in-time control data for each accepted historical episode.

Every episode has its own availability cutoff, and each needs the macro series as they
stood on *its* date. One shared vintage would be wrong for every episode but one, so this
walks the episodes and requests a separate vintage per episode.

Only revised series are requested. A series that is never revised has the same value at
every vintage, so asking for one would spend an API call to learn what is already known.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

from asro.backfill.manifest import EpisodeManifest
from asro.benchmark.controls_ingest import (
    CONTROL_PLANS,
    CONTROL_PLANS_BY_ID,
    ControlSeriesPlan,
    VintageBasis,
    fetch_series_vintage,
    ingest_series,
)
from asro.benchmark.episodes import ROSTERS, EpisodeRoster, build_episode
from asro.benchmark.readiness import episode_acceptances

MANIFEST_FILES: dict[str, str] = {
    "shale-financing": "shale_financing",
    "regional-bank-stress": "regional_bank_stress",
    "benign-infrastructure-capex": "benign_infrastructure",
    "pandemic-technology-acceleration": "pandemic_technology",
    "current-ai-cycle": "current_ai_cycle",
    "dotcom-telecom": "dotcom_telecom",
    "housing-credit": "housing_credit",
}

MANIFEST_DIR = Path(__file__).resolve().parent.parent / "backfill" / "episodes"


@dataclass(frozen=True)
class VintageOutcome:
    episode_id: str
    vintage_date: date
    series_id: str
    written: int
    already_present: int
    error: str | None = None


def as_published_plans_for(
    rosters: tuple[EpisodeRoster, ...] = ROSTERS,
) -> tuple[ControlSeriesPlan, ...]:
    """Return the immutable market controls needed to bootstrap these episodes.

    A restored production database can predate the historical benchmark.  In that
    case there are no accepted episodes yet, but the episodes cannot become accepted
    until their non-revised controls have first been loaded.  Keep this list bounded
    to controls actually named by the requested rosters.
    """
    required = {series_id for roster in rosters for series_id in roster.controls}
    return tuple(
        plan
        for plan in CONTROL_PLANS
        if plan.series_id in required and plan.vintage_basis is VintageBasis.AS_PUBLISHED
    )


def revised_series_for(roster: EpisodeRoster) -> tuple[str, ...]:
    """The episode's own controls that are revised and therefore need a vintage."""
    return tuple(
        series_id
        for series_id in roster.controls
        if series_id in CONTROL_PLANS_BY_ID
        and CONTROL_PLANS_BY_ID[series_id].vintage_basis is VintageBasis.LATEST_REVISION
    )


def acquire_episode_vintages(
    connection: sqlite3.Connection,
    *,
    api_key: str,
    user_agent: str,
    rosters: tuple[EpisodeRoster, ...] = ROSTERS,
    accepted_only: bool = True,
    session: requests.Session | None = None,
) -> dict[str, object]:
    """Fetch and store each episode's revised controls as of that episode's own cutoff."""
    if not api_key:
        raise ValueError(
            "no FRED API key configured; set ASRO_FRED_API_KEY to acquire point-in-time "
            "control data"
        )
    accepted = {item.episode_id for item in episode_acceptances(connection) if item.accepted}
    outcomes: list[VintageOutcome] = []
    for roster in rosters:
        if accepted_only and roster.episode_id not in accepted:
            continue
        for series_id in revised_series_for(roster):
            plan = CONTROL_PLANS_BY_ID[series_id]
            try:
                fetch = fetch_series_vintage(
                    plan,
                    api_key=api_key,
                    vintage_date=roster.availability_cutoff,
                    user_agent=user_agent,
                    session=session,
                )
                report = ingest_series(connection, fetch)
                written = report["written"]
                already = report["already_present"]
            except (requests.RequestException, ValueError) as exc:
                outcomes.append(
                    VintageOutcome(
                        roster.episode_id,
                        roster.availability_cutoff,
                        series_id,
                        0,
                        0,
                        str(exc)[:300],
                    )
                )
                continue
            outcomes.append(
                VintageOutcome(
                    roster.episode_id,
                    roster.availability_cutoff,
                    series_id,
                    written if isinstance(written, int) else 0,
                    already if isinstance(already, int) else 0,
                )
            )
    return {
        "episodes_considered": [
            roster.episode_id
            for roster in rosters
            if not accepted_only or roster.episode_id in accepted
        ],
        "outcomes": [
            {
                "episode_id": item.episode_id,
                "vintage_date": item.vintage_date.isoformat(),
                "series_id": item.series_id,
                "written": item.written,
                "already_present": item.already_present,
                "error": item.error,
            }
            for item in outcomes
        ],
        "series_written": sum(item.written for item in outcomes),
        "failures": [item.series_id for item in outcomes if item.error],
    }


def rebuild_episodes(
    connection: sqlite3.Connection,
    *,
    user_agent: str,
    cache_dir: Path,
    code_commit: str,
    feature_set_version: str,
    rosters: tuple[EpisodeRoster, ...] = ROSTERS,
) -> dict[str, object]:
    """Re-run every episode so its snapshots pick up the newly stored vintages."""
    results: dict[str, object] = {}
    for roster in rosters:
        manifest_path = MANIFEST_DIR / f"{MANIFEST_FILES[roster.episode_id]}.toml"
        manifest = EpisodeManifest.from_toml(manifest_path)
        try:
            build = build_episode(
                connection,
                roster,
                manifest,
                user_agent=user_agent,
                cache_dir=cache_dir,
                code_commit=code_commit,
                feature_set_version=feature_set_version,
            )
        except ValueError as exc:
            results[roster.episode_id] = {"error": str(exc)[:300]}
            continue
        results[roster.episode_id] = (
            {"measurable": False}
            if build.result is None
            else {
                "coverage_passed": bool(build.result.coverage_passed),
                "leakage_passed": bool(build.result.leakage_passed),
                "observations_written": build.observations_written,
            }
        )
    return results
