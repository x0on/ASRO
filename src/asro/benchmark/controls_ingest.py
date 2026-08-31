"""Control-series acquisition with explicit vintage honesty.

Historical measurements must not be read in isolation, so the benchmark needs macro and
market controls. It also must not pretend those controls are vintage-correct when they
are not.

Two vintage bases exist:

as_published
    The series is not materially revised after first publication (policy rates, market
    prices, spreads). The latest print equals what was knowable at the time.
latest_revision
    The series is revised, sometimes heavily, and the only version reachable here is
    today's. Using it in a backtest leaks information backwards. Series in this class are
    ingested and labelled, never silently treated as vintage-correct.

Genuine vintage access requires a FRED API key with `realtime_start`; the public CSV
endpoint ignores vintage suffixes and returns current data for any date, which is why
this module never attempts one.
"""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

import requests

from asro.backfill.controls import ControlObservation, register_control_observation

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
PUBLISHER = "Federal Reserve Bank of St. Louis (FRED)"


class VintageBasis(StrEnum):
    AS_PUBLISHED = "as_published"
    LATEST_REVISION = "latest_revision"


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True)
class ControlSeriesPlan:
    """One logical control series and the FRED series that supplies it."""

    series_id: str
    fred_id: str
    unit: str
    frequency: Frequency
    vintage_basis: VintageBasis
    description: str
    publication_lag_days: int
    proxy_for: str | None = None


CONTROL_PLANS: tuple[ControlSeriesPlan, ...] = (
    ControlSeriesPlan(
        "policy_rate",
        "FEDFUNDS",
        "percent",
        Frequency.MONTHLY,
        VintageBasis.AS_PUBLISHED,
        "Effective federal funds rate, monthly average.",
        1,
    ),
    ControlSeriesPlan(
        "ten_year_treasury",
        "DGS10",
        "percent",
        Frequency.DAILY,
        VintageBasis.AS_PUBLISHED,
        "10-year Treasury constant maturity yield.",
        1,
    ),
    ControlSeriesPlan(
        "mortgage_rate",
        "MORTGAGE30US",
        "percent",
        Frequency.WEEKLY,
        VintageBasis.AS_PUBLISHED,
        "30-year fixed mortgage average rate; paired with the 10-year for the "
        "housing-episode mortgage spread.",
        0,
    ),
    ControlSeriesPlan(
        "oil_price",
        "WTISPLC",
        "usd_per_barrel",
        Frequency.MONTHLY,
        VintageBasis.AS_PUBLISHED,
        "West Texas Intermediate spot price, monthly.",
        15,
    ),
    ControlSeriesPlan(
        "electricity_price",
        "APU000072610",
        "usd_per_kwh",
        Frequency.MONTHLY,
        VintageBasis.AS_PUBLISHED,
        "Average US residential electricity price per kilowatt hour.",
        15,
    ),
    ControlSeriesPlan(
        "bank_deposits",
        "DPSACBW027SBOG",
        "usd_billions",
        Frequency.WEEKLY,
        VintageBasis.LATEST_REVISION,
        "Deposits at all commercial banks; the regional-bank episode's funding series.",
        8,
    ),
    ControlSeriesPlan(
        "commercial_industrial_loans",
        "BUSLOANS",
        "usd_billions",
        Frequency.MONTHLY,
        VintageBasis.LATEST_REVISION,
        "Commercial and industrial loans at all commercial banks.",
        15,
    ),
    ControlSeriesPlan(
        "business_loan_delinquency",
        "DRBLACBS",
        "percent",
        Frequency.QUARTERLY,
        VintageBasis.LATEST_REVISION,
        "Delinquency rate on business loans at all commercial banks.",
        60,
        proxy_for="speculative_default_rate",
    ),
    ControlSeriesPlan(
        "charge_off_rate",
        "CORBLACBS",
        "percent",
        Frequency.QUARTERLY,
        VintageBasis.LATEST_REVISION,
        "Charge-off rate on business loans at all commercial banks.",
        60,
    ),
    ControlSeriesPlan(
        "unemployment_rate",
        "UNRATE",
        "percent",
        Frequency.MONTHLY,
        VintageBasis.LATEST_REVISION,
        "Civilian unemployment rate.",
        7,
    ),
    ControlSeriesPlan(
        "real_gdp",
        "GDPC1",
        "usd_billions_chained",
        Frequency.QUARTERLY,
        VintageBasis.LATEST_REVISION,
        "Real gross domestic product, chained dollars.",
        30,
    ),
    ControlSeriesPlan(
        "private_fixed_investment",
        "PNFI",
        "usd_billions",
        Frequency.QUARTERLY,
        VintageBasis.LATEST_REVISION,
        "Private nonresidential fixed investment.",
        30,
    ),
    ControlSeriesPlan(
        "real_personal_consumption",
        "PCEC96",
        "usd_billions_chained",
        Frequency.MONTHLY,
        VintageBasis.LATEST_REVISION,
        "Real personal consumption expenditures.",
        30,
    ),
)

CONTROL_PLANS_BY_ID: dict[str, ControlSeriesPlan] = {plan.series_id: plan for plan in CONTROL_PLANS}

#: Catalog control series with no freely reachable source from this environment.
UNAVAILABLE_CONTROL_SERIES: dict[str, str] = {
    "high_yield_spread": (
        "ICE BofA high-yield option-adjusted spread (BAMLH0A0HYM2) is licensed and the "
        "public CSV returns only a rolling three-year window, so history before 2023 is "
        "not reachable."
    ),
    "investment_grade_spread": (
        "ICE BofA investment-grade option-adjusted spread (BAMLC0A0CM) is truncated to a "
        "rolling three-year window on the public endpoint."
    ),
    "speculative_default_rate": (
        "Rating-agency speculative-grade default rates are not published on a freely "
        "accessible endpoint; business_loan_delinquency is ingested as a labelled proxy "
        "and is not a substitute."
    ),
    "equity_index_pe": ("Index-level earnings multiples require an index-provider licence."),
    "equity_index_concentration": (
        "Index constituent weights require an index-provider licence or N-PORT parsing "
        "that is out of scope for the control layer."
    ),
}


@dataclass(frozen=True)
class SeriesFetch:
    plan: ControlSeriesPlan
    source_url: str
    content_sha256: str
    fetched_at: str
    rows: tuple[tuple[date, float], ...]


def fetch_series(
    plan: ControlSeriesPlan,
    *,
    user_agent: str,
    session: requests.Session | None = None,
) -> SeriesFetch:
    """Download one series and record its content hash and retrieval time."""
    client = session or requests.Session()
    url = FRED_CSV.format(series=plan.fred_id)
    response = client.get(url, headers={"User-Agent": user_agent}, timeout=60)
    response.raise_for_status()
    content = response.content
    if not content:
        raise ValueError(f"empty control response for {plan.series_id}")
    rows = _parse_csv(content, plan.fred_id)
    if not rows:
        raise ValueError(f"no observations parsed for {plan.series_id}")
    return SeriesFetch(
        plan=plan,
        source_url=url,
        content_sha256=hashlib.sha256(content).hexdigest(),
        fetched_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        rows=rows,
    )


def _parse_csv(content: bytes, column: str) -> tuple[tuple[date, float], ...]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    field_names = reader.fieldnames or []
    value_column = column if column in field_names else None
    if value_column is None:
        candidates = [name for name in field_names if name not in {"observation_date", "DATE"}]
        if len(candidates) != 1:
            raise ValueError(f"cannot identify the value column among {field_names}")
        value_column = candidates[0]
    date_column = "observation_date" if "observation_date" in field_names else "DATE"
    parsed: list[tuple[date, float]] = []
    for row in reader:
        raw_value = (row.get(value_column) or "").strip()
        if not raw_value or raw_value == ".":
            continue  # FRED marks an unpublished observation with a dot; it stays missing
        parsed.append((date.fromisoformat(str(row[date_column])), float(raw_value)))
    return tuple(parsed)


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end_month = start.month % 12 + 1
    end_year = start.year + (1 if start.month == 12 else 0)
    end = date(end_year, end_month, 1)
    return start, date.fromordinal(end.toordinal() - 1)


def monthly_observations(fetch: SeriesFetch) -> tuple[tuple[date, date, float, int], ...]:
    """Collapse a series to calendar months, carrying the observation count per month.

    Daily and weekly series are averaged within the month. Quarterly series are attached
    to the month their period starts, and are not spread forward, so an absent month
    stays absent rather than being invented.
    """
    buckets: dict[tuple[date, date], list[float]] = {}
    for observed_on, value in fetch.rows:
        key = _month_bounds(observed_on)
        buckets.setdefault(key, []).append(value)
    collapsed = {key: (sum(values) / len(values), len(values)) for key, values in buckets.items()}
    if fetch.plan.frequency is not Frequency.QUARTERLY:
        return tuple(
            (start, end, value, count) for (start, end), (value, count) in sorted(collapsed.items())
        )
    # A quarterly reading describes its whole quarter, so it is carried to each month of
    # that quarter rather than leaving two months of every three empty. The carry is
    # recorded in provenance and never changes the value or its availability date.
    carried: dict[tuple[date, date], tuple[float, int]] = {}
    for (start, _), (value, count) in sorted(collapsed.items()):
        for offset in range(3):
            month = start.month - 1 + offset
            month_start = date(start.year + month // 12, month % 12 + 1, 1)
            carried[_month_bounds(month_start)] = (value, count)
    return tuple(
        (start, end, value, count) for (start, end), (value, count) in sorted(carried.items())
    )


def ingest_series(
    connection: sqlite3.Connection,
    fetch: SeriesFetch,
    *,
    series_version: str = "1.0.0",
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict[str, object]:
    """Register the series definition and write monthly control observations."""
    plan = fetch.plan
    connection.execute(
        """INSERT OR IGNORE INTO control_series_definition
           VALUES(?,?,?,?,?)""",
        (
            plan.series_id,
            series_version,
            plan.unit,
            '{"publisher":"required","source_url":"required","vintage":"required"}',
            datetime.now(UTC).replace(microsecond=0).isoformat(),
        ),
    )
    written = 0
    skipped = 0
    for start, end, value, sample_count in monthly_observations(fetch):
        if period_start and start < period_start:
            continue
        if period_end and end > period_end:
            continue
        observed_at = datetime(end.year, end.month, end.day, tzinfo=UTC)
        available_at = datetime.fromordinal(end.toordinal() + plan.publication_lag_days).replace(
            tzinfo=UTC
        )
        provenance = {
            "publisher": PUBLISHER,
            "source_url": fetch.source_url,
            # `vintage` is the honest label, not a date we do not have.
            "vintage": plan.vintage_basis.value,
            "vintage_note": (
                "series is not materially revised after first publication"
                if plan.vintage_basis is VintageBasis.AS_PUBLISHED
                else "latest revision only; vintage as-of-date not reachable without a "
                "FRED API key, so this series must not carry a leakage-free backtest"
            ),
            "fred_series_id": plan.fred_id,
            "frequency": plan.frequency.value,
            "content_sha256": fetch.content_sha256,
            "fetched_at": fetch.fetched_at,
            "monthly_sample_count": str(sample_count),
            "monthly_carry": (
                "quarterly reading applied to each month of its quarter"
                if plan.frequency is Frequency.QUARTERLY
                else "none"
            ),
            "publication_lag_days": str(plan.publication_lag_days),
        }
        if plan.proxy_for:
            provenance["proxy_for"] = plan.proxy_for
        observation = ControlObservation(
            control_observation_id=(
                f"control-{plan.series_id}-{series_version}-{start.isoformat()}"
            ),
            series_id=plan.series_id,
            series_version=series_version,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            observed_at=observed_at.isoformat(),
            availability_at=available_at.isoformat(),
            value_numeric=value,
            unit=plan.unit,
            provenance=provenance,
        )
        if register_control_observation(connection, observation):
            written += 1
        else:
            skipped += 1
    connection.commit()
    return {
        "series_id": plan.series_id,
        "fred_series_id": plan.fred_id,
        "vintage_basis": plan.vintage_basis.value,
        "unit": plan.unit,
        "written": written,
        "already_present": skipped,
        "content_sha256": fetch.content_sha256,
        "source_url": fetch.source_url,
        "raw_observation_count": len(fetch.rows),
        "first_observation": fetch.rows[0][0].isoformat(),
        "last_observation": fetch.rows[-1][0].isoformat(),
    }


def ingest_controls(
    connection: sqlite3.Connection,
    *,
    user_agent: str,
    plans: Sequence[ControlSeriesPlan] | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    session: requests.Session | None = None,
) -> dict[str, object]:
    """Acquire and register every planned control series."""
    selected: Iterable[ControlSeriesPlan] = plans if plans is not None else CONTROL_PLANS
    results: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for plan in selected:
        try:
            fetch = fetch_series(plan, user_agent=user_agent, session=session)
        except (requests.RequestException, ValueError) as exc:
            failures.append({"series_id": plan.series_id, "error": str(exc)[:300]})
            continue
        results.append(
            ingest_series(
                connection,
                fetch,
                period_start=period_start,
                period_end=period_end,
            )
        )
    return {
        "ingested": results,
        "failures": failures,
        "unavailable_catalog_series": dict(UNAVAILABLE_CONTROL_SERIES),
        "vintage_warning": (
            "series labelled latest_revision are today's revised values. They are ingested "
            "for description only and must not be presented as what was knowable at the time."
        ),
    }
