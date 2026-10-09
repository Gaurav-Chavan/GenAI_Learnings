"""Trip Explorer aggregation and map construction checks.

Coordinates here are synthetic test values (A near Delhi, B near Panipat)."""

from __future__ import annotations

import json
import tomllib

import pandas as pd
import pytest
from conftest import lookup_frame, prepared_from_rows, reviewed, trip, unresolved

import maps
from preprocessing import PROJECT_ROOT, attach_locations

LOOKUP = [reviewed("A", "28.60", "77.20"), reviewed("B", "29.40", "76.98"), unresolved("C")]


@pytest.fixture
def cohort() -> pd.DataFrame:
    rows = (
        [trip("A", "B")] * 3
        + [trip("B", "A")] * 2
        + [trip("A", "C", "Cancelled by Driver")] * 1
        + [trip("C", "C")] * 1  # same location
    )
    lookup = lookup_frame([reviewed("A"), reviewed("B", "28.5", "77.0"), unresolved("C")])
    return attach_locations(prepared_from_rows(rows), lookup)


def test_map_coverage_counts_reviewed_labels_and_layer_rows(cohort):
    lookup = lookup_frame([reviewed("A"), reviewed("B", "28.5", "77.0"), unresolved("C")])
    coverage = maps.map_coverage(cohort, lookup)

    assert (coverage.lookup_labels, coverage.reviewed_labels) == (3, 2)
    assert coverage.proposed_labels == 0
    assert coverage.cohort_rows == 7
    assert coverage.pickup_mapped_rows == 6  # A x4, B x2
    assert coverage.drop_mapped_rows == 5  # B x3, A x2
    assert coverage.flow_mapped_rows == 5  # A->B x3, B->A x2
    assert coverage.has_mappable_rows


def test_top_connections_separates_top_n_unmapped_and_same_location(cohort):
    summary = maps.top_connections(cohort, "requested", top_n=1)

    assert summary.table[["pickup_location", "drop_location", "bookings"]].values.tolist() == [
        ["A", "B", 3]
    ]
    assert summary.table["both_endpoints_mapped"].tolist() == [True]
    assert summary.rows_in_scope == 7
    assert summary.distinct_pairs == 4
    assert summary.rows_shown == 3
    assert summary.rows_beyond_top_n == 3  # B->A x2 and A->C x1; same-location excluded
    assert summary.rows_unmapped == 2  # A->C and C->C lack a mapped endpoint
    assert summary.same_location_rows == 1
    assert summary.largest_pair == 3


def test_completed_flow_scope_does_not_change_cohort(cohort):
    summary = maps.top_connections(cohort, "completed", top_n=10)

    assert summary.rows_in_scope == 6  # the driver cancellation is excluded from flows only
    assert ("A", "C") not in set(
        zip(summary.table["pickup_location"], summary.table["drop_location"], strict=True)
    )
    assert len(cohort) == 7


def test_top_connections_rejects_non_positive_top_n(cohort):
    with pytest.raises(ValueError):
        maps.top_connections(cohort, "requested", top_n=0)


def test_location_hotspots_count_each_endpoint(cohort):
    pickups = maps.location_hotspots(cohort, "pickup")
    drops = maps.location_hotspots(cohort, "drop")

    assert pickups[["location", "bookings"]].values.tolist() == [["A", 4], ["B", 2], ["C", 1]]
    assert pickups["is_mapped"].tolist() == [True, True, False]
    assert drops.set_index("location")["bookings"].to_dict() == {"B": 3, "A": 2, "C": 2}
    assert pickups["share"].sum() == pytest.approx(1.0)


def test_reliability_ranks_small_samples_last():
    rows = [trip("Big", "X", "Cancelled by Customer")] * 10 + [trip("Big", "X")] * 40
    rows += [trip("Tiny", "X", "Cancelled by Driver")] * 2
    table = maps.reliability_table(
        prepared_from_rows(rows).assign(pickup_is_mapped=False, drop_is_mapped=False),
        "pickup",
        "cancellation_rate",
    )

    # Tiny has a 100% cancellation rate but only 2 records, so it must not lead.
    assert table["location"].tolist() == ["Big", "Tiny"]
    assert table["sufficient_sample"].tolist() == [True, False]
    assert table.loc[0, "cancellation_rate"] == pytest.approx(0.2)


@pytest.fixture
def points() -> pd.DataFrame:
    return maps.reviewed_points(lookup_frame(LOOKUP))


def test_reviewed_points_exclude_unreviewed_labels(points):
    assert points["location"].tolist() == ["A", "B"]
    assert points[["latitude", "longitude"]].values.tolist() == [[28.6, 77.2], [29.4, 76.98]]


def test_mapped_flows_count_drawn_top_n_unmapped_and_same_location(cohort, points):
    # cohort: A->B x3, B->A x2, A->C x1 (C unmapped), C->C x1 (unmapped, same label)
    flows = maps.mapped_flows(cohort, points, "requested", top_n=1)

    assert flows.arcs[["pickup_location", "drop_location", "bookings"]].values.tolist() == [
        ["A", "B", 3]
    ]
    assert flows.rows_in_scope == 7
    assert flows.rows_mapped == 5  # A->B and B->A
    assert flows.rows_drawn == 3
    assert flows.rows_not_drawn_top_n == 2  # B->A, mapped but beyond top-1
    assert flows.rows_unmapped == 2  # A->C and C->C
    assert flows.same_location_rows == 0  # C->C is unmapped, not a mapped self-loop
    assert flows.mapped_pairs == 2
    arc = flows.arcs.iloc[0]
    assert (arc["src_lat"], arc["dst_lat"]) == (28.6, 29.4)
    assert arc["width"] == 10.0  # thickest arc
    assert 85 < arc["distance_km"] < 95  # synthetic Delhi -> Panipat-like distance


def test_mapped_flows_origin_drill_down_and_completed_scope(cohort, points):
    from_b = maps.mapped_flows(cohort, points, "requested", top_n=10, origin="B")
    assert from_b.arcs["pickup_location"].unique().tolist() == ["B"]
    assert from_b.rows_in_scope == 2

    completed = maps.mapped_flows(cohort, points, "completed", top_n=10)
    assert completed.rows_in_scope == 6  # the driver cancellation A->C is excluded
    assert completed.rows_mapped == 5


def test_mapped_flows_without_reviewed_points_draws_nothing(cohort):
    flows = maps.mapped_flows(
        cohort, maps.reviewed_points(lookup_frame([unresolved("A")])), "requested", top_n=20
    )
    assert flows.arcs.empty
    assert flows.rows_unmapped == len(cohort)


def test_fit_view_keeps_all_points_including_distant_ones(points):
    view = maps.fit_view(points["latitude"], points["longitude"])

    assert view["latitude"] == pytest.approx(29.0)
    assert 76.98 < view["longitude"] < 77.2
    # At this zoom the visible latitude span (560 px) must exceed the 0.8 degree spread.
    visible_lat = 560 * 360 / (256 * 2 ** view["zoom"]) * 0.875
    assert visible_lat > 0.8
    assert maps.fit_view(pd.Series([], dtype=float), pd.Series([], dtype=float))["zoom"] > 0


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (None, None),
        ({"selection": {"indices": {}, "objects": {}}}, None),
        (
            {"selection": {"objects": {"flows": [{"pickup_location": "A", "drop_location": "B"}]}}},
            ("connection", ("A", "B")),
        ),
        ({"selection": {"objects": {"locations": [{"location": "B"}]}}}, ("location", "B")),
        ({"selection": {"objects": {"reliability": [{"location": "A"}]}}}, ("location", "A")),
        ({"selection": {"objects": {"labels": [{"location": "A"}]}}}, None),
    ],
)
def test_selection_target_maps_clicks_to_detail_scopes(state, expected):
    assert maps.selection_target(state) == expected


def test_flow_deck_uses_osm_basemap_explicit_ids_and_plain_json(cohort, points):
    flows = maps.mapped_flows(cohort, points, "requested", top_n=10)
    nodes = maps.location_nodes(cohort, points)
    spec = json.loads(maps.flow_deck(flows, nodes, selected="A").to_json())

    assert spec["mapStyle"] == maps.BASEMAP_STYLE_URL
    assert spec["mapProvider"] == "maplibre"
    assert "cartocdn" not in json.dumps(spec)
    layers = {layer["id"]: layer for layer in spec["layers"]}
    assert set(layers) == {"flows", "locations", "labels"}
    assert layers["flows"]["pickable"] and layers["locations"]["pickable"]
    assert len(layers["flows"]["data"]) == 2  # A->B and B->A
    selected = next(n for n in layers["locations"]["data"] if n["location"] == "A")
    assert selected["fill"][:3] == maps.SELECTED_RGB
    for record in layers["locations"]["data"]:
        assert {"tt_title", "tt_line1", "tt_line2", "tt_line3"} <= set(record)


def test_only_data_accessors_are_serialized_as_expressions(cohort, points):
    # Regression: pydeck turns bare strings into "@@=" expressions; widthUnits="@@=pixels"
    # rendered arcs as giant fans and radiusUnits="@@=pixels" hid the location dots.
    nodes = maps.location_nodes(cohort, points)
    table = maps.reliability_table(cohort, "pickup", "cancellation_rate")
    decks = [
        maps.flow_deck(maps.mapped_flows(cohort, points, "requested", 10), nodes),
        maps.hotspot_deck(nodes, "pickup"),
        maps.reliability_deck(table, points, "cancellation_rate", "Cancellation", 0.5),
    ]
    for deck in decks:
        for layer in json.loads(deck.to_json())["layers"]:
            for prop, value in layer.items():
                if isinstance(value, str) and value.startswith("@@="):
                    assert prop.startswith("get"), f"{layer['id']}.{prop} = {value!r}"
            for prop in ("widthUnits", "radiusUnits"):
                if prop in layer:
                    assert layer[prop] == "pixels"


def test_reliability_deck_greys_out_small_samples(points):
    rows = [trip("A", "B", "Cancelled by Driver")] * 10 + [trip("A", "B")] * 40
    rows += [trip("B", "A", "Cancelled by Driver")] * 2
    cohort = attach_locations(prepared_from_rows(rows), lookup_frame(LOOKUP))
    table = maps.reliability_table(cohort, "pickup", "cancellation_rate")
    spec = json.loads(
        maps.reliability_deck(table, points, "cancellation_rate", "Cancellation", 0.5).to_json()
    )
    data = {d["location"]: d for d in spec["layers"][0]["data"]}

    assert data["B"]["fill"][:3] == maps.INSUFFICIENT_RGB  # 2 records < 50
    assert data["A"]["fill"][:3] != maps.INSUFFICIENT_RGB
    assert "low sample" in data["B"]["tt_line2"]


def test_basemap_style_and_static_serving_are_configured():
    style = json.loads((PROJECT_ROOT / "static" / "osm_basemap_style.json").read_text("utf-8"))
    source = style["sources"]["osm"]
    config = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text("utf-8"))

    assert source["tiles"] == ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"]
    assert "OpenStreetMap" in source["attribution"]
    assert maps.BASEMAP_STYLE_URL == "app/static/osm_basemap_style.json"
    assert config["server"]["enableStaticServing"] is True


def test_reliability_rejects_unknown_rate(cohort):
    with pytest.raises(ValueError):
        maps.reliability_table(cohort, "pickup", "completion_rate")
