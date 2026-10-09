"""Geographic aggregation for the Trip Explorer.

Builds on metrics.py and the prepared `*_is_mapped` / coordinate columns. A booking
endpoint is mappable only when its lookup row is reviewed and has coordinates;
unmapped records are counted and reported, never placed at a default point.

Coordinates are representative locality points: connections show origin-destination
relationships, not actual roads traveled. Only `reviewed` lookup rows are drawn.

The basemap is OpenStreetMap's standard raster tiles, referenced through a small style
file served from ./static (see .streamlit/config.toml). Pydeck's default CARTO style is
deliberately not used: CARTO's current terms require the user's own API key.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pydeck as pdk

import metrics
from metrics import Endpoint, FlowScope

MAP_DISCLAIMER = (
    "Representative locality coordinates. Connections show origin–destination "
    "relationships, not actual roads traveled."
)
BASEMAP_STYLE_URL = "app/static/osm_basemap_style.json"
BASEMAP_ATTRIBUTION = "Basemap © OpenStreetMap contributors (openstreetmap.org/copyright)"

ORIGIN_RGB = [42, 120, 214]  # pickup / origin (blue)
DESTINATION_RGB = [235, 104, 52]  # drop / destination (orange)
SELECTED_RGB = [250, 178, 25]
INSUFFICIENT_RGB = [137, 135, 129]
RATE_RAMP = [  # one-hue (red) sequential ramp, light -> dark, for failure rates
    [253, 219, 199],
    [244, 165, 130],
    [214, 96, 77],
    [178, 24, 43],
    [103, 0, 31],
]
TOOLTIP = {
    "html": "<b>{tt_title}</b><br/>{tt_line1}<br/>{tt_line2}<br/>{tt_line3}",
    "style": {
        "backgroundColor": "rgba(20, 20, 19, 0.92)",
        "color": "#ffffff",
        "fontSize": "12px",
        "fontFamily": 'system-ui, -apple-system, "Segoe UI", sans-serif',
        "padding": "8px 10px",
        "borderRadius": "6px",
    },
}
LAYER_LOCATION_KIND = {"locations": "location", "hotspots": "location", "reliability": "location"}

RELIABILITY_RATES = {
    "cancellation_rate": "Cancellation rate (customer + driver)",
    "no_driver_rate": "No-driver-found rate",
    "incomplete_rate": "Incomplete rate",
}


@dataclass(frozen=True)
class MapCoverage:
    lookup_labels: int
    reviewed_labels: int
    proposed_labels: int  # sourced but not yet reviewed: never mapped
    cohort_rows: int
    pickup_mapped_rows: int
    drop_mapped_rows: int
    flow_mapped_rows: int

    @property
    def has_mappable_rows(self) -> bool:
        return self.pickup_mapped_rows > 0 or self.drop_mapped_rows > 0


def map_coverage(cohort: pd.DataFrame, lookup: pd.DataFrame) -> MapCoverage:
    """Reviewed lookup labels and the cohort rows each map layer could draw."""
    status = lookup["review_status"].str.strip()
    pickup = cohort["pickup_is_mapped"]
    drop = cohort["drop_is_mapped"]
    return MapCoverage(
        lookup_labels=len(lookup),
        reviewed_labels=int((status == "reviewed").sum()),
        proposed_labels=int((status == "proposed").sum()),
        cohort_rows=len(cohort),
        pickup_mapped_rows=int(pickup.sum()),
        drop_mapped_rows=int(drop.sum()),
        flow_mapped_rows=int((pickup & drop).sum()),
    )


@dataclass(frozen=True)
class ConnectionSummary:
    table: pd.DataFrame  # pickup_location, drop_location, bookings, both_endpoints_mapped
    scope: FlowScope
    rows_in_scope: int  # cohort rows counted by this flow scope
    distinct_pairs: int
    rows_shown: int  # records represented by the rows in `table`
    rows_beyond_top_n: int  # records in pairs not shown because of the top-N limit
    rows_unmapped: int  # records in scope lacking a mapped pickup or drop
    same_location_rows: int  # pickup == drop; never drawn as zero-length arcs
    largest_pair: int


def top_connections(cohort: pd.DataFrame, scope: FlowScope, top_n: int) -> ConnectionSummary:
    """Top-N directional pickup -> drop pairs with separate omission counts.

    `rows_beyond_top_n` (display limit) and `rows_unmapped` (missing coordinates)
    are reported separately; they can overlap and must not be summed.
    """
    if top_n < 1:
        raise ValueError(f"top_n must be at least 1, got {top_n}")
    flows = metrics.od_flows(cohort, scope)
    scoped = cohort if scope == "requested" else cohort.loc[cohort["is_completed"]]
    mapped = _mapped_labels(cohort)
    different = flows.loc[~flows["is_same_location"]]
    shown = different.head(top_n).copy()
    shown["both_endpoints_mapped"] = shown["pickup_location"].isin(mapped["pickup"]) & shown[
        "drop_location"
    ].isin(mapped["drop"])
    rows_in_pairs = int(different["bookings"].sum())
    return ConnectionSummary(
        table=shown.drop(columns="is_same_location").reset_index(drop=True),
        scope=scope,
        rows_in_scope=len(scoped),
        distinct_pairs=len(flows),
        rows_shown=int(shown["bookings"].sum()),
        rows_beyond_top_n=rows_in_pairs - int(shown["bookings"].sum()),
        rows_unmapped=int((~(scoped["pickup_is_mapped"] & scoped["drop_is_mapped"])).sum()),
        same_location_rows=int(flows.loc[flows["is_same_location"], "bookings"].sum()),
        largest_pair=int(flows["bookings"].max()) if len(flows) else 0,
    )


def location_hotspots(cohort: pd.DataFrame, endpoint: Endpoint) -> pd.DataFrame:
    """Booking counts per pickup or drop label (locality-level, not GPS-level)."""
    column = f"{endpoint}_location"
    rows = cohort.dropna(subset=[column])
    table = rows.groupby(column).size().rename("bookings").reset_index()
    table = table.rename(columns={column: "location"})
    table["share"] = table["bookings"] / len(cohort) if len(cohort) else pd.NA
    mapped = _mapped_labels(cohort)[endpoint]
    table["is_mapped"] = table["location"].isin(mapped)
    return table.sort_values(["bookings", "location"], ascending=[False, True]).reset_index(
        drop=True
    )


def reliability_table(
    cohort: pd.DataFrame,
    endpoint: Endpoint,
    rate: str,
    min_denominator: int = metrics.RELIABILITY_MIN_DENOMINATOR,
) -> pd.DataFrame:
    """Per-location outcome rates over all outcomes, ranked by `rate`.

    Locations below `min_denominator` sort after sufficient samples so a tiny
    denominator never tops the ranking.
    """
    if rate not in RELIABILITY_RATES:
        raise ValueError(f"rate must be one of {list(RELIABILITY_RATES)}, got {rate!r}")
    table = metrics.location_reliability(cohort, endpoint, min_denominator)
    table["is_mapped"] = table["location"].isin(_mapped_labels(cohort)[endpoint])
    return table.sort_values(
        ["sufficient_sample", rate, "bookings", "location"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)


def _mapped_labels(cohort: pd.DataFrame) -> dict[str, set[str]]:
    return {
        end: set(cohort.loc[cohort[f"{end}_is_mapped"], f"{end}_location"].dropna())
        for end in ("pickup", "drop")
    }


# ---------------------------------------------------------------------------
# Map data (reviewed coordinates only)
# ---------------------------------------------------------------------------


def reviewed_points(lookup: pd.DataFrame) -> pd.DataFrame:
    """Reviewed lookup rows with coordinates: location, latitude, longitude, city_or_zone."""
    status = lookup["review_status"].str.strip()
    lat = pd.to_numeric(lookup["latitude"].str.strip(), errors="coerce")
    lon = pd.to_numeric(lookup["longitude"].str.strip(), errors="coerce")
    keep = (status == "reviewed") & lat.notna() & lon.notna()
    return pd.DataFrame(
        {
            "location": lookup.loc[keep, "original_location"].str.strip().astype(str),
            "latitude": lat[keep].astype(float),
            "longitude": lon[keep].astype(float),
            "city_or_zone": lookup.loc[keep, "city_or_zone"].astype(str),
        }
    ).reset_index(drop=True)


@dataclass(frozen=True)
class MapFlows:
    arcs: pd.DataFrame  # drawn connections with coordinates, width and tooltip fields
    scope: FlowScope
    origin: str | None  # drill-down origin, or None for all mapped origins
    rows_in_scope: int  # records this view covers (scope + optional origin)
    rows_mapped: int  # records with both endpoints reviewed (excl. same-location)
    rows_drawn: int  # records represented by drawn arcs
    rows_not_drawn_top_n: int  # mapped records in connections beyond the top-N limit
    rows_unmapped: int  # records lacking a reviewed pickup or drop
    same_location_rows: int  # mapped records with pickup == drop (not drawn as arcs)
    mapped_pairs: int


def mapped_flows(
    cohort: pd.DataFrame,
    points: pd.DataFrame,
    scope: FlowScope,
    top_n: int,
    origin: str | None = None,
) -> MapFlows:
    """Top-N directional connections whose endpoints both have reviewed coordinates.

    Unmapped records and records beyond the top-N limit are counted separately;
    the two groups never overlap here (top-N only ever ranks mapped connections).
    """
    if top_n < 1:
        raise ValueError(f"top_n must be at least 1, got {top_n}")
    scoped = cohort if scope == "requested" else cohort.loc[cohort["is_completed"]]
    if origin is not None:
        scoped = scoped.loc[scoped["pickup_location"] == origin]
    labels = set(points["location"])
    both = scoped["pickup_location"].isin(labels) & scoped["drop_location"].isin(labels)
    flows = metrics.od_flows(scoped.loc[both], "requested")  # scope already applied
    same = flows.loc[flows["is_same_location"]]
    pairs = flows.loc[~flows["is_same_location"]]
    drawn = pairs.head(top_n)
    coords = points.set_index("location")
    arcs = drawn.assign(
        src_lat=drawn["pickup_location"].map(coords["latitude"]),
        src_lon=drawn["pickup_location"].map(coords["longitude"]),
        dst_lat=drawn["drop_location"].map(coords["latitude"]),
        dst_lon=drawn["drop_location"].map(coords["longitude"]),
    ).drop(columns="is_same_location")
    if len(arcs):
        top = int(arcs["bookings"].max())
        arcs["width"] = [round(1.5 + 8.5 * b / top, 2) for b in arcs["bookings"]]
        arcs["distance_km"] = [
            round(_haversine_km(a, b, c, d), 1)
            for a, b, c, d in zip(
                arcs["src_lat"], arcs["src_lon"], arcs["dst_lat"], arcs["dst_lon"], strict=True
            )
        ]
        arcs["tt_title"] = [
            f"{a} → {b}"
            for a, b in zip(arcs["pickup_location"], arcs["drop_location"], strict=True)
        ]
        noun = "completed bookings" if scope == "completed" else "bookings (all outcomes)"
        arcs["tt_line1"] = [f"{b:,} {noun}" for b in arcs["bookings"]]
        arcs["tt_line2"] = [f"Straight-line distance ≈ {d:,.1f} km" for d in arcs["distance_km"]]
        arcs["tt_line3"] = "Click to see this connection's details"
    mapped_rows = int(pairs["bookings"].sum())
    return MapFlows(
        arcs=arcs.reset_index(drop=True),
        scope=scope,
        origin=origin,
        rows_in_scope=len(scoped),
        rows_mapped=mapped_rows,
        rows_drawn=int(drawn["bookings"].sum()),
        rows_not_drawn_top_n=mapped_rows - int(drawn["bookings"].sum()),
        rows_unmapped=int((~both).sum()),
        same_location_rows=int(same["bookings"].sum()),
        mapped_pairs=len(pairs),
    )


def location_nodes(cohort: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Reviewed locations present in the cohort, with pickup/drop counts and pickup rates."""
    pickups = cohort["pickup_location"].value_counts()
    drops = cohort["drop_location"].value_counts()
    rates = metrics.location_reliability(cohort, "pickup").set_index("location")
    nodes = points.assign(
        pickups=points["location"].map(pickups).fillna(0).astype(int),
        drops=points["location"].map(drops).fillna(0).astype(int),
    )
    nodes = nodes.loc[(nodes["pickups"] + nodes["drops"]) > 0].reset_index(drop=True)
    nodes["completion_rate"] = nodes["location"].map(rates["completion_rate"])
    nodes["cancellation_rate"] = nodes["location"].map(rates["cancellation_rate"])
    return nodes


def fit_view(
    latitudes: pd.Series, longitudes: pd.Series, *, width_px: int = 800, height_px: int = 560
) -> dict[str, float]:
    """Center and zoom (web-mercator approximation) that fit all points with padding."""
    if len(latitudes) == 0:
        return {"latitude": 28.61, "longitude": 77.21, "zoom": 8.5}  # Delhi, empty map
    lat_lo, lat_hi = float(latitudes.min()), float(latitudes.max())
    lon_lo, lon_hi = float(longitudes.min()), float(longitudes.max())
    center_lat = (lat_lo + lat_hi) / 2
    pad = 1.6
    lat_span = max(lat_hi - lat_lo, 0.02)
    lon_span = max(lon_hi - lon_lo, 0.02)
    zoom_lat = math.log2(
        height_px * 360 * math.cos(math.radians(center_lat)) / (256 * lat_span * pad)
    )
    zoom_lon = math.log2(width_px * 360 / (256 * lon_span * pad))
    return {
        "latitude": center_lat,
        "longitude": (lon_lo + lon_hi) / 2,
        "zoom": round(min(max(min(zoom_lat, zoom_lon), 4.0), 12.0), 2),
    }


def selection_target(state: Mapping[str, Any] | None) -> tuple[str, Any] | None:
    """Translate a st.pydeck_chart selection into ("location", name) or
    ("connection", (pickup, drop)); None when nothing is selected."""
    if not state:
        return None
    objects = (state.get("selection") or {}).get("objects") or {}
    arcs = objects.get("flows") or []
    if arcs:
        return "connection", (arcs[0]["pickup_location"], arcs[0]["drop_location"])
    for layer in LAYER_LOCATION_KIND:
        picked = objects.get(layer) or []
        if picked:
            return "location", picked[0]["location"]
    return None


# ---------------------------------------------------------------------------
# Deck construction
# ---------------------------------------------------------------------------


def flow_deck(flows: MapFlows, nodes: pd.DataFrame, selected: str | None = None) -> pdk.Deck:
    """Arcs (blue pickup end -> orange drop end, width ~ bookings) over location points."""
    node_data = nodes.assign(
        radius=_scaled(nodes["pickups"] + nodes["drops"], 6, 15),
        fill=[(SELECTED_RGB if n == selected else [33, 33, 31]) + [235] for n in nodes["location"]],
        tt_title=nodes["location"] + " · " + nodes["city_or_zone"],
        tt_line1=[
            f"Pickups {p:,} · drops {d:,}"
            for p, d in zip(nodes["pickups"], nodes["drops"], strict=True)
        ],
        tt_line2=[_rate_line("Completion (pickups)", r) for r in nodes["completion_rate"]],
        tt_line3="Click to explore connections from here",
    )
    layers = [
        pdk.Layer(
            "ArcLayer",
            id="flows",
            data=_records(flows.arcs),
            get_source_position=["src_lon", "src_lat"],
            get_target_position=["dst_lon", "dst_lat"],
            get_source_color=ORIGIN_RGB + [230],
            get_target_color=DESTINATION_RGB + [230],
            get_width="width",
            width_units=_literal("pixels"),
            get_height=0.6,
            great_circle=False,
            pickable=True,
            auto_highlight=True,
            highlight_color=SELECTED_RGB + [255],
        ),
        _point_layer("locations", node_data, line_rgb=[255, 255, 255]),
        _label_layer(nodes),
    ]
    return _deck(layers, nodes)


def hotspot_deck(nodes: pd.DataFrame, endpoint: Endpoint, selected: str | None = None) -> pdk.Deck:
    """Locality points sized by pickup or drop bookings (area ~ count)."""
    column = "pickups" if endpoint == "pickup" else "drops"
    rgb = ORIGIN_RGB if endpoint == "pickup" else DESTINATION_RGB
    data = nodes.assign(
        radius=_scaled(nodes[column], 8, 34, sqrt=True),
        fill=[(SELECTED_RGB if n == selected else rgb) + [190] for n in nodes["location"]],
        tt_title=nodes["location"] + " · " + nodes["city_or_zone"],
        tt_line1=[f"{v:,} {column}" for v in nodes[column]],
        tt_line2="Locality-level concentration (not GPS)",
        tt_line3="Click for location details",
    )
    return _deck([_point_layer("hotspots", data), _label_layer(nodes)], nodes)


def reliability_deck(
    table: pd.DataFrame,
    points: pd.DataFrame,
    rate: str,
    rate_label: str,
    domain_max: float,
    selected: str | None = None,
) -> pdk.Deck:
    """Points sized by bookings and colored by the selected failure rate.

    Locations below the minimum denominator are grey. The color domain is fixed
    from 0 to `domain_max` so small differences are not stretched across the ramp.
    """
    data = table.merge(points, on="location", how="inner")
    data = data.assign(
        radius=_scaled(data["bookings"], 8, 26, sqrt=True),
        fill=[
            (
                SELECTED_RGB
                if loc == selected
                else (_ramp(value / domain_max) if ok else INSUFFICIENT_RGB)
            )
            + [215]
            for loc, value, ok in zip(
                data["location"], data[rate], data["sufficient_sample"], strict=True
            )
        ],
        tt_title=data["location"] + " · " + data["city_or_zone"],
        tt_line1=[_rate_line(rate_label, v) for v in data[rate]],
        tt_line2=[
            f"{b:,} bookings"
            + ("" if ok else f" — below {metrics.RELIABILITY_MIN_DENOMINATOR}, low sample")
            for b, ok in zip(data["bookings"], data["sufficient_sample"], strict=True)
        ],
        tt_line3=[_rate_line("Completion", v) for v in data["completion_rate"]],
    )
    return _deck([_point_layer("reliability", data), _label_layer(data)], data)


def _point_layer(layer_id: str, data: pd.DataFrame, line_rgb: list[int] | None = None) -> pdk.Layer:
    return pdk.Layer(
        "ScatterplotLayer",
        id=layer_id,
        data=_records(data),
        get_position=["longitude", "latitude"],
        get_radius="radius",
        radius_units=_literal("pixels"),
        get_fill_color="fill",
        stroked=True,
        get_line_color=(line_rgb or [255, 255, 255]) + [255],
        line_width_min_pixels=1.5,
        pickable=True,
        auto_highlight=True,
        highlight_color=SELECTED_RGB + [255],
    )


def _label_layer(data: pd.DataFrame) -> pdk.Layer:
    return pdk.Layer(
        "TextLayer",
        id="labels",
        data=_records(data[["location", "latitude", "longitude"]]),
        get_position=["longitude", "latitude"],
        get_text="location",
        get_size=13,
        get_color=[11, 11, 11, 255],
        get_pixel_offset=[0, -22],
        background=True,
        get_background_color=[255, 255, 255, 215],
        background_padding=[4, 2],
        font_family=_literal('system-ui, -apple-system, "Segoe UI", sans-serif'),
        font_weight=600,
        pickable=False,
    )


def _deck(layers: list[pdk.Layer], points: pd.DataFrame) -> pdk.Deck:
    view = fit_view(points["latitude"], points["longitude"])
    return pdk.Deck(
        layers=layers,
        initial_view_state=pdk.ViewState(**view, pitch=30, bearing=0),
        map_provider="maplibre",
        map_style=BASEMAP_STYLE_URL,
        tooltip=TOOLTIP,
    )


def _literal(value: str) -> pdk.types.String:
    """A plain string property. pydeck turns bare strings into `@@=` JavaScript
    expressions, which breaks settings such as `widthUnits="pixels"`."""
    return pdk.types.String(value)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Plain-Python records (no numpy/pandas scalars or NA) for JSON serialization."""
    records = []
    for row in frame.to_dict("records"):
        clean = {}
        for key, value in row.items():
            if isinstance(value, list):
                clean[key] = [
                    int(v) if isinstance(v, (int, float)) and float(v).is_integer() else v
                    for v in value
                ]
            elif value is None or (not isinstance(value, str) and pd.isna(value)):
                clean[key] = None
            elif hasattr(value, "item"):
                clean[key] = value.item()
            else:
                clean[key] = value
        records.append(clean)
    return records


def _scaled(values: pd.Series, low: float, high: float, *, sqrt: bool = False) -> list[float]:
    vals = [math.sqrt(v) if sqrt else float(v) for v in values]
    top = max(vals) if vals else 0
    return [round(low + (high - low) * (v / top), 2) if top else low for v in vals]


def _ramp(fraction: float) -> list[int]:
    fraction = min(max(fraction, 0.0), 1.0) * (len(RATE_RAMP) - 1)
    i = min(int(fraction), len(RATE_RAMP) - 2)
    t = fraction - i
    return [round(a + (b - a) * t) for a, b in zip(RATE_RAMP[i], RATE_RAMP[i + 1], strict=True)]


def _rate_line(label: str, value: float | None) -> str:
    return f"{label}: {'N/A' if value is None or pd.isna(value) else f'{value:.1%}'}"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))
