"""Episode rosters and the end-to-end historical build.

An episode name is an evaluation stratum, not a fixed cast of companies. Where the
originally named entity cannot be measured from primary sources, the roster records the
substitution and the reason, so the swap is visible rather than quietly convenient.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
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
    register_feature_definitions,
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

#: Depositories report neither capital expenditure nor product revenue, so the industrial
#: set is not merely sparse for them, it is inapplicable. Measuring a bank means measuring
#: how it is funded and what its securities book is worth.
BANK_FEATURES: tuple[str, ...] = (
    "external_cash_generation",
    "debt_to_assets",
    "debt_to_operating_cash_flow",
    "fixed_obligations_to_external_cash",
    "deposit_funding_share",
    "equity_to_assets",
    "accumulated_other_comprehensive_income",
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
    "fixed_obligations_to_external_cash": "ratio",
    "deposit_funding_share": "ratio",
    "equity_to_assets": "ratio",
    "accumulated_other_comprehensive_income": "currency",
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
    #: Features this episode is gated on. Defaults to the industrial set; a stratum whose
    #: entities do not report those measurements declares its own rather than being
    #: scored against numbers that do not exist for it.
    features: tuple[str, ...] = EPISODE_FEATURES


ROSTERS: tuple[EpisodeRoster, ...] = (
    EpisodeRoster(
        "shale-financing",
        "crisis",
        date(2010, 1, 1),
        date(2017, 12, 31),
        date(2018, 1, 31),
        (
            EntityPlan("Chesapeake Energy", 895126),
            EntityPlan("Continental Resources", 732834),
            EntityPlan("Halliburton", 45012),
        ),
        ("policy_rate", "oil_price", "corporate_bond_spread"),
        # No lease, purchase or guarantee obligation is tagged by any of these filers
        # before ASC 842, so fixed_obligations_to_external_cash cannot be established for
        # this window and is not gated on here. It is measured in the bank stratum, where
        # the legs are reported.
        features=EPISODE_FEATURES,
        substitutions={
            "Whiting Petroleum": (
                "Whiting filed continuously through the episode but its structured facts "
                "are sparse: only 86% of the required quarterly measurements are tagged, "
                "against 98% for Continental Resources. Continental is substituted as a "
                "pure-play Bakken driller of the same stratum whose filings actually "
                "carry the measurements, rather than gating the episode on a filer whose "
                "tagging happens to be thin."
            )
        },
    ),
    EpisodeRoster(
        "regional-bank-stress",
        "crisis",
        date(2021, 1, 1),
        # The stratum is the duration-and-funding stress itself, ending with the quarter
        # in which it broke. Running past 2023 Q1 would gate the episode on quarters no
        # filing covers: SVB ceased to exist in March 2023, and every filer's final
        # quarter is reported after the cutoff. SVB's three uncovered months are the
        # failure, not a data gap, and are left visible in the coverage report.
        date(2023, 3, 31),
        date(2023, 7, 31),
        (
            EntityPlan("SVB Financial Group", 719739),
            EntityPlan("PacWest Bancorp", 1102112),
            EntityPlan("Western Alliance Bancorporation", 1212545),
        ),
        ("policy_rate", "ten_year_treasury", "corporate_bond_spread"),
        features=BANK_FEATURES,
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


def feature_specs(feature_keys: Sequence[str] = EPISODE_FEATURES) -> list[FeatureSpec]:
    return [
        FeatureSpec(
            feature_key=key,
            feature_version=FEATURE_VERSION,
            aggregation=Aggregation.AS_OF_LATEST,
            unit=FEATURE_UNITS[key],
            expected_facts_per_period=1,
            max_age_months=MAX_AGE_MONTHS,
        )
        for key in feature_keys
    ]


def ecosystem_specs(
    feature_keys: Sequence[str] = EPISODE_FEATURES,
) -> list[EcosystemFeatureSpec]:
    return [
        EcosystemFeatureSpec(
            source_feature_key=key,
            source_feature_version=FEATURE_VERSION,
            feature_key=f"ecosystem_{key}",
            feature_version=FEATURE_VERSION,
            aggregation=Aggregation.SUM if FEATURE_UNITS[key] == "currency" else Aggregation.MEAN,
            unit=FEATURE_UNITS[key],
        )
        for key in feature_keys
    ]


def register_ecosystem_definitions(
    connection: sqlite3.Connection, feature_keys: Sequence[str] = EPISODE_FEATURES
) -> None:
    """Ecosystem features need their own registered semantics before a build reads them."""
    for spec in ecosystem_specs(feature_keys):
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

    # Every feature the episode is gated on must be registered even when no entity
    # produced a fact for it, so the build records an explicit unknown instead of the
    # episode failing to build at all.
    register_feature_definitions(connection, roster.features, max_age_months=MAX_AGE_MONTHS)

    entity_build_id: str | None = None
    ecosystem_build_id: str | None = None
    if roster.entities:
        build = FeatureStoreBuilder(connection).build_entity_month(
            feature_specs(roster.features),
            roster.availability_cutoff.isoformat(),
            [plan.entity_id for plan in roster.entities],
            code_commit,
            feature_set_version,
            roster.period_start.isoformat(),
            roster.period_end.isoformat(),
        )
        entity_build_id = build.build_id
        register_ecosystem_definitions(connection, roster.features)
        ecosystem = EcosystemFeatureStoreBuilder(connection).build_months(
            build.build_id,
            ecosystem_specs(roster.features),
            code_commit,
            feature_set_version,
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
