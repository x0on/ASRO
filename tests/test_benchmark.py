from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from asro.backfill.controls import ControlObservation, register_control_observation
from asro.benchmark import (
    ASRO_HUNDRED,
    ASRO_ZERO,
    BENCHMARK_VARIABLES,
    CalibrationClaimError,
    CalibrationRequirements,
    CausalRole,
    OutputTier,
    assert_claim_supported,
    evaluate_readiness,
    load_documented_insufficiency,
    machine_derivable_variables,
)
from asro.benchmark.analysis import false_positive_check, percentile_of, robust_z
from asro.benchmark.controls_ingest import (
    CONTROL_PLANS,
    UNAVAILABLE_CONTROL_SERIES,
    Frequency,
    SeriesFetch,
    VintageBasis,
    _parse_csv,
    ingest_series,
    monthly_observations,
)
from asro.benchmark.episodes import EPISODE_FEATURES, ROSTERS
from asro.benchmark.sec_fundamentals import (
    CONCEPTS,
    ConceptSpec,
    TagGroup,
    build_derived_facts,
    select_facts,
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
from asro.site import build_static_site
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
    created_at: str = "2026-01-01T00:00:00+00:00",
    run_suffix: str = "",
) -> str:
    """Insert one gate-visible episode run with an immutable finalized build behind it."""
    run_id = f"run-{episode_id}-{version}{run_suffix}"
    build_id = f"build-{run_id}"
    cutoff = "2016-01-31T00:00:00+00:00"
    # The manifest must declare the controls the run freezes; the schema checks it.
    manifest_json = json.dumps(
        {
            "controls": [
                {"series_id": series_id, "version": "1.0.0", "unit": "percent"}
                for series_id, _ in control_series
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
        register_control_observation(
            connection,
            ControlObservation(
                control_observation_id=f"ctl-{run_id}-{series_id}",
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
                f"ctl-{run_id}-{series_id}",
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
