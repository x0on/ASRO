"""Vintage-correct entity fundamentals from SEC XBRL companyfacts.

Every fact in `companyfacts` carries the accession and the date it was filed, and a
restated figure appears as a separate entry with a later filing date. Selecting the
*earliest* fact for a period whose filing date is at or before an episode's availability
cutoff therefore reconstructs the number as it was originally reported and as it was
knowable at the time. That is genuine vintage correctness, and it is what makes the
entity layer safe for a backtest even though the macro control layer is not.

Duration facts are classified by their own length rather than by the label on the filing,
because a year-to-date figure and a quarterly figure share a tag and confusing them
silently corrupts every flow measurement.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import requests

from asro.evidence import (
    CanonicalFactAssignment,
    EconomicScope,
    EvidenceRepository,
    FactStatus,
    FeatureDefinitionV2,
    ObservationV2,
    SourceTier,
)
from asro.models import EventType, FinancialEvent, SourceItem
from asro.scoring import score
from asro.storage import SqliteRepository

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
EXTRACTOR_NAME = "sec-companyfacts-benchmark"
EXTRACTOR_VERSION = "1.0.0"
FEATURE_VERSION = "1.0.0"
#: Immutable release time for this feature-set version; never "now".
FEATURE_RELEASED_AT = datetime(2026, 1, 1, tzinfo=UTC)

PeriodKind = Literal["instant", "quarterly", "annual"]

_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100


@dataclass(frozen=True)
class EntityPlan:
    entity_id: str
    cik: int


@dataclass(frozen=True)
class TagGroup:
    """One way of expressing a measurement.

    `partial` marks a group whose tags are components that a filer may report in any
    combination, so summing whatever is present is correct. It is false for a group whose
    tags must all be present to be meaningful, such as a current-plus-noncurrent split
    where a missing half would silently understate the total.
    """

    tags: tuple[str, ...]
    partial: bool = False


@dataclass(frozen=True)
class ConceptSpec:
    """A measurement and the XBRL tags that can supply it.

    `tag_groups` is a preference list. Each group is tried in order; the tags inside one
    group are summed and every one of them must be present for that period, which is how
    a current-plus-noncurrent split is reassembled without ever double counting a total
    against its own components.
    """

    feature_key: str
    tag_groups: tuple[TagGroup, ...]
    kind: PeriodKind
    unit: str
    currency: str | None = "USD"
    xbrl_unit: str = "USD"
    catalog_variable: bool = True

    @property
    def tags(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(tag for group in self.tag_groups for tag in group.tags))


@dataclass(frozen=True)
class RatioSpec:
    """A measurement derived from two base measurements."""

    feature_key: str
    numerator: str
    denominator: str
    unit: str
    numerator_trailing_quarters: int = 1
    denominator_trailing_quarters: int = 1
    scale: float = 1.0
    catalog_variable: bool = True


#: Base measurements. Helper keys (marked `catalog_variable=False`) exist so ratios have
#: audited inputs; they never count toward causal-role coverage on their own.
def _g(*tags: str, partial: bool = False) -> TagGroup:
    return TagGroup(tags=tags, partial=partial)


CONCEPTS: tuple[ConceptSpec, ...] = (
    ConceptSpec(
        "capital_expenditure",
        (
            # The filer's own total line comes first; component sums are a fallback for
            # exploration-and-production filers who publish no single capex total.
            _g("PaymentsToAcquireProductiveAssets"),
            _g("PaymentsToAcquirePropertyPlantAndEquipment"),
            _g("PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"),
            _g(
                "PaymentsToExploreAndDevelopOilAndGasProperties",
                "PaymentsToAcquireOilAndGasPropertyAndEquipment",
                "PaymentsToAcquireOtherPropertyPlantAndEquipment",
                partial=True,
            ),
        ),
        "quarterly",
        "currency",
    ),
    ConceptSpec(
        "productive_capacity_stock",
        (
            _g("PropertyPlantAndEquipmentGross"),
            _g("OilAndGasPropertySuccessfulEffortMethodGross"),
            _g("OilAndGasPropertyFullCostMethodGross"),
        ),
        "instant",
        "currency",
    ),
    ConceptSpec(
        "external_revenue",
        (
            _g("Revenues"),
            _g("SalesRevenueNet"),
            _g("RevenueFromContractWithCustomerExcludingAssessedTax"),
            _g("RevenueFromContractWithCustomerIncludingAssessedTax"),
        ),
        "quarterly",
        "currency",
    ),
    ConceptSpec(
        "external_cash_generation",
        (
            _g("NetCashProvidedByUsedInOperatingActivities"),
            _g("NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
        ),
        "quarterly",
        "currency",
    ),
    ConceptSpec(
        "equity_financed_investment",
        (_g("ProceedsFromIssuanceOfCommonStock"),),
        "quarterly",
        "currency",
    ),
    ConceptSpec(
        "impairment",
        (
            _g("AssetImpairmentCharges"),
            _g("ImpairmentOfOilAndGasProperties"),
            _g("GoodwillImpairmentLoss"),
            _g("ImpairmentOfLongLivedAssetsHeldAndUsed"),
        ),
        "quarterly",
        "currency",
    ),
    ConceptSpec(
        "lease_and_guarantee_commitments",
        (
            _g("OperatingLeaseLiability"),
            _g("OperatingLeaseLiabilityCurrent", "OperatingLeaseLiabilityNoncurrent"),
        ),
        "instant",
        "currency",
    ),
    ConceptSpec(
        "collateral_residual_value_assumption",
        (_g("PropertyPlantAndEquipmentUsefulLife"),),
        "instant",
        "years",
        currency=None,
        xbrl_unit="Y",
    ),
    ConceptSpec(
        "guarantees_to_equity_exposure",
        (_g("GuaranteeObligationsMaximumExposure"),),
        "instant",
        "currency",
        catalog_variable=False,
    ),
    # helpers: audited inputs for ratios; never counted toward causal-role coverage
    ConceptSpec("total_assets", (_g("Assets"),), "instant", "currency", catalog_variable=False),
    ConceptSpec(
        "total_debt",
        (
            _g("LongTermDebt"),
            _g("LongTermDebtCurrent", "LongTermDebtNoncurrent"),
            _g("LongTermDebtAndCapitalLeaseObligations"),
            _g("DebtLongtermAndShorttermCombinedAmount"),
            _g("LongTermDebtNoncurrent"),
        ),
        "instant",
        "currency",
        catalog_variable=False,
    ),
    ConceptSpec(
        "stockholders_equity",
        (_g("StockholdersEquity"),),
        "instant",
        "currency",
        catalog_variable=False,
    ),
    ConceptSpec(
        "operating_income",
        (_g("OperatingIncomeLoss"),),
        "quarterly",
        "currency",
        catalog_variable=False,
    ),
    ConceptSpec(
        "interest_expense",
        (
            _g("InterestExpense"),
            _g("InterestExpenseDebt"),
            _g("InterestExpenseNonoperating"),
        ),
        "quarterly",
        "currency",
        catalog_variable=False,
    ),
    ConceptSpec(
        "liquid_resources",
        (
            _g("CashCashEquivalentsAndShortTermInvestments"),
            _g("CashAndCashEquivalentsAtCarryingValue"),
        ),
        "instant",
        "currency",
        catalog_variable=False,
    ),
    ConceptSpec(
        "debt_repayment",
        (
            _g("RepaymentsOfLongTermDebt"),
            _g("RepaymentsOfDebt"),
        ),
        "quarterly",
        "currency",
        catalog_variable=False,
    ),
    ConceptSpec(
        "debt_issuance",
        (
            _g("ProceedsFromIssuanceOfLongTermDebt"),
            _g("ProceedsFromIssuanceOfDebt"),
        ),
        "quarterly",
        "currency",
        catalog_variable=False,
    ),
)

#: Derived measurements. Stocks are compared against a trailing four-quarter flow so a
#: balance-sheet number is never divided by a single quarter's cash flow.
RATIOS: tuple[RatioSpec, ...] = (
    RatioSpec("capex_to_revenue", "capital_expenditure", "external_revenue", "ratio"),
    RatioSpec(
        "free_cash_flow_margin",
        "free_cash_flow",
        "external_revenue",
        "ratio",
        numerator_trailing_quarters=4,
        denominator_trailing_quarters=4,
    ),
    RatioSpec("debt_to_assets", "total_debt", "total_assets", "ratio"),
    RatioSpec(
        "debt_to_operating_cash_flow",
        "total_debt",
        "external_cash_generation",
        "ratio",
        denominator_trailing_quarters=4,
    ),
    RatioSpec(
        "interest_coverage",
        "operating_income",
        "interest_expense",
        "ratio",
        numerator_trailing_quarters=4,
        denominator_trailing_quarters=4,
    ),
    RatioSpec(
        "margin_improvement",
        "operating_income",
        "external_revenue",
        "percent",
        numerator_trailing_quarters=4,
        denominator_trailing_quarters=4,
        scale=100.0,
    ),
    RatioSpec(
        "fixed_obligations_to_external_cash",
        "total_fixed_obligations",
        "external_cash_generation",
        "ratio",
        denominator_trailing_quarters=4,
    ),
    RatioSpec(
        "guarantees_to_equity", "guarantees_to_equity_exposure", "stockholders_equity", "ratio"
    ),
    RatioSpec(
        "resilient_liquidity_runway", "liquid_resources", "monthly_committed_spending", "months"
    ),
    RatioSpec(
        "debt_financed_investment_share", "net_debt_issuance", "capital_expenditure", "ratio"
    ),
)

#: Derived flows assembled from base measurements before ratios are computed.
COMPOSITES: dict[str, tuple[str, ...]] = {
    "free_cash_flow": ("external_cash_generation", "capital_expenditure"),
    "total_fixed_obligations": ("total_debt", "lease_and_guarantee_commitments"),
    "net_debt_issuance": ("debt_issuance", "debt_repayment"),
    "monthly_committed_spending": ("capital_expenditure",),
    "deleveraging": ("debt_repayment", "debt_issuance"),
}

CATALOG_COMPOSITES = {"free_cash_flow", "deleveraging"}


@dataclass(frozen=True)
class Fact:
    """One selected XBRL fact, with the provenance that makes it auditable."""

    feature_key: str
    entity_id: str
    period_start: date
    period_end: date
    value: float
    tag: str
    accession: str
    form: str
    filed: date
    frame: str | None
    unit: str
    currency: str | None
    fact_status: FactStatus
    derivation_method: str | None = None
    derivation_inputs: tuple[str, ...] = ()


def fetch_companyfacts(
    plan: EntityPlan,
    *,
    user_agent: str,
    cache_dir: Path,
    session: requests.Session | None = None,
) -> tuple[dict[str, Any], str, str]:
    """Return companyfacts with its content hash and retrieval time, caching by CIK."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"companyfacts-{plan.cik:010d}.json"
    if path.exists():
        content = path.read_bytes()
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    else:
        client = session or requests.Session()
        response = client.get(
            COMPANYFACTS_URL.format(cik=plan.cik),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=120,
        )
        response.raise_for_status()
        content = response.content
        if not content:
            raise ValueError(f"empty companyfacts response for {plan.entity_id}")
        path.write_bytes(content)
        fetched_at = datetime.now(UTC)
    return (
        json.loads(content),
        hashlib.sha256(content).hexdigest(),
        fetched_at.replace(microsecond=0).isoformat(),
    )


def _classify(entry: Mapping[str, Any]) -> PeriodKind | None:
    start_text = entry.get("start")
    if start_text is None:
        return "instant"
    span = (date.fromisoformat(str(entry["end"])) - date.fromisoformat(str(start_text))).days
    if _QUARTER_MIN_DAYS <= span <= _QUARTER_MAX_DAYS:
        return "quarterly"
    if 350 <= span <= 380:
        return "annual"
    return None  # year-to-date and other spans are never treated as a period flow


@dataclass(frozen=True)
class _Point:
    value: float
    filed: date
    accession: str
    form: str
    frame: str | None
    derivation: str | None


def _earliest(entries: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    return min(entries, key=lambda item: (str(item["filed"]), str(item.get("accn", ""))))


def _tag_points(
    entries: Sequence[Mapping[str, Any]],
    kind: PeriodKind,
    *,
    availability_cutoff: date,
    rejected: list[dict[str, object]],
    context: dict[str, str],
) -> dict[tuple[date, date], _Point]:
    """Reduce one tag's entries to one as-originally-reported point per period.

    Quarterly flows are taken directly where the filer tagged a quarter, and otherwise
    reconstructed by differencing two year-to-date figures that share a start date. A
    year-to-date figure is never used as if it were a quarter.
    """
    raw: dict[tuple[date, date], list[Mapping[str, Any]]] = {}
    for entry in entries:
        filed = date.fromisoformat(str(entry["filed"]))
        end = date.fromisoformat(str(entry["end"]))
        if filed > availability_cutoff:
            rejected.append(
                {
                    **context,
                    "period_end": end.isoformat(),
                    "filed": filed.isoformat(),
                    "reason": "filed after the episode availability cutoff",
                }
            )
            continue
        start_text = entry.get("start")
        if kind == "instant":
            if start_text is not None:
                continue
            raw.setdefault((end, end), []).append(entry)
        else:
            if start_text is None:
                continue
            raw.setdefault((date.fromisoformat(str(start_text)), end), []).append(entry)

    points: dict[tuple[date, date], _Point] = {}
    for period, candidates in raw.items():
        first = _earliest(candidates)
        for later in candidates:
            if float(later["val"]) != float(first["val"]):
                rejected.append(
                    {
                        **context,
                        "period_end": period[1].isoformat(),
                        "reason": "later restatement not used; original filing retained",
                        "original_value": float(first["val"]),
                        "restated_value": float(later["val"]),
                        "original_filed": str(first["filed"]),
                        "restated_filed": str(later["filed"]),
                    }
                )
        points[period] = _Point(
            value=float(first["val"]),
            filed=date.fromisoformat(str(first["filed"])),
            accession=str(first.get("accn", "")),
            form=str(first.get("form", "")),
            frame=str(first["frame"]) if first.get("frame") else None,
            derivation=None,
        )
    if kind == "instant":
        return points

    quarterly: dict[tuple[date, date], _Point] = {}
    for (span_start, span_end), point in points.items():
        if _QUARTER_MIN_DAYS <= (span_end - span_start).days <= _QUARTER_MAX_DAYS:
            quarterly[(span_start, span_end)] = point
    by_start: dict[date, list[tuple[date, _Point]]] = {}
    for (span_start, span_end), point in points.items():
        by_start.setdefault(span_start, []).append((span_end, point))
    for span_start, ends in by_start.items():
        ends.sort()
        for index in range(1, len(ends)):
            previous_end, previous_point = ends[index - 1]
            current_end, current_point = ends[index]
            gap = (current_end - previous_end).days
            if not (_QUARTER_MIN_DAYS <= gap <= _QUARTER_MAX_DAYS):
                continue
            period = (previous_end + timedelta(days=1), current_end)
            if period in quarterly:
                continue
            quarterly[period] = _Point(
                value=current_point.value - previous_point.value,
                filed=max(current_point.filed, previous_point.filed),
                accession=current_point.accession,
                form=current_point.form,
                frame=current_point.frame,
                derivation=(
                    "quarter_differenced_from_year_to_date_"
                    f"{span_start.isoformat()}_{previous_end.isoformat()}_"
                    f"to_{current_end.isoformat()}"
                ),
            )
    return quarterly


def select_facts(
    spec: ConceptSpec,
    entity_id: str,
    companyfacts: Mapping[str, Any],
    *,
    availability_cutoff: date,
    period_start: date,
    period_end: date,
    scoring_start: date | None = None,
) -> tuple[list[Fact], list[dict[str, object]]]:
    """Pick the as-originally-reported measurement for each period in the window.

    `period_start` is how far back to read, which extends before the episode so trailing
    ratios can be complete at its first month. `scoring_start` is the episode's own start:
    tag groups are ranked on how well they cover the episode, never on how well they cover
    the lookback, so a group that is rich in old history but thin during the window cannot
    win.
    """
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    rejected: list[dict[str, object]] = []
    per_tag: dict[str, dict[tuple[date, date], _Point]] = {}
    for tag in spec.tags:
        entries = gaap.get(tag, {}).get("units", {}).get(spec.xbrl_unit, [])
        if not entries:
            continue
        per_tag[tag] = _tag_points(
            entries,
            spec.kind,
            availability_cutoff=availability_cutoff,
            rejected=rejected,
            context={"entity": entity_id, "feature": spec.feature_key, "tag": tag},
        )

    def periods_for(group: TagGroup) -> dict[tuple[date, date], list[str]]:
        available = [tag for tag in group.tags if tag in per_tag]
        if not available or (not group.partial and len(available) != len(group.tags)):
            return {}
        if group.partial:
            union: set[tuple[date, date]] = set()
            for tag in available:
                union |= set(per_tag[tag])
            candidates = union
        else:
            candidates = set(per_tag[available[0]])
            for tag in available[1:]:
                candidates &= set(per_tag[tag])
        return {
            period: [tag for tag in available if period in per_tag[tag]]
            for period in candidates
            if period_start <= period[1] <= period_end
        }

    rank_from = scoring_start or period_start

    # One tag group is chosen for the whole entity window. Switching definitions between
    # periods would produce a series that changes meaning partway through, which is worse
    # than a shorter series with one meaning.
    scored: list[tuple[int, int, TagGroup, dict[tuple[date, date], list[str]]]] = []
    for index, group in enumerate(spec.tag_groups):
        periods = periods_for(group)
        if periods:
            in_window = sum(1 for period in periods if period[1] >= rank_from)
            scored.append((in_window, -index, group, periods))
    chosen: dict[date, Fact] = {}
    if not scored:
        return [], rejected
    _, _, group, periods = max(scored, key=lambda item: (item[0], item[1]))
    for period in sorted(periods):
        span_start, span_end = period
        contributing = periods[period]
        points = [per_tag[tag][period] for tag in contributing]
        summed = len(contributing) > 1
        derivations = [item.derivation for item in points if item.derivation]
        method: str | None = None
        inputs: tuple[str, ...] = ()
        if summed:
            method = "sum_of_" + "_and_".join(contributing)
            inputs = tuple(f"us-gaap:{tag}" for tag in contributing)
        if derivations:
            method = "; ".join(filter(None, [method, *derivations]))
            inputs = inputs or tuple(f"us-gaap:{tag}" for tag in contributing)
        chosen[span_end] = Fact(
            feature_key=spec.feature_key,
            entity_id=entity_id,
            period_start=span_start,
            period_end=span_end,
            value=sum(item.value for item in points),
            tag="+".join(contributing),
            accession=points[0].accession,
            form=points[0].form,
            filed=max(item.filed for item in points),
            frame=points[0].frame,
            unit=spec.unit,
            currency=spec.currency,
            fact_status=FactStatus.INFERRED if method else FactStatus.DIRECT,
            derivation_method=method,
            derivation_inputs=inputs,
        )
    return [chosen[key] for key in sorted(chosen)], rejected


def _trailing_sum(
    series: Mapping[date, Fact], period_end: date, quarters: int
) -> tuple[float, list[Fact]] | None:
    """Sum the trailing N quarterly facts ending at `period_end`, or nothing if incomplete."""
    ordered = sorted(series)
    if period_end not in series:
        return None
    index = ordered.index(period_end)
    if index + 1 < quarters:
        return None
    window = [series[key] for key in ordered[index + 1 - quarters : index + 1]]
    span = (window[-1].period_end - window[0].period_start).days
    if quarters > 1 and not (quarters * 80 <= span <= quarters * 100):
        return None  # a gap in the quarterly series must not be summed as if continuous
    return sum(item.value for item in window), window


def build_derived_facts(
    entity_id: str, base: Mapping[str, list[Fact]]
) -> tuple[list[Fact], list[dict[str, object]]]:
    """Assemble composites and ratios from the selected base facts."""
    indexed: dict[str, dict[date, Fact]] = {
        key: {item.period_end: item for item in values} for key, values in base.items()
    }
    derived: list[Fact] = []
    notes: list[dict[str, object]] = []

    for key, inputs in COMPOSITES.items():
        if any(name not in indexed for name in inputs):
            notes.append(
                {
                    "entity": entity_id,
                    "feature": key,
                    "reason": "missing input measurement",
                    "inputs": list(inputs),
                }
            )
            continue
        periods = set(indexed[inputs[0]])
        for name in inputs[1:]:
            periods &= set(indexed[name])
        produced: dict[date, Fact] = {}
        for end in sorted(periods):
            parts = [indexed[name][end] for name in inputs]
            if key == "free_cash_flow":
                value = parts[0].value - parts[1].value
                method = "operating_cash_flow_less_capital_expenditure"
            elif key == "net_debt_issuance":
                value = parts[0].value - parts[1].value
                method = "debt_issuance_less_repayment"
            elif key == "deleveraging":
                value = parts[0].value - parts[1].value
                method = "debt_repayment_less_issuance"
            elif key == "monthly_committed_spending":
                value = parts[0].value / 3.0
                method = "quarterly_capital_expenditure_divided_by_three"
            else:
                value = sum(item.value for item in parts)
                method = "sum_of_" + "_and_".join(inputs)
            if key == "monthly_committed_spending" and value <= 0:
                continue
            produced[end] = Fact(
                feature_key=key,
                entity_id=entity_id,
                period_start=parts[0].period_start,
                period_end=end,
                value=value,
                tag="+".join(sorted({item.tag for item in parts})),
                accession=parts[0].accession,
                form=parts[0].form,
                filed=max(item.filed for item in parts),
                frame=parts[0].frame,
                unit=parts[0].unit,
                currency=parts[0].currency,
                fact_status=FactStatus.INFERRED,
                derivation_method=method,
                derivation_inputs=tuple(sorted(inputs)),
            )
        indexed[key] = produced
        if key in CATALOG_COMPOSITES:
            derived.extend(produced.values())

    for ratio in RATIOS:
        numerator = indexed.get(ratio.numerator)
        denominator = indexed.get(ratio.denominator)
        if not numerator or not denominator:
            notes.append(
                {
                    "entity": entity_id,
                    "feature": ratio.feature_key,
                    "reason": "missing numerator or denominator measurement",
                }
            )
            continue
        for end in sorted(set(numerator) & set(denominator)):
            top = _trailing_sum(numerator, end, ratio.numerator_trailing_quarters)
            bottom = _trailing_sum(denominator, end, ratio.denominator_trailing_quarters)
            if top is None or bottom is None:
                continue
            top_value, top_window = top
            bottom_value, bottom_window = bottom
            if bottom_value == 0:
                notes.append(
                    {
                        "entity": entity_id,
                        "feature": ratio.feature_key,
                        "period_end": end.isoformat(),
                        "reason": "denominator is zero; value left unknown",
                    }
                )
                continue
            source = numerator[end]
            derived.append(
                Fact(
                    feature_key=ratio.feature_key,
                    entity_id=entity_id,
                    period_start=min(top_window[0].period_start, bottom_window[0].period_start),
                    period_end=end,
                    value=(top_value / bottom_value) * ratio.scale,
                    tag=f"{ratio.numerator}/{ratio.denominator}",
                    accession=source.accession,
                    form=source.form,
                    filed=max(
                        max(item.filed for item in top_window),
                        max(item.filed for item in bottom_window),
                    ),
                    frame=source.frame,
                    unit=ratio.unit,
                    currency=None,
                    fact_status=FactStatus.INFERRED,
                    derivation_method=(
                        f"{ratio.numerator}"
                        f"[{ratio.numerator_trailing_quarters}q]/"
                        f"{ratio.denominator}[{ratio.denominator_trailing_quarters}q]"
                    ),
                    derivation_inputs=tuple(sorted({ratio.numerator, ratio.denominator})),
                )
            )
    return derived, notes


def register_feature_definitions(
    connection: sqlite3.Connection, feature_keys: Sequence[str], *, max_age_months: int
) -> None:
    """Register immutable semantics for each benchmark feature before any build reads it."""
    # Fixed so a feature version has one immutable release time across every rebuild.
    released = FEATURE_RELEASED_AT
    for key in sorted(set(feature_keys)):
        unit = _unit_for(key)
        definition = FeatureDefinitionV2(
            feature_key=key,
            feature_version=FEATURE_VERSION,
            definition_json=json.dumps(
                {
                    "aggregation": "as_of_latest",
                    "unit": unit,
                    "expected_facts_per_period": 1,
                    "max_age_months": max_age_months,
                    "source": "sec-xbrl-companyfacts",
                    "vintage_rule": "earliest filing at or before the availability cutoff",
                },
                sort_keys=True,
            ),
            released_at=released,
        )
        EvidenceRepository.register_feature(connection, definition)
    connection.commit()


def _unit_for(feature_key: str) -> str:
    for spec in CONCEPTS:
        if spec.feature_key == feature_key:
            return spec.unit
    for ratio in RATIOS:
        if ratio.feature_key == feature_key:
            return ratio.unit
    if feature_key in {"free_cash_flow", "deleveraging"}:
        return "currency"
    raise KeyError(f"no unit registered for {feature_key}")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


_EVENT_TYPES: dict[str, EventType] = {
    "capital_expenditure": EventType.CAPEX_COMMITMENT,
    "external_revenue": EventType.REVENUE_REPORT,
    "external_cash_generation": EventType.FREE_CASH_FLOW,
    "free_cash_flow": EventType.FREE_CASH_FLOW,
    "free_cash_flow_margin": EventType.FREE_CASH_FLOW,
    "impairment": EventType.IMPAIRMENT,
}


def _event_type(feature_key: str) -> EventType:
    return _EVENT_TYPES.get(feature_key, EventType.BALANCE_SHEET_REPORT)


def _filing_url(cik: int, accession: str) -> str:
    plain = accession.replace("-", "")
    if not plain:
        return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}"
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{accession}-index.htm"


def _persist_filing(
    connection: sqlite3.Connection,
    repository: SqliteRepository,
    plan: EntityPlan,
    *,
    accession: str,
    form: str,
    filed: date,
    content_sha256: str,
    fetched_at: str,
) -> str:
    """Register the SEC filing that reported a fact as that fact's source document.

    The companyfacts payload is a retrieval mechanism, not the source. Attributing an
    observation to the filing keeps the document's own availability equal to its filing
    date, which is what makes source coverage mean "an investor could have read this".
    """
    url = _filing_url(plan.cik, accession)
    item = score(
        SourceItem.model_validate(
            {
                "title": (
                    f"{plan.entity_id} {form or 'filing'} {accession or 'unknown accession'} "
                    f"filed {filed.isoformat()}"
                ),
                "url": url,
                "source": "SEC EDGAR filing",
                "summary": (
                    "Structured XBRL facts as filed; historical benchmark context, not "
                    "AI-attributed."
                ),
                "published_at": filed.isoformat(),
                "discovered_at": filed.isoformat(),
            }
        ),
        [plan.entity_id],
    )
    repository.insert(connection, item)
    existing = connection.execute(
        "SELECT text FROM documents WHERE item_id=?", (item.item_id,)
    ).fetchone()
    if existing is None:
        repository.upsert_document(
            connection,
            item.item_id,
            filed.isoformat(),
            "application/json",
            "fetched",
            (
                f"XBRL facts for {plan.entity_id} from accession {accession or 'unknown'} "
                f"({form or 'unknown form'}) filed {filed.isoformat()}; read from the SEC "
                f"companyfacts payload sha256={content_sha256} retrieved {fetched_at}."
            ),
        )
    return item.item_id


def _write_fact(
    connection: sqlite3.Connection,
    repository: SqliteRepository,
    fact: Fact,
    *,
    document_id: str,
    plan: EntityPlan,
    content_sha256: str,
    review_time: str,
) -> str:
    """Write one fact as an event, a canonical fact and an append-only observation."""
    identity = (fact.entity_id, fact.feature_key, fact.period_end.isoformat())
    event_id = _stable_id("benchmark-event", *identity)
    observation_id = _stable_id("benchmark-observation", *identity)
    canonical_fact_id = _stable_id("benchmark-fact", *identity)
    assignment_id = _stable_id("benchmark-assignment", *identity)
    value_text = f"{fact.value:,.4f}".rstrip("0").rstrip(".")
    evidence = (
        f"SEC XBRL companyfacts reports {fact.tag} of {value_text} {fact.unit} for "
        f"{fact.entity_id} covering {fact.period_start.isoformat()} to "
        f"{fact.period_end.isoformat()}, from accession {fact.accession or 'unknown'} "
        f"({fact.form or 'unknown form'}) filed {fact.filed.isoformat()}."
    )
    locator = (
        f"CIK {plan.cik:010d}; us-gaap:{fact.tag}; "
        f"period={fact.period_start.isoformat()}..{fact.period_end.isoformat()}; "
        f"accession={fact.accession or 'unknown'}; frame={fact.frame or 'unframed'}; "
        f"companyfacts_sha256={content_sha256}"
    )
    filed_at = datetime(fact.filed.year, fact.filed.month, fact.filed.day, tzinfo=UTC)
    repository.insert_event(
        connection,
        FinancialEvent.model_validate(
            {
                "event_id": event_id,
                "document_id": document_id,
                "event_type": _event_type(fact.feature_key),
                "source_entity": fact.entity_id,
                "amount": fact.value if fact.currency else None,
                "currency": fact.currency,
                "instrument": f"reported fundamental: {fact.feature_key}",
                "effective_date": fact.period_end.isoformat(),
                "confidence": 1.0,
                "evidence_text": evidence,
                "extractor": f"{EXTRACTOR_NAME}-{EXTRACTOR_VERSION}",
                "processed_at": review_time,
            }
        ),
    )
    cursor = connection.execute(
        """INSERT INTO evidence_reviews
           (fingerprint, decision, canonical_fingerprint, confidence, reasoning, model, reviewed_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            event_id,
            # 'confirm' is the decision the backfill runner recognises as reviewed.
            "confirm",
            event_id,
            1.0,
            (
                "Deterministic selection of the earliest XBRL filing at or before the "
                "episode availability cutoff; period length validated against the tag's "
                "own start and end dates."
            ),
            f"{EXTRACTOR_NAME}-{EXTRACTOR_VERSION}",
            review_time,
        ),
    )
    review_id = int(cursor.lastrowid or 0)
    EvidenceRepository.register_canonical_fact(connection, canonical_fact_id)
    EvidenceRepository.assign_canonical_fact(
        connection,
        CanonicalFactAssignment.model_validate(
            {
                "assignment_id": assignment_id,
                "event_id": event_id,
                "canonical_fact_id": canonical_fact_id,
                "available_at": filed_at.isoformat(),
                "reviewer_id": review_id,
                "assigned_by": EXTRACTOR_NAME,
                "assignment_method": "xbrl-tag-period-and-filing-identity",
                "provenance": {
                    "cik": f"{plan.cik:010d}",
                    "tag": fact.tag,
                    "accession": fact.accession,
                    "form": fact.form,
                    "frame": fact.frame,
                    "filed": fact.filed.isoformat(),
                    "companyfacts_sha256": content_sha256,
                    "vintage_rule": "earliest filing at or before the availability cutoff",
                },
                "created_at": review_time,
            }
        ),
    )
    EvidenceRepository.insert(
        connection,
        ObservationV2.model_validate(
            {
                "observation_id": observation_id,
                "event_id": event_id,
                "source_document_id": document_id,
                "source_locator": locator,
                "evidence_text": evidence,
                "entity_id": fact.entity_id,
                "entity_role": "reporting_company",
                "feature_key": fact.feature_key,
                "feature_version": FEATURE_VERSION,
                "value_numeric": fact.value,
                "unit": fact.unit,
                "currency": fact.currency,
                "economic_scope": EconomicScope.ENTITY,
                "period_start": fact.period_start.isoformat(),
                "period_end": fact.period_end.isoformat(),
                "event_at": fact.period_end.isoformat(),
                "published_at": filed_at.isoformat(),
                "availability_at": filed_at.isoformat(),
                "extracted_at": review_time,
                "fact_status": fact.fact_status,
                "source_tier": SourceTier.PRIMARY,
                "source_quality": 1.0,
                "extraction_confidence": 1.0,
                "review_confidence": 1.0,
                "extractor_name": EXTRACTOR_NAME,
                "extractor_version": EXTRACTOR_VERSION,
                "review_id": review_id,
                "derivation_method": fact.derivation_method,
                "derivation_inputs": list(fact.derivation_inputs),
            }
        ),
    )
    return observation_id


def ingest_entity_fundamentals(
    connection: sqlite3.Connection,
    plan: EntityPlan,
    *,
    availability_cutoff: date,
    period_start: date,
    period_end: date,
    user_agent: str,
    cache_dir: Path,
    lookback_quarters: int = 12,
    session: requests.Session | None = None,
) -> dict[str, object]:
    """Acquire, select and promote one entity's fundamentals for one episode window.

    Facts are read from `lookback_quarters` before the episode so that the first months of
    the window can be covered by a filing that was genuinely available at the time,
    instead of being blank until the first in-window filing lands. Twelve quarters is the
    default because a trailing-twelve-month ratio needs four quarters already complete at
    the first month of the window, and a filer can lag by two more.
    """
    companyfacts, content_sha256, fetched_at = fetch_companyfacts(
        plan, user_agent=user_agent, cache_dir=cache_dir, session=session
    )
    read_from = period_start - timedelta(days=int(lookback_quarters * 92))
    base: dict[str, list[Fact]] = {}
    rejections: list[dict[str, object]] = []
    for spec in CONCEPTS:
        facts, rejected = select_facts(
            spec,
            plan.entity_id,
            companyfacts,
            availability_cutoff=availability_cutoff,
            period_start=read_from,
            period_end=period_end,
            scoring_start=period_start,
        )
        rejections.extend(rejected)
        if facts:
            base[spec.feature_key] = facts
    derived, notes = build_derived_facts(plan.entity_id, base)

    catalog_keys = (
        {spec.feature_key for spec in CONCEPTS if spec.catalog_variable}
        | {ratio.feature_key for ratio in RATIOS if ratio.catalog_variable}
        | CATALOG_COMPOSITES
    )
    promoted = [
        fact
        for fact in ([item for values in base.values() for item in values] + derived)
        if fact.feature_key in catalog_keys
    ]
    register_feature_definitions(
        connection, [fact.feature_key for fact in promoted], max_age_months=6
    )
    repository = SqliteRepository(Path("unused"))
    review_time = datetime.now(UTC).replace(microsecond=0).isoformat()
    written = 0
    documents: dict[tuple[str, str], str] = {}
    for fact in promoted:
        existing = connection.execute(
            "SELECT 1 FROM observation_v2 WHERE observation_id=?",
            (
                _stable_id(
                    "benchmark-observation",
                    fact.entity_id,
                    fact.feature_key,
                    fact.period_end.isoformat(),
                ),
            ),
        ).fetchone()
        if existing:
            continue
        document_key = (fact.accession, fact.form)
        if document_key not in documents:
            documents[document_key] = _persist_filing(
                connection,
                repository,
                plan,
                accession=fact.accession,
                form=fact.form,
                filed=fact.filed,
                content_sha256=content_sha256,
                fetched_at=fetched_at,
            )
        _write_fact(
            connection,
            repository,
            fact,
            document_id=documents[document_key],
            plan=plan,
            content_sha256=content_sha256,
            review_time=review_time,
        )
        written += 1
    connection.commit()
    by_feature: dict[str, int] = {}
    for fact in promoted:
        by_feature[fact.feature_key] = by_feature.get(fact.feature_key, 0) + 1
    return {
        "entity_id": plan.entity_id,
        "cik": f"{plan.cik:010d}",
        "companyfacts_sha256": content_sha256,
        "companyfacts_url": COMPANYFACTS_URL.format(cik=plan.cik),
        "fetched_at": fetched_at,
        "availability_cutoff": availability_cutoff.isoformat(),
        "read_from": read_from.isoformat(),
        "observations_written": written,
        "source_filings": len(documents),
        "facts_by_feature": dict(sorted(by_feature.items())),
        "restatements_and_late_filings_rejected": len(rejections),
        "rejection_sample": rejections[:25],
        "derivation_notes": notes,
    }
