"""Checks against the local ncr_ride_bookings.csv.

General invariants run whenever the CSV is present. The prior-audit regression runs only
when the local file has the audited SHA-256; otherwise it is skipped with the reason, so a
different source version is investigated rather than forced to match.
All outputs go to a temporary directory; the project's data/ and reports/ are not touched.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import metrics
from preprocessing import PROJECT_ROOT, read_prepared_csv, run_preparation

pytestmark = pytest.mark.real_data

REAL_CSV = PROJECT_ROOT / "ncr_ride_bookings.csv"
BASELINE_PATH = Path(__file__).parent / "baselines" / "prior_audit_545118f7.json"


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    if not REAL_CSV.exists():
        pytest.skip(f"Real CSV absent at {REAL_CSV.name}; real-data checks not run")
    out = tmp_path_factory.mktemp("real_run")
    before = hashlib.sha256(REAL_CSV.read_bytes()).hexdigest()
    report = run_preparation(
        REAL_CSV, out / "rides_prepared.csv", out / "location_lookup.csv", out / "reports"
    )
    after = hashlib.sha256(REAL_CSV.read_bytes()).hexdigest()
    prepared = read_prepared_csv(out / "rides_prepared.csv")
    lookup = pd.read_csv(out / "location_lookup.csv", dtype=str, keep_default_na=False)
    return {
        "report": report,
        "prepared": prepared,
        "lookup": lookup,
        "sha_before": before,
        "sha_after": after,
    }


@pytest.fixture(scope="module")
def independent_counts():
    """Counts read with the csv module, independent of the pandas pipeline."""
    if not REAL_CSV.exists():
        pytest.skip("Real CSV absent")
    with REAL_CSV.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = 0
        labels: set[str] = set()
        for row in reader:
            rows += 1
            labels.add(row["Pickup Location"].strip())
            labels.add(row["Drop Location"].strip())
    return {"rows": rows, "labels": labels}


def test_raw_csv_is_unchanged_by_preparation(real):
    assert real["sha_before"] == real["sha_after"]
    assert real["report"]["source"]["sha256"] == real["sha_before"]


def test_every_source_record_is_preserved(real, independent_counts):
    prepared = real["prepared"]
    assert len(prepared) == independent_counts["rows"]
    assert [s["rows"] for s in real["report"]["row_counts"]] == [independent_counts["rows"]] * 4
    assert prepared["source_record_key"].is_unique
    assert prepared["source_row_number"].tolist() == list(range(1, len(prepared) + 1))


def test_lookup_template_has_every_label_unresolved_without_coordinates(real, independent_counts):
    lookup = real["lookup"]
    assert set(lookup["original_location"]) == independent_counts["labels"]
    assert lookup["original_location"].is_unique
    assert set(lookup["review_status"]) == {"unresolved"}
    assert (lookup["latitude"] == "").all() and (lookup["longitude"] == "").all()
    coverage = real["report"]["geography"]["layer_coverage"]
    assert all(layer["eligible_rows"] == 0 for layer in coverage.values())


def test_status_dependent_measures_are_not_zero_filled(real):
    prepared = real["prepared"]
    no_driver = prepared.loc[prepared["is_no_driver_found"]]
    assert no_driver["booking_value"].isna().all()
    assert not (prepared["booking_value"] == 0).any()


def test_prior_audit_regression_for_audited_source_version(real):
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    local_sha = real["report"]["source"]["sha256"]
    if local_sha != baseline["source_sha256"]:
        pytest.skip(
            f"Local CSV sha256 {local_sha[:16]}… differs from audited "
            f"{baseline['source_sha256'][:16]}…; snapshot baseline does not apply. "
            "Investigate the difference instead of editing the baseline."
        )

    report, prepared = real["report"], real["prepared"]
    ids = report["identifiers"]
    geo = report["geography"]
    kpis = metrics.booking_kpis(prepared)
    composition = metrics.outcome_composition(prepared)
    flows = metrics.od_flows(prepared)
    vehicles = metrics.bookings_by_vehicle_type(prepared)
    payments = metrics.payment_mix(prepared).table

    actual = {
        "source_size_bytes": report["source"]["size_bytes"],
        "rows": len(prepared),
        "columns": report["source"]["column_count"],
        "distinct_booking_ids": ids["booking_id"]["distinct_normalized"],
        "booking_ids_appearing_more_than_once": ids["booking_id"]["ids_appearing_more_than_once"],
        "booking_id_excess_occurrences": ids["booking_id"]["excess_occurrences"],
        "exact_duplicate_rows": report["exact_duplicates"]["rows_beyond_first"],
        "distinct_customer_ids": kpis.unique_customer_ids,
        "customer_ids_appearing_more_than_once": ids["customer_id"]["ids_appearing_more_than_once"],
        "status_counts": dict(
            zip(composition["booking_status"], composition["bookings"], strict=True)
        ),
        "first_date": report["coverage"]["first_date"],
        "last_date": report["coverage"]["last_date"],
        "distinct_dates": report["coverage"]["distinct_dates"],
        "distinct_pickup_labels": geo["distinct_pickup_labels"],
        "distinct_drop_labels": geo["distinct_drop_labels"],
        "distinct_labels_both_endpoints": geo["distinct_labels_both_endpoints"],
        "distinct_ordered_pairs": len(flows),
        "max_records_for_one_ordered_pair": int(flows["bookings"].max()),
        "vehicle_type_counts": dict(
            zip(vehicles["vehicle_type"], vehicles["bookings"], strict=True)
        ),
        "payment_method_completed_counts": dict(
            zip(payments["payment_method"], payments["completed_bookings"], strict=True)
        ),
        "top_pickups": [
            [r.location, r.bookings] for r in metrics.top_locations(prepared, "pickup").itertuples()
        ],
        "top_drops": [
            [r.location, r.bookings] for r in metrics.top_locations(prepared, "drop").itertuples()
        ],
    }
    for key, value in actual.items():
        assert value == baseline[key], f"{key}: local {value!r} != audit {baseline[key]!r}"

    assert kpis.completion_rate == pytest.approx(baseline["completion_rate"])
    assert kpis.cancellation_rate == pytest.approx(baseline["cancellation_rate"])
    assert kpis.non_completion_rate == pytest.approx(baseline["non_completion_rate"])

    hourly = prepared["hour"].value_counts()
    assert int(hourly.idxmax()) == baseline["top_hour"]
    assert int(hourly.max()) == baseline["top_hour_bookings"]

    days = metrics.eligible_dates(
        date(2024, 1, 1), date(2024, 12, 30), excluded_dates=metrics.coverage_gaps(prepared)
    )
    weekday = metrics.weekday_demand(prepared, days).set_index("weekday")
    for name in metrics.WEEKDAY_LABELS:
        assert weekday.loc[name, "bookings"] == baseline["weekday_totals"][name]
        assert weekday.loc[name, "eligible_days"] == baseline["weekday_occurrences"][name]
        assert (
            round(float(weekday.loc[name, "avg_bookings_per_day"]), 2)
            == (baseline["weekday_avg_per_occurrence"][name])
        )

    value = metrics.completed_booking_value(prepared)
    assert value.total == baseline["completed_booking_value_sum"]
    assert round(value.total / value.valid_records, 2) == baseline["completed_booking_value_mean"]
    assert prepared["booking_value"].sum() == baseline["all_records_booking_value_sum"]
