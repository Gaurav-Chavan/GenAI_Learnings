"""Headless Streamlit checks of app.py with streamlit.testing (AppTest).

These run the real script against the local prepared data, so they exercise widgets,
reruns and session state — but not a browser: tiles, layout, hover tooltips and visual
rendering are not covered here. Expected values come from the prior-audit baseline and
apply only when the prepared data was built from the audited source version.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import preprocessing

pytestmark = pytest.mark.real_data

APP = preprocessing.PROJECT_ROOT / "app.py"
BASELINE = json.loads(
    (Path(__file__).parent / "baselines" / "prior_audit_545118f7.json").read_text(encoding="utf-8")
)
HEATMAP_COLUMNS = {"weekday_num", "weekday", "hour", "bookings", "avg_bookings_per_day"}


def _unavailable_reason() -> str | None:
    problems = preprocessing.staleness_problems(
        preprocessing.DEFAULT_PREPARED, lookup_path=preprocessing.DEFAULT_LOOKUP
    )
    if problems:
        return "Prepared data unavailable or stale: " + "; ".join(problems)
    meta = json.loads(
        preprocessing.meta_path_for(preprocessing.DEFAULT_PREPARED).read_text(encoding="utf-8")
    )
    if meta["source_sha256"] != BASELINE["source_sha256"]:
        return "Prepared data comes from a different source version than the audited baseline"
    return None


@pytest.fixture
def app() -> AppTest:
    reason = _unavailable_reason()
    if reason:
        pytest.skip(reason)
    at = AppTest.from_file(str(APP), default_timeout=180).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def kpis(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def rerun(at: AppTest) -> AppTest:
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_unfiltered_kpis_match_the_audit(app):
    values = kpis(app)
    status = BASELINE["status_counts"]

    assert [t.label for t in app.tabs] == ["Executive Overview", "Rider & Demand", "Trip Explorer"]
    assert values["Total bookings"] == f"{BASELINE['rows']:,}"
    assert values["Unique customer IDs"] == f"{BASELINE['distinct_customer_ids']:,}"
    assert values["Completion rate"] == f"{BASELINE['completion_rate']:.1%}"
    assert values["Cancellation rate"] == f"{BASELINE['cancellation_rate']:.1%}"
    assert values["Customer cancellations"] == f"{status['Cancelled by Customer']:,}"
    assert values["Driver cancellations"] == f"{status['Cancelled by Driver']:,}"


def test_operational_insight_is_computed_from_outcomes(app):
    text = " ".join(m.value for m in app.markdown)
    # Driver cancellations (27,000) are the largest non-completion outcome:
    # 27,000 / 150,000 = 18.0%; 27,000 / 57,000 non-completed = 47.4%.
    assert "**Cancelled by Driver** is the largest non-completion outcome" in text
    assert "27,000 records (18.0% of all bookings, 47.4% of the 57,000 bookings" in text


def _reviewed_labels() -> list[str]:
    lookup = preprocessing.read_lookup(preprocessing.DEFAULT_LOOKUP)
    return sorted(lookup.loc[lookup["review_status"] == "reviewed", "original_location"])


def _deck_specs(at: AppTest) -> list[dict]:
    return [json.loads(e.proto.json) for e in at.get("deck_gl_json_chart")]


def test_map_status_reports_reviewed_coverage_honestly(app):
    reviewed = _reviewed_labels()
    banner = " ".join(i.value for i in app.info) + " ".join(w.value for w in app.warning)
    assert any("not actual roads traveled" in c.value for c in app.caption)
    if reviewed:
        assert f"{len(reviewed)} of 176 location labels have reviewed coordinates" in banner
    else:
        assert "Map not available yet" in banner


def test_connection_map_draws_only_reviewed_locations_on_osm(app):
    reviewed = set(_reviewed_labels())
    if not reviewed:
        pytest.skip("No reviewed coordinates: the map is intentionally not drawn")
    (spec,) = _deck_specs(app)
    layers = {layer["id"]: layer for layer in spec["layers"]}

    assert spec["mapStyle"] == "app/static/osm_basemap_style.json"
    assert "cartocdn" not in json.dumps(spec)
    assert {n["location"] for n in layers["locations"]["data"]} <= reviewed
    for arc in layers["flows"]["data"]:
        assert {arc["pickup_location"], arc["drop_location"]} <= reviewed
        assert arc["pickup_location"] != arc["drop_location"]
    assert 0 < len(layers["flows"]["data"]) <= 20  # default top-N
    caption = next(c.value for c in app.caption if c.value.startswith("Drawn:"))
    assert "beyond the top-20 limit" in caption and "without reviewed coordinates" in caption


def test_selecting_a_location_drills_the_map_down_to_its_connections(app):
    reviewed = _reviewed_labels()
    if not reviewed:
        pytest.skip("No reviewed coordinates")
    origin = reviewed[0]
    app.selectbox(key="d_location").select(origin)
    rerun(app)

    (spec,) = _deck_specs(app)
    arcs = next(layer for layer in spec["layers"] if layer["id"] == "flows")["data"]
    assert arcs and {a["pickup_location"] for a in arcs} == {origin}
    assert any(m.value == f"##### Connections from {origin}" for m in app.markdown)


@pytest.mark.parametrize("mode", ["Demand hotspots", "Service reliability"])
def test_other_map_modes_draw_reviewed_points(app, mode):
    if not _reviewed_labels():
        pytest.skip("No reviewed coordinates")
    app.segmented_control(key="t3_mode").set_value(mode)
    rerun(app)
    (spec,) = _deck_specs(app)
    assert {layer["id"] for layer in spec["layers"]} & {"hotspots", "reliability"}


def test_vehicle_filter_updates_every_tab(app):
    expected = BASELINE["vehicle_type_counts"]["Auto"]
    app.sidebar.multiselect(key="f_vehicles").select("Auto")
    rerun(app)

    assert kpis(app)["Total bookings"] == f"{expected:,}"
    heatmap = next(d.value for d in app.dataframe if HEATMAP_COLUMNS <= set(d.value.columns))
    assert int(heatmap["bookings"].sum()) == expected  # Rider & Demand tab
    flows_caption = next(c.value for c in app.caption if c.value.startswith("Drawn:"))
    assert f"of {expected:,} records in this view" in flows_caption  # Trip Explorer tab
    assert any("Vehicle: Auto" in m.value for m in app.markdown)  # active filter summary


def test_reset_restores_the_full_dataset(app):
    app.sidebar.multiselect(key="f_vehicles").select("Bike")
    app.sidebar.multiselect(key="f_weekdays").select(0)
    app.sidebar.slider(key="f_hours").set_range(8, 10)
    app.sidebar.multiselect(key="f_pickups").select("Saket")
    rerun(app)
    assert kpis(app)["Total bookings"] != f"{BASELINE['rows']:,}"

    app.sidebar.button[0].click()
    rerun(app)

    assert kpis(app)["Total bookings"] == f"{BASELINE['rows']:,}"
    assert app.sidebar.multiselect(key="f_vehicles").value == []
    assert app.sidebar.slider(key="f_hours").value == (0, 23)
    assert any("No filters applied" in m.value for m in app.markdown)


def test_empty_selection_shows_a_message_not_an_error(app):
    # No booking in this file starts and ends at the same label.
    app.sidebar.multiselect(key="f_pickups").select("Saket")
    app.sidebar.multiselect(key="f_drops").select("Saket")
    rerun(app)

    values = kpis(app)
    assert values["Total bookings"] == "0"
    assert values["Completion rate"] == "N/A"
    assert len(app.info) >= 3  # one per tab


def test_location_detail_and_stale_selection_clearing(app):
    expected = dict(BASELINE["top_pickups"])["Saket"]
    app.selectbox(key="d_location").select("Saket")
    rerun(app)
    assert kpis(app)["Bookings"] == f"{expected:,}"
    assert any("Pickup scope" in c.value for c in app.caption)

    app.sidebar.multiselect(key="f_pickups").select("Khandsa")
    rerun(app)
    assert app.selectbox(key="d_location").value is None
    assert any("was cleared" in c.value for c in app.caption)


@pytest.mark.parametrize("mode", ["Demand hotspots", "Service reliability", "Trip flows"])
def test_trip_explorer_modes_render(app, mode):
    app.segmented_control(key="t3_mode").set_value(mode)
    rerun(app)


def test_daily_trend_and_connection_detail_render(app):
    app.segmented_control(key="t1_freq").set_value("D")
    app.segmented_control(key="d_scope").set_value("connection")
    rerun(app)
    first = app.selectbox(key="d_connection").options[0]
    app.selectbox(key="d_connection").select(first)
    rerun(app)
    assert any("Route scope" in c.value for c in app.caption)
