from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest
from conftest import prepared_from_rows, raw_row

import charts
import metrics


@pytest.mark.parametrize(
    ("func", "value", "expected"),
    [
        (charts.fmt_count, 150000, "150,000"),
        (charts.fmt_count, 0, "0"),
        (charts.fmt_count, None, "N/A"),
        (charts.fmt_count, pd.NA, "N/A"),
        (charts.fmt_pct, 0.62, "62.0%"),
        (charts.fmt_pct, 0.0, "0.0%"),
        (charts.fmt_pct, None, "N/A"),
        (charts.fmt_decimal, 408.3774, "408.4"),
    ],
)
def test_formatters_show_na_for_missing_and_zero_for_zero(func, value, expected):
    assert func(value) == expected


def test_status_colors_follow_the_outcome_not_its_rank():
    composition = metrics.outcome_composition(
        prepared_from_rows([raw_row("Incomplete")] * 3 + [raw_row("Completed")])
    )
    bar = charts.status_distribution(composition).data[0]
    colors = dict(zip(bar.y, bar.marker.color, strict=True))

    for status, color in charts.STATUS_COLORS.items():
        assert colors[status] == color
    assert all(isinstance(t, str) and "·" in t for t in bar.text)  # every bar is labeled


def test_heatmap_leaves_masked_cells_blank():
    prepared = prepared_from_rows([raw_row(fields={"Date": "2024-01-01", "Time": "08:00:00"})])
    dates = metrics.eligible_dates(date(2024, 1, 1), date(2024, 1, 2))  # Monday, Tuesday
    grid = metrics.weekday_hour_demand(prepared, dates, hours=[8, 9])
    z = charts.weekday_hour_heatmap(grid).data[0].z

    assert z[0][8] == 1.0  # Monday 08:00, 1 booking / 1 Monday
    assert z[1][8] == 0.0  # Tuesday 08:00 is eligible: a measured zero
    assert math.isnan(z[0][10])  # unselected hour
    assert math.isnan(z[3][8])  # Thursday has no eligible dates


def test_trend_and_bar_builders_handle_empty_inputs():
    empty = prepared_from_rows([raw_row()]).iloc[0:0]
    dates = metrics.eligible_dates(date(2024, 1, 1), date(2024, 1, 3))
    trend = metrics.volume_trend(empty, dates, "D")

    fig = charts.trend_line(trend, "completion_rate", "Completion rate", percent=True)
    assert all(math.isnan(v) for v in fig.data[0].y)
    assert len(charts.ranked_bars([], [], value_title="Bookings").data[0].x) == 0
