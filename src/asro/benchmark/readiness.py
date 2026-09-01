"""The calibration readiness gate.

ASRO publishes a 0-100 reading. Until historical episodes have actually been accepted,
that reading is a deterministic heuristic and must be described as one. This module is
the thing that makes the distinction enforceable rather than a matter of wording: it
computes what the evidence base currently supports, and refuses claims beyond it.

Three output tiers exist, and they are ordered:

heuristic
    A rules-based reading with no historical reference frame. The default.
descriptive
    Historical evidence exists and is comparable, but not enough strata are accepted to
    support a calibrated claim. Percentiles against history may be shown, labelled.
historically_calibrated
    Enough accepted episodes across strata, with coverage for every causal role or an
    explicit documented insufficiency, to place a current reading against history.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from asro.benchmark.catalog import (
    BENCHMARK_VARIABLES,
    CausalRole,
    variables_for_role,
)
from asro.evidence.time import normalize_timestamp


class OutputTier(StrEnum):
    HEURISTIC = "heuristic"
    DESCRIPTIVE = "descriptive"
    HISTORICALLY_CALIBRATED = "historically_calibrated"


_TIER_ORDER: dict[OutputTier, int] = {
    OutputTier.HEURISTIC: 0,
    OutputTier.DESCRIPTIVE: 1,
    OutputTier.HISTORICALLY_CALIBRATED: 2,
}


class CalibrationVerdict(StrEnum):
    NOT_YET_CALIBRATED = "NOT_YET_CALIBRATED"
    HISTORICALLY_CALIBRATED = "HISTORICALLY_CALIBRATED"


class EpisodeAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str
    episode_version: str
    stratum: str
    run_id: str
    coverage_passed: bool
    leakage_passed: bool
    coverage_cell_count: int
    source_count: int
    control_count: int

    @property
    def accepted(self) -> bool:
        return self.coverage_passed and self.leakage_passed and self.coverage_cell_count > 0


class RoleCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    causal_role: CausalRole
    catalog_variable_count: int
    observed_variable_count: int
    documented_insufficiency: str | None = None

    @property
    def satisfied(self) -> bool:
        """Only measurement satisfies a role.

        A documented insufficiency explains why a role is unmeasured and is required
        before the gap can even be discussed, but an explanation is not a measurement. It
        is carried here so the report can show the reason next to the gap, never to close
        it.
        """
        return self.observed_variable_count > 0


class CalibrationRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_crisis_episodes: int = Field(default=2, ge=1)
    minimum_benign_episodes: int = Field(default=1, ge=1)
    minimum_current_episodes: int = Field(default=1, ge=1)
    require_all_causal_roles: bool = True
    require_debt_and_obligation_ratios: bool = True
    require_control_relative_comparison: bool = True
    require_counter_evidence: bool = True
    require_vintage_correct_controls: bool = True


DEBT_AND_OBLIGATION_KEYS: tuple[str, ...] = (
    "debt_to_operating_cash_flow",
    "debt_to_assets",
    "fixed_obligations_to_external_cash",
)


class CalibrationReadiness(BaseModel):
    """What the accepted evidence currently supports, and what it does not."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: CalibrationVerdict
    output_tier: OutputTier
    accepted_crisis_episodes: int
    accepted_benign_episodes: int
    accepted_current_episodes: int
    role_coverage: tuple[RoleCoverage, ...]
    observed_variable_keys: tuple[str, ...]
    revised_only_control_series: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...]
    requirements: CalibrationRequirements

    @property
    def historically_calibrated(self) -> bool:
        return self.verdict is CalibrationVerdict.HISTORICALLY_CALIBRATED

    def as_public_status(self) -> dict[str, object]:
        """The shape the static site and any published artifact must render."""
        return {
            "verdict": self.verdict.value,
            "output_tier": self.output_tier.value,
            "historically_calibrated": self.historically_calibrated,
            "accepted_episodes": {
                "crisis": self.accepted_crisis_episodes,
                "benign": self.accepted_benign_episodes,
                "current": self.accepted_current_episodes,
            },
            "causal_role_coverage": {
                item.causal_role.value: {
                    "catalog_variables": item.catalog_variable_count,
                    "observed_variables": item.observed_variable_count,
                    "documented_insufficiency": item.documented_insufficiency,
                    "satisfied": item.satisfied,
                }
                for item in self.role_coverage
            },
            "revised_only_control_series": list(self.revised_only_control_series),
            "blocking_reasons": list(self.blocking_reasons),
        }


class CalibrationClaimError(RuntimeError):
    """Raised when a claim would exceed what the accepted evidence supports."""


def episode_acceptances(
    connection: sqlite3.Connection,
    episode_ids: Sequence[str] | None = None,
) -> tuple[EpisodeAcceptance, ...]:
    """The latest run for each distinct episode, with its gate outcomes.

    Keyed on `episode_id` alone, never on `(episode_id, version)`. Re-running an episode
    or bumping its manifest version produces more rows in `backfill_run`, and counting
    those as separate episodes would let one crisis satisfy a requirement for two.
    """
    scope = ""
    parameters: tuple[str, ...] = ()
    if episode_ids is not None:
        placeholders = ",".join("?" for _ in episode_ids) or "''"
        scope = f"AND run.episode_id IN ({placeholders})"  # noqa: S608
        parameters = tuple(episode_ids)
    rows = connection.execute(
        f"""SELECT run.episode_id, run.episode_version, episode.stratum, run.run_id,
                  run.coverage_passed, run.leakage_passed, run.coverage_cell_count,
                  run.source_count, run.control_count
             FROM backfill_run AS run
             JOIN backfill_episode AS episode
               ON episode.episode_id = run.episode_id
              AND episode.version = run.episode_version
            WHERE run.rowid = (
                    SELECT inner_run.rowid FROM backfill_run AS inner_run
                     WHERE inner_run.episode_id = run.episode_id
                     ORDER BY inner_run.created_at DESC, inner_run.rowid DESC
                     LIMIT 1)
              {scope}
            ORDER BY run.episode_id""",  # noqa: S608
        parameters,
    ).fetchall()
    return tuple(
        EpisodeAcceptance(
            episode_id=str(row[0]),
            episode_version=str(row[1]),
            stratum=str(row[2]),
            run_id=str(row[3]),
            coverage_passed=bool(row[4]),
            leakage_passed=bool(row[5]),
            coverage_cell_count=int(row[6]),
            source_count=int(row[7]),
            control_count=int(row[8]),
        )
        for row in rows
    )


def observed_variable_keys(
    connection: sqlite3.Connection, accepted_run_ids: Sequence[str] | None = None
) -> tuple[str, ...]:
    """Catalog variables evidenced by episodes that actually passed their gates.

    Evidence is counted only where it reached a finalized feature value inside an accepted
    episode's immutable build, or a control snapshot frozen into an accepted run. A row
    sitting loose in `observation_v2` proves nothing about an episode: it may come from a
    failed episode, from live daily collection, or from an unrelated backfill. A value
    that is missing never counts, whatever its provenance.
    """
    if accepted_run_ids is None:
        accepted_run_ids = [
            item.run_id for item in episode_acceptances(connection) if item.accepted
        ]
    if not accepted_run_ids:
        return ()
    placeholders = ",".join("?" for _ in accepted_run_ids)
    observed: set[str] = set()
    for (feature_key,) in connection.execute(
        f"""SELECT DISTINCT value.feature_key
              FROM backfill_build_link link
              JOIN finalized_entity_feature_value value ON value.build_id = link.build_id
              JOIN feature_value_contributor contributor
                ON contributor.feature_value_id = value.feature_value_id
             WHERE link.run_id IN ({placeholders})
               AND link.grain = 'entity_month'
               AND value.value_numeric IS NOT NULL""",  # noqa: S608
        tuple(accepted_run_ids),
    ):
        if str(feature_key) in BENCHMARK_VARIABLES:
            observed.add(str(feature_key))
    control_rows = {
        str(row[0])
        for row in connection.execute(
            f"""SELECT DISTINCT series_id FROM backfill_control_snapshot_v2
                 WHERE run_id IN ({placeholders})""",  # noqa: S608
            tuple(accepted_run_ids),
        )
    }
    for key, variable in BENCHMARK_VARIABLES.items():
        if control_rows.intersection(variable.control_series):
            observed.add(key)
    return tuple(sorted(observed))


POINT_IN_TIME_PATTERN = re.compile(r"^point_in_time:(\d{4}-\d{2}-\d{2})$")


def point_in_time_date(vintage: str) -> date | None:
    """The vintage date, or None when the marking is not a well-formed point-in-time one.

    Parsed strictly. `point_in_time:2018-1-3`, `point_in_time:soon` and
    `point_in_time:2018-01-31 (approx)` are all rejected rather than trusted on the
    strength of their prefix.
    """
    match = POINT_IN_TIME_PATTERN.match(vintage)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:  # pragma: no cover - the pattern already constrains the shape
        return None


def revised_only_control_series(
    connection: sqlite3.Connection, accepted_run_ids: Sequence[str]
) -> tuple[str, ...]:
    """Control series in accepted runs whose provenance is not usable point-in-time.

    Two markings are acceptable, and only two. `as_published` means the series is never
    materially revised, so today's print is what was published. `point_in_time:<date>`
    means the values came from the FRED API with `realtime_start` and `realtime_end`
    pinned to that date -- and that date must be no later than the episode's own
    availability cutoff, because a vintage cut after the cutoff is a later revision
    wearing a point-in-time label. Anything else is today's revised value.
    """
    if not accepted_run_ids:
        return ()
    placeholders = ",".join("?" for _ in accepted_run_ids)
    offending: set[str] = set()
    for series_id, provenance, cutoff in connection.execute(
        f"""SELECT snapshot.series_id, snapshot.provenance_json,
                   episode.availability_cutoff
              FROM backfill_control_snapshot_v2 AS snapshot
              JOIN backfill_run AS run ON run.run_id = snapshot.run_id
              JOIN backfill_episode AS episode
                ON episode.episode_id = run.episode_id
               AND episode.version = run.episode_version
             WHERE snapshot.run_id IN ({placeholders})""",  # noqa: S608
        tuple(accepted_run_ids),
    ):
        vintage = str(json.loads(str(provenance)).get("vintage", ""))
        if vintage == "as_published":
            continue
        vintage_date = point_in_time_date(vintage)
        if vintage_date is None or vintage_date > normalize_timestamp(str(cutoff)).date():
            offending.add(str(series_id))
    return tuple(sorted(offending))


def load_documented_insufficiency(path: Path) -> dict[CausalRole, str]:
    """Read reviewed statements of why a causal role cannot be measured.

    A documented insufficiency is an explicit, reviewed admission, not a silent pass. The
    file must name the role and give a reason; anything else is rejected.
    """
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("documented insufficiency file must contain an object")
    entries = payload.get("insufficiencies")
    if not isinstance(entries, list):
        raise ValueError("documented insufficiency file must contain an insufficiencies list")
    resolved: dict[CausalRole, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each insufficiency must be an object")
        role_text = entry.get("causal_role")
        reason = entry.get("reason")
        if not isinstance(role_text, str) or not isinstance(reason, str) or not reason.strip():
            raise ValueError("each insufficiency requires a causal_role and a non-empty reason")
        resolved[CausalRole(role_text)] = reason.strip()
    return resolved


def evaluate_readiness(
    connection: sqlite3.Connection,
    *,
    requirements: CalibrationRequirements | None = None,
    documented_insufficiency: Mapping[CausalRole, str] | None = None,
    episode_ids: Sequence[str] | None = None,
) -> CalibrationReadiness:
    """Compute what the accepted historical evidence supports right now."""
    rules = requirements or CalibrationRequirements()
    insufficiency = dict(documented_insufficiency or {})
    acceptances = episode_acceptances(connection, episode_ids)
    accepted = [item for item in acceptances if item.accepted]
    # Distinct episodes, so a re-run or a version bump cannot inflate a stratum.
    distinct: dict[str, set[str]] = {"crisis": set(), "benign": set(), "current": set()}
    for item in accepted:
        if item.stratum in distinct:
            distinct[item.stratum].add(item.episode_id)
    counts = {key: len(value) for key, value in distinct.items()}

    accepted_run_ids = [item.run_id for item in accepted]
    observed = observed_variable_keys(connection, accepted_run_ids)
    observed_set = set(observed)
    revised_controls = revised_only_control_series(connection, accepted_run_ids)
    coverage: list[RoleCoverage] = []
    for role in CausalRole:
        catalog_keys = {item.key for item in variables_for_role(role)}
        coverage.append(
            RoleCoverage(
                causal_role=role,
                catalog_variable_count=len(catalog_keys),
                observed_variable_count=len(catalog_keys & observed_set),
                documented_insufficiency=insufficiency.get(role),
            )
        )

    blocking: list[str] = []
    if counts["crisis"] < rules.minimum_crisis_episodes:
        blocking.append(
            f"accepted crisis episodes {counts['crisis']} "
            f"below required {rules.minimum_crisis_episodes}"
        )
    if counts["benign"] < rules.minimum_benign_episodes:
        blocking.append(
            f"accepted benign episodes {counts['benign']} "
            f"below required {rules.minimum_benign_episodes}"
        )
    if counts["current"] < rules.minimum_current_episodes:
        blocking.append(
            f"accepted current-cycle episodes {counts['current']} "
            f"below required {rules.minimum_current_episodes}"
        )
    if rules.require_all_causal_roles:
        unmet = [item.causal_role.value for item in coverage if not item.satisfied]
        if unmet:
            documented = sorted(
                item.causal_role.value
                for item in coverage
                if not item.satisfied and item.documented_insufficiency
            )
            detail = f" ({len(documented)} with a documented reason, which does not close them)"
            blocking.append(
                "causal roles with no measured evidence in an accepted episode: "
                + ", ".join(sorted(unmet))
                + (detail if documented else "")
            )
    if rules.require_debt_and_obligation_ratios:
        missing_ratios = [key for key in DEBT_AND_OBLIGATION_KEYS if key not in observed_set]
        if missing_ratios:
            blocking.append(
                "debt and fixed-obligation ratios not observed: " + ", ".join(missing_ratios)
            )
    if rules.require_control_relative_comparison and not any(
        item.control_count > 0 for item in accepted
    ):
        blocking.append("no accepted episode carries control observations")
    if rules.require_vintage_correct_controls and revised_controls:
        blocking.append(
            "control series in accepted episodes are latest-revision, not point-in-time: "
            + ", ".join(revised_controls)
        )
    if rules.require_counter_evidence:
        resilience_keys = {item.key for item in variables_for_role(CausalRole.RESILIENCE)}
        if not resilience_keys & observed_set:
            blocking.append("no resilience or counter-evidence variable is observed")

    verdict = (
        CalibrationVerdict.HISTORICALLY_CALIBRATED
        if not blocking
        else CalibrationVerdict.NOT_YET_CALIBRATED
    )
    if verdict is CalibrationVerdict.HISTORICALLY_CALIBRATED:
        tier = OutputTier.HISTORICALLY_CALIBRATED
    elif observed:
        tier = OutputTier.DESCRIPTIVE
    else:
        tier = OutputTier.HEURISTIC

    return CalibrationReadiness(
        verdict=verdict,
        output_tier=tier,
        accepted_crisis_episodes=counts["crisis"],
        accepted_benign_episodes=counts["benign"],
        accepted_current_episodes=counts["current"],
        role_coverage=tuple(coverage),
        observed_variable_keys=observed,
        revised_only_control_series=revised_controls,
        blocking_reasons=tuple(blocking),
        requirements=rules,
    )


def assert_claim_supported(readiness: CalibrationReadiness, claimed_tier: OutputTier) -> None:
    """Refuse a published claim stronger than the accepted evidence supports."""
    if _TIER_ORDER[claimed_tier] > _TIER_ORDER[readiness.output_tier]:
        reasons = "; ".join(readiness.blocking_reasons) or "insufficient accepted evidence"
        raise CalibrationClaimError(
            f"cannot publish a {claimed_tier.value} claim: "
            f"evidence supports {readiness.output_tier.value} only ({reasons})"
        )
