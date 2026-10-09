"""Metric contract checks. Expected values are worked out by hand in comments."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from conftest import prepared_from_rows, raw_row

import metrics
from metrics import (
    STATUS_ORDER,
    WEEKDAY_LABELS,
    CohortFilters,
    booking_kpis,
    cancellation_reasons,
    completed_booking_value,
    eligible_dates,
    filter_base_cohort,
    hourly_demand,
    location_reliability,
    od_flows,
    outcome_composition,
    payment_mix,
    safe_rate,
    volume_trend,
    weekday_demand,
    weekday_hour_demand,
)


def on(day: str, status: str = "Completed", **fields: str) -> dict[str, str]:
    """Row on a given date; keyword fields use source names with spaces as underscores."""
    return raw_row(status, {"Date": day, **{k.replace("_", " "): v for k, v in fields.items()}})


@pytest.fixture
def ten_bookings() -> pd.DataFrame:
    # 4 completed, 2 customer-cancelled, 1 driver-cancelled, 2 no-driver, 1 incomplete.
    statuses = (
        ["Completed"] * 4
        + ["Cancelled by Customer"] * 2
        + ["Cancelled by Driver"]
        + ["No Driver Found"] * 2
        + ["Incomplete"]
    )
    rows = [raw_row(s) for s in statuses]
    rows[1]["Customer ID"] = rows[0]["Customer ID"]  # repeated customer
    rows[2]["Customer ID"] = "null"  # missing customer
    return prepared_from_rows(rows)


def test_safe_rate_returns_none_only_for_empty_denominator():
    assert safe_rate(0, 0) is None
    assert safe_rate(0, 5) == 0.0
    assert safe_rate(3, 4) == 0.75


def test_kpis_use_all_cohort_rows_as_denominator(ten_bookings):
    k = booking_kpis(ten_bookings)

    assert k.total_bookings == 10
    assert k.unique_customer_ids == 8  # 10 rows - 1 repeat - 1 missing
    assert (k.completed, k.cancelled_by_customer, k.cancelled_by_driver) == (4, 2, 1)
    assert k.completion_rate == pytest.approx(0.4)
    assert k.cancellation_rate == pytest.approx(0.3)  # (2 + 1) / 10; excludes no-driver
    assert k.cancelled == 3
    assert k.no_driver_rate == pytest.approx(0.2)
    assert k.incomplete_rate == pytest.approx(0.1)
    assert k.non_completion_rate == pytest.approx(0.6)  # not equal to the cancellation rate


def test_empty_cohort_gives_zero_counts_and_na_rates(ten_bookings):
    empty = ten_bookings.iloc[0:0]
    k = booking_kpis(empty)

    assert k.total_bookings == 0 and k.completed == 0
    assert k.completion_rate is None and k.cancellation_rate is None
    composition = outcome_composition(empty)
    assert composition["booking_status"].tolist() == list(STATUS_ORDER)
    assert composition["bookings"].tolist() == [0] * 5
    assert composition["share"].isna().all()
    mix = payment_mix(empty)
    assert mix.eligible_records == 0 and mix.table.empty
    assert completed_booking_value(empty).total is None


def test_outcome_composition_covers_all_statuses_in_order(ten_bookings):
    table = outcome_composition(ten_bookings)

    assert table["booking_status"].tolist() == list(STATUS_ORDER)
    assert table["bookings"].tolist() == [4, 2, 1, 2, 1]
    assert table["share"].sum() == pytest.approx(1.0)


def test_all_missing_completed_values_give_na_not_zero():
    prepared = prepared_from_rows([raw_row(fields={"Booking Value": "null"})] * 2)
    value = completed_booking_value(prepared)

    assert value.total is None
    assert (value.valid_records, value.completed_records) == (0, 2)


def test_completed_booking_value_ignores_incomplete_rows():
    prepared = prepared_from_rows(
        [
            raw_row(fields={"Booking Value": "100"}),
            raw_row(fields={"Booking Value": "null"}),
            raw_row("Incomplete", {"Booking Value": "999"}),
        ]
    )
    value = completed_booking_value(prepared)

    assert value.total == 100
    assert (value.valid_records, value.completed_records) == (1, 2)


def test_payment_mix_uses_completed_rows_with_a_recorded_method():
    prepared = prepared_from_rows(
        [
            raw_row(fields={"Payment Method": "UPI"}),
            raw_row(fields={"Payment Method": "UPI"}),
            raw_row(fields={"Payment Method": "Cash"}),
            raw_row(fields={"Payment Method": "null"}),
            raw_row("Incomplete", {"Payment Method": "UPI"}),
        ]
    )
    mix = payment_mix(prepared)

    assert mix.eligible_records == 3
    assert mix.completed_without_method == 1
    assert mix.table["payment_method"].tolist() == ["UPI", "Cash"]
    assert mix.table["completed_bookings"].tolist() == [2, 1]
    assert mix.table["share"].tolist() == pytest.approx([2 / 3, 1 / 3])


def test_aggregate_rate_comes_from_counts_not_average_of_subgroup_rates():
    rows = [raw_row(fields={"Vehicle Type": "Auto"})]
    rows += [raw_row(fields={"Vehicle Type": "Bike"})]
    rows += [raw_row("No Driver Found", {"Vehicle Type": "Bike"}) for _ in range(8)]
    prepared = prepared_from_rows(rows)

    # Auto 1/1 = 100%, Bike 1/9; unweighted mean would be 55.6%, the correct rate is 2/10.
    assert booking_kpis(prepared).completion_rate == pytest.approx(0.2)


def test_bike_and_ebike_stay_distinct():
    prepared = prepared_from_rows(
        [raw_row(fields={"Vehicle Type": "Bike"}), raw_row(fields={"Vehicle Type": "eBike"})]
    )
    table = metrics.bookings_by_vehicle_type(prepared)

    assert sorted(table["vehicle_type"]) == ["Bike", "eBike"]


def test_filters_never_use_status_and_date_filter_excludes_unparsed_dates():
    prepared = prepared_from_rows(
        [
            on("2024-01-01"),
            on("2024-01-02", "Cancelled by Driver"),
            on("bad-date"),
        ]
    )

    assert len(filter_base_cohort(prepared)) == 3  # default keeps every record
    in_range = filter_base_cohort(prepared, CohortFilters(start_date=date(2024, 1, 1)))
    assert len(in_range) == 2
    assert not hasattr(CohortFilters(), "booking_status")


def test_filters_combine_vehicle_weekday_hour_and_location():
    prepared = prepared_from_rows(
        [
            raw_row(fields={"Vehicle Type": "Auto", "Time": "08:00:00"}),
            raw_row(fields={"Vehicle Type": "Auto", "Time": "09:00:00"}),
            raw_row(fields={"Vehicle Type": "Bike", "Time": "08:00:00"}),
            raw_row(
                fields={"Vehicle Type": "Auto", "Time": "08:00:00", "Pickup Location": "Other"}
            ),
        ]
    )
    filters = CohortFilters(
        vehicle_types=("Auto",), weekdays=(0,), hours=(8,), pickup_locations=("Alpha",)
    )

    assert filter_base_cohort(prepared, filters)["source_row_number"].tolist() == [1]


def test_invalid_filters_are_rejected():
    with pytest.raises(ValueError):
        CohortFilters(start_date=date(2024, 2, 1), end_date=date(2024, 1, 1))
    with pytest.raises(ValueError):
        CohortFilters(hours=(24,))


def test_eligible_dates_come_from_the_calendar():
    # 2024-01-01 is a Monday; two full weeks.
    days = eligible_dates(date(2024, 1, 1), date(2024, 1, 14))
    assert len(days) == 14
    assert metrics.weekday_exposure(days).tolist() == [2] * 7

    mondays = eligible_dates(date(2024, 1, 1), date(2024, 1, 14), weekdays=[0])
    assert [d.date() for d in mondays] == [date(2024, 1, 1), date(2024, 1, 8)]

    clipped = eligible_dates(
        date(2023, 12, 25),
        date(2024, 1, 14),
        coverage=(date(2024, 1, 1), date(2024, 1, 10)),
        excluded_dates=[date(2024, 1, 5)],
    )
    assert len(clipped) == 9  # Jan 1-10 minus the gap on Jan 5


def test_weekday_average_keeps_zero_booking_filtered_days_in_denominator():
    # Monday Jan 1: 3 Auto bookings. Monday Jan 8: 1 Bike booking only.
    prepared = prepared_from_rows([on("2024-01-01")] * 3 + [on("2024-01-08", Vehicle_Type="Bike")])
    autos = filter_base_cohort(prepared, CohortFilters(vehicle_types=("Auto",)))
    days = eligible_dates(date(2024, 1, 1), date(2024, 1, 14))

    table = weekday_demand(autos, days)
    monday = table.iloc[0]
    assert table["weekday"].tolist() == list(WEEKDAY_LABELS)
    assert (monday["bookings"], monday["eligible_days"]) == (3, 2)
    assert monday["avg_bookings_per_day"] == pytest.approx(1.5)  # not 3.0
    assert table.iloc[1]["avg_bookings_per_day"] == 0  # measured zero on eligible Tuesdays


def test_weekday_order_is_monday_to_sunday_regardless_of_data_order():
    prepared = prepared_from_rows([on("2024-01-07"), on("2024-01-03"), on("2024-01-01")])
    table = weekday_demand(prepared, eligible_dates(date(2024, 1, 1), date(2024, 1, 7)))

    assert table["weekday"].tolist() == list(WEEKDAY_LABELS)
    assert table["bookings"].tolist() == [1, 0, 1, 0, 0, 0, 1]


def test_weekdays_without_exposure_are_masked_not_zero():
    prepared = prepared_from_rows([on("2024-01-01")])
    table = weekday_demand(prepared, eligible_dates(date(2024, 1, 1), date(2024, 1, 3)))

    assert table["eligible_days"].tolist() == [1, 1, 1, 0, 0, 0, 0]
    assert table["avg_bookings_per_day"].isna().tolist() == [False] * 3 + [True] * 4
    assert table["bookings"].isna().tolist() == [False] * 3 + [True] * 4


def test_weekday_hour_cells_use_weekday_occurrences_and_mask_unselected_hours():
    prepared = prepared_from_rows(
        [on("2024-01-01", Time="08:10:00")] * 3 + [on("2024-01-08", Time="08:30:00")]
    )
    days = eligible_dates(date(2024, 1, 1), date(2024, 1, 14))
    grid = weekday_hour_demand(prepared, days, hours=[8])

    assert len(grid) == 7 * 24
    cell = grid[(grid["weekday_num"] == 0) & (grid["hour"] == 8)].iloc[0]
    assert (cell["bookings"], cell["eligible_days"]) == (4, 2)
    assert cell["avg_bookings_per_day"] == pytest.approx(2.0)
    tuesday_8 = grid[(grid["weekday_num"] == 1) & (grid["hour"] == 8)].iloc[0]
    assert tuesday_8["avg_bookings_per_day"] == 0
    assert grid.loc[grid["hour"] != 8, "avg_bookings_per_day"].isna().all()


def test_hourly_demand_divides_by_eligible_dates():
    prepared = prepared_from_rows(
        [on("2024-01-01", Time="18:00:00"), on("2024-01-02", Time="18:45:00")]
    )
    table = hourly_demand(prepared, eligible_dates(date(2024, 1, 1), date(2024, 1, 4)))

    assert table.loc[18, "bookings"] == 2
    assert table.loc[18, "avg_bookings_per_day"] == pytest.approx(0.5)  # 2 bookings / 4 days


def test_daily_trend_includes_zero_booking_days_and_weekly_shows_partial_weeks():
    prepared = prepared_from_rows(
        [on("2024-01-01"), on("2024-01-01", "Cancelled by Driver"), on("2024-01-09")]
    )
    days = eligible_dates(date(2024, 1, 1), date(2024, 1, 10))

    daily = volume_trend(prepared, days, "D")
    assert len(daily) == 10
    assert daily.loc[0, ["bookings", "completed"]].tolist() == [2, 1]
    assert daily.loc[0, "completion_rate"] == 0.5
    assert daily.loc[1, "bookings"] == 0 and pd.isna(daily.loc[1, "completion_rate"])

    weekly = volume_trend(prepared, days, "W")
    assert weekly["period_start"].dt.date.tolist() == [date(2024, 1, 1), date(2024, 1, 8)]
    assert weekly["eligible_days"].tolist() == [7, 3]
    assert weekly["bookings"].tolist() == [2, 1]
    # Partial edge week compared per day: 2 bookings / 7 days vs 1 booking / 3 days.
    assert weekly["avg_bookings_per_day"].tolist() == pytest.approx([2 / 7, 1 / 3])


def test_od_flows_keep_direction_and_flag_same_location():
    prepared = prepared_from_rows(
        [
            raw_row(fields={"Pickup Location": "A", "Drop Location": "B"}),
            raw_row(fields={"Pickup Location": "A", "Drop Location": "B"}),
            raw_row("Cancelled by Driver", {"Pickup Location": "B", "Drop Location": "A"}),
            raw_row(fields={"Pickup Location": "A", "Drop Location": "A"}),
        ]
    )

    requested = od_flows(prepared, "requested")
    as_tuples = list(
        requested[["pickup_location", "drop_location", "bookings"]].itertuples(
            index=False, name=None
        )
    )
    assert as_tuples == [("A", "B", 2), ("A", "A", 1), ("B", "A", 1)]
    assert requested["is_same_location"].tolist() == [False, True, False]

    completed = od_flows(prepared, "completed")
    assert ("B", "A") not in set(
        zip(completed["pickup_location"], completed["drop_location"], strict=True)
    )


def test_location_reliability_marks_small_samples_and_uses_all_outcomes():
    rows = [raw_row(fields={"Pickup Location": "Big"}) for _ in range(30)]
    rows += [raw_row("Cancelled by Customer", {"Pickup Location": "Big"}) for _ in range(20)]
    rows += [raw_row("No Driver Found", {"Pickup Location": "Small"}) for _ in range(5)]
    table = location_reliability(prepared_from_rows(rows), "pickup").set_index("location")

    assert table.loc["Big", "bookings"] == 50
    assert table.loc["Big", "completion_rate"] == pytest.approx(0.6)
    assert table.loc["Big", "cancellation_rate"] == pytest.approx(0.4)
    assert table.loc["Big", "sufficient_sample"]
    assert not table.loc["Small", "sufficient_sample"]
    assert table.loc["Small", "no_driver_rate"] == 1.0


def test_cancellation_reason_shares_use_records_with_a_reason():
    prepared = prepared_from_rows(
        [
            raw_row("Cancelled by Customer", {"Reason for cancelling by Customer": "Late"}),
            raw_row("Cancelled by Customer", {"Reason for cancelling by Customer": "Late"}),
            raw_row("Cancelled by Customer", {"Reason for cancelling by Customer": "null"}),
            raw_row("Completed"),
        ]
    )
    breakdown = cancellation_reasons(prepared, "customer")

    assert (breakdown.status_records, breakdown.with_reason) == (3, 2)
    assert breakdown.table["reason"].tolist() == ["Late"]
    assert breakdown.table["share_of_recorded_reasons"].tolist() == [1.0]


def test_top_locations_break_ties_alphabetically():
    prepared = prepared_from_rows(
        [
            raw_row(fields={"Pickup Location": "Zeta"}),
            raw_row(fields={"Pickup Location": "Eta"}),
            raw_row(fields={"Pickup Location": "Zeta"}),
            raw_row(fields={"Pickup Location": "Beta"}),
        ]
    )
    top = metrics.top_locations(prepared, "pickup", n=2)

    assert top["location"].tolist() == ["Zeta", "Beta"]
    assert top["bookings"].tolist() == [2, 1]
