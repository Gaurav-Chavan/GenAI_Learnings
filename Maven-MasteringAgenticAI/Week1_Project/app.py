"""NCR RidePulse — Streamlit entry point.

    uv run streamlit run app.py

UI only. Prepared data comes from preprocessing.py, every number from metrics.py /
maps.py, and every figure from charts.py. The raw CSV is never read here.
"""

from __future__ import annotations

import functools
import hashlib
from datetime import date
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

import charts
import maps
import metrics
import preprocessing
from metrics import WEEKDAY_LABELS, CohortFilters

TITLE = "NCR RidePulse"
SUBTITLE = "Ride Demand, Reliability & Trip Intelligence"
EMPTY_MESSAGE = "No booking records match the current filters. Adjust or reset the filters."

# Columns the dashboard needs; keeping the cached frame narrow keeps reruns fast.
APP_COLUMNS = [
    "source_record_key",
    "customer_id",
    "booking_date",
    "weekday_num",
    "hour",
    "booking_status",
    "is_completed",
    "is_cancelled_by_customer",
    "is_cancelled_by_driver",
    "is_no_driver_found",
    "is_incomplete",
    "is_cancelled",
    "vehicle_type",
    "pickup_location",
    "drop_location",
    "booking_value",
    "payment_method",
    "customer_cancel_reason",
    "driver_cancel_reason",
    "incomplete_reason",
    "pickup_latitude",
    "pickup_longitude",
    "pickup_is_mapped",
    "drop_latitude",
    "drop_longitude",
    "drop_is_mapped",
]

FLOW_SCOPES = {"requested": "Requested (all outcomes)", "completed": "Completed only"}
ENDPOINTS = {"pickup": "Pickup", "drop": "Drop"}
MODES = ("Trip flows", "Demand hotspots", "Service reliability")
DETAIL_KEYS = ("d_location", "d_connection")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def _file_signature(*paths: Path) -> tuple:
    """Cache key that changes whenever an input file is rewritten."""
    return tuple(
        (str(p), p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else (str(p), None, None)
        for p in paths
    )


@st.cache_data(show_spinner="Loading prepared booking data…", max_entries=2)
def load_data(signature: tuple) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load prepared bookings and the lookup after fingerprint (staleness) checks.

    `signature` only keys the cache. The source CSV fingerprint is checked when the
    file is present; it is never read for analysis here.
    """
    source = preprocessing.DEFAULT_INPUT
    rides = preprocessing.load_prepared(
        preprocessing.DEFAULT_PREPARED,
        source_path=source if source.exists() else None,
        lookup_path=preprocessing.DEFAULT_LOOKUP,
    )
    lookup = preprocessing.read_lookup(preprocessing.DEFAULT_LOOKUP)
    if lookup is None:
        raise preprocessing.PreparationError("Location lookup file is missing.")
    return rides[APP_COLUMNS], lookup


def load_or_stop() -> tuple[pd.DataFrame, pd.DataFrame]:
    signature = _file_signature(
        preprocessing.DEFAULT_PREPARED,
        preprocessing.meta_path_for(preprocessing.DEFAULT_PREPARED),
        preprocessing.DEFAULT_LOOKUP,
        preprocessing.DEFAULT_INPUT,
    )
    try:
        return load_data(signature)
    except preprocessing.PreparationError as exc:
        st.error(f"Prepared data is unavailable or out of date.\n\n{exc}")
        st.markdown("Regenerate it from the project root, then reload this page:")
        st.code("uv run python preprocessing.py --input ncr_ride_bookings.csv", language="text")
        st.stop()


# ---------------------------------------------------------------------------
# Global filters (sidebar)
# ---------------------------------------------------------------------------


def filter_defaults(interval: tuple[date, date]) -> dict:
    return {
        "f_dates": interval,
        "f_vehicles": [],
        "f_weekdays": [],
        "f_hours": (0, 23),
        "f_pickups": [],
        "f_drops": [],
    }


def reset_filters(defaults: dict) -> None:
    for key, value in defaults.items():
        st.session_state[key] = value
    for key in DETAIL_KEYS:
        st.session_state.pop(key, None)


def render_sidebar(rides: pd.DataFrame, defaults: dict) -> tuple[CohortFilters, list[str]]:
    """Global filters shared by all tabs. Empty multiselects mean "all"."""
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    lo, hi = defaults["f_dates"]
    notes: list[str] = []
    with st.sidebar:
        st.header("Filters")
        st.caption("Apply to every tab. Booking status is intentionally not a global filter.")
        st.date_input(
            "Booking date range", min_value=lo, max_value=hi, key="f_dates", format="YYYY-MM-DD"
        )
        st.multiselect(
            "Vehicle type",
            sorted(rides["vehicle_type"].dropna().unique()),
            key="f_vehicles",
            placeholder="All vehicle types",
        )
        st.multiselect(
            "Day of week",
            list(range(7)),
            format_func=lambda d: WEEKDAY_LABELS[d],
            key="f_weekdays",
            placeholder="All days",
        )
        st.slider("Booking hour", 0, 23, key="f_hours", format="%02d:00")
        st.multiselect(
            "Pickup location",
            sorted(rides["pickup_location"].dropna().unique()),
            key="f_pickups",
            placeholder="All pickup locations (type to search)",
        )
        st.multiselect(
            "Drop location",
            sorted(rides["drop_location"].dropna().unique()),
            key="f_drops",
            placeholder="All drop locations (type to search)",
        )
        st.button(
            "Reset all filters",
            on_click=reset_filters,
            args=(defaults,),
            icon=":material/restart_alt:",
            width="stretch",
        )

    selected = st.session_state["f_dates"]
    if isinstance(selected, (tuple, list)) and len(selected) == 2:
        start, end = selected
    elif isinstance(selected, (tuple, list)) and len(selected) == 1:
        start, end = selected[0], hi
        notes.append("Select an end date — showing data from the start date onward.")
    else:
        start, end = lo, hi
    hours = st.session_state["f_hours"]
    filters = CohortFilters(
        start_date=start,
        end_date=end,
        vehicle_types=tuple(st.session_state["f_vehicles"]) or None,
        weekdays=tuple(sorted(st.session_state["f_weekdays"])) or None,
        hours=None if tuple(hours) == (0, 23) else tuple(range(hours[0], hours[1] + 1)),
        pickup_locations=tuple(st.session_state["f_pickups"]) or None,
        drop_locations=tuple(st.session_state["f_drops"]) or None,
    )
    return filters, notes


def active_filter_summary(filters: CohortFilters, interval: tuple[date, date]) -> list[str]:
    parts = []
    if (filters.start_date, filters.end_date) != interval:
        parts.append(f"Dates {filters.start_date:%d %b %Y} – {filters.end_date:%d %b %Y}")
    if filters.vehicle_types:
        parts.append("Vehicle: " + ", ".join(filters.vehicle_types))
    if filters.weekdays:
        parts.append("Days: " + ", ".join(WEEKDAY_LABELS[d][:3] for d in filters.weekdays))
    if filters.hours:
        parts.append(f"Hours {filters.hours[0]:02d}:00–{filters.hours[-1]:02d}:59")
    for label, values in (("Pickup", filters.pickup_locations), ("Drop", filters.drop_locations)):
        if values:
            shown = ", ".join(values[:3]) + (f" +{len(values) - 3} more" if len(values) > 3 else "")
            parts.append(f"{label}: {shown}")
    return parts


# ---------------------------------------------------------------------------
# Shared presentation helpers
# ---------------------------------------------------------------------------


def chart(fig, key: str) -> None:
    st.plotly_chart(fig, width="stretch", config=charts.PLOTLY_CONFIG, key=key)


def data_view(table: pd.DataFrame, label: str = "View data table", **kwargs) -> None:
    """Table twin for a chart, collapsed to keep the layout uncluttered."""
    with st.expander(label):
        st.dataframe(table, hide_index=True, width="stretch", **kwargs)


def section(title: str, caption: str | None = None) -> None:
    st.markdown(f"##### {title}")
    if caption:
        st.caption(caption)


def operational_insight(kpis: metrics.BookingKpis) -> str | None:
    """One factual statement computed from the cohort's outcome counts."""
    if kpis.total_bookings == 0:
        return None
    outcomes = {
        metrics.STATUS_CANCELLED_BY_DRIVER: kpis.cancelled_by_driver,
        metrics.STATUS_CANCELLED_BY_CUSTOMER: kpis.cancelled_by_customer,
        metrics.STATUS_NO_DRIVER_FOUND: kpis.no_driver_found,
        metrics.STATUS_INCOMPLETE: kpis.incomplete,
    }
    not_completed = kpis.total_bookings - kpis.completed
    if not_completed == 0:
        return f"All {kpis.total_bookings:,} booking records in this selection were completed."
    label, count = max(outcomes.items(), key=lambda item: item[1])  # ties keep listed order
    return (
        f"**{label}** is the largest non-completion outcome in this selection: "
        f"{count:,} records ({charts.fmt_pct(count / kpis.total_bookings)} of all bookings, "
        f"{charts.fmt_pct(count / not_completed)} of the {not_completed:,} bookings that did not "
        f"complete). Completion rate: {charts.fmt_pct(kpis.completion_rate)}."
    )


# ---------------------------------------------------------------------------
# Tab 1 — Executive Overview
# ---------------------------------------------------------------------------


def render_overview(cohort: pd.DataFrame, dates: pd.DatetimeIndex) -> None:
    k = metrics.booking_kpis(cohort)
    share = {"delta_color": "off", "delta_arrow": "off", "delta_description": "of bookings"}
    cards = st.columns(6)
    cards[0].metric(
        "Total bookings",
        charts.fmt_count(k.total_bookings),
        border=True,
        help="Booking records in the filtered cohort. One source row = one booking record.",
    )
    cards[1].metric(
        "Unique customer IDs",
        charts.fmt_count(k.unique_customer_ids),
        border=True,
        help="Distinct non-missing customer IDs — identifiers, not verified individual people.",
    )
    cards[2].metric(
        "Completion rate",
        charts.fmt_pct(k.completion_rate),
        border=True,
        help="Completed / all booking records in the cohort.",
    )
    cards[3].metric(
        "Cancellation rate",
        charts.fmt_pct(k.cancellation_rate),
        border=True,
        help="(Customer + driver cancellations) / all booking records. "
        "No Driver Found and Incomplete are not cancellations.",
    )
    cards[4].metric(
        "Customer cancellations",
        charts.fmt_count(k.cancelled_by_customer),
        delta=charts.fmt_pct(k.customer_cancellation_share),
        border=True,
        help="Records with status Cancelled by Customer.",
        **share,
    )
    cards[5].metric(
        "Driver cancellations",
        charts.fmt_count(k.cancelled_by_driver),
        delta=charts.fmt_pct(k.driver_cancellation_share),
        border=True,
        help="Records with status Cancelled by Driver.",
        **share,
    )
    if cohort.empty:
        st.info(EMPTY_MESSAGE)
        return

    left, right = st.columns([3, 2], gap="large")
    with left:
        section("Booking outcomes", "All five outcomes; share of all booking records.")
        composition = metrics.outcome_composition(cohort)
        chart(charts.status_distribution(composition), "overview_status")
        data_view(
            composition,
            column_config={"share": st.column_config.NumberColumn("share", format="percent")},
        )
    with right:
        section("Operational insight", "Computed from the filtered cohort; descriptive only.")
        with st.container(border=True):
            st.markdown(operational_insight(k))
            st.caption(
                f"No driver found: {charts.fmt_pct(k.no_driver_rate)} · "
                f"Incomplete: {charts.fmt_pct(k.incomplete_rate)} · "
                f"Non-completion: {charts.fmt_pct(k.non_completion_rate)}"
            )

    head, control = st.columns([3, 1], vertical_alignment="bottom")
    with head:
        section("Trends over time")
    with control:
        freq = st.segmented_control(
            "Granularity",
            ["W", "D"],
            format_func={"W": "Weekly", "D": "Daily"}.get,
            default="W",
            required=True,
            key="t1_freq",
            label_visibility="collapsed",
        )
    trend = metrics.volume_trend(cohort, dates, freq)
    volume_col, rate_col = st.columns(2, gap="large")
    with volume_col:
        if freq == "W":
            st.caption("Average daily booking records per Monday-start week (partial weeks fair).")
            fig = charts.trend_line(trend, "avg_bookings_per_day", "Avg bookings per day")
        else:
            st.caption("Booking records per calendar day (zero-booking days shown as 0).")
            fig = charts.trend_line(trend, "bookings", "Bookings")
        chart(fig, "overview_volume")
    with rate_col:
        st.caption("Completion rate per period (completed / all booking records).")
        chart(
            charts.trend_line(
                trend,
                "completion_rate",
                "Completion rate",
                percent=True,
                color=charts.STATUS_COLORS[metrics.STATUS_COMPLETED],
            ),
            "overview_completion",
        )
    data_view(trend, "View trend data")

    pickups, drops = st.columns(2, gap="large")
    for column, endpoint, color in (
        (pickups, "pickup", charts.ORIGIN_COLOR),
        (drops, "drop", charts.DESTINATION_COLOR),
    ):
        with column:
            table = metrics.top_locations(cohort, endpoint, n=5)
            section(f"Top 5 {endpoint} locations", "Booking records, all outcomes.")
            chart(
                charts.ranked_bars(
                    table["location"], table["bookings"], value_title="Bookings", color=color
                ),
                f"overview_top_{endpoint}",
            )


# ---------------------------------------------------------------------------
# Tab 2 — Rider & Demand
# ---------------------------------------------------------------------------


def render_demand(cohort: pd.DataFrame, dates: pd.DatetimeIndex, filters: CohortFilters) -> None:
    if cohort.empty:
        st.info(EMPTY_MESSAGE)
        return
    if len(dates) == 0:
        st.info("The selected dates and weekdays contain no eligible calendar days.")
        return

    section(
        "When are bookings requested?",
        f"Average booking records per eligible calendar occurrence of each weekday "
        f"({len(dates):,} eligible dates in the selection). Blank cells are outside the "
        "selected days or hours — not zero demand. Demand = booking requests in this "
        "dataset, all outcomes.",
    )
    grid = metrics.weekday_hour_demand(cohort, dates, filters.hours)
    chart(charts.weekday_hour_heatmap(grid), "demand_heatmap")
    data_view(grid, "View heatmap data")

    weekday_col, hour_col = st.columns(2, gap="large")
    with weekday_col:
        weekday = metrics.weekday_demand(cohort, dates)
        visible = weekday.dropna(subset=["avg_bookings_per_day"])
        visible = visible.loc[visible["weekday_num"].isin(filters.weekdays or range(7))]
        spread = ""
        if len(visible) >= 2:
            hi_row = visible.loc[visible["avg_bookings_per_day"].idxmax()]
            lo_row = visible.loc[visible["avg_bookings_per_day"].idxmin()]
            gap = metrics.safe_rate(
                hi_row["avg_bookings_per_day"] - lo_row["avg_bookings_per_day"],
                lo_row["avg_bookings_per_day"],
            )
            high = charts.fmt_decimal(hi_row["avg_bookings_per_day"])
            low = charts.fmt_decimal(lo_row["avg_bookings_per_day"])
            spread = (
                f" Highest: {hi_row['weekday']} ({high}/day); lowest: {lo_row['weekday']} "
                f"({low}/day); difference {charts.fmt_pct(gap)}."
            )
        section(
            "Bookings by day of week", "Average per eligible occurrence of each weekday." + spread
        )
        show = weekday["weekday_num"].isin(filters.weekdays or range(7))
        chart(
            charts.column_bars(
                weekday["weekday"],
                weekday["avg_bookings_per_day"].where(show),
                value_title="Avg bookings per day",
                hover_lines=[
                    f"{r.weekday}<br>Avg per day: {charts.fmt_decimal(r.avg_bookings_per_day)}"
                    f"<br>Bookings: {charts.fmt_count(r.bookings)}"
                    f"<br>Eligible {r.weekday}s: {r.eligible_days}"
                    for r in weekday.itertuples()
                ],
            ),
            "demand_weekday",
        )
    with hour_col:
        hourly = metrics.hourly_demand(cohort, dates, filters.hours)
        section("Bookings by hour of day", "Average booking records per eligible date.")
        chart(
            charts.column_bars(
                [f"{h:02d}" for h in hourly["hour"]],
                hourly["avg_bookings_per_day"],
                value_title="Avg bookings per day",
                hover_lines=[
                    f"{r.hour:02d}:00–{r.hour:02d}:59<br>Avg per day: "
                    f"{charts.fmt_decimal(r.avg_bookings_per_day)}"
                    f"<br>Bookings: {charts.fmt_count(r.bookings)}"
                    for r in hourly.itertuples()
                ],
            ),
            "demand_hourly",
        )

    vehicle_col, payment_col = st.columns(2, gap="large")
    with vehicle_col:
        vehicles = metrics.bookings_by_vehicle_type(cohort)
        section(
            "Bookings by vehicle type",
            "Booking records per vehicle category — not a count of vehicles in the fleet.",
        )
        chart(
            charts.ranked_bars(
                vehicles["vehicle_type"],
                vehicles["bookings"],
                value_title="Bookings",
                text=[
                    f"{charts.fmt_count(n)} · {charts.fmt_pct(s)}"
                    for n, s in zip(vehicles["bookings"], vehicles["share"], strict=True)
                ],
            ),
            "demand_vehicle",
        )
    with payment_col:
        mix = metrics.payment_mix(cohort)
        section(
            "Payment methods (completed bookings)",
            f"Share of {mix.eligible_records:,} completed bookings with a recorded payment method"
            + (
                f"; {mix.completed_without_method:,} completed without one."
                if mix.completed_without_method
                else "."
            ),
        )
        if mix.eligible_records == 0:
            st.info("No completed bookings with a recorded payment method in this selection.")
        else:
            chart(
                charts.ranked_bars(
                    mix.table["payment_method"],
                    mix.table["completed_bookings"],
                    value_title="Completed bookings",
                    text=[
                        f"{charts.fmt_count(n)} · {charts.fmt_pct(s)}"
                        for n, s in zip(
                            mix.table["completed_bookings"], mix.table["share"], strict=True
                        )
                    ],
                ),
                "demand_payment",
            )


# ---------------------------------------------------------------------------
# Tab 3 — Trip Explorer
# ---------------------------------------------------------------------------

MAP_HEIGHT = 560


def clear_if_stale(key: str, options: list[str]) -> bool:
    """Drop a selection that the current filters no longer contain."""
    if key in st.session_state and st.session_state[key] is not None:
        if st.session_state[key] not in options:
            del st.session_state[key]
            return True
    return False


def clear_details() -> None:
    for key in DETAIL_KEYS:
        st.session_state.pop(key, None)


def connection_id(pickup: str, drop: str) -> str:
    return f"{pickup} → {drop}"


def select_from_map(chart_key: str) -> None:
    """on_select callback: copy a clicked point or arc into the details panel."""
    target = maps.selection_target(st.session_state.get(chart_key))
    if target is None:
        return
    kind, value = target
    st.session_state["d_scope"] = kind
    if kind == "location":
        st.session_state["d_location"] = value
    else:
        st.session_state["d_connection"] = connection_id(*value)


def show_map(deck: pdk.Deck, name: str, alt: str) -> None:
    """Render a selectable map. The key follows the drawn data, so an old click
    selection never survives a change of filters, mode or focus."""
    digest = hashlib.md5(deck.to_json().encode("utf-8")).hexdigest()[:12]
    key = f"map_{name}_{digest}"
    st.pydeck_chart(
        deck,
        height=MAP_HEIGHT,
        on_select=functools.partial(select_from_map, key),
        selection_mode="single-object",
        key=key,
        alt=alt,
    )


def legend(items: list[tuple[str, str]]) -> None:
    """Compact static legend: (CSS swatch style, label) pairs."""
    spans = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;margin:0 16px 4px 0">'
        f'<span style="{swatch}"></span>{label}</span>'
        for swatch, label in items
    )
    st.markdown(
        f'<div style="font-size:0.82rem;line-height:1.6">{spans}</div>'
        f'<div style="font-size:0.75rem;opacity:0.7">{maps.BASEMAP_ATTRIBUTION}</div>',
        unsafe_allow_html=True,
    )


def _dot(rgb: list[int], size: int = 12) -> str:
    return (
        f"display:inline-block;width:{size}px;height:{size}px;border-radius:50%;"
        f"background:rgb({rgb[0]},{rgb[1]},{rgb[2]});border:1.5px solid #fff;"
        "box-shadow:0 0 0 1px rgba(0,0,0,0.25)"
    )


def _gradient(colors: list[list[int]], width: int = 64) -> str:
    stops = ",".join(f"rgb({c[0]},{c[1]},{c[2]})" for c in colors)
    return (
        f"display:inline-block;width:{width}px;height:8px;border-radius:4px;"
        f"background:linear-gradient(90deg,{stops})"
    )


def render_map_status(coverage: maps.MapCoverage) -> None:
    rows = coverage.cohort_rows
    if coverage.reviewed_labels == 0:
        pending = (
            f" {coverage.proposed_labels} have proposed coordinates awaiting review."
            if coverage.proposed_labels
            else ""
        )
        st.warning(
            f"**Map not available yet.** 0 of {coverage.lookup_labels} location labels have "
            f"reviewed coordinates, so no booking can be placed on a map.{pending} Unreviewed "
            "labels are never drawn at default points; the views below use the same booking "
            "data as tables.",
            icon=":material/map:",
        )
    else:
        st.info(
            f"**Partial map — pilot coverage.** {coverage.reviewed_labels} of "
            f"{coverage.lookup_labels} location labels have reviewed coordinates; ambiguous "
            "labels such as MG Road and Khandsa stay unmapped. Only bookings with reviewed "
            "endpoints are drawn, but every booking still counts in the KPIs, charts and tables.",
            icon=":material/map:",
        )
    st.caption(
        f"Mappable records in this selection — pickups: "
        f"{charts.fmt_count(coverage.pickup_mapped_rows)} "
        f"({charts.fmt_pct(metrics.safe_rate(coverage.pickup_mapped_rows, rows))}), drops: "
        f"{charts.fmt_count(coverage.drop_mapped_rows)} "
        f"({charts.fmt_pct(metrics.safe_rate(coverage.drop_mapped_rows, rows))}), both endpoints "
        f"(flows): {charts.fmt_count(coverage.flow_mapped_rows)} "
        f"({charts.fmt_pct(metrics.safe_rate(coverage.flow_mapped_rows, rows))}) of "
        f"{rows:,} records."
    )


def render_flows(
    cohort: pd.DataFrame, points: pd.DataFrame, nodes: pd.DataFrame, selected: str | None
) -> None:
    scope_col, n_col = st.columns([3, 2], vertical_alignment="bottom")
    with scope_col:
        scope = st.segmented_control(
            "Flow outcome",
            list(FLOW_SCOPES),
            format_func=FLOW_SCOPES.get,
            default="requested",
            required=True,
            key="t3_flow_scope",
            help="Changes only which records form the connections — never the KPI or "
            "reliability denominators.",
        )
    with n_col:
        top_n = st.slider("Connections drawn", 5, 50, 20, step=5, key="t3_top_n")

    if not nodes.empty:
        origin = selected if selected in set(points["location"]) else None
        flows = maps.mapped_flows(cohort, points, scope, top_n, origin)
        if origin:
            section(
                f"Connections from {origin}",
                "Pickup scope: arcs leave the selected location. Clear the selection in the "
                "details panel to see all mapped connections.",
            )
        else:
            section(
                f"Top {len(flows.arcs)} connections between reviewed locations",
                "Click an arc or a location to inspect it. Directional: A → B and B → A are "
                "separate arcs.",
            )
        show_map(
            maps.flow_deck(flows, nodes, selected),
            "flows",
            alt="Map of origin–destination connections between reviewed Delhi-NCR localities",
        )
        legend(
            [
                (
                    _gradient([maps.ORIGIN_RGB, maps.DESTINATION_RGB]),
                    "Arc: pickup (blue) → drop (orange)",
                ),
                (
                    "display:inline-block;width:28px;height:4px;border-radius:2px;background:#888",
                    "Arc width = bookings",
                ),
                (_dot([33, 33, 31]), "Reviewed location"),
                (_dot(maps.SELECTED_RGB), "Selected"),
            ]
        )
        st.caption(
            f"Drawn: {len(flows.arcs)} connections representing {flows.rows_drawn:,} of "
            f"{flows.rows_in_scope:,} records in this view. Not drawn — beyond the top-{top_n} "
            f"limit: {flows.rows_not_drawn_top_n:,}; an endpoint without reviewed coordinates: "
            f"{flows.rows_unmapped:,}; same pickup and drop: {flows.same_location_rows:,}."
        )
        if flows.arcs.empty:
            st.info("No connection between reviewed locations matches this selection.")

    summary = maps.top_connections(cohort, scope, top_n)
    with st.expander("All connections — table view (includes unmapped locations)"):
        if summary.table.empty:
            st.caption("No connections in this selection.")
        else:
            st.dataframe(
                summary.table,
                hide_index=True,
                width="stretch",
                column_config={
                    "pickup_location": "From (pickup)",
                    "drop_location": "To (drop)",
                    "bookings": st.column_config.NumberColumn("Bookings", format="%d"),
                    "both_endpoints_mapped": st.column_config.CheckboxColumn("Mapped"),
                },
            )
            st.caption(
                f"Top {len(summary.table)} of {summary.distinct_pairs:,} directed pairs "
                f"({summary.rows_shown:,} of {summary.rows_in_scope:,} records). The busiest "
                f"pair has only {summary.largest_pair} records, so route-level rates are not "
                "reliable evidence."
            )


def render_hotspots(cohort: pd.DataFrame, nodes: pd.DataFrame, selected: str | None) -> None:
    endpoint = st.segmented_control(
        "Endpoint",
        list(ENDPOINTS),
        format_func=ENDPOINTS.get,
        default="pickup",
        required=True,
        key="t3_hot_endpoint",
    )
    table = maps.location_hotspots(cohort, endpoint)
    if table.empty:
        st.info("No locations in this selection.")
        return
    noun = ENDPOINTS[endpoint].lower()
    if not nodes.empty:
        section(
            f"{ENDPOINTS[endpoint]} demand at reviewed locations",
            "Circle area ∝ booking records (all outcomes). Locality-level concentration, "
            "not GPS hotspots.",
        )
        show_map(
            maps.hotspot_deck(nodes, endpoint, selected),
            f"hotspots_{endpoint}",
            alt=f"Map of {noun} booking volume at reviewed Delhi-NCR localities",
        )
        rgb = maps.ORIGIN_RGB if endpoint == "pickup" else maps.DESTINATION_RGB
        legend(
            [
                (_dot(rgb, 10), "Small"),
                (_dot(rgb, 18), f"Large = more {noun}s"),
                (_dot(maps.SELECTED_RGB), "Selected"),
            ]
        )
        mapped = int(table.loc[table["is_mapped"], "bookings"].sum())
        st.caption(
            f"Mapped: {mapped:,} of {int(table['bookings'].sum()):,} {noun} records; the rest "
            "are at locations without reviewed coordinates (see the chart below)."
        )
    section(
        f"Busiest {noun} locations — all labels",
        "Includes locations not yet on the map.",
    )
    top = table.head(15)
    chart(
        charts.ranked_bars(
            top["location"],
            top["bookings"],
            value_title="Bookings",
            color=charts.ORIGIN_COLOR if endpoint == "pickup" else charts.DESTINATION_COLOR,
        ),
        f"hotspots_{endpoint}",
    )
    busiest, quietest = table.iloc[0], table.iloc[-1]
    st.caption(
        f"{len(table)} locations. Busiest: {busiest['location']} "
        f"({busiest['bookings']:,}); quietest: {quietest['location']} ({quietest['bookings']:,})."
    )
    data_view(
        table,
        "View all locations",
        column_config={
            "share": st.column_config.NumberColumn("Share of bookings", format="percent"),
            "is_mapped": st.column_config.CheckboxColumn("Mapped"),
        },
    )


def render_reliability(cohort: pd.DataFrame, points: pd.DataFrame, selected: str | None) -> None:
    end_col, rate_col = st.columns(2, vertical_alignment="bottom")
    with end_col:
        endpoint = st.segmented_control(
            "Location role",
            list(ENDPOINTS),
            format_func=ENDPOINTS.get,
            default="pickup",
            required=True,
            key="t3_rel_endpoint",
        )
    with rate_col:
        rate = st.selectbox(
            "Failure rate",
            list(maps.RELIABILITY_RATES),
            format_func=maps.RELIABILITY_RATES.get,
            key="t3_rel_rate",
        )
    table = maps.reliability_table(cohort, endpoint, rate)
    minimum = metrics.RELIABILITY_MIN_DENOMINATOR
    label = maps.RELIABILITY_RATES[rate]
    if table.empty:
        st.info("No locations in this selection.")
        return
    on_map = table.loc[table["location"].isin(set(points["location"]))]
    if not on_map.empty:
        sufficient = table.loc[table["sufficient_sample"], rate]
        domain_max = max(0.5, float(sufficient.max()) if len(sufficient) else 0.5)
        section(
            f"{label} at reviewed {ENDPOINTS[endpoint].lower()} locations",
            "Circle size = booking records; color = rate over all outcomes (independent of the "
            "flow toggle).",
        )
        show_map(
            maps.reliability_deck(table, points, rate, label, domain_max, selected),
            f"reliability_{endpoint}_{rate}",
            alt=f"Map of {label.lower()} at reviewed Delhi-NCR localities",
        )
        legend(
            [
                (_gradient(maps.RATE_RAMP), f"0% → {domain_max:.0%}"),
                (_dot(maps.INSUFFICIENT_RGB), f"Fewer than {minimum} records"),
                (_dot(maps.SELECTED_RGB), "Selected"),
            ]
        )
    section(
        f"{label} by {ENDPOINTS[endpoint].lower()} location — all labels",
        f"Locations with fewer than {minimum} records are ranked last and labeled — a product "
        "guardrail, not a statistical test.",
    )
    ranked = table.loc[table["sufficient_sample"]].head(15)
    if ranked.empty:
        st.info(f"No location has at least {minimum} records in this selection.")
    else:
        chart(
            charts.ranked_bars(
                ranked["location"],
                ranked[rate],
                value_title=label,
                text=[charts.fmt_pct(v) for v in ranked[rate]],
                color=charts.STATUS_COLORS[metrics.STATUS_CANCELLED_BY_DRIVER],
            ),
            f"reliability_{endpoint}_{rate}",
        )
    view = table.assign(
        sample=table["sufficient_sample"].map({True: "OK", False: f"Below {minimum}"})
    )[["location", "bookings", rate, "completion_rate", "sample", "is_mapped"]]
    data_view(
        view,
        "View all locations",
        column_config={
            rate: st.column_config.NumberColumn(label, format="percent"),
            "completion_rate": st.column_config.NumberColumn("Completion rate", format="percent"),
            "is_mapped": st.column_config.CheckboxColumn("Mapped"),
        },
    )


def render_details(cohort: pd.DataFrame, mapped_connections: list[str]) -> None:
    section("Selection details", "Click the map or choose below.")
    st.session_state.setdefault("d_scope", "location")
    scope = st.segmented_control(
        "Detail scope",
        ["location", "connection"],
        format_func={"location": "Pickup location", "connection": "Connection"}.get,
        required=True,
        key="d_scope",
        label_visibility="collapsed",
    )
    if scope == "location":
        options = sorted(cohort["pickup_location"].dropna().unique())
        key, noun = "d_location", "pickup location"
    else:
        flows = metrics.od_flows(cohort).head(50)
        busiest = [
            connection_id(a, b)
            for a, b in zip(flows["pickup_location"], flows["drop_location"], strict=True)
        ]
        options = list(dict.fromkeys(busiest + mapped_connections))
        current = st.session_state.get("d_connection")
        if current and current not in options and " → " in current:
            a, b = current.split(" → ", 1)
            if ((cohort["pickup_location"] == a) & (cohort["drop_location"] == b)).any():
                options.append(current)
        key, noun = "d_connection", "connection"
    if clear_if_stale(key, options):
        st.caption("The previous selection is not in the current filters and was cleared.")
    choice = st.selectbox(
        f"Choose a {noun}", options, index=None, key=key, placeholder=f"Choose a {noun}…"
    )
    if choice is None:
        st.caption("Select a location or connection to see its outcomes and reasons.")
        return
    st.button("Clear selection", on_click=clear_details, icon=":material/close:")

    if scope == "location":
        rows = cohort.loc[cohort["pickup_location"] == choice]
        st.caption(
            f"**Pickup scope:** bookings picked up at {choice}, all outcomes, current filters."
        )
    else:
        pickup, drop = choice.split(" → ", 1)
        rows = cohort.loc[(cohort["pickup_location"] == pickup) & (cohort["drop_location"] == drop)]
        st.caption(f"**Route scope:** {choice} only, all outcomes, current filters.")

    k = metrics.booking_kpis(rows)
    a, b = st.columns(2)
    a.metric("Bookings", charts.fmt_count(k.total_bookings))
    b.metric("Completion", charts.fmt_pct(k.completion_rate))
    a.metric("Cancellation", charts.fmt_pct(k.cancellation_rate))
    b.metric("No driver found", charts.fmt_pct(k.no_driver_rate))
    if k.total_bookings < metrics.RELIABILITY_MIN_DENOMINATOR:
        st.caption(
            f"Fewer than {metrics.RELIABILITY_MIN_DENOMINATOR} records — treat these rates as "
            "indicative only."
        )

    if scope == "location":
        destinations = metrics.top_locations(rows, "drop", n=5)
        st.markdown("**Top destinations**")
        st.dataframe(destinations, hide_index=True, width="stretch")

    for kind, label in (("customer", "Customer"), ("driver", "Driver")):
        reasons = metrics.cancellation_reasons(rows, kind)
        st.markdown(f"**{label} cancellation reasons**")
        if reasons.with_reason == 0:
            st.caption("None in this scope.")
            continue
        st.caption(
            f"Share of {reasons.with_reason:,} {label.lower()} cancellations with a recorded "
            f"reason (of {reasons.status_records:,} {label.lower()} cancellations)."
        )
        st.dataframe(
            reasons.table,
            hide_index=True,
            width="stretch",
            column_config={
                "share_of_recorded_reasons": st.column_config.NumberColumn(
                    "Share", format="percent"
                )
            },
        )


def render_trip_explorer(cohort: pd.DataFrame, lookup: pd.DataFrame) -> None:
    st.caption(maps.MAP_DISCLAIMER)
    if cohort.empty:
        st.info(EMPTY_MESSAGE)
        return
    points = maps.reviewed_points(lookup)
    render_map_status(maps.map_coverage(cohort, lookup))
    mode = st.segmented_control(
        "Explorer mode", list(MODES), default=MODES[0], required=True, key="t3_mode"
    )
    nodes = maps.location_nodes(cohort, points) if len(points) else points
    location = st.session_state.get("d_location")
    in_location_scope = st.session_state.get("d_scope", "location") == "location"
    selected = (
        location
        if in_location_scope and location in set(cohort["pickup_location"].dropna())
        else None
    )
    all_mapped = maps.mapped_flows(cohort, points, "requested", top_n=10_000).arcs
    mapped_connections = [
        connection_id(a, b)
        for a, b in zip(
            all_mapped.get("pickup_location", []), all_mapped.get("drop_location", []), strict=True
        )
    ]

    main, details = st.columns([7, 3], gap="large")
    with main:
        if mode == "Trip flows":
            render_flows(cohort, points, nodes, selected)
        elif mode == "Demand hotspots":
            render_hotspots(cohort, nodes, selected)
        else:
            render_reliability(cohort, points, selected)
    with details, st.container(border=True):
        render_details(cohort, mapped_connections)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title=f"{TITLE} — {SUBTITLE}", page_icon="🚕", layout="wide")
    rides, lookup = load_or_stop()
    interval = metrics.coverage_interval(rides)
    if interval is None:
        st.error("The prepared data contains no parseable booking dates.")
        st.stop()

    defaults = filter_defaults(interval)
    filters, notes = render_sidebar(rides, defaults)
    cohort = metrics.filter_base_cohort(rides, filters)
    dates = metrics.eligible_dates(
        filters.start_date,
        filters.end_date,
        weekdays=filters.weekdays,
        coverage=interval,
        excluded_dates=metrics.coverage_gaps(rides),
    )

    st.title(TITLE)
    st.markdown(f"**{SUBTITLE}**")
    st.caption(
        "Independent educational analysis of a Delhi-NCR ride-booking dataset associated with "
        "a public Kaggle dataset — not an official Uber dashboard or live operational data. "
        "Booking times are treated as local time."
    )
    active = active_filter_summary(filters, interval)
    st.markdown(
        f"Showing **{len(cohort):,}** of {len(rides):,} booking records "
        f"({charts.fmt_pct(metrics.safe_rate(len(cohort), len(rides)))}) · "
        + ("Filters: " + " · ".join(active) if active else "No filters applied")
    )
    for note in notes:
        st.caption(note)

    overview, demand, explorer = st.tabs(["Executive Overview", "Rider & Demand", "Trip Explorer"])
    with overview:
        render_overview(cohort, dates)
    with demand:
        render_demand(cohort, dates, filters)
    with explorer:
        render_trip_explorer(cohort, lookup)


main()
