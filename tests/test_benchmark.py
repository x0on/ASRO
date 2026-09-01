from __future__ import annotations

import gzip
import hashlib
import json
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from asro.backfill.controls import ControlObservation, register_control_observation
from asro.benchmark import (
    ASRO_HUNDRED,
    ASRO_ZERO,
    BENCHMARK_VARIABLES,
    CalibrationClaimError,
    CalibrationRequirements,
    CausalRole,
    Direction,
    OutputTier,
    assert_claim_supported,
    evaluate_readiness,
    load_documented_insufficiency,
    machine_derivable_variables,
    variables_for_role,
)
from asro.benchmark.analysis import false_positive_check, percentile_of, robust_z
from asro.benchmark.controls_ingest import (
    CONTROL_PLANS,
    CONTROL_PLANS_BY_ID,
    UNAVAILABLE_CONTROL_SERIES,
    Frequency,
    SeriesFetch,
    VintageBasis,
    _parse_csv,
    fetch_series_vintage,
    ingest_controls,
    ingest_series,
    monthly_observations,
)
from asro.benchmark.episodes import (
    BANK_FEATURES,
    EPISODE_FEATURES,
    ROSTERS,
    ecosystem_specs,
    feature_specs,
)
from asro.benchmark.readiness import point_in_time_date
from asro.benchmark.reports import REPORT_NAMES, write_benchmark_reports
from asro.benchmark.sec_fundamentals import (
    CONCEPTS,
    PARTIAL_COMPOSITES,
    ConceptSpec,
    TagGroup,
    build_derived_facts,
    select_facts,
)
from asro.benchmark.vintages import (
    acquire_episode_vintages,
    as_published_plans_for,
    missing_as_published_plans,
    revised_series_for,
)
from asro.evidence import (
    CanonicalFactAssignment,
    EconomicScope,
    EvidenceRepository,
    FactStatus,
    FeatureDefinitionV2,
    ObservationV2,
    SourceTier,
)
from asro.migrations.runner import apply_migrations
from asro.models import EventType, FinancialEvent, SourceItem
from asro.scoring import score
from asro.settings import Settings
from asro.site import _database_state_identity, build_static_site
from asro.state_assets import package_state
from asro.storage import SqliteRepository


@pytest.fixture()
def connection(tmp_path: Path) -> sqlite3.Connection:
    handle = SqliteRepository(tmp_path / "benchmark.db").connect()
    apply_migrations(handle)
    return handle


# ---------------------------------------------------------------- catalog


def test_catalog_covers_every_causal_role() -> None:
    roles = {variable.causal_role for variable in BENCHMARK_VARIABLES.values()}
    assert roles == set(CausalRole)


def test_ratio_variables_declare_both_sides() -> None:
    for variable in BENCHMARK_VARIABLES.values():
        if variable.unit == "ratio":
            assert variable.numerator_concept and variable.denominator_concept


def test_machine_derivable_variables_name_a_source() -> None:
    for variable in machine_derivable_variables():
        assert variable.xbrl_concepts or variable.control_series


def test_endpoints_define_every_role_and_disclaim_probability() -> None:
    assert ASRO_ZERO.roles_covered() == set(CausalRole)
    assert ASRO_HUNDRED.roles_covered() == set(CausalRole)
    assert any("not a 100 percent" in item for item in ASRO_HUNDRED.explicit_non_claims)


# ---------------------------------------------------------------- readiness gate


def test_empty_database_is_not_calibrated(connection: sqlite3.Connection) -> None:
    readiness = evaluate_readiness(connection)
    assert readiness.verdict.value == "NOT_YET_CALIBRATED"
    assert readiness.output_tier is OutputTier.HEURISTIC
    assert readiness.blocking_reasons


def test_gate_refuses_a_claim_beyond_the_evidence(connection: sqlite3.Connection) -> None:
    readiness = evaluate_readiness(connection)
    with pytest.raises(CalibrationClaimError):
        assert_claim_supported(readiness, OutputTier.HISTORICALLY_CALIBRATED)
    assert_claim_supported(readiness, OutputTier.HEURISTIC)


def test_requirements_are_configurable_but_default_to_two_crisis_episodes() -> None:
    assert CalibrationRequirements().minimum_crisis_episodes == 2
    assert CalibrationRequirements().require_counter_evidence is True


def test_documented_insufficiency_requires_a_reason(tmp_path: Path) -> None:
    path = tmp_path / "insufficiency.json"
    path.write_text(json.dumps({"insufficiencies": [{"causal_role": "shock", "reason": "  "}]}))
    with pytest.raises(ValueError):
        load_documented_insufficiency(path)
    path.write_text(
        json.dumps({"insufficiencies": [{"causal_role": "shock", "reason": "no feed"}]})
    )
    assert load_documented_insufficiency(path)[CausalRole.SHOCK] == "no feed"


def test_missing_insufficiency_file_is_empty_not_permissive(tmp_path: Path) -> None:
    assert load_documented_insufficiency(tmp_path / "absent.json") == {}


# ---------------------------------------------------------------- vintage and leakage


def _entry(start: str | None, end: str, val: float, filed: str, accn: str = "a-1") -> dict:
    payload = {"end": end, "val": val, "filed": filed, "accn": accn, "form": "10-Q"}
    if start:
        payload["start"] = start
    return payload


def _facts(entries: list[dict], kind: str = "quarterly") -> dict:
    return {"facts": {"us-gaap": {"Tag": {"units": {"USD": entries}}}}}


SPEC = ConceptSpec("external_revenue", (TagGroup(("Tag",)),), "quarterly", "currency")
INSTANT_SPEC = ConceptSpec("total_assets", (TagGroup(("Tag",)),), "instant", "currency")


def test_original_filing_wins_over_later_restatement() -> None:
    payload = _facts(
        [
            _entry("2015-01-01", "2015-03-31", 100.0, "2015-05-01", "orig"),
            _entry("2015-01-01", "2015-03-31", 130.0, "2016-05-01", "restated"),
        ]
    )
    facts, rejected = select_facts(
        SPEC,
        "E",
        payload,
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    assert [item.value for item in facts] == [100.0]
    assert any("restatement" in str(item["reason"]) for item in rejected)


def test_a_fact_filed_after_the_cutoff_is_excluded() -> None:
    payload = _facts([_entry("2015-01-01", "2015-03-31", 100.0, "2015-05-01")])
    facts, rejected = select_facts(
        SPEC,
        "E",
        payload,
        availability_cutoff=date(2015, 4, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    assert facts == []
    assert any("after the episode availability cutoff" in str(i["reason"]) for i in rejected)


def test_year_to_date_is_differenced_never_read_as_a_quarter() -> None:
    payload = _facts(
        [
            _entry("2015-01-01", "2015-03-31", 100.0, "2015-05-01"),
            _entry("2015-01-01", "2015-06-30", 250.0, "2015-08-01"),
            _entry("2015-01-01", "2015-09-30", 400.0, "2015-11-01"),
        ]
    )
    facts, _ = select_facts(
        SPEC,
        "E",
        payload,
        availability_cutoff=date(2016, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    values = {item.period_end.isoformat(): item.value for item in facts}
    assert values == {"2015-03-31": 100.0, "2015-06-30": 150.0, "2015-09-30": 150.0}
    differenced = [item for item in facts if item.period_end.isoformat() == "2015-06-30"]
    assert differenced[0].derivation_method is not None
    assert "year_to_date" in str(differenced[0].derivation_method)


def test_a_gap_in_the_quarterly_chain_is_not_summed() -> None:
    payload = _facts(
        [
            _entry("2015-01-01", "2015-03-31", 100.0, "2015-05-01"),
            _entry("2015-01-01", "2015-12-31", 500.0, "2016-02-01"),
        ]
    )
    facts, _ = select_facts(
        SPEC,
        "E",
        payload,
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    # The 275-day remainder is not a quarter and must not be presented as one.
    assert [item.period_end.isoformat() for item in facts] == ["2015-03-31"]


def test_instant_and_duration_facts_are_never_confused() -> None:
    payload = _facts([_entry(None, "2015-03-31", 900.0, "2015-05-01")])
    facts, _ = select_facts(
        INSTANT_SPEC,
        "E",
        payload,
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    assert facts[0].period_start == facts[0].period_end
    empty, _ = select_facts(
        SPEC,
        "E",
        payload,
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    assert empty == []


def test_partial_group_sums_only_where_declared() -> None:
    strict = ConceptSpec("total_debt", (TagGroup(("A", "B")),), "instant", "currency")
    payload = {
        "facts": {
            "us-gaap": {
                "A": {"units": {"USD": [_entry(None, "2015-03-31", 10.0, "2015-05-01")]}},
            }
        }
    }
    facts, _ = select_facts(
        strict,
        "E",
        payload,
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    assert facts == []  # a missing half must not silently halve the total
    permissive = ConceptSpec(
        "capital_expenditure", (TagGroup(("A", "B"), partial=True),), "instant", "currency"
    )
    facts, _ = select_facts(
        permissive,
        "E",
        payload,
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    assert [item.value for item in facts] == [10.0]


# ---------------------------------------------------------------- ratios and units


def test_ratio_uses_trailing_four_quarters_against_a_stock() -> None:
    quarters = [
        _entry(f"201{y}-{m:02d}-01", end, 100.0, "2016-05-01")
        for y, m, end in [
            (5, 1, "2015-03-31"),
            (5, 4, "2015-06-30"),
            (5, 7, "2015-09-30"),
            (5, 10, "2015-12-31"),
        ]
    ]
    flow, _ = select_facts(
        ConceptSpec("external_cash_generation", (TagGroup(("Tag",)),), "quarterly", "currency"),
        "E",
        _facts(quarters),
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    stock, _ = select_facts(
        ConceptSpec("total_debt", (TagGroup(("Tag",)),), "instant", "currency"),
        "E",
        _facts([_entry(None, "2015-12-31", 800.0, "2016-02-01")]),
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    derived, _ = build_derived_facts("E", {"external_cash_generation": flow, "total_debt": stock})
    ratio = [item for item in derived if item.feature_key == "debt_to_operating_cash_flow"]
    assert ratio and ratio[0].value == pytest.approx(2.0)  # 800 / (4 * 100)
    assert ratio[0].currency is None and ratio[0].unit == "ratio"


def test_zero_denominator_leaves_the_value_unknown() -> None:
    flow = select_facts(
        ConceptSpec("external_revenue", (TagGroup(("Tag",)),), "quarterly", "currency"),
        "E",
        _facts([_entry("2015-01-01", "2015-03-31", 0.0, "2015-05-01")]),
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )[0]
    capex = select_facts(
        ConceptSpec("capital_expenditure", (TagGroup(("Tag",)),), "quarterly", "currency"),
        "E",
        _facts([_entry("2015-01-01", "2015-03-31", 5.0, "2015-05-01")]),
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )[0]
    derived, notes = build_derived_facts(
        "E", {"external_revenue": flow, "capital_expenditure": capex}
    )
    assert not [item for item in derived if item.feature_key == "capex_to_revenue"]
    assert any("denominator is zero" in str(item["reason"]) for item in notes)


def test_every_concept_declares_a_currency_only_for_currency_units() -> None:
    for spec in CONCEPTS:
        assert (spec.currency is not None) == (spec.unit == "currency")


# ---------------------------------------------------------------- controls


def test_revised_series_are_labelled_and_not_claimed_as_vintage() -> None:
    revised = [item for item in CONTROL_PLANS if item.vintage_basis is VintageBasis.LATEST_REVISION]
    assert revised, "the revised series must be represented, and labelled"
    assert all(item.vintage_basis in set(VintageBasis) for item in CONTROL_PLANS)


def test_unavailable_series_are_declared_rather_than_substituted_silently() -> None:
    assert "high_yield_spread" in UNAVAILABLE_CONTROL_SERIES
    proxies = [item for item in CONTROL_PLANS if item.proxy_for]
    assert all(item.proxy_for in UNAVAILABLE_CONTROL_SERIES for item in proxies)


def test_missing_control_observations_stay_missing() -> None:
    rows = _parse_csv(b"observation_date,X\n2015-01-01,1.5\n2015-02-01,.\n", "X")
    assert [item[1] for item in rows] == [1.5]


def test_quarterly_control_is_carried_across_its_own_quarter(
    connection: sqlite3.Connection,
) -> None:
    plan = next(item for item in CONTROL_PLANS if item.frequency is Frequency.QUARTERLY)
    fetch = SeriesFetch(
        plan=plan,
        source_url="https://fred.stlouisfed.org/x",
        content_sha256="0" * 64,
        fetched_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        rows=((date(2015, 1, 1), 3.0),),
    )
    months = monthly_observations(fetch)
    assert [item[0].isoformat() for item in months] == ["2015-01-01", "2015-02-01", "2015-03-01"]
    assert {item[2] for item in months} == {3.0}
    report = ingest_series(connection, fetch)
    assert report["written"] == 3
    stored = connection.execute(
        "SELECT provenance_json FROM historical_control_observation_v2 LIMIT 1"
    ).fetchone()
    provenance = json.loads(str(stored[0]))
    assert provenance["vintage"] == plan.vintage_basis.value
    assert provenance["monthly_carry"].startswith("quarterly reading")


def test_control_availability_never_precedes_its_observation(
    connection: sqlite3.Connection,
) -> None:
    plan = CONTROL_PLANS[0]
    fetch = SeriesFetch(
        plan=plan,
        source_url="https://fred.stlouisfed.org/x",
        content_sha256="1" * 64,
        fetched_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        rows=((date(2015, 1, 15), 2.0),),
    )
    ingest_series(connection, fetch)
    for observed_at, availability_at in connection.execute(
        "SELECT observed_at, availability_at FROM historical_control_observation_v2"
    ):
        assert str(availability_at) >= str(observed_at)


# ---------------------------------------------------------------- analysis


def test_percentile_and_robust_z_handle_degenerate_input() -> None:
    assert percentile_of(1.0, []) is None
    assert robust_z(1.0, [1.0, 1.0, 1.0, 1.0]) is None
    assert percentile_of(3.0, [1.0, 2.0, 4.0]) == pytest.approx(2 / 3)


def test_false_positive_check_reports_overlap_rather_than_hiding_it() -> None:
    comparison = {
        "episodes": {
            "c": {"stratum": "crisis", "features": {"capex_to_revenue": {"level": 0.3}}},
            "b": {"stratum": "benign", "features": {"capex_to_revenue": {"level": 0.5}}},
        }
    }
    result = false_positive_check(comparison, ["capex_to_revenue"])
    assert result["features_evaluated"] == 1
    assert result["findings"][0]["separable"] is True
    overlapping = false_positive_check(
        {
            "episodes": {
                "c": {"stratum": "crisis", "features": {"x": {"level": 0.3}}},
                "c2": {"stratum": "crisis", "features": {"x": {"level": 0.9}}},
                "b": {"stratum": "benign", "features": {"x": {"level": 0.5}}},
            }
        },
        ["x"],
    )
    assert overlapping["findings"][0]["overlap"] is True
    assert "not evidence of predictive power" in str(overlapping["caveat"])


# ---------------------------------------------------------------- episode rosters


def test_every_stratum_is_represented_and_substitutions_carry_reasons() -> None:
    strata = {item.stratum for item in ROSTERS}
    assert strata == {"crisis", "benign", "current"}
    for roster in ROSTERS:
        for original, reason in roster.substitutions.items():
            assert original and len(reason) > 40
        if not roster.entities:
            assert roster.unmeasurable_reason


def test_episode_features_are_all_catalog_variables() -> None:
    assert set(EPISODE_FEATURES) <= set(BENCHMARK_VARIABLES)


# ---------------------------------------------------------------- gate integrity
#
# These cover the false-positive paths a reviewer found in the first cut of the gate:
# a re-run counting twice, evidence leaking in from failed episodes or live collection,
# a written excuse closing a causal role, and revised macro data passing as vintage.


def _seed_evidence(
    connection: sqlite3.Connection, run_id: str, feature_key: str, cutoff: str
) -> tuple[str, str, str]:
    """Create a genuine document -> event -> review -> fact -> observation chain.

    The feature store's triggers verify real lineage, so a fixture cannot shortcut it.
    Building the chain properly is also what makes these tests meaningful: they prove the
    gate reads evidence that actually exists, not rows that merely look like it.
    """
    suffix = f"{run_id}-{feature_key}"
    item = score(
        SourceItem.model_validate(
            {
                "title": f"filing for {feature_key}",
                "url": f"https://www.sec.gov/{suffix}",
                "source": "SEC EDGAR filing",
                "published_at": "2015-02-01",
                "discovered_at": "2015-02-01T00:00:00+00:00",
            }
        ),
        ["E"],
    )
    repository = SqliteRepository(Path("unused"))
    repository.insert(connection, item)
    repository.upsert_document(
        connection,
        item.item_id,
        "2015-02-01T00:00:00+00:00",
        "application/json",
        "fetched",
        "fixture filing body",
    )
    event_id = f"event-{suffix}"
    repository.insert_event(
        connection,
        FinancialEvent.model_validate(
            {
                "event_id": event_id,
                "document_id": item.item_id,
                "event_type": EventType.BALANCE_SHEET_REPORT,
                "source_entity": "E",
                "amount": 1.0,
                "currency": "USD",
                "effective_date": "2015-01-31",
                "confidence": 1.0,
                "evidence_text": f"fixture {feature_key}",
                "extractor": "fixture-1.0.0",
                "processed_at": "2015-02-01T00:00:00+00:00",
            }
        ),
    )
    cursor = connection.execute(
        """INSERT INTO evidence_reviews
           (fingerprint, decision, canonical_fingerprint, confidence, reasoning, model,
            reviewed_at) VALUES(?,?,?,?,?,?,?)""",
        (
            event_id,
            "confirm",
            event_id,
            1.0,
            "fixture",
            "fixture-1.0.0",
            "2015-02-01T00:00:00+00:00",
        ),
    )
    review_id = int(cursor.lastrowid or 0)
    fact_id = f"fact-{suffix}"
    assignment_id = f"assign-{suffix}"
    observation_id = f"obs-{suffix}"
    EvidenceRepository.register_canonical_fact(connection, fact_id)
    EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment.model_validate(
            {
                "assignment_id": assignment_id,
                "event_id": event_id,
                "canonical_fact_id": fact_id,
                "available_at": "2015-02-01T00:00:00+00:00",
                "reviewer_id": review_id,
                "assigned_by": "fixture",
                "assignment_method": "fixture",
                "created_at": "2015-02-01T00:00:00+00:00",
            }
        ),
    )
    EvidenceRepository.register_feature(
        connection,
        FeatureDefinitionV2(
            feature_key=feature_key,
            feature_version="1.0.0",
            definition_json='{"aggregation":"latest"}',
            released_at=datetime(2015, 1, 1, tzinfo=UTC),
        ),
    )
    EvidenceRepository.insert(
        connection,
        ObservationV2.model_validate(
            {
                "observation_id": observation_id,
                "event_id": event_id,
                "source_document_id": item.item_id,
                "source_locator": "fixture",
                "evidence_text": f"fixture {feature_key}",
                "entity_id": "E",
                "feature_key": feature_key,
                "feature_version": "1.0.0",
                "value_numeric": 1.0,
                "unit": "currency",
                "currency": "USD",
                "economic_scope": EconomicScope.ENTITY,
                "period_start": "2015-01-01",
                "period_end": "2015-01-31",
                "published_at": "2015-02-01T00:00:00+00:00",
                "availability_at": "2015-02-01T00:00:00+00:00",
                "extracted_at": "2015-02-01T00:00:00+00:00",
                "fact_status": FactStatus.DIRECT,
                "source_tier": SourceTier.PRIMARY,
                "source_quality": 1.0,
                "extraction_confidence": 1.0,
                "review_confidence": 1.0,
                "extractor_name": "fixture",
                "extractor_version": "1.0.0",
                "review_id": review_id,
            }
        ),
    )
    assert cutoff >= "2015-02-01T00:00:00+00:00"
    return fact_id, assignment_id, observation_id


def _seed_episode(
    connection: sqlite3.Connection,
    episode_id: str,
    stratum: str,
    *,
    version: str = "1.0.0",
    coverage_passed: bool = True,
    leakage_passed: bool = True,
    feature_keys: tuple[str, ...] = (),
    control_series: tuple[tuple[str, str], ...] = (),
    existing_controls: tuple[str, ...] = (),
    created_at: str = "2026-01-01T00:00:00+00:00",
    run_suffix: str = "",
) -> str:
    """Insert one gate-visible episode run with an immutable finalized build behind it."""
    run_id = f"run-{episode_id}-{version}{run_suffix}"
    build_id = f"build-{run_id}"
    cutoff = "2016-01-31T00:00:00+00:00"
    # The manifest must declare the controls the run freezes; the schema checks it.
    existing_rows = [
        dict(
            zip(
                (
                    "control_observation_id",
                    "series_id",
                    "series_version",
                    "period_start",
                    "period_end",
                    "observed_at",
                    "availability_at",
                    "value_numeric",
                    "unit",
                    "provenance_json",
                ),
                row,
                strict=True,
            )
        )
        for row in connection.execute(
            """SELECT control_observation_id, series_id, series_version, period_start,
                      period_end, observed_at, availability_at, value_numeric, unit,
                      provenance_json
                 FROM historical_control_observation_v2
                WHERE control_observation_id IN ({})""".format(  # noqa: S608
                ",".join("?" for _ in existing_controls) or "''"
            ),
            existing_controls,
        )
    ]
    manifest_json = json.dumps(
        {
            "controls": [
                {"series_id": series_id, "version": "1.0.0", "unit": "percent"}
                for series_id, _ in control_series
            ]
            + [
                {"series_id": row["series_id"], "version": "1.0.0", "unit": row["unit"]}
                for row in existing_rows
            ]
        }
    )
    connection.execute(
        "INSERT OR IGNORE INTO backfill_episode VALUES(?,?,?,?,?,?,?,?,?)",
        (
            episode_id,
            version,
            stratum,
            "2015-01-01",
            "2015-12-31",
            cutoff,
            manifest_json,
            f"checksum-{episode_id}-{version}",
            created_at,
        ),
    )
    connection.execute(
        "INSERT INTO backfill_run VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            episode_id,
            version,
            f"checksum-{episode_id}-{version}",
            f"input-{run_id}",
            "{}",
            "cov",
            "{}",
            "leak",
            int(coverage_passed),
            int(leakage_passed),
            1,
            1,
            created_at,
            len(control_series),
            1,
        ),
    )
    if feature_keys:
        connection.execute(
            "INSERT INTO dataset_build VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                build_id,
                "commit",
                "v1",
                cutoff,
                "2015-01-01",
                "2015-12-31",
                len(feature_keys),
                "{}",
                f"build-checksum-{run_id}",
                created_at,
            ),
        )
        for key in feature_keys:
            fact_id, assignment_id, observation_id = _seed_evidence(connection, run_id, key, cutoff)
            value_id = f"value-{run_id}-{key}"
            connection.execute(
                "INSERT INTO feature_value VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value_id,
                    build_id,
                    "E",
                    "2015-01-01",
                    "2015-01-31",
                    key,
                    "1.0.0",
                    1.0,
                    None,
                    1.0,
                    1.0,
                    1,
                    1,
                ),
            )
            connection.execute(
                "INSERT INTO feature_value_fact VALUES(?,?,?,?)",
                (value_id, fact_id, assignment_id, observation_id),
            )
            connection.execute(
                "INSERT INTO feature_value_contributor VALUES(?,?,?,?)",
                (value_id, fact_id, assignment_id, observation_id),
            )
        # finalization validates the build against the rows already written
        connection.execute(
            "INSERT INTO dataset_build_finalization VALUES(?,?)", (build_id, created_at)
        )
        connection.execute(
            "INSERT INTO backfill_build_link VALUES(?,?,?,?)",
            (run_id, "entity_month", build_id, f"build-checksum-{run_id}"),
        )
    for series_id, vintage in control_series:
        provenance = {"publisher": "p", "source_url": "u", "vintage": vintage}
        connection.execute(
            "INSERT OR IGNORE INTO control_series_definition VALUES(?,?,?,?,?)",
            (
                series_id,
                "1.0.0",
                "percent",
                '{"publisher":"required","source_url":"required","vintage":"required"}',
                "2026-01-01T00:00:00+00:00",
            ),
        )
        # The frozen snapshot must mirror a real control observation, so seed both.
        # The shared store holds one row per series, period and vintage; re-registering
        # it for a second episode is a no-op rather than a conflict.
        register_control_observation(
            connection,
            ControlObservation(
                control_observation_id=f"control-{series_id}-1.0.0-2015-01-01-{vintage}",
                series_id=series_id,
                series_version="1.0.0",
                period_start="2015-01-01",
                period_end="2015-01-31",
                observed_at="2015-01-31T00:00:00+00:00",
                availability_at="2015-02-01T00:00:00+00:00",
                value_numeric=1.0,
                unit="percent",
                provenance=provenance,
            ),
        )
        connection.execute(
            "INSERT INTO backfill_control_snapshot_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                f"control-{series_id}-1.0.0-2015-01-01-{vintage}",
                series_id,
                "1.0.0",
                "2015-01-01",
                "2015-01-31",
                "2015-01-31T00:00:00+00:00",
                "2015-02-01T00:00:00+00:00",
                1.0,
                "percent",
                json.dumps(provenance, sort_keys=True, separators=(",", ":")),
            ),
        )
    for row in existing_rows:
        # Snapshot a control already in the shared store, exactly as stored.
        connection.execute(
            "INSERT INTO backfill_control_snapshot_v2 VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                row["control_observation_id"],
                row["series_id"],
                row["series_version"],
                row["period_start"],
                row["period_end"],
                row["observed_at"],
                row["availability_at"],
                row["value_numeric"],
                row["unit"],
                row["provenance_json"],
            ),
        )
    connection.commit()
    return run_id


_ALL_ROLE_FEATURES: tuple[str, ...] = (
    "capital_expenditure",  # boom
    "capex_to_revenue",  # validation
    "fixed_obligations_to_external_cash",
    "debt_to_operating_cash_flow",  # vulnerability
    "debt_to_assets",
    "impairment",  # activated stress
    "deleveraging",  # resilience
)
_ALL_ROLE_CONTROLS: tuple[tuple[str, str], ...] = (
    ("policy_rate", "as_published"),  # shock
    ("commercial_industrial_loans", "as_published"),  # transmission + real economy
)


def _fully_qualified(
    connection: sqlite3.Connection, feature_keys: tuple[str, ...] = _ALL_ROLE_FEATURES
) -> None:
    """Seed the minimum that genuinely satisfies every rule the gate enforces."""
    for episode_id, stratum in (
        ("dotcom", "crisis"),
        ("shale", "crisis"),
        ("infra", "benign"),
        ("current", "current"),
    ):
        _seed_episode(
            connection,
            episode_id,
            stratum,
            feature_keys=feature_keys,
            control_series=_ALL_ROLE_CONTROLS,
        )


def test_a_re_run_of_one_crisis_does_not_count_as_two(
    connection: sqlite3.Connection,
) -> None:
    _seed_episode(
        connection, "shale", "crisis", version="1.0.0", feature_keys=("capital_expenditure",)
    )
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="2.0.0",
        feature_keys=("capital_expenditure",),
        created_at="2026-02-01T00:00:00+00:00",
    )
    readiness = evaluate_readiness(connection)
    assert readiness.accepted_crisis_episodes == 1
    assert any("crisis episodes 1 below required 2" in item for item in readiness.blocking_reasons)


def test_repeated_runs_of_one_version_do_not_count_twice(
    connection: sqlite3.Connection,
) -> None:
    _seed_episode(connection, "shale", "crisis", feature_keys=("capital_expenditure",))
    _seed_episode(
        connection,
        "shale",
        "crisis",
        run_suffix="-again",
        created_at="2026-03-01T00:00:00+00:00",
        feature_keys=("capital_expenditure",),
    )
    assert evaluate_readiness(connection).accepted_crisis_episodes == 1


def test_evidence_from_a_failed_episode_does_not_count(
    connection: sqlite3.Connection,
) -> None:
    _seed_episode(
        connection,
        "failed",
        "crisis",
        coverage_passed=False,
        feature_keys=("capital_expenditure", "debt_to_assets"),
        control_series=(("policy_rate", "as_published"),),
    )
    readiness = evaluate_readiness(connection)
    assert readiness.observed_variable_keys == ()
    assert readiness.accepted_crisis_episodes == 0


def test_evidence_that_never_reached_an_accepted_build_does_not_count(
    connection: sqlite3.Connection,
) -> None:
    """A loose observation proves nothing: it may be live collection or an unrelated run."""
    connection.execute(
        "INSERT OR IGNORE INTO feature_definition VALUES(?,?,?,?,?)",
        ("capital_expenditure", "1.0.0", '{"a":1}', "2026-01-01T00:00:00+00:00", None),
    )
    _seed_episode(connection, "benign-one", "benign", feature_keys=("capex_to_revenue",))
    readiness = evaluate_readiness(connection)
    assert "capital_expenditure" not in readiness.observed_variable_keys
    assert "capex_to_revenue" in readiness.observed_variable_keys


def test_a_documented_insufficiency_never_closes_a_causal_role(
    connection: sqlite3.Connection,
) -> None:
    # Everything measured except activated stress, which is then excused in writing.
    _fully_qualified(connection, tuple(key for key in _ALL_ROLE_FEATURES if key != "impairment"))
    excused = evaluate_readiness(
        connection,
        documented_insufficiency={CausalRole.ACTIVATED_STRESS: "no free ratings feed"},
    )
    assert excused.verdict.value == "NOT_YET_CALIBRATED"
    role = next(
        item for item in excused.role_coverage if item.causal_role is CausalRole.ACTIVATED_STRESS
    )
    assert role.documented_insufficiency  # the reason is reported
    assert role.satisfied is False  # and it still does not close the role
    assert any("activated_stress" in item for item in excused.blocking_reasons)


def test_revised_only_control_data_blocks_calibration(
    connection: sqlite3.Connection,
) -> None:
    for episode_id, stratum in (
        ("dotcom", "crisis"),
        ("shale", "crisis"),
        ("infra", "benign"),
        ("current", "current"),
    ):
        _seed_episode(
            connection,
            episode_id,
            stratum,
            feature_keys=_ALL_ROLE_FEATURES,
            control_series=(
                ("policy_rate", "as_published"),
                ("unemployment_rate", "latest_revision"),
            ),
        )
    readiness = evaluate_readiness(connection)
    assert "unemployment_rate" in readiness.revised_only_control_series
    assert readiness.verdict.value == "NOT_YET_CALIBRATED"
    assert any("latest-revision" in item for item in readiness.blocking_reasons)


def test_the_gate_can_pass_when_every_rule_is_genuinely_satisfied(
    connection: sqlite3.Connection,
) -> None:
    """The positive case, so the gate is known to be reachable and not merely strict."""
    _fully_qualified(connection)
    readiness = evaluate_readiness(connection)
    assert readiness.blocking_reasons == ()
    assert readiness.verdict.value == "HISTORICALLY_CALIBRATED"
    assert readiness.output_tier is OutputTier.HISTORICALLY_CALIBRATED
    assert readiness.accepted_crisis_episodes == 2
    assert readiness.accepted_benign_episodes == 1
    assert readiness.accepted_current_episodes == 1
    assert readiness.revised_only_control_series == ()
    assert all(item.satisfied for item in readiness.role_coverage)
    assert_claim_supported(readiness, OutputTier.HISTORICALLY_CALIBRATED)


def test_removing_any_single_requirement_reblocks_the_gate(
    connection: sqlite3.Connection,
) -> None:
    """Each rule is load-bearing: drop one input and the verdict must revert."""
    _fully_qualified(connection, tuple(key for key in _ALL_ROLE_FEATURES if key != "deleveraging"))
    reblocked = evaluate_readiness(connection)
    assert reblocked.verdict.value == "NOT_YET_CALIBRATED"
    assert any(
        "resilience" in item or "counter-evidence" in item for item in reblocked.blocking_reasons
    )


# ---------------------------------------------------------------- publication path


def test_publishing_cannot_claim_calibration_when_the_gate_fails(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """The site build consults the gate before writing anything, and refuses."""
    _seed_episode(connection, "shale", "crisis", feature_keys=("capital_expenditure",))
    connection.commit()
    database = Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))
    output = tmp_path / "site"
    with pytest.raises(CalibrationClaimError):
        build_static_site(output, database, claimed_tier=OutputTier.HISTORICALLY_CALIBRATED)
    assert not (output / "data" / "snapshot.json").exists()


def test_published_snapshot_always_carries_the_verdict(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_episode(connection, "shale", "crisis", feature_keys=("capital_expenditure",))
    connection.commit()
    database = Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))
    output = build_static_site(tmp_path / "site", database)
    payload = json.loads((output / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert payload["signal"]["calibration_label"] == "NOT YET CALIBRATED"
    assert payload["calibration"]["verdict"] == "NOT_YET_CALIBRATED"
    assert payload["calibration"]["historically_calibrated"] is False
    assert payload["calibration"]["blocking_reasons"]
    assert "not calibrated against" in payload["calibration"]["statement"]


def test_publishing_a_calibrated_claim_is_allowed_only_once_earned(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    _fully_qualified(connection)
    database = Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))
    output = build_static_site(
        tmp_path / "site", database, claimed_tier=OutputTier.HISTORICALLY_CALIBRATED
    )
    payload = json.loads((output / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert payload["signal"]["calibration_label"] == "HISTORICALLY CALIBRATED"
    assert payload["calibration"]["claimed_tier"] == "historically_calibrated"


CALIBRATION_WARNING = (
    "NOT YET HISTORICALLY CALIBRATED — this is an evidence/convergence indicator, "
    "not a crash probability."
)


def test_rendered_dashboard_shows_the_warning_beside_the_score(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """The visitor, not just the database, must be able to see that this is uncalibrated."""
    _seed_episode(connection, "shale", "crisis", feature_keys=("capital_expenditure",))
    connection.commit()
    database = Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))
    output = build_static_site(tmp_path / "site", database)
    page = (output / "index.html").read_text(encoding="utf-8")

    assert CALIBRATION_WARNING in page
    # One banner beside the hero score, one beside the dashboard "Current reading" score.
    assert page.count('class="calibration-banner"') == 2
    hero = page.index('id="overviewScore"')
    dashboard = page.index('id="dashScore"')
    first_banner = page.index('class="calibration-banner"')
    second_banner = page.index('class="calibration-banner"', first_banner + 1)
    assert hero < first_banner < dashboard < second_banner
    # Wired to the published signal, with the warning as the fail-closed default.
    assert "renderCalibration" in page
    assert "calibration_label" in page

    payload = json.loads((output / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert payload["signal"]["calibration_label"] == "NOT YET CALIBRATED"


def test_the_warning_survives_a_dead_script(connection: sqlite3.Connection, tmp_path: Path) -> None:
    """With scripting removed the banner text is still in the document."""
    _seed_episode(connection, "shale", "crisis", feature_keys=("capital_expenditure",))
    connection.commit()
    database = Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))
    output = build_static_site(tmp_path / "site", database)
    page = (output / "index.html").read_text(encoding="utf-8")
    without_scripts = re.sub(r"<script\b.*?</script>", "", page, flags=re.DOTALL)
    assert CALIBRATION_WARNING in without_scripts


def test_a_rejected_publication_writes_nothing_at_all(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_episode(connection, "shale", "crisis", feature_keys=("capital_expenditure",))
    connection.commit()
    database = Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))
    output = tmp_path / "site"
    with pytest.raises(CalibrationClaimError):
        build_static_site(output, database, claimed_tier=OutputTier.HISTORICALLY_CALIBRATED)
    assert not output.exists()  # not even an empty directory is left behind


def test_an_earned_calibration_replaces_the_warning(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    _fully_qualified(connection)
    database = Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))
    output = build_static_site(
        tmp_path / "site", database, claimed_tier=OutputTier.HISTORICALLY_CALIBRATED
    )
    payload = json.loads((output / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert payload["signal"]["calibration_label"] == "HISTORICALLY CALIBRATED"
    # The script swaps the banner text; the served default stays the cautious one.
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "this reading is placed against accepted historical episodes" in page


# ---------------------------------------------------------------- point-in-time controls


def test_the_credit_spread_control_is_point_in_time() -> None:
    """The spread that measures activated stress must not be a revision."""
    plan = CONTROL_PLANS_BY_ID["corporate_bond_spread"]
    assert plan.vintage_basis is VintageBasis.AS_PUBLISHED
    assert plan.fred_id == "BAA10Y"
    assert plan.proxy_for == "investment_grade_spread"
    assert plan.proxy_for in UNAVAILABLE_CONTROL_SERIES


def test_activated_stress_and_shock_can_be_measured_point_in_time() -> None:
    """Both roles must have at least one variable served by a non-revised series."""
    published = {
        plan.series_id for plan in CONTROL_PLANS if plan.vintage_basis is VintageBasis.AS_PUBLISHED
    }
    for role in (CausalRole.ACTIVATED_STRESS, CausalRole.SHOCK):
        servable = [
            variable
            for variable in variables_for_role(role)
            if published.intersection(variable.control_series)
        ]
        assert servable, f"{role.value} has no point-in-time control series"


# ---------------------------------------------------------------- fixed obligations


def test_a_strict_composite_still_requires_every_leg() -> None:
    """Only the composites declared partial may be assembled from a subset."""
    assert "total_fixed_obligations" in PARTIAL_COMPOSITES
    assert "free_cash_flow" not in PARTIAL_COMPOSITES
    capex = select_facts(
        ConceptSpec("capital_expenditure", (TagGroup(("Tag",)),), "quarterly", "currency"),
        "E",
        _facts([_entry("2015-01-01", "2015-03-31", 5.0, "2015-05-01")]),
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )[0]
    derived, notes = build_derived_facts("E", {"capital_expenditure": capex})
    assert not [item for item in derived if item.feature_key == "free_cash_flow"]
    assert any(item.get("feature") == "free_cash_flow" for item in notes)


# ---------------------------------------------------------------- per-episode features


def test_a_bank_stratum_is_not_gated_on_measurements_banks_do_not_report() -> None:
    roster = next(item for item in ROSTERS if item.episode_id == "regional-bank-stress")
    assert roster.features == BANK_FEATURES
    assert "capital_expenditure" not in roster.features
    assert "external_revenue" not in roster.features
    assert "deposit_funding_share" in roster.features
    # and the feature set it does use is real catalog vocabulary
    assert set(roster.features) <= set(BENCHMARK_VARIABLES)


def test_episode_feature_sets_drive_their_own_builds() -> None:
    """Specs must follow the roster, not a module-level default."""
    roster = next(item for item in ROSTERS if item.episode_id == "regional-bank-stress")
    assert {spec.feature_key for spec in feature_specs(roster.features)} == set(roster.features)
    assert {spec.source_feature_key for spec in ecosystem_specs(roster.features)} == set(
        roster.features
    )


# ---------------------------------------------------------------- FRED vintage support


class _StubResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.url = "https://api.stlouisfed.org/fred/series/observations"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _StubSession:
    """Records the request so the test can assert on realtime parameters, not just output."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> _StubResponse:
        self.calls.append({"url": url, **kwargs})
        return _StubResponse(self.payload)


_VINTAGE_PAYLOAD: dict[str, object] = {
    "observations": [
        {"date": "2015-01-01", "value": "5.7"},
        {"date": "2015-02-01", "value": "5.5"},
        {"date": "2015-03-01", "value": "."},
    ]
}


def test_vintage_request_pins_both_realtime_bounds() -> None:
    plan = CONTROL_PLANS_BY_ID["unemployment_rate"]
    session = _StubSession(_VINTAGE_PAYLOAD)
    fetch = fetch_series_vintage(
        plan,
        api_key="test-key",
        vintage_date=date(2018, 1, 31),
        user_agent="test",
        session=cast(Any, session),
    )
    params = cast(dict[str, str], session.calls[0]["params"])
    assert params["realtime_start"] == "2018-01-31"
    assert params["realtime_end"] == "2018-01-31"
    assert params["series_id"] == "UNRATE"
    assert fetch.vintage_date == date(2018, 1, 31)
    assert fetch.vintage == "point_in_time:2018-01-31"
    # An unpublished observation stays missing rather than becoming zero.
    assert [value for _, value in fetch.rows] == [5.7, 5.5]


def test_the_api_key_never_reaches_stored_provenance(
    connection: sqlite3.Connection,
) -> None:
    plan = CONTROL_PLANS_BY_ID["unemployment_rate"]
    session = _StubSession(_VINTAGE_PAYLOAD)
    fetch = fetch_series_vintage(
        plan,
        api_key="super-secret-key",
        vintage_date=date(2018, 1, 31),
        user_agent="test",
        session=cast(Any, session),
    )
    assert "super-secret-key" not in fetch.source_url
    ingest_series(connection, fetch)
    for (provenance,) in connection.execute(
        "SELECT provenance_json FROM historical_control_observation_v2"
    ):
        assert "super-secret-key" not in str(provenance)
        assert json.loads(str(provenance))["vintage"] == "point_in_time:2018-01-31"


def test_vintage_acquisition_requires_a_key() -> None:
    with pytest.raises(ValueError, match="FRED API key"):
        fetch_series_vintage(
            CONTROL_PLANS_BY_ID["unemployment_rate"],
            api_key="",
            vintage_date=date(2018, 1, 31),
            user_agent="test",
        )


def test_only_revised_series_are_fetched_as_vintages() -> None:
    """A series that is never revised does not need, and does not get, an API call."""
    session = _StubSession(_VINTAGE_PAYLOAD)
    report = ingest_controls(
        sqlite3.connect(":memory:"),
        user_agent="test",
        plans=[CONTROL_PLANS_BY_ID["policy_rate"]],
        api_key="test-key",
        vintage_date=date(2018, 1, 31),
        session=cast(Any, session),
    )
    # policy_rate is as_published, so the vintage endpoint was never called; the CSV
    # route was used instead and (with a stub session) simply failed rather than
    # silently producing a fake vintage.
    assert session.calls == [] or all(
        "realtime_start" not in str(call.get("params", "")) for call in session.calls
    )
    assert report["fred_api_key_supplied"] is True


def test_a_genuine_vintage_satisfies_the_point_in_time_rule(
    connection: sqlite3.Connection,
) -> None:
    """The gate must accept real vintages and still refuse revisions."""
    _seed_episode(
        connection,
        "shale",
        "crisis",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("policy_rate", "point_in_time:2016-01-31"),),
    )
    readiness = evaluate_readiness(connection)
    assert readiness.revised_only_control_series == ()
    assert not any("latest-revision" in item for item in readiness.blocking_reasons)


def test_a_revision_is_still_refused_after_vintage_support_exists(
    connection: sqlite3.Connection,
) -> None:
    _seed_episode(
        connection,
        "shale",
        "crisis",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("unemployment_rate", "latest_revision"),),
    )
    readiness = evaluate_readiness(connection)
    assert readiness.revised_only_control_series == ("unemployment_rate",)
    assert any("latest-revision" in item for item in readiness.blocking_reasons)


def test_settings_expose_the_key_without_defaulting_to_one() -> None:
    assert Settings(_env_file=None).fred_api_key == ""
    assert Settings(_env_file=None, fred_api_key="abc").fred_api_key == "abc"


# ---------------------------------------------------------------- obligation integrity


def test_debt_alone_does_not_become_a_fixed_obligation_total() -> None:
    """Without a lease, purchase or guarantee leg the total stays unknown."""
    debt = select_facts(
        ConceptSpec("total_debt", (TagGroup(("Tag",)),), "instant", "currency"),
        "E",
        _facts([_entry(None, "2015-12-31", 900.0, "2016-02-01")]),
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )[0]
    flow = select_facts(
        ConceptSpec("external_cash_generation", (TagGroup(("Tag",)),), "quarterly", "currency"),
        "E",
        _facts(
            [
                _entry("2015-01-01", "2015-03-31", 100.0, "2015-05-01"),
                _entry("2015-01-01", "2015-06-30", 200.0, "2015-08-01"),
                _entry("2015-01-01", "2015-09-30", 300.0, "2015-11-01"),
                _entry("2015-01-01", "2015-12-31", 400.0, "2016-02-01"),
            ]
        ),
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )[0]
    derived, notes = build_derived_facts(
        "E", {"total_debt": debt, "external_cash_generation": flow}
    )
    assert not [
        item for item in derived if item.feature_key == "fixed_obligations_to_external_cash"
    ]
    assert any(
        "debt alone would not be a fixed-obligation total" in str(item.get("reason", ""))
        for item in notes
    )


def test_one_supplementary_leg_is_enough_to_establish_the_total() -> None:
    common = dict(
        availability_cutoff=date(2017, 1, 1),
        period_start=date(2015, 1, 1),
        period_end=date(2015, 12, 31),
    )
    debt = select_facts(
        ConceptSpec("total_debt", (TagGroup(("Tag",)),), "instant", "currency"),
        "E",
        _facts([_entry(None, "2015-12-31", 900.0, "2016-02-01")]),
        **common,
    )[0]
    lease = select_facts(
        ConceptSpec(
            "lease_and_guarantee_commitments", (TagGroup(("Tag",)),), "instant", "currency"
        ),
        "E",
        _facts([_entry(None, "2015-12-31", 100.0, "2016-02-01")]),
        **common,
    )[0]
    flow = select_facts(
        ConceptSpec("external_cash_generation", (TagGroup(("Tag",)),), "quarterly", "currency"),
        "E",
        _facts(
            [
                _entry("2015-01-01", "2015-03-31", 100.0, "2015-05-01"),
                _entry("2015-01-01", "2015-06-30", 200.0, "2015-08-01"),
                _entry("2015-01-01", "2015-09-30", 300.0, "2015-11-01"),
                _entry("2015-01-01", "2015-12-31", 400.0, "2016-02-01"),
            ]
        ),
        **common,
    )[0]
    derived, _ = build_derived_facts(
        "E",
        {
            "total_debt": debt,
            "lease_and_guarantee_commitments": lease,
            "external_cash_generation": flow,
        },
    )
    ratio = [item for item in derived if item.feature_key == "fixed_obligations_to_external_cash"]
    assert ratio and ratio[0].value == pytest.approx(2.5)  # (900 + 100) / 400


def test_aoci_is_named_and_directed_honestly() -> None:
    variable = BENCHMARK_VARIABLES["accumulated_other_comprehensive_income"]
    # A more negative balance is a bigger hole, so a higher reading is less pressure.
    assert variable.direction is Direction.HIGHER_IS_LESS_PRESSURE
    assert variable.causal_role is CausalRole.VULNERABILITY
    assert "not an unrealised-securities-loss line" in variable.comparability
    assert "unrealized_securities_loss" not in BENCHMARK_VARIABLES


# ---------------------------------------------------------------- vintage pipeline


def test_a_vintage_lands_beside_an_existing_revision_without_colliding(
    connection: sqlite3.Connection,
) -> None:
    """The upgrade path: a database already holding today's revisions accepts a vintage.

    This is the case the earlier patch got wrong. Both versions of the same period are
    legitimate and both must survive: the revision is what the series says now, the
    vintage is what it said then.
    """
    plan = CONTROL_PLANS_BY_ID["unemployment_rate"]
    revision = SeriesFetch(
        plan=plan,
        source_url="https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE",
        content_sha256="0" * 64,
        fetched_at="2026-09-01T00:00:00+00:00",
        rows=((date(2015, 1, 1), 5.7),),
    )
    assert ingest_series(connection, revision)["written"] == 1

    session = _StubSession(_VINTAGE_PAYLOAD)
    vintage = fetch_series_vintage(
        plan,
        api_key="test-key",
        vintage_date=date(2018, 1, 31),
        user_agent="test",
        session=cast(Any, session),
    )
    assert ingest_series(connection, vintage)["written"] >= 1

    stored = {
        str(row[0]): float(row[1])
        for row in connection.execute(
            """SELECT vintage, value_numeric FROM historical_control_observation_v2
                WHERE series_id='unemployment_rate' AND period_start='2015-01-01'"""
        )
    }
    assert stored == {"latest_revision": 5.7, "point_in_time:2018-01-31": 5.7}
    assert len(stored) == 2, "both versions of the same period must remain stored"


def test_each_episode_asks_for_its_own_cutoff_not_one_shared_vintage() -> None:
    cutoffs = {
        roster.episode_id: roster.availability_cutoff
        for roster in ROSTERS
        if revised_series_for(roster)
    }
    assert len(set(cutoffs.values())) > 1, "episodes must not share a single vintage date"
    for roster in ROSTERS:
        for series_id in revised_series_for(roster):
            plan = CONTROL_PLANS_BY_ID[series_id]
            assert plan.vintage_basis is VintageBasis.LATEST_REVISION


def test_bootstrap_loads_only_required_as_published_controls() -> None:
    plans = as_published_plans_for()
    assert plans
    required = {series_id for roster in ROSTERS for series_id in roster.controls}
    assert {plan.series_id for plan in plans} <= required
    assert all(plan.vintage_basis is VintageBasis.AS_PUBLISHED for plan in plans)
    assert "policy_rate" in {plan.series_id for plan in plans}
    assert "corporate_bond_spread" in {plan.series_id for plan in plans}
    assert "commercial_industrial_loans" not in {plan.series_id for plan in plans}


def test_bootstrap_reuses_as_published_controls_already_in_state(
    connection: sqlite3.Connection,
) -> None:
    plan = CONTROL_PLANS_BY_ID["policy_rate"]
    ingest_series(
        connection,
        SeriesFetch(
            plan=plan,
            source_url="https://example.test/fedfunds",
            content_sha256="a" * 64,
            fetched_at="2026-01-01T00:00:00+00:00",
            rows=((date(2015, 1, 1), 0.11),),
        ),
    )
    assert "policy_rate" not in {item.series_id for item in missing_as_published_plans(connection)}


def test_acquisition_requests_one_vintage_per_accepted_episode(
    connection: sqlite3.Connection,
) -> None:
    """The key reaches the request, each episode gets its own date, and nothing leaks."""
    for episode_id, stratum in (("pandemic", "benign"), ("current", "current")):
        _seed_episode(connection, episode_id, stratum, feature_keys=("capital_expenditure",))
    rosters = tuple(
        item
        for item in ROSTERS
        if item.episode_id in {"pandemic-technology-acceleration", "current-ai-cycle"}
    )
    session = _StubSession(_VINTAGE_PAYLOAD)
    report = acquire_episode_vintages(
        connection,
        api_key="super-secret-key",
        user_agent="test",
        rosters=rosters,
        accepted_only=False,
        session=cast(Any, session),
    )
    requested = {cast(dict[str, str], call["params"])["realtime_start"] for call in session.calls}
    assert requested == {"2023-01-31", "2026-08-01"}, requested
    for call in session.calls:
        params = cast(dict[str, str], call["params"])
        assert params["realtime_start"] == params["realtime_end"]
        assert params["api_key"] == "super-secret-key"
    assert int(cast(int, report["series_written"])) > 0
    assert report["failures"] == []
    # The key reached the request and nothing else.
    for (provenance,) in connection.execute(
        "SELECT provenance_json FROM historical_control_observation_v2"
    ):
        assert "super-secret-key" not in str(provenance)
    for (url,) in connection.execute(
        "SELECT DISTINCT json_extract(provenance_json,'$.source_url') "
        "FROM historical_control_observation_v2"
    ):
        assert "api_key" not in str(url)

    first_call_count = len(session.calls)
    repeated = acquire_episode_vintages(
        connection,
        api_key="super-secret-key",
        user_agent="test",
        rosters=rosters,
        accepted_only=False,
        session=cast(Any, session),
    )
    assert len(session.calls) == first_call_count
    assert repeated["series_written"] == 0
    repeated_outcomes = cast(list[dict[str, object]], repeated["outcomes"])
    assert sum(int(item["already_present"]) for item in repeated_outcomes) > 0


def test_acquisition_refuses_to_run_without_a_key(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError, match="ASRO_FRED_API_KEY"):
        acquire_episode_vintages(connection, api_key="", user_agent="test")


def test_readiness_scope_excludes_unrelated_accepted_backfills(
    connection: sqlite3.Connection,
) -> None:
    _seed_episode(connection, "benchmark", "current", feature_keys=("capital_expenditure",))
    _seed_episode(
        connection,
        "unrelated-slice",
        "current",
        feature_keys=("capital_expenditure",),
    )
    scoped = evaluate_readiness(connection, episode_ids=("benchmark",))
    assert scoped.accepted_current_episodes == 1


def test_a_vintage_cut_after_the_cutoff_is_refused(
    connection: sqlite3.Connection,
) -> None:
    """A later revision wearing a point-in-time label must not pass."""
    _seed_episode(
        connection,
        "shale",
        "crisis",
        feature_keys=_ALL_ROLE_FEATURES,
        # the seeded episode's cutoff is 2016-01-31
        control_series=(("unemployment_rate", "point_in_time:2020-06-30"),),
    )
    readiness = evaluate_readiness(connection)
    assert readiness.revised_only_control_series == ("unemployment_rate",)
    assert any("latest-revision" in item for item in readiness.blocking_reasons)


def test_a_malformed_point_in_time_marking_is_refused(
    connection: sqlite3.Connection,
) -> None:
    _seed_episode(
        connection,
        "shale",
        "crisis",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("unemployment_rate", "point_in_time:whenever"),),
    )
    assert evaluate_readiness(connection).revised_only_control_series == ("unemployment_rate",)


def test_strict_point_in_time_parsing() -> None:
    assert point_in_time_date("point_in_time:2018-01-31") == date(2018, 1, 31)
    for bad in (
        "point_in_time:2018-1-3",
        "point_in_time:soon",
        "point_in_time:2018-01-31 (approx)",
        "as_published",
        "latest_revision",
        "",
    ):
        assert point_in_time_date(bad) is None


def test_an_episode_snapshots_the_vintage_matching_its_own_cutoff(
    connection: sqlite3.Connection,
) -> None:
    """With several vintages stored, the runner must freeze the one for this cutoff."""
    _seed_episode(
        connection,
        "shale",
        "crisis",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(
            ("unemployment_rate", "latest_revision"),
            ("unemployment_rate", "point_in_time:2016-01-31"),
        ),
    )
    readiness = evaluate_readiness(connection)
    # Both rows exist, but only the one matching the episode cutoff should qualify it.
    stored = {
        str(row[0])
        for row in connection.execute(
            "SELECT vintage FROM historical_control_observation_v2 "
            "WHERE series_id='unemployment_rate'"
        )
    }
    assert stored == {"latest_revision", "point_in_time:2016-01-31"}
    assert isinstance(readiness.revised_only_control_series, tuple)


def test_upgrading_a_revision_only_database_clears_the_blocker(
    connection: sqlite3.Connection,
) -> None:
    """End to end: revisions in place, vintages acquired, episode rebuilt, blocker gone.

    This is the path the reviewer reproduced by hand. It starts from a database that
    already holds latest-revision control data, acquires genuine (mocked) vintages cut at
    the episode's own cutoff, rebuilds the episode's snapshot, and asserts three things:
    both versions survive, the rebuilt episode freezes the vintage, and the revised-only
    blocker clears without the gate being touched.
    """
    # 1. a database whose only control evidence is today's revision
    _seed_episode(
        connection,
        "shale",
        "crisis",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("unemployment_rate", "latest_revision"),),
    )
    before = evaluate_readiness(connection)
    assert before.revised_only_control_series == ("unemployment_rate",)
    assert any("latest-revision" in item for item in before.blocking_reasons)

    # 2. acquire the vintage for that episode's own cutoff
    plan = CONTROL_PLANS_BY_ID["unemployment_rate"]
    session = _StubSession(_VINTAGE_PAYLOAD)
    fetch = fetch_series_vintage(
        plan,
        api_key="test-key",
        vintage_date=date(2016, 1, 31),  # the seeded episode's cutoff
        user_agent="test",
        session=cast(Any, session),
    )
    assert ingest_series(connection, fetch)["written"] >= 1

    # 3. both versions of the same period remain stored
    versions = {
        str(row[0])
        for row in connection.execute(
            "SELECT vintage FROM historical_control_observation_v2 "
            "WHERE series_id='unemployment_rate'"
        )
    }
    assert versions == {"latest_revision", "point_in_time:2016-01-31"}

    # 4. rebuild the episode: a later run whose snapshot carries the vintage
    vintage_ids = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT control_observation_id FROM historical_control_observation_v2 "
            "WHERE vintage = 'point_in_time:2016-01-31'"
        )
    )
    assert vintage_ids
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="2.0.0",
        created_at="2026-02-01T00:00:00+00:00",
        feature_keys=_ALL_ROLE_FEATURES,
        existing_controls=vintage_ids,
    )

    # 5. the blocker clears, and the episode still counts once
    after = evaluate_readiness(connection)
    assert after.revised_only_control_series == ()
    assert not any("latest-revision" in item for item in after.blocking_reasons)
    assert after.accepted_crisis_episodes == 1, "a rebuild is not a second crisis"
    assert after.verdict.value == "NOT_YET_CALIBRATED"  # still one crisis short


# ---------------------------------------------------------------- deployment order

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "monitor.yml"


def _workflow_step_names() -> list[str]:
    """Step names in file order. Parsed textually so no YAML dependency is needed."""
    return re.findall(r"^      - name: (.+)$", WORKFLOW_PATH.read_text(encoding="utf-8"), re.M)


def test_vintages_are_acquired_before_the_state_is_packaged() -> None:
    """The packaged database must already contain the vintages it will be published with.

    Acquiring after packaging publishes a database that predates its own evidence, and
    the release check then validates a state pointer that does not describe what the site
    is serving.
    """
    steps = _workflow_step_names()
    order = {name: index for index, name in enumerate(steps)}
    required = [
        "Restore verified observatory state",
        "Acquire point-in-time control vintages",
        "Package candidate immutable state",
        "Build static site",
        "Validate usable release artifact",
        "Publish immutable state assets",
    ]
    for name in required:
        assert name in order, f"workflow step missing: {name}"
    positions = [order[name] for name in required]
    assert positions == sorted(positions), dict(zip(required, positions, strict=True))


def test_the_vintage_step_carries_the_key_and_cannot_fail_the_run() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    step = text[text.index("      - name: Acquire point-in-time control vintages") :]
    step = step[: step.index("      - name: Package candidate immutable state")]
    assert "ASRO_FRED_API_KEY: ${{ secrets.ASRO_FRED_API_KEY }}" in step
    assert "continue-on-error: true" in step
    assert "run: asro acquire-vintages --bootstrap" in step


def test_packaged_state_contains_the_vintages_and_matches_what_is_published(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """Package after acquisition: the artifact holds the vintage rows and one identity."""
    _seed_episode(
        connection,
        "shale",
        "crisis",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("policy_rate", "as_published"),),
    )
    plan = CONTROL_PLANS_BY_ID["unemployment_rate"]
    session = _StubSession(_VINTAGE_PAYLOAD)
    ingest_series(
        connection,
        fetch_series_vintage(
            plan,
            api_key="test-key",
            vintage_date=date(2016, 1, 31),
            user_agent="test",
            session=cast(Any, session),
        ),
    )
    connection.commit()
    database = Path(str(connection.execute("PRAGMA database_list").fetchone()[2]))

    packaged = package_state(
        database,
        tmp_path / "state",
        repository="x0on/ASRO",
        source_commit="deadbeef",
        workflow_run_id="1",
    )

    # (2) the packaged database carries the vintage rows
    restored = tmp_path / "restored.db"
    with gzip.open(str(packaged["asset_path"]), "rb") as handle:
        restored.write_bytes(handle.read())
    with sqlite3.connect(restored) as opened:
        vintages = {
            str(row[0])
            for row in opened.execute(
                "SELECT DISTINCT vintage FROM historical_control_observation_v2"
            )
        }
    assert "point_in_time:2016-01-31" in vintages

    # (3) the packaged identity is the identity of that same database
    version = cast(dict[str, Any], packaged["version"])
    assert version["database_sha256"] == hashlib.sha256(database.read_bytes()).hexdigest()
    pointer = cast(dict[str, Any], packaged["pointer"])
    assert pointer["current_version_id"] == version["version_id"]
    identity = _database_state_identity(database)
    assert identity["database_sha256"] == version["database_sha256"]


def test_every_report_reflects_the_same_final_build(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """One pass, one database: the reports cannot disagree about which build they describe."""
    _seed_episode(
        connection,
        "shale",
        "crisis",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(
            ("policy_rate", "as_published"),
            ("unemployment_rate", "latest_revision"),
        ),
    )
    insufficiency = tmp_path / "insufficiency.json"
    insufficiency.write_text(json.dumps({"insufficiencies": []}), encoding="utf-8")
    reports = tmp_path / "reports"
    readiness = write_benchmark_reports(connection, reports, insufficiency_path=insufficiency)

    written = {path.name for path in reports.iterdir()}
    assert written == set(REPORT_NAMES)

    status = json.loads((reports / "readiness.json").read_text(encoding="utf-8"))
    coverage = json.loads((reports / "coverage.json").read_text(encoding="utf-8"))
    vintage = json.loads((reports / "revision_and_vintage.json").read_text(encoding="utf-8"))

    # readiness and coverage agree about which episodes were accepted
    accepted_in_coverage = {
        episode_id
        for episode_id, entry in coverage["episodes"].items()
        if entry.get("coverage_passed") and entry.get("leakage_passed")
    }
    accepted_in_readiness = {
        entry["episode_id"] for entry in status["episode_runs_considered"] if entry["accepted"]
    }
    assert accepted_in_coverage == accepted_in_readiness

    # the vintage report describes the same control rows the gate judged -- equality,
    # because a report that named more or fewer revised series than the gate blocked on
    # would be describing a different selection than the one that produced the verdict
    reported_revised = {
        series_id
        for series_id, entries in vintage["control_series"].items()
        for entry in entries
        if entry["in_accepted_run"]
        and entry["vintage"] != "as_published"
        and not str(entry["vintage"]).startswith("point_in_time:")
    }
    assert reported_revised == {"unemployment_rate"}
    assert reported_revised == set(status["revised_only_control_series"])
    assert vintage["reported_run_ids"] == status["reported_run_ids"]
    assert status["verdict"] == readiness.verdict.value


# ---------------------------------------------------------------- report run selection


def _report_payloads(connection: sqlite3.Connection, tmp_path: Path) -> dict[str, Any]:
    insufficiency = tmp_path / "insufficiency.json"
    insufficiency.write_text(json.dumps({"insufficiencies": []}), encoding="utf-8")
    directory = tmp_path / "reports"
    write_benchmark_reports(connection, directory, insufficiency_path=insufficiency)
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in directory.iterdir()}


def test_reports_describe_only_the_latest_run_after_a_rebuild(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """A rebuild leaves an older run behind; no report may still be describing it.

    This is the upgrade shape: the first run is revision-only and fails its gates, the
    rebuild after vintage acquisition passes. Readiness reads the newer run, so every
    other report must read the newer run too, or the set contradicts itself.
    """
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="1.0.0",
        coverage_passed=False,
        leakage_passed=False,
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("unemployment_rate", "latest_revision"),),
    )
    old_run = "run-shale-1.0.0"
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="2.0.0",
        created_at="2026-06-01T00:00:00+00:00",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("policy_rate", "as_published"),),
    )
    new_run = "run-shale-2.0.0"

    payloads = _report_payloads(connection, tmp_path)
    status = payloads["readiness.json"]
    assert status["reported_run_ids"] == [new_run]
    assert [entry["run_id"] for entry in status["episode_runs_considered"]] == [new_run]

    # coverage reflects the rebuild, not the run it replaced
    episode = payloads["coverage.json"]["episodes"]["shale"]
    assert episode["coverage_passed"] is True
    assert episode["leakage_passed"] is True

    # and no report smuggles the old run's rows back in
    for name in ("coverage.json", "leakage.json", "missingness.json"):
        assert old_run not in json.dumps(payloads[name])
    assert status["revised_only_control_series"] == []


def test_a_failed_rebuild_does_not_silently_fall_back_to_the_older_accepted_run(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """The reverse case: if the newest run fails, the reports must say so."""
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="1.0.0",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("policy_rate", "as_published"),),
    )
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="2.0.0",
        created_at="2026-06-01T00:00:00+00:00",
        coverage_passed=False,
        leakage_passed=False,
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("unemployment_rate", "latest_revision"),),
    )
    payloads = _report_payloads(connection, tmp_path)
    status = payloads["readiness.json"]
    assert status["reported_run_ids"] == ["run-shale-2.0.0"]
    assert status["accepted_episodes"]["crisis"] == 0, (
        "an older accepted run must not keep an episode alive after a failed rebuild"
    )
    episode = payloads["coverage.json"]["episodes"]["shale"]
    assert episode["coverage_passed"] is False
    assert "run-shale-1.0.0" not in json.dumps(payloads["coverage.json"])

    # the failed run's controls are reported, but must not be labelled as judged: the
    # gate blocks on accepted runs only, so a report that called them accepted would
    # name a blocker the gate never raised
    entries = [
        entry
        for entries in payloads["revision_and_vintage.json"]["control_series"].values()
        for entry in entries
    ]
    assert entries, "the failed run's snapshots must still be reported"
    assert [entry["in_accepted_run"] for entry in entries] == [False] * len(entries)
    assert payloads["revision_and_vintage.json"]["accepted_run_ids"] == []
    assert status["revised_only_control_series"] == []


def test_readiness_and_coverage_cannot_disagree_about_acceptance(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """The invariant the shared run set exists to guarantee, asserted directly."""
    _seed_episode(
        connection,
        "shale",
        "crisis",
        coverage_passed=False,
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("policy_rate", "as_published"),),
    )
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="2.0.0",
        created_at="2026-06-01T00:00:00+00:00",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("policy_rate", "as_published"),),
    )
    _seed_episode(
        connection,
        "infra",
        "benign",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("policy_rate", "as_published"),),
    )
    payloads = _report_payloads(connection, tmp_path)
    accepted_in_readiness = {
        entry["episode_id"]
        for entry in payloads["readiness.json"]["episode_runs_considered"]
        if entry["accepted"]
    }
    accepted_in_coverage = {
        episode_id
        for episode_id, entry in payloads["coverage.json"]["episodes"].items()
        if entry.get("coverage_passed") and entry.get("leakage_passed")
    }
    assert accepted_in_readiness == accepted_in_coverage == {"shale", "infra"}


def test_a_superseded_latest_revision_control_is_not_reported_as_selected(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """The upgrade that the vintage report must actually show.

    The first run froze a latest-revision series. Vintage acquisition ran, the rebuild
    froze a point-in-time series, and the old row is still sitting in the shared control
    store. A vintage report built from that store reports the superseded row as though
    the gate had judged it -- which reads as a blocker that no longer exists, and would
    equally hide a blocker that does.
    """
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="1.0.0",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("unemployment_rate", "latest_revision"),),
    )
    _seed_episode(
        connection,
        "shale",
        "crisis",
        version="2.0.0",
        created_at="2026-06-01T00:00:00+00:00",
        feature_keys=_ALL_ROLE_FEATURES,
        control_series=(("unemployment_rate", "point_in_time:2016-01-31"),),
    )
    payloads = _report_payloads(connection, tmp_path)
    vintage = payloads["revision_and_vintage.json"]
    status = payloads["readiness.json"]

    assert vintage["reported_run_ids"] == ["run-shale-2.0.0"]
    selected = {
        (series_id, entry["vintage"])
        for series_id, entries in vintage["control_series"].items()
        for entry in entries
    }
    assert selected == {("unemployment_rate", "point_in_time:2016-01-31")}
    assert status["revised_only_control_series"] == []

    # the superseded row is not lost -- it is reported as store inventory, labelled as
    # such, and kept out of the selection the gate judged
    inventory = {
        (series_id, entry["vintage"])
        for series_id, entries in vintage["store_inventory"].items()
        for entry in entries
    }
    assert ("unemployment_rate", "latest_revision") in inventory


def test_episode_comparison_does_not_fall_back_to_a_superseded_build(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    """A failed rebuild with no linked build must leave the episode with no comparison.

    The older run's immutable build is still there and still queryable, and
    `episode_series` without a run filter takes the latest run that *has* a build -- so
    it walks straight past the failed rebuild and reads the one it replaced. The gate
    would report the episode rejected while the comparison quietly described its
    superseded feature levels.
    """
    # the episode id must be one the comparison enumerates, or nothing is being tested
    episode_id = "shale-financing"
    features = next(roster.features for roster in ROSTERS if roster.episode_id == episode_id)
    _seed_episode(
        connection,
        episode_id,
        "crisis",
        version="1.0.0",
        feature_keys=tuple(features),
        control_series=(("policy_rate", "as_published"),),
    )
    _seed_episode(
        connection,
        episode_id,
        "crisis",
        version="2.0.0",
        created_at="2026-06-01T00:00:00+00:00",
        coverage_passed=False,
        leakage_passed=False,
        feature_keys=(),  # the rebuild failed before finalizing a build
        control_series=(("policy_rate", "as_published"),),
    )
    old_run = f"run-{episode_id}-1.0.0"
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM backfill_build_link WHERE run_id=?", (old_run,)
        ).fetchone()[0]
        == 1
    ), "the superseded build must still exist, or nothing is proven"

    payloads = _report_payloads(connection, tmp_path)
    assert payloads["readiness.json"]["reported_run_ids"] == [f"run-{episode_id}-2.0.0"]
    episode = payloads["episode_comparison.json"]["episodes"][episode_id]
    assert episode["features"] == {}, (
        "the failed rebuild linked no build, so the comparison must report nothing for "
        "this episode rather than reading the build it replaced"
    )
    assert episode["stratum"] is None
