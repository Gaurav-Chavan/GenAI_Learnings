"""Location lookup and geographic join checks. Coordinates here are synthetic test values."""

from __future__ import annotations

import pandas as pd
import pytest
from conftest import (
    lookup_frame,
    prepared_from_rows,
    reviewed,
    run,
    trip,
    unresolved,
    write_raw_csv,
)

from metrics import booking_kpis
from preprocessing import (
    LOOKUP_COLUMNS,
    LookupValidationError,
    attach_locations,
    geographic_coverage,
    update_lookup,
    validate_lookup,
)


def test_initial_lookup_lists_every_label_unresolved_with_blank_coordinates(project):
    write_raw_csv(project["input"], [trip("Alpha", "Beta"), trip("Gamma", "Alpha")])
    run(project)

    lookup = pd.read_csv(project["lookup"], dtype=str, keep_default_na=False)
    assert list(lookup.columns) == list(LOOKUP_COLUMNS)
    assert lookup["original_location"].tolist() == ["Alpha", "Beta", "Gamma"]
    assert set(lookup["review_status"]) == {"unresolved"}
    assert (lookup["latitude"] == "").all() and (lookup["longitude"] == "").all()


def test_rerun_preserves_reviewed_rows_and_appends_new_labels(project):
    write_raw_csv(project["input"], [trip("Alpha", "Beta")])
    project["lookup"].parent.mkdir(parents=True)
    existing = lookup_frame([reviewed("Alpha", "28.55", "77.25"), unresolved("Beta")])
    existing.to_csv(project["lookup"], index=False)
    run(project)

    write_raw_csv(project["input"], [trip("Alpha", "Beta"), trip("Delta", "Alpha")])
    report = run(project)

    lookup = pd.read_csv(project["lookup"], dtype=str, keep_default_na=False)
    assert lookup.iloc[0].to_dict() == reviewed("Alpha", "28.55", "77.25")
    assert lookup["original_location"].tolist() == ["Alpha", "Beta", "Delta"]
    assert lookup.loc[2, "review_status"] == "unresolved"
    assert report["geography"]["lookup"]["labels_added_this_run"] == 1


def test_lookup_rows_for_labels_no_longer_in_source_are_kept(project):
    write_raw_csv(project["input"], [trip("Alpha", "Beta")])
    project["lookup"].parent.mkdir(parents=True)
    lookup_frame([unresolved("Alpha"), unresolved("Beta"), unresolved("Retired")]).to_csv(
        project["lookup"], index=False
    )
    report = run(project)

    assert report["geography"]["lookup"]["labels_in_lookup_not_in_source"] == ["Retired"]
    assert len(pd.read_csv(project["lookup"])) == 3


@pytest.mark.parametrize("second_key", ["Alpha", "Alpha  ", " Alpha"])
def test_duplicate_lookup_keys_are_rejected(second_key):
    lookup = lookup_frame([unresolved("Alpha"), unresolved(second_key)])

    with pytest.raises(LookupValidationError, match="duplicate original_location"):
        validate_lookup(lookup)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({**reviewed("A"), "coordinate_source": ""}, "need coordinates and a coordinate_source"),
        ({**reviewed("A"), "latitude": "", "longitude": ""}, "need coordinates"),
        (reviewed("A", "19.07", "72.87"), "outside the study-area box"),
        ({**unresolved("A"), "latitude": "28.6", "longitude": "77.2"}, "must have blank"),
        ({**reviewed("A"), "longitude": ""}, "both or neither"),
        ({**reviewed("A"), "latitude": "north"}, "non-numeric"),
        ({**unresolved("A"), "review_status": "approved"}, "review_status must be one of"),
    ],
)
def test_invalid_lookup_rows_are_rejected(row, message):
    with pytest.raises(LookupValidationError, match=message):
        validate_lookup(lookup_frame([row]))


def test_join_keeps_row_count_and_maps_only_reviewed_labels():
    prepared = prepared_from_rows(
        [trip("Alpha", "Beta"), trip("Beta", "Gamma"), trip("Gamma", "Unknown")]
    )
    lookup = lookup_frame(
        [
            reviewed("Alpha"),
            {
                **unresolved("Beta"),
                "review_status": "proposed",
                "latitude": "28.7",
                "longitude": "77.1",
                "coordinate_source": "candidate",
            },
            unresolved("Gamma"),
        ]
    )

    joined = attach_locations(prepared, lookup)

    assert len(joined) == len(prepared)
    assert joined["source_record_key"].tolist() == prepared["source_record_key"].tolist()
    assert joined["pickup_is_mapped"].tolist() == [True, False, False]
    assert joined["drop_is_mapped"].tolist() == [False, False, False]
    assert joined.loc[0, "pickup_latitude"] == 28.6
    assert joined["pickup_review_status"].tolist() == ["reviewed", "proposed", "unresolved"]
    assert pd.isna(joined.loc[2, "drop_review_status"])  # label absent from lookup


def test_join_rejects_a_lookup_that_would_multiply_rows():
    prepared = prepared_from_rows([trip("Alpha", "Beta")])
    lookup = lookup_frame([reviewed("Alpha"), reviewed("Alpha", "28.7", "77.3")])

    with pytest.raises(LookupValidationError):
        attach_locations(prepared, lookup)


def test_layer_coverage_uses_the_relevant_endpoints():
    prepared = prepared_from_rows(
        [
            trip("Alpha", "Beta"),  # both mapped -> flow eligible
            trip("Alpha", "Gamma"),  # pickup only
            trip("Gamma", "Alpha"),  # drop only
            trip("Gamma", "Gamma"),  # neither
        ]
    )
    lookup = lookup_frame(
        [reviewed("Alpha"), reviewed("Beta", "28.5", "77.0"), unresolved("Gamma")]
    )
    joined = attach_locations(prepared, lookup)
    coverage = geographic_coverage(joined)

    assert coverage["pickup_points"]["eligible_rows"] == 2
    assert coverage["drop_points"]["eligible_rows"] == 2
    assert coverage["flows_both_endpoints"]["eligible_rows"] == 1
    assert coverage["flows_both_endpoints"]["share_of_rows"] == 0.25


def test_unmapped_rows_stay_in_non_geographic_totals():
    prepared = prepared_from_rows([trip("Alpha", "Beta"), trip("Gamma", "Gamma", "Incomplete")])
    joined = attach_locations(
        prepared, lookup_frame([reviewed("Alpha"), unresolved("Beta"), unresolved("Gamma")])
    )
    kpis = booking_kpis(joined)

    assert kpis.total_bookings == 2
    assert kpis.completion_rate == 0.5


def test_update_lookup_without_existing_file_sorts_new_labels():
    lookup, added = update_lookup(None, {"Beta", "Alpha"})

    assert added == ["Alpha", "Beta"]
    assert lookup["review_status"].tolist() == ["unresolved", "unresolved"]
