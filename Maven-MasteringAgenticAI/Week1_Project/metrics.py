"""Shared filters and metric calculations for prepared NCR ride booking records.

Every function takes the prepared booking-level table written by preprocessing.py
(one row per source booking record; see docs/DATA_DICTIONARY.md) and computes its
result from that data. Nothing here reads files or holds dashboard constants.

Conventions:
- Rates return None (display as N/A) when the denominator is zero. Actual zero
  counts are returned as 0.
- Aggregate rates are always recomputed from aggregate counts.
- Demand averages divide by eligible calendar dates (see `eligible_dates`), not by
  the dates that happen to survive vehicle/location filters.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

STATUS_COMPLETED = "Completed"
STATUS_CANCELLED_BY_CUSTOMER = "Cancelled by Customer"
STATUS_CANCELLED_BY_DRIVER = "Cancelled by Driver"
STATUS_NO_DRIVER_FOUND = "No Driver Found"
STATUS_INCOMPLETE = "Incomplete"
STATUS_ORDER = (
    STATUS_COMPLETED,
    STATUS_CANCELLED_BY_CUSTOMER,
    STATUS_CANCELLED_BY_DRIVER,
    STATUS_NO_DRIVER_FOUND,
    STATUS_INCOMPLETE,
)

WEEKDAY_LABELS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
HOURS = tuple(range(24))

# Product guardrail for comparing location rates, not a statistical test.
RELIABILITY_MIN_DENOMINATOR = 50

MISSING_LABEL = "(missing)"

Endpoint = Literal["pickup", "drop"]
FlowScope = Literal["requested", "completed"]
ReasonKind = Literal["customer", "driver", "incomplete"]

_LOCATION_COLUMN: dict[str, str] = {"pickup": "pickup_location", "drop": "drop_location"}
_REASON_SOURCE: dict[str, tuple[str, str]] = {
    "customer": ("is_cancelled_by_customer", "customer_cancel_reason"),
    "driver": ("is_cancelled_by_driver", "driver_cancel_reason"),
    "incomplete": ("is_incomplete", "incomplete_reason"),
}


def safe_rate(numerator: float, denominator: float) -> float | None:
    """Return numerator / denominator, or None when the denominator is zero."""
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


# ---------------------------------------------------------------------------
# Base cohort
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CohortFilters:
    """Global filter selections. None means "no restriction" for that dimension.

    Rows whose date (or hour) could not be parsed are kept when no date/weekday
    (or hour) filter is active and excluded once such a filter is applied,
    because they cannot be placed in the selected range.
    """

    start_date: date | None = None
    end_date: date | None = None
    vehicle_types: tuple[str, ...] | None = None
    weekdays: tuple[int, ...] | None = None
    hours: tuple[int, ...] | None = None
    pickup_locations: tuple[str, ...] | None = None
    drop_locations: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError(f"start_date {self.start_date} is after end_date {self.end_date}")
        if self.weekdays is not None and not set(self.weekdays) <= set(range(7)):
            raise ValueError(f"weekdays must be 0 (Monday) to 6 (Sunday), got {self.weekdays}")
        if self.hours is not None and not set(self.hours) <= set(HOURS):
            raise ValueError(f"hours must be 0 to 23, got {self.hours}")


def filter_base_cohort(rides: pd.DataFrame, filters: CohortFilters | None = None) -> pd.DataFrame:
    """Return base cohort B: the rows matching the global filters (never a status filter)."""
    if filters is None:
        return rides
    mask = pd.Series(True, index=rides.index)
    if filters.start_date is not None:
        mask &= (rides["booking_date"] >= pd.Timestamp(filters.start_date)).fillna(False)
    if filters.end_date is not None:
        mask &= (rides["booking_date"] <= pd.Timestamp(filters.end_date)).fillna(False)
    if filters.vehicle_types is not None:
        mask &= rides["vehicle_type"].isin(filters.vehicle_types).fillna(False)
    if filters.weekdays is not None:
        mask &= rides["weekday_num"].isin(filters.weekdays).fillna(False)
    if filters.hours is not None:
        mask &= rides["hour"].isin(filters.hours).fillna(False)
    if filters.pickup_locations is not None:
        mask &= rides["pickup_location"].isin(filters.pickup_locations).fillna(False)
    if filters.drop_locations is not None:
        mask &= rides["drop_location"].isin(filters.drop_locations).fillna(False)
    return rides.loc[mask.astype(bool)]


# ---------------------------------------------------------------------------
# Executive KPIs and outcomes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookingKpis:
    total_bookings: int
    unique_customer_ids: int
    completed: int
    cancelled_by_customer: int
    cancelled_by_driver: int
    no_driver_found: int
    incomplete: int
    completion_rate: float | None
    cancellation_rate: float | None
    customer_cancellation_share: float | None
    driver_cancellation_share: float | None
    no_driver_rate: float | None
    incomplete_rate: float | None
    non_completion_rate: float | None

    @property
    def cancelled(self) -> int:
        """Customer plus driver cancellations (No Driver Found and Incomplete excluded)."""
        return self.cancelled_by_customer + self.cancelled_by_driver


def booking_kpis(cohort: pd.DataFrame) -> BookingKpis:
    """Headline counts and rates; every rate uses all rows of the cohort as denominator."""
    total = len(cohort)
    completed = int(cohort["is_completed"].sum())
    by_customer = int(cohort["is_cancelled_by_customer"].sum())
    by_driver = int(cohort["is_cancelled_by_driver"].sum())
    no_driver = int(cohort["is_no_driver_found"].sum())
    incomplete = int(cohort["is_incomplete"].sum())
    return BookingKpis(
        total_bookings=total,
        unique_customer_ids=int(cohort["customer_id"].nunique(dropna=True)),
        completed=completed,
        cancelled_by_customer=by_customer,
        cancelled_by_driver=by_driver,
        no_driver_found=no_driver,
        incomplete=incomplete,
        completion_rate=safe_rate(completed, total),
        cancellation_rate=safe_rate(by_customer + by_driver, total),
        customer_cancellation_share=safe_rate(by_customer, total),
        driver_cancellation_share=safe_rate(by_driver, total),
        no_driver_rate=safe_rate(no_driver, total),
        incomplete_rate=safe_rate(incomplete, total),
        non_completion_rate=safe_rate(total - completed, total),
    )


def outcome_composition(cohort: pd.DataFrame) -> pd.DataFrame:
    """Rows per booking status: the five known statuses first (zero if absent), then any others."""
    counts = cohort["booking_status"].fillna(MISSING_LABEL).value_counts()
    extra = sorted(label for label in counts.index if label not in STATUS_ORDER)
    labels = [*STATUS_ORDER, *extra]
    total = len(cohort)
    bookings = [int(counts.get(label, 0)) for label in labels]
    return pd.DataFrame(
        {
            "booking_status": labels,
            "bookings": bookings,
            "share": _rates(bookings, total),
        }
    )


@dataclass(frozen=True)
class PaymentMix:
    table: pd.DataFrame  # payment_method, completed_bookings, share
    eligible_records: int  # completed rows with a recorded payment method
    completed_without_method: int


def payment_mix(cohort: pd.DataFrame) -> PaymentMix:
    """Payment methods among completed rows that have a recorded method."""
    completed = cohort.loc[cohort["is_completed"]]
    methods = completed["payment_method"].dropna()
    eligible = len(methods)
    table = _ranked_counts(methods, "payment_method", "completed_bookings")
    table["share"] = _rates(table["completed_bookings"], eligible)
    return PaymentMix(
        table=table,
        eligible_records=eligible,
        completed_without_method=len(completed) - eligible,
    )


@dataclass(frozen=True)
class CompletedValue:
    total: float | None  # None when no completed row has a recorded value
    valid_records: int
    completed_records: int


def completed_booking_value(cohort: pd.DataFrame) -> CompletedValue:
    """Sum of recorded Booking Value on completed rows (source currency units)."""
    values = cohort.loc[cohort["is_completed"], "booking_value"]
    valid = values.dropna()
    return CompletedValue(
        total=float(valid.sum()) if len(valid) else None,
        valid_records=len(valid),
        completed_records=len(values),
    )


def top_locations(cohort: pd.DataFrame, endpoint: Endpoint, n: int = 5) -> pd.DataFrame:
    """Most frequent pickup or drop labels; ties broken alphabetically for stable output."""
    column = _LOCATION_COLUMN[endpoint]
    return _ranked_counts(cohort[column].dropna(), "location", "bookings").head(n)


def bookings_by_vehicle_type(cohort: pd.DataFrame) -> pd.DataFrame:
    """Booking records per vehicle category (not counts of physical vehicles)."""
    table = _ranked_counts(cohort["vehicle_type"].fillna(MISSING_LABEL), "vehicle_type", "bookings")
    table["share"] = _rates(table["bookings"], len(cohort))
    return table


@dataclass(frozen=True)
class ReasonBreakdown:
    table: pd.DataFrame  # reason, bookings, share_of_recorded_reasons
    status_records: int  # rows with this outcome
    with_reason: int  # of those, rows with a recorded reason (the share denominator)


def cancellation_reasons(cohort: pd.DataFrame, kind: ReasonKind) -> ReasonBreakdown:
    """Reason counts for one exception type; shares use rows with a recorded reason."""
    flag_column, reason_column = _REASON_SOURCE[kind]
    rows = cohort.loc[cohort[flag_column]]
    reasons = rows[reason_column].dropna()
    table = _ranked_counts(reasons, "reason", "bookings")
    table["share_of_recorded_reasons"] = _rates(table["bookings"], len(reasons))
    return ReasonBreakdown(table=table, status_records=len(rows), with_reason=len(reasons))


# ---------------------------------------------------------------------------
# Calendar exposure and demand normalization
# ---------------------------------------------------------------------------


def coverage_interval(rides: pd.DataFrame) -> tuple[date, date] | None:
    """First and last observed booking dates in the full dataset, or None if no dates parse."""
    dates = rides["booking_date"].dropna()
    if dates.empty:
        return None
    return dates.min().date(), dates.max().date()


def coverage_gaps(rides: pd.DataFrame) -> list[date]:
    """Dates inside the coverage interval with no source records at all.

    These are unknown coverage, not measured zero demand: exclude them from
    exposure (via `eligible_dates(excluded_dates=...)`) and report them.
    """
    interval = coverage_interval(rides)
    if interval is None:
        return []
    calendar = pd.date_range(interval[0], interval[1], freq="D")
    observed = pd.DatetimeIndex(rides["booking_date"].dropna().unique())
    return [ts.date() for ts in calendar.difference(observed)]


def eligible_dates(
    start: date,
    end: date,
    *,
    weekdays: Iterable[int] | None = None,
    coverage: tuple[date, date] | None = None,
    excluded_dates: Iterable[date] = (),
) -> pd.DatetimeIndex:
    """Calendar dates that count in demand denominators.

    Dates come from the calendar, not from surviving booking rows, so a date with
    zero bookings after vehicle/location filters still counts. `coverage` clips the
    range to the dataset's known interval; `excluded_dates` removes coverage gaps.
    """
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    lo, hi = start, end
    if coverage is not None:
        lo, hi = max(lo, coverage[0]), min(hi, coverage[1])
    if lo > hi:
        return pd.DatetimeIndex([], dtype="datetime64[us]")
    days = pd.date_range(lo, hi, freq="D", unit="us")
    if weekdays is not None:
        days = days[days.weekday.isin(sorted(set(weekdays)))]
    excluded = pd.DatetimeIndex(pd.to_datetime(list(excluded_dates))).as_unit("us")
    return days.difference(excluded)


def weekday_exposure(dates: pd.DatetimeIndex) -> pd.Series:
    """Number of eligible dates per weekday, indexed 0 (Monday) to 6 (Sunday)."""
    counts = pd.Series(dates.weekday).value_counts()
    return counts.reindex(range(7), fill_value=0).astype(int)


def weekday_demand(cohort: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Bookings per weekday and the average per eligible occurrence of that weekday.

    Only rows dated on an eligible date are counted, so numerator and denominator
    cover the same days. Weekdays with no eligible dates are masked (NA), not zero.
    """
    exposure = weekday_exposure(dates)
    in_scope = cohort.loc[cohort["booking_date"].isin(dates)]
    counts = in_scope["weekday_num"].value_counts().reindex(range(7), fill_value=0)
    has_exposure = exposure > 0
    table = pd.DataFrame(
        {
            "weekday_num": range(7),
            "weekday": WEEKDAY_LABELS,
            "bookings": counts.astype("Int64").where(has_exposure, pd.NA).to_numpy(),
            "eligible_days": exposure.to_numpy(),
        }
    )
    table["avg_bookings_per_day"] = _masked_average(table["bookings"], table["eligible_days"])
    return table


def hourly_demand(
    cohort: pd.DataFrame, dates: pd.DatetimeIndex, hours: Iterable[int] | None = None
) -> pd.DataFrame:
    """Bookings per hour of day and the average per eligible date; unselected hours are NA."""
    selected = set(HOURS if hours is None else hours)
    in_scope = cohort.loc[cohort["booking_date"].isin(dates)]
    counts = in_scope["hour"].value_counts().reindex(HOURS, fill_value=0)
    is_selected = pd.Series([h in selected for h in HOURS], index=HOURS)
    table = pd.DataFrame(
        {
            "hour": HOURS,
            "bookings": counts.astype("Int64").where(is_selected, pd.NA).to_numpy(),
            "eligible_days": len(dates),
        }
    )
    table["avg_bookings_per_day"] = _masked_average(table["bookings"], table["eligible_days"])
    return table


def weekday_hour_demand(
    cohort: pd.DataFrame, dates: pd.DatetimeIndex, hours: Iterable[int] | None = None
) -> pd.DataFrame:
    """Long-form Monday-Sunday x 00-23 grid: bookings, eligible weekday occurrences, average.

    A cell is masked (NA) when its hour is unselected or its weekday has no eligible
    dates, so it is never shown as zero demand.
    """
    selected = set(HOURS if hours is None else hours)
    exposure = weekday_exposure(dates)
    in_scope = cohort.loc[cohort["booking_date"].isin(dates)]
    counts = in_scope.groupby(["weekday_num", "hour"]).size()
    grid = pd.MultiIndex.from_product([range(7), HOURS], names=["weekday_num", "hour"])
    table = counts.reindex(grid, fill_value=0).rename("bookings").reset_index()
    table.insert(1, "weekday", [WEEKDAY_LABELS[d] for d in table["weekday_num"]])
    table["eligible_days"] = exposure.reindex(table["weekday_num"]).to_numpy()
    visible = table["hour"].isin(selected) & (table["eligible_days"] > 0)
    table["bookings"] = table["bookings"].astype("Int64").where(visible, pd.NA)
    table["avg_bookings_per_day"] = _masked_average(table["bookings"], table["eligible_days"])
    return table


def volume_trend(
    cohort: pd.DataFrame, dates: pd.DatetimeIndex, freq: Literal["D", "W"] = "D"
) -> pd.DataFrame:
    """Bookings, completed bookings and completion rate per day or Monday-start week.

    Eligible dates with no bookings appear with 0 bookings and an N/A completion
    rate. `eligible_days` shows partial weeks at the edges of the range.
    """
    if freq not in ("D", "W"):
        raise ValueError(f"freq must be 'D' or 'W', got {freq!r}")
    in_scope = cohort.loc[cohort["booking_date"].isin(dates)]
    daily = pd.DataFrame(
        {
            "bookings": in_scope.groupby("booking_date").size(),
            "completed": in_scope.groupby("booking_date")["is_completed"].sum(),
        }
    )
    daily = daily.reindex(dates, fill_value=0).astype(int)
    daily["eligible_days"] = 1
    daily.index.name = "period_start"
    if freq == "W":
        week_start = daily.index - pd.to_timedelta(daily.index.weekday, unit="D")
        daily = daily.groupby(week_start).sum()
        daily.index.name = "period_start"
    table = daily.reset_index()[["period_start", "eligible_days", "bookings", "completed"]]
    # Per-day average keeps partial edge weeks comparable with full weeks.
    table["avg_bookings_per_day"] = _masked_average(table["bookings"], table["eligible_days"])
    table["completion_rate"] = pd.array(
        [safe_rate(c, b) for c, b in zip(table["completed"], table["bookings"], strict=True)],
        dtype="Float64",
    )
    return table


# ---------------------------------------------------------------------------
# Geography-ready aggregates (coordinate eligibility is applied in maps.py)
# ---------------------------------------------------------------------------


def od_flows(cohort: pd.DataFrame, scope: FlowScope = "requested") -> pd.DataFrame:
    """Directional pickup -> drop counts. A->B and B->A stay separate.

    `scope="completed"` counts only completed rows; it is a flow-layer view and
    must not be used as the denominator for location reliability rates.
    """
    if scope not in ("requested", "completed"):
        raise ValueError(f"scope must be 'requested' or 'completed', got {scope!r}")
    rows = cohort if scope == "requested" else cohort.loc[cohort["is_completed"]]
    rows = rows.dropna(subset=["pickup_location", "drop_location"])
    table = rows.groupby(["pickup_location", "drop_location"]).size().rename("bookings")
    table = table.reset_index().sort_values(
        ["bookings", "pickup_location", "drop_location"], ascending=[False, True, True]
    )
    table["is_same_location"] = table["pickup_location"] == table["drop_location"]
    return table.reset_index(drop=True)


def location_reliability(
    cohort: pd.DataFrame,
    endpoint: Endpoint = "pickup",
    min_denominator: int = RELIABILITY_MIN_DENOMINATOR,
) -> pd.DataFrame:
    """Outcome counts and rates per location over all outcomes in the cohort.

    `sufficient_sample` marks locations at or above `min_denominator` records;
    rates below it are kept for transparency but must be shown as low-sample.
    """
    column = _LOCATION_COLUMN[endpoint]
    rows = cohort.dropna(subset=[column])
    grouped = rows.groupby(column)
    table = pd.DataFrame(
        {
            "bookings": grouped.size(),
            "completed": grouped["is_completed"].sum(),
            "cancelled": grouped["is_cancelled"].sum(),
            "no_driver_found": grouped["is_no_driver_found"].sum(),
            "incomplete": grouped["is_incomplete"].sum(),
        }
    ).astype(int)
    for count_col, rate_col in [
        ("completed", "completion_rate"),
        ("cancelled", "cancellation_rate"),
        ("no_driver_found", "no_driver_rate"),
        ("incomplete", "incomplete_rate"),
    ]:
        table[rate_col] = table[count_col] / table["bookings"]
    table["sufficient_sample"] = table["bookings"] >= min_denominator
    table.index.name = "location"
    return table.reset_index().sort_values(["bookings", "location"], ascending=[False, True])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ranked_counts(values: pd.Series, label: str, count: str) -> pd.DataFrame:
    counts = values.value_counts()
    table = pd.DataFrame({label: counts.index.astype(object), count: counts.to_numpy(dtype=int)})
    return table.sort_values([count, label], ascending=[False, True]).reset_index(drop=True)


def _rates(counts: Iterable[int], denominator: int) -> pd.arrays.FloatingArray:
    """Shares of one denominator as a nullable float column (NA when it is zero)."""
    return pd.array([safe_rate(n, denominator) for n in counts], dtype="Float64")


def _masked_average(numerator: pd.Series, days: pd.Series | Sequence[int]) -> pd.Series:
    days = pd.Series(days, index=numerator.index).astype("Float64")
    return (numerator.astype("Float64") / days.where(days > 0, pd.NA)).astype("Float64")
