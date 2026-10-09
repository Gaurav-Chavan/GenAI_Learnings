"""Small synthetic fixtures. These records are invented for tests only and are never
used by the application."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

import preprocessing
from preprocessing import EXPECTED_COLUMNS

FAKE_SHA = "ab" * 32

_STATUS_FIELDS = {
    "Completed": {
        "Booking Value": "250",
        "Ride Distance": "10.5",
        "Avg CTAT": "20.0",
        "Driver Ratings": "4.5",
        "Customer Rating": "4.8",
        "Payment Method": "UPI",
    },
    "Cancelled by Customer": {
        "Cancelled Rides by Customer": "1",
        "Reason for cancelling by Customer": "Change of plans",
    },
    "Cancelled by Driver": {
        "Cancelled Rides by Driver": "1",
        "Driver Cancellation Reason": "Vehicle issue",
    },
    "No Driver Found": {"Avg VTAT": "null"},
    "Incomplete": {
        "Incomplete Rides": "1",
        "Incomplete Rides Reason": "Vehicle breakdown",
        "Booking Value": "300",
        "Ride Distance": "8.0",
        "Avg CTAT": "15.0",
        "Payment Method": "Cash",
    },
}

_counter = iter(range(1, 10_000_000))


def raw_row(status: str = "Completed", fields: dict[str, str] | None = None) -> dict[str, str]:
    """One synthetic source row, shaped like the real file (quoted IDs, literal 'null').

    `fields` overrides source columns by their exact source names.
    """
    n = next(_counter)
    row = {column: "null" for column in EXPECTED_COLUMNS}
    row.update(
        {
            "Date": "2024-01-01",
            "Time": "08:00:00",
            "Booking ID": f'"CNR{n:07d}"',
            "Booking Status": status,
            "Customer ID": f'"CID{n:07d}"',
            "Vehicle Type": "Auto",
            "Pickup Location": "Alpha",
            "Drop Location": "Beta",
            "Avg VTAT": "5.0",
        }
    )
    row.update(_STATUS_FIELDS.get(status, {}))
    for column, value in (fields or {}).items():
        if column not in row:
            raise KeyError(f"Unknown source column {column!r}")
        row[column] = value
    return row


def trip(pickup: str, drop: str, status: str = "Completed") -> dict[str, str]:
    return raw_row(status, {"Pickup Location": pickup, "Drop Location": drop})


def lookup_frame(rows: list[dict[str, str]]) -> pd.DataFrame:
    defaults = {c: "" for c in preprocessing.LOOKUP_COLUMNS}
    columns = list(preprocessing.LOOKUP_COLUMNS)
    return pd.DataFrame([{**defaults, **r} for r in rows], columns=columns).astype("string")


def reviewed(label: str, lat: str = "28.6", lon: str = "77.2") -> dict[str, str]:
    """A reviewed lookup row with synthetic coordinates (tests only)."""
    return {
        "original_location": label,
        "normalized_location": label,
        "latitude": lat,
        "longitude": lon,
        "city_or_zone": "Test zone",
        "coordinate_source": "synthetic test fixture",
        "review_status": "reviewed",
        "review_notes": "test only",
    }


def unresolved(label: str) -> dict[str, str]:
    return {"original_location": label, "normalized_location": label, "review_status": "unresolved"}


def raw_frame(rows: list[dict[str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(EXPECTED_COLUMNS)).astype("string")


def prepared_from_rows(rows: list[dict[str, str]]) -> pd.DataFrame:
    prepared, _ = preprocessing.prepare_bookings(raw_frame(rows), FAKE_SHA)
    return prepared


def write_raw_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(EXPECTED_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def project(tmp_path: Path) -> dict[str, Path]:
    """Isolated input/output paths so tests never touch the real project outputs."""
    return {
        "input": tmp_path / "bookings.csv",
        "prepared": tmp_path / "data" / "processed" / "rides_prepared.csv",
        "lookup": tmp_path / "data" / "reference" / "location_lookup.csv",
        "reports": tmp_path / "reports",
    }


def run(project: dict[str, Path]) -> dict:
    return preprocessing.run_preparation(
        project["input"], project["prepared"], project["lookup"], project["reports"]
    )
