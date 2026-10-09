"""Plotly figure builders and display formatting for the RidePulse dashboard.

Figures take tables already computed by metrics.py / maps.py and only handle
presentation. Colors follow fixed roles so an entity keeps its color under any
filter: one accent per single-series chart, a fixed status palette for booking
outcomes (always paired with text labels), and a one-hue sequential ramp for
magnitude.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd
import plotly.graph_objects as go

from metrics import (
    STATUS_CANCELLED_BY_CUSTOMER,
    STATUS_CANCELLED_BY_DRIVER,
    STATUS_COMPLETED,
    STATUS_INCOMPLETE,
    STATUS_NO_DRIVER_FOUND,
    WEEKDAY_LABELS,
)

ORIGIN_COLOR = "#2a78d6"  # pickup / single-series accent
DESTINATION_COLOR = "#eb6834"  # drop
STATUS_COLORS = {
    STATUS_COMPLETED: "#0ca30c",
    STATUS_CANCELLED_BY_CUSTOMER: "#ec835a",
    STATUS_CANCELLED_BY_DRIVER: "#d03b3b",
    STATUS_NO_DRIVER_FOUND: "#fab219",
    STATUS_INCOMPLETE: "#898781",
}
OTHER_STATUS_COLOR = "#c3c2b7"
SEQUENTIAL_BLUE = [
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
    "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]  # fmt: skip
GRID_COLOR = "rgba(137, 135, 129, 0.22)"
PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def fmt_count(value: float | int | None) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{int(value):,}"


def fmt_pct(value: float | None, digits: int = 1) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{value:.{digits}%}"


def fmt_decimal(value: float | None, digits: int = 1) -> str:
    return "N/A" if value is None or pd.isna(value) else f"{value:,.{digits}f}"


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def _style(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 24, "t": 8, "b": 8},
        showlegend=False,
        bargap=0.35,
        hovermode="closest",
        font={"family": 'system-ui, -apple-system, "Segoe UI", sans-serif'},
    )
    fig.update_xaxes(gridcolor=GRID_COLOR, zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=GRID_COLOR, zeroline=False, automargin=True)
    return fig


def status_distribution(composition: pd.DataFrame) -> go.Figure:
    """Horizontal bars for every booking outcome, each labeled with count and share."""
    table = composition.iloc[::-1]  # first status at the top
    colors = [STATUS_COLORS.get(s, OTHER_STATUS_COLOR) for s in table["booking_status"]]
    labels = [
        f"{fmt_count(n)} · {fmt_pct(s)}"
        for n, s in zip(table["bookings"], table["share"], strict=True)
    ]
    fig = go.Figure(
        go.Bar(
            x=table["bookings"],
            y=table["booking_status"],
            orientation="h",
            marker={"color": colors, "cornerradius": 4},
            text=labels,
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}<br>%{text}<extra></extra>",
        )
    )
    fig.update_xaxes(title_text="Booking records", showgrid=True)
    fig.update_yaxes(showgrid=False)
    return _style(fig, height=260)


def trend_line(
    trend: pd.DataFrame,
    value_column: str,
    y_title: str,
    *,
    percent: bool = False,
    color: str = ORIGIN_COLOR,
) -> go.Figure:
    """Single-series line over `period_start`; gaps (N/A) are left unconnected."""
    values = trend[value_column].astype("Float64").astype(float)
    hover_value = "%{y:.1%}" if percent else "%{y:,.1f}"
    fig = go.Figure(
        go.Scatter(
            x=trend["period_start"],
            y=values,
            mode="lines",
            line={"color": color, "width": 2},
            connectgaps=False,
            customdata=trend[["eligible_days", "bookings"]].to_numpy(),
            hovertemplate=(
                "%{x|%d %b %Y}<br>"
                + y_title
                + ": "
                + hover_value
                + "<br>Bookings: %{customdata[1]:,}"
                + "<br>Calendar days: %{customdata[0]}<extra></extra>"
            ),
        )
    )
    fig.update_yaxes(title_text=y_title, rangemode="tozero")
    if percent:
        fig.update_yaxes(tickformat=".0%", range=[0, 1])
    fig.update_xaxes(showgrid=False)
    return _style(fig, height=260)


def ranked_bars(
    labels: Sequence[str],
    values: Sequence[float],
    *,
    value_title: str,
    color: str = ORIGIN_COLOR,
    text: Sequence[str] | None = None,
    height: int | None = None,
) -> go.Figure:
    """Horizontal bars, largest at the top. One series, one color."""
    labels, values = list(labels)[::-1], list(values)[::-1]
    text = list(text)[::-1] if text is not None else [fmt_count(v) for v in values]
    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker={"color": color, "cornerradius": 4},
            text=text,
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}<br>" + value_title + ": %{text}<extra></extra>",
        )
    )
    fig.update_xaxes(title_text=value_title, showgrid=True, rangemode="tozero")
    fig.update_yaxes(showgrid=False)
    return _style(fig, height=height or max(200, 34 * len(labels) + 60))


def column_bars(
    categories: Sequence[str],
    values: Sequence[float | None],
    *,
    value_title: str,
    hover_lines: Sequence[str],
    color: str = ORIGIN_COLOR,
    height: int = 280,
) -> go.Figure:
    """Vertical bars in the given category order; masked (None) values are blank."""
    fig = go.Figure(
        go.Bar(
            x=list(categories),
            y=[None if v is None or pd.isna(v) else float(v) for v in values],
            marker={"color": color, "cornerradius": 4},
            customdata=list(hover_lines),
            hovertemplate="%{customdata}<extra></extra>",
        )
    )
    fig.update_xaxes(type="category", showgrid=False)
    fig.update_yaxes(title_text=value_title, rangemode="tozero")
    return _style(fig, height=height)


def weekday_hour_heatmap(grid: pd.DataFrame) -> go.Figure:
    """Monday-Sunday x 00-23 average bookings per eligible weekday occurrence.

    Masked cells (unselected hour or no eligible dates) render blank, never as zero.
    """
    pivot = grid.pivot(index="weekday_num", columns="hour", values="avg_bookings_per_day")
    pivot = pivot.reindex(index=range(7), columns=range(24))
    bookings = grid.pivot(index="weekday_num", columns="hour", values="bookings").reindex(
        index=range(7), columns=range(24)
    )
    days = grid.pivot(index="weekday_num", columns="hour", values="eligible_days").reindex(
        index=range(7), columns=range(24)
    )
    z = pivot.astype("Float64").astype(float).to_numpy()
    hover = [
        [
            (
                f"{WEEKDAY_LABELS[d]} {h:02d}:00<br>Avg bookings per day: {fmt_decimal(z[d][h], 2)}"
                f"<br>Bookings: {fmt_count(bookings.iat[d, h])}"
                f"<br>Eligible {WEEKDAY_LABELS[d]}s: {fmt_count(days.iat[d, h])}"
            )
            if not pd.isna(z[d][h])
            else f"{WEEKDAY_LABELS[d]} {h:02d}:00<br>Not in selection (masked)"
            for h in range(24)
        ]
        for d in range(7)
    ]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=[f"{h:02d}" for h in range(24)],
            y=list(WEEKDAY_LABELS),
            colorscale=[[i / (len(SEQUENTIAL_BLUE) - 1), c] for i, c in enumerate(SEQUENTIAL_BLUE)],
            xgap=2,
            ygap=2,
            hoverongaps=True,
            text=hover,
            hovertemplate="%{text}<extra></extra>",
            colorbar={"title": {"text": "Avg / day", "side": "right"}, "thickness": 12},
        )
    )
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(title_text="Hour of day (booking time)", showgrid=False, type="category")
    return _style(fig, height=330)
