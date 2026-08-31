"""Episode rosters and the end-to-end historical build.

An episode name is an evaluation stratum, not a fixed cast of companies. Where the
originally named entity cannot be measured from primary sources, the roster records the
substitution and the reason, so the swap is visible rather than quietly convenient.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from asro.backfill.manifest import EpisodeManifest
from asro.backfill.runner import BackfillResult, BackfillRunner
from asro.benchmark.sec_fundamentals import (
    FEATURE_RELEASED_AT,
    FEATURE_VERSION,
    EntityPlan,
    ingest_entity_fundamentals,
)
from asro.evidence import EvidenceRepository, FeatureDefinitionV2
from asro.features import (
    Aggregation,
    EcosystemFeatureSpec,
    EcosystemFeatureStoreBuilder,
    FeatureSpec,
    FeatureStoreBuilder,
)

#: Features required of every episode. Chosen because each is derivable from XBRL for
#: any filer and together they span boom, validation, vulnerability and resilience.
EPISODE_FEATURES: tuple[str, ...] = (
    "capital_expenditure",
    "external_revenue",
    "external_cash_generation",
    "free_cash_flow",
    "capex_to_revenue",
    "debt_to_assets",
    "debt_to_operating_cash_flow",
    "resilient_liquidity_runway",
)

FEATURE_UNITS: dict[str, str] = {
    "capital_expenditure": "currency",
    "external_revenue": "currency",
    "external_cash_generation": "currency",
    "free_cash_flow": "currency",
    "capex_to_revenue": "ratio",
    "debt_to_assets": "ratio",
    "debt_to_operating_cash_flow": "ratio",
    "resilient_liquidity_runway": "months",
}

MAX_AGE_MONTHS = 6


@dataclass(frozen=True)
class EpisodeRoster:
    episode_id: str
    stratum: str
    period_start: date
    period_end: date
    availability_cutoff: date
    entities: tuple[EntityPlan, ...]
    controls: tuple[str, ...]
    substitutions: dict[str, str] = field(default_factory=dict)
    unmeasurable_reason: str | None = None


ROSTERS: tuple[EpisodeRoster, ...] = (
    EpisodeRoster(
        "shale-financing",
        "crisis",
        date(2010, 1, 1),
        date(2017, 12, 31),
        date(2018, 1, 31),
        (
            EntityPlan("Chesapeake Energy", 895126),
            EntityPlan("Whiting Petroleum", 1255474),
            EntityPlan("Halliburton", 45012),
        ),
        ("policy_rate", "oil_price", "commercial_industrial_loans"),
    ),
    EpisodeRoster(
        "regional-bank-stress",
        "crisis",
        date(2021, 1, 1),
        date(2024, 12, 31),
        date(2025, 1, 31),
        (
            EntityPlan("SVB Financial Group", 719739),
            EntityPlan("PacWest Bancorp", 1102112),
            EntityPlan("Western Alliance Bancorporation", 1212545),
        ),
        ("policy_rate", "bank_deposits", "ten_year_treasury"),
        substitutions={
            "First Republic Bank": (
                "First Republic was a state-chartered bank whose periodic reports went to "
                "the FDIC, not the SEC; EDGAR holds only ownership filings for it and no "
                "XBRL facts exist. PacWest Bancorp, which suffered the same 2023 deposit "
                "run and was absorbed by Banc of California, is substituted."
            ),
            "Signature Bank": (
                "Signature Bank likewise filed periodic reports with the FDIC; EDGAR holds "
                "no XBRL facts. Western Alliance Bancorporation, which experienced the same "
                "March 2023 funding stress and survived it, is substituted so the stratum "
                "contains both a failure and a survivor."
            ),
        },
    ),
    EpisodeRoster(
        "benign-infrastructure-capex",
        "benign",
        date(2012, 1, 1),
        date(2016, 12, 31),
        date(2017, 1, 31),
        (
            EntityPlan("American Tower", 1053507),
            EntityPlan("Equinix", 1101239),
            EntityPlan("Crown Castle", 1051470),
        ),
        ("policy_rate", "commercial_industrial_loans", "private_fixed_investment"),
    ),
    EpisodeRoster(
        "pandemic-technology-acceleration",
        "benign",
        date(2020, 1, 1),
        date(2022, 12, 31),
        date(2023, 1, 31),
        (
            EntityPlan("Microsoft", 789019),
            EntityPlan("Amazon", 1018724),
            EntityPlan("Zoom", 1585521),
        ),
        ("policy_rate", "unemployment_rate", "real_personal_consumption"),
    ),
    EpisodeRoster(
        "current-ai-cycle",
        "current",
        date(2022, 1, 1),
        date(2026, 7, 31),
        date(2026, 8, 1),
        (
            EntityPlan("Microsoft", 789019),
            EntityPlan("Amazon", 1018724),
            EntityPlan("Alphabet", 1652044),
            EntityPlan("Meta", 1326801),
            EntityPlan("NVIDIA", 1045810),
        ),
        ("policy_rate", "electricity_price", "commercial_industrial_loans"),
        substitutions={
            "OpenAI": (
                "OpenAI files no periodic reports with the SEC and has no CIK, so no "
                "primary fundamentals exist. It is removed from the measured roster; its "
                "obligations appear only through counterparties that do file."
            )
        },
    ),
    EpisodeRoster(
        "dotcom-telecom",
        "crisis",
        date(1998, 1, 1),
        date(2003, 12, 31),
        date(2004, 1, 31),
        (),
        ("policy_rate", "commercial_industrial_loans", "unemployment_rate"),
        unmeasurable_reason=(
            "XBRL reporting began in 2009. Cisco's earliest structured facts are from 2008, "
            "five years after this episode ends, and WorldCom and Global Crossing have no "
            "usable structured facts at all. Entity-level fundamentals for 1998-2003 would "
            "require parsing unstructured filing text, which is not attempted here rather "
            "than being approximated."
        ),
    ),
    EpisodeRoster(
        "housing-credit",
        "crisis",
        date(2004, 1, 1),
        date(2010, 12, 31),
        date(2011, 1, 31),
        (),
        ("policy_rate", "mortgage_rate", "unemployment_rate"),
        unmeasurable_reason=(
            "Lehman Brothers, Bear Stearns and Countrywide all failed or were absorbed "
            "before XBRL reporting began, so none has structured facts. The episode is "
            "carried at control-series level only."
        ),
    ),
)

ROSTERS_BY_ID: dict[str, EpisodeRoster] = {item.episode_id: item for item in ROSTERS}


def feature_specs() -> list[FeatureSpec]:
    return [
        FeatureSpec(
            feature_key=key,
            feature_version=FEATURE_VERSION,
            aggregation=Aggregation.AS_OF_LATEST,
            unit=FEATURE_UNITS[key],
            expected_facts_per_period=1,
            max_age_months=MAX_AGE_MONTHS,
        )
        for key in EPISODE_FEATURES
    ]


def ecosystem_specs() -> list[EcosystemFeatureSpec]:
    return [
        EcosystemFeatureSpec(
            source_feature_key=key,
            source_feature_version=FEATURE_VERSION,
            feature_key=f"ecosystem_{key}",
            feature_version=FEATURE_VERSION,
            aggregation=Aggregation.SUM if FEATURE_UNITS[key] == "currency" else Aggregation.MEAN,
            unit=FEATURE_UNITS[key],
        )
        for key in EPISODE_FEATURES
    ]


def register_ecosystem_definitions(connection: sqlite3.Connection) -> None:
    """Ecosystem features need their own registered semantics before a build reads them."""
    for spec in ecosystem_specs():
        EvidenceRepository.register_feature(
            connection,
            FeatureDefinitionV2(
                feature_key=spec.feature_key,
                feature_version=spec.feature_version,
                definition_json=json.dumps(
                    {
                        "grain": "ecosystem_month",
                        "aggregation": spec.aggregation.value,
                        "unit": spec.unit,
                        "source_feature": (
                            f"{spec.source_feature_key}@{spec.source_feature_version}"
                        ),
                    },
                    sort_keys=True,
                ),
                released_at=FEATURE_RELEASED_AT,
            ),
        )
    connection.commit()


@dataclass(frozen=True)
class EpisodeBuild:
    episode_id: str
    entity_build_id: str | None
    ecosystem_build_id: str | None
    observations_written: int
    ingestion: tuple[dict[str, object], ...]
    result: BackfillResult | None


def build_episode(
    connection: sqlite3.Connection,
    roster: EpisodeRoster,
    manifest: EpisodeManifest,
    *,
    user_agent: str,
    cache_dir: Path,
    code_commit: str,
    feature_set_version: str,
) -> EpisodeBuild:
    """Ingest, build and gate one episode end to end."""
    ingestion: list[dict[str, object]] = []
    written = 0
    for plan in roster.entities:
        report = ingest_entity_fundamentals(
            connection,
            plan,
            availability_cutoff=roster.availability_cutoff,
            period_start=roster.period_start,
            period_end=roster.period_end,
            user_agent=user_agent,
            cache_dir=cache_dir,
        )
        ingestion.append(report)
        count = report["observations_written"]
        written += count if isinstance(count, int) else 0

    entity_build_id: str | None = None
    ecosystem_build_id: str | None = None
    if roster.entities:
        build = FeatureStoreBuilder(connection).build_entity_month(
            feature_specs(),
            roster.availability_cutoff.isoformat(),
            [plan.entity_id for plan in roster.entities],
            code_commit,
            feature_set_version,
            roster.period_start.isoformat(),
            roster.period_end.isoformat(),
        )
        entity_build_id = build.build_id
        register_ecosystem_definitions(connection)
        ecosystem = EcosystemFeatureStoreBuilder(connection).build_months(
            build.build_id, ecosystem_specs(), code_commit, feature_set_version
        )
        ecosystem_build_id = ecosystem.build_id

    if not roster.entities:
        # An episode with no measurable entity is not silently skipped and is certainly
        # not accepted; it is returned without a gate result so it can never be counted.
        return EpisodeBuild(
            episode_id=roster.episode_id,
            entity_build_id=None,
            ecosystem_build_id=None,
            observations_written=0,
            ingestion=(),
            result=None,
        )
    result = BackfillRunner(connection).run(manifest, entity_build_id, ecosystem_build_id)
    return EpisodeBuild(
        episode_id=roster.episode_id,
        entity_build_id=entity_build_id,
        ecosystem_build_id=ecosystem_build_id,
        observations_written=written,
        ingestion=tuple(ingestion),
        result=result,
    )
