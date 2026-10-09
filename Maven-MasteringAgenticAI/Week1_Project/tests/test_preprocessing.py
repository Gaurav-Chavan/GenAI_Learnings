from __future__ import annotations

import hashlib

import pandas as pd
import pytest
from conftest import FAKE_SHA, prepared_from_rows, raw_frame, raw_row, run, write_raw_csv

import preprocessing
from preprocessing import (
    OutputPathError,
    SchemaError,
    StaleArtifactError,
    load_prepared,
    prepare_bookings,
    read_prepared_csv,
)


def test_identifiers_are_normalized_and_raw_text_is_preserved():
    rows = [
        raw_row(fields={"Booking ID": '"CNR1"', "Customer ID": '"CID1"'}),
        raw_row(fields={"Booking ID": "  CNR2 ", "Customer ID": "'CID2'"}),
        raw_row(fields={"Booking ID": "null", "Customer ID": '""'}),
    ]
    prepared = prepared_from_rows(rows)

    assert prepared["booking_id_raw"].tolist() == ['"CNR1"', "  CNR2 ", "null"]
    assert prepared["booking_id"].tolist()[:2] == ["CNR1", "CNR2"]
    assert prepared["customer_id"].tolist()[:2] == ["CID1", "CID2"]
    # Missing IDs are NA, never the string "nan" or an empty string.
    assert pd.isna(prepared.loc[2, "booking_id"])
    assert pd.isna(prepared.loc[2, "customer_id"])
    assert prepared["booking_id"].dtype == "string"


def test_repeated_booking_ids_are_retained_and_flagged():
    rows = [
        raw_row(fields={"Booking ID": '"CNR9"', "Date": "2024-01-01"}),
        raw_row(fields={"Booking ID": '"CNR9"', "Date": "2024-02-01"}),
        raw_row(fields={"Booking ID": '"CNR8"'}),
    ]
    prepared = prepared_from_rows(rows)

    assert len(prepared) == 3
    assert prepared["booking_id_occurrences"].tolist() == [2, 2, 1]
    assert prepared["is_repeated_booking_id"].tolist() == [True, True, False]


def test_exact_duplicate_rows_are_flagged_not_dropped():
    row = raw_row()
    prepared, details = prepare_bookings(raw_frame([row, dict(row), raw_row()]), FAKE_SHA)

    assert len(prepared) == 3
    assert prepared["is_exact_duplicate_row"].tolist() == [True, True, False]
    assert details["exact_duplicate_rows_beyond_first"] == 1
    assert prepared["source_record_key"].is_unique


def test_missing_numeric_values_stay_missing():
    prepared = prepared_from_rows(
        [raw_row("Cancelled by Driver"), raw_row("Completed", {"Booking Value": "100"})]
    )

    assert pd.isna(prepared.loc[0, "booking_value"])
    assert pd.isna(prepared.loc[0, "driver_rating"])
    assert prepared["booking_value"].dtype == "Float64"
    assert prepared["booking_value"].sum() == 100  # NA ignored, not counted as 0 rows
    assert prepared["booking_value"].notna().sum() == 1


def test_parse_failures_are_reported_and_rows_kept():
    rows = [
        raw_row(fields={"Date": "2024-13-45", "Time": "25:99:00", "Booking Value": "abc"}),
        raw_row(),
    ]
    prepared, details = prepare_bookings(raw_frame(rows), FAKE_SHA)

    assert len(prepared) == 2
    assert details["parse_failures"]["Date"] == {"count": 1, "example_source_rows": [1]}
    assert details["parse_failures"]["Time"]["count"] == 1
    assert details["parse_failures"]["Booking Value"]["count"] == 1
    assert pd.isna(prepared.loc[0, "booking_date"])
    assert pd.isna(prepared.loc[0, "booking_value"])
    issues = prepared.loc[0, "quality_issues"].split(";")
    assert {"date_parse_failed", "time_parse_failed", "booking_value_parse_failed"} <= set(issues)
    assert pd.isna(prepared.loc[1, "quality_issues"])


def test_status_labels_normalized_and_unexpected_status_kept():
    rows = [
        raw_row(fields={"Booking Status": "  completed "}),
        raw_row(fields={"Booking Status": "Pending"}),
    ]
    prepared = prepared_from_rows(rows)

    assert prepared["booking_status"].tolist() == ["Completed", "Pending"]
    assert prepared["is_completed"].tolist() == [True, False]
    assert prepared["is_known_status"].tolist() == [True, False]
    outcome_cols = list(preprocessing.OUTCOME_FLAGS) + ["is_cancelled"]
    assert not prepared.loc[1, outcome_cols].any()
    assert "unexpected_status" in prepared.loc[1, "quality_issues"]


def test_outcome_flags_and_cancellation_definition():
    statuses = [
        "Completed",
        "Cancelled by Customer",
        "Cancelled by Driver",
        "No Driver Found",
        "Incomplete",
    ]
    prepared = prepared_from_rows([raw_row(s) for s in statuses])

    assert prepared["is_cancelled"].tolist() == [False, True, True, False, False]
    assert prepared["is_no_driver_found"].tolist() == [False, False, False, True, False]
    assert prepared["is_incomplete"].tolist() == [False, False, False, False, True]


def test_exception_flags_validated_against_status():
    rows = [
        raw_row("Cancelled by Customer"),  # consistent
        raw_row("Cancelled by Customer", {"Cancelled Rides by Customer": "null"}),  # missing flag
        raw_row("Completed", {"Cancelled Rides by Customer": "1"}),  # flag on wrong status
        raw_row("Completed"),  # flag missing off-status: missing, not zero
    ]
    _, details = prepare_bookings(raw_frame(rows), FAKE_SHA)
    check = details["flag_validation"]["Cancelled Rides by Customer"]

    assert check["status_rows"] == 2
    assert check["status_rows_flag_1"] == 1
    assert check["status_rows_flag_missing"] == 1
    assert check["other_rows_flag_1"] == 1
    assert check["other_rows_flag_0"] == 0
    assert check["other_rows_flag_missing"] == 1
    assert check["mismatched_rows"] == 2


def test_time_fields_are_derived_without_timezone_conversion():
    prepared = prepared_from_rows([raw_row(fields={"Date": "2024-01-06", "Time": "23:15:30"})])
    row = prepared.iloc[0]

    assert row["booking_ts"] == pd.Timestamp("2024-01-06 23:15:30")
    assert row["booking_date"] == pd.Timestamp("2024-01-06")
    assert row["weekday_num"] == 5
    assert row["weekday"] == "Saturday"
    assert row["hour"] == 23
    assert row["is_weekend"]
    assert row["year_month"] == "2024-01"


def test_source_record_key_is_deterministic():
    rows = [raw_row(), raw_row()]
    first = prepared_from_rows(rows)["source_record_key"].tolist()
    second = prepared_from_rows(rows)["source_record_key"].tolist()

    assert first == second == [f"{FAKE_SHA[:16]}:1", f"{FAKE_SHA[:16]}:2"]


def test_missing_expected_column_is_a_schema_error(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("Date,Time\n2024-01-01,08:00:00\n", encoding="utf-8")

    with pytest.raises(SchemaError, match="missing expected columns"):
        preprocessing.read_raw(path)


def test_end_to_end_run_leaves_source_unchanged_and_reloads_identically(project):
    write_raw_csv(project["input"], [raw_row(), raw_row("No Driver Found"), raw_row("Incomplete")])
    before = project["input"].read_bytes()

    report = run(project)

    assert project["input"].read_bytes() == before
    assert report["source"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert [step["rows"] for step in report["row_counts"]] == [3, 3, 3, 3]
    for path in (
        project["prepared"],
        project["lookup"],
        project["reports"] / "data_quality_report.json",
        project["reports"] / "data_quality_report.md",
    ):
        assert path.exists()

    loaded = load_prepared(
        project["prepared"], source_path=project["input"], lookup_path=project["lookup"]
    )
    assert len(loaded) == 3
    assert loaded["booking_id"].dtype == "string"
    assert loaded["booking_value"].dtype == "Float64"
    assert loaded["is_completed"].dtype == bool
    assert loaded["booking_date"].dtype == preprocessing.DATETIME_DTYPE
    assert pd.isna(loaded.loc[1, "booking_value"])
    assert loaded["booking_id_raw"].tolist()[0].startswith('"')


def test_reload_round_trip_with_missing_and_failed_values(project):
    rows = [
        raw_row(fields={"Date": "not-a-date", "Customer ID": "null"}),
        raw_row("Cancelled by Driver"),
        raw_row(fields={"Booking ID": ""}),
    ]
    write_raw_csv(project["input"], rows)
    run(project)  # run_preparation itself asserts the reload is identical

    reloaded = read_prepared_csv(project["prepared"])
    assert reloaded.loc[2, "booking_id_raw"] == ""  # verbatim raw text, not NA
    assert pd.isna(reloaded.loc[2, "booking_id"])
    assert pd.isna(reloaded.loc[0, "is_weekend"])


@pytest.mark.parametrize("target", ["prepared", "lookup"])
def test_outputs_cannot_overwrite_the_raw_input(project, target):
    write_raw_csv(project["input"], [raw_row()])
    before = project["input"].read_bytes()
    paths = dict(project)
    paths[target] = project["input"]

    with pytest.raises(OutputPathError, match="overwrite the raw input"):
        run(paths)
    assert project["input"].read_bytes() == before


def test_changed_lookup_or_prepared_file_is_detected_as_stale(project):
    write_raw_csv(project["input"], [raw_row()])
    run(project)

    lookup_text = project["lookup"].read_text(encoding="utf-8")
    project["lookup"].write_text(lookup_text.replace("not yet sourced", "edited"), encoding="utf-8")
    with pytest.raises(StaleArtifactError, match="lookup changed"):
        load_prepared(project["prepared"], lookup_path=project["lookup"])

    with project["prepared"].open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(StaleArtifactError, match="prepared file changed"):
        load_prepared(project["prepared"])


def test_changed_source_is_detected_as_stale(project):
    write_raw_csv(project["input"], [raw_row()])
    run(project)
    write_raw_csv(project["input"], [raw_row(), raw_row()])

    with pytest.raises(StaleArtifactError, match="source CSV changed"):
        load_prepared(project["prepared"], source_path=project["input"])
