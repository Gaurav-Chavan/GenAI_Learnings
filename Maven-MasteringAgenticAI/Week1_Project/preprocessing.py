"""Prepare the NCR ride bookings CSV for analysis.

Run from the project root:

    uv run python preprocessing.py --input ncr_ride_bookings.csv

The raw CSV is only read, never modified. Outputs:
    data/processed/rides_prepared.csv         one row per source booking record
    data/processed/rides_prepared.meta.json   fingerprints used to detect stale output
    data/reference/location_lookup.csv        location labels; reviewed rows are preserved
    reports/data_quality_report.json / .md    profiling and validation results

Field definitions and derivations are documented in docs/DATA_DICTIONARY.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

import metrics
from metrics import STATUS_ORDER, WEEKDAY_LABELS

PIPELINE_VERSION = "1.0.0"

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_ROOT / "ncr_ride_bookings.csv"
DEFAULT_PREPARED = PROJECT_ROOT / "data" / "processed" / "rides_prepared.csv"
DEFAULT_LOOKUP = PROJECT_ROOT / "data" / "reference" / "location_lookup.csv"
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports"

EXPECTED_COLUMNS = (
    "Date",
    "Time",
    "Booking ID",
    "Booking Status",
    "Customer ID",
    "Vehicle Type",
    "Pickup Location",
    "Drop Location",
    "Avg VTAT",
    "Avg CTAT",
    "Cancelled Rides by Customer",
    "Reason for cancelling by Customer",
    "Cancelled Rides by Driver",
    "Driver Cancellation Reason",
    "Incomplete Rides",
    "Incomplete Rides Reason",
    "Booking Value",
    "Ride Distance",
    "Driver Ratings",
    "Customer Rating",
    "Payment Method",
)

# Compared case-insensitively after trimming whitespace.
MISSING_TOKENS = frozenset({"", "null", "nan", "none"})
ID_QUOTE_CHARS = "\"'"

ID_COLUMNS = {"Booking ID": "booking_id", "Customer ID": "customer_id"}
TEXT_COLUMNS = {
    "Booking Status": "booking_status",
    "Vehicle Type": "vehicle_type",
    "Pickup Location": "pickup_location",
    "Drop Location": "drop_location",
    "Reason for cancelling by Customer": "customer_cancel_reason",
    "Driver Cancellation Reason": "driver_cancel_reason",
    "Incomplete Rides Reason": "incomplete_reason",
    "Payment Method": "payment_method",
}
# Avg VTAT / Avg CTAT keep their source terminology; their meaning is unverified.
NUMERIC_COLUMNS = {
    "Avg VTAT": "avg_vtat",
    "Avg CTAT": "avg_ctat",
    "Booking Value": "booking_value",
    "Ride Distance": "ride_distance",
    "Driver Ratings": "driver_rating",
    "Customer Rating": "customer_rating",
}
# Source exception flag -> (prepared column, status it should describe).
FLAG_COLUMNS = {
    "Cancelled Rides by Customer": ("cancelled_by_customer_flag", "Cancelled by Customer"),
    "Cancelled Rides by Driver": ("cancelled_by_driver_flag", "Cancelled by Driver"),
    "Incomplete Rides": ("incomplete_flag", "Incomplete"),
}
REASON_STATUS = {
    "customer_cancel_reason": "Cancelled by Customer",
    "driver_cancel_reason": "Cancelled by Driver",
    "incomplete_reason": "Incomplete",
}
# Plausibility ranges (inclusive). Violations are flagged, never repaired.
NUMERIC_RANGES: dict[str, tuple[float | None, float | None]] = {
    "avg_vtat": (0, None),
    "avg_ctat": (0, None),
    "booking_value": (0, None),
    "ride_distance": (0, None),
    "driver_rating": (1, 5),
    "customer_rating": (1, 5),
}
OUTCOME_FLAGS = {
    "is_completed": "Completed",
    "is_cancelled_by_customer": "Cancelled by Customer",
    "is_cancelled_by_driver": "Cancelled by Driver",
    "is_no_driver_found": "No Driver Found",
    "is_incomplete": "Incomplete",
}

LOOKUP_COLUMNS = (
    "original_location",
    "normalized_location",
    "latitude",
    "longitude",
    "city_or_zone",
    "coordinate_source",
    "review_status",
    "review_notes",
)
REVIEW_STATUSES = ("unresolved", "proposed", "reviewed")
MAPPABLE_REVIEW_STATUS = "reviewed"
NEW_LABEL_NOTE = "Observed in source; coordinates not yet sourced or reviewed."
# Coarse sanity box for the Delhi-NCR study area including outlying labels such as
# Panipat, Meerut and Bhiwadi. A validation guard only; adjust deliberately if a
# reviewed location legitimately falls outside it.
STUDY_AREA_LAT = (27.5, 30.0)
STUDY_AREA_LON = (76.0, 78.5)

DATETIME_DTYPE = "datetime64[us]"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_FORMAT = "%Y-%m-%d"

# Prepared column -> dtype. Order here is the column order of the prepared CSV.
PREPARED_SCHEMA: dict[str, str] = {
    "source_record_key": "string",
    "source_row_number": "Int64",
    "booking_id_raw": "string",
    "booking_id": "string",
    "customer_id_raw": "string",
    "customer_id": "string",
    "booking_ts": "datetime",
    "booking_date": "datetime",
    "year_month": "string",
    "weekday_num": "Int64",
    "weekday": "string",
    "hour": "Int64",
    "is_weekend": "boolean",
    "booking_status": "string",
    "is_known_status": "bool",
    **{name: "bool" for name in OUTCOME_FLAGS},
    "is_cancelled": "bool",
    "vehicle_type": "string",
    "pickup_location": "string",
    "drop_location": "string",
    **{name: "Float64" for name in NUMERIC_COLUMNS.values()},
    **{name: "Int64" for name, _ in FLAG_COLUMNS.values()},
    "customer_cancel_reason": "string",
    "driver_cancel_reason": "string",
    "incomplete_reason": "string",
    "payment_method": "string",
    "booking_id_occurrences": "Int64",
    "is_repeated_booking_id": "bool",
    "customer_id_occurrences": "Int64",
    "is_repeated_customer_id": "bool",
    "is_exact_duplicate_row": "bool",
    "quality_issues": "string",
}
GEO_SCHEMA: dict[str, str] = {
    f"{end}_{name}": dtype
    for end in ("pickup", "drop")
    for name, dtype in [
        ("normalized_location", "string"),
        ("latitude", "Float64"),
        ("longitude", "Float64"),
        ("city_or_zone", "string"),
        ("review_status", "string"),
        ("is_mapped", "bool"),
    ]
}
FULL_SCHEMA = {**PREPARED_SCHEMA, **GEO_SCHEMA}
# Raw identifier text is kept verbatim, so an empty string there is data, not missing.
VERBATIM_COLUMNS = ("booking_id_raw", "customer_id_raw")


class PreparationError(Exception):
    """Base error for problems that should stop preparation with a clear message."""


class SchemaError(PreparationError):
    pass


class LookupValidationError(PreparationError):
    pass


class OutputPathError(PreparationError):
    pass


class StaleArtifactError(PreparationError):
    pass


# ---------------------------------------------------------------------------
# Reading and normalization
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_raw(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read every cell as verbatim text and check the header against the expected schema."""
    if not path.is_file():
        raise PreparationError(f"Input CSV not found: {path}")
    try:
        raw = pd.read_csv(
            path, dtype=str, keep_default_na=False, na_filter=False, encoding="utf-8-sig"
        )
    except pd.errors.EmptyDataError as exc:
        raise SchemaError(f"Input CSV is empty: {path}") from exc
    header = [str(c) for c in raw.columns]
    missing = [c for c in EXPECTED_COLUMNS if c not in header]
    extra = [c for c in header if c not in EXPECTED_COLUMNS]
    if missing:
        raise SchemaError(f"Input CSV is missing expected columns: {missing}. Found: {header}")
    schema = {
        "columns": header,
        "column_count": len(header),
        "matches_expected": header == list(EXPECTED_COLUMNS),
        "extra_columns_ignored": extra,
    }
    return raw.astype("string"), schema


def missing_mask(values: pd.Series) -> pd.Series:
    """True where a raw text value is one of the documented missing tokens."""
    return values.str.strip().str.casefold().isin(MISSING_TOKENS)


def normalize_text(values: pd.Series) -> pd.Series:
    """Trim and collapse whitespace; documented missing tokens become NA."""
    cleaned = values.str.strip().str.replace(r"\s+", " ", regex=True)
    return cleaned.mask(missing_mask(values), pd.NA).astype("string")


def normalize_identifier(values: pd.Series) -> pd.Series:
    """Trim whitespace and literal surrounding quote characters; missing stays NA (not 'nan')."""
    cleaned = values.str.strip().str.strip(ID_QUOTE_CHARS).str.strip()
    is_missing = missing_mask(cleaned) | missing_mask(values)
    return cleaned.mask(is_missing, pd.NA).astype("string")


def parse_numeric(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Parse text to nullable floats. Returns (values, parse-failure mask). Missing stays NA."""
    is_missing = missing_mask(values)
    parsed = pd.to_numeric(values.where(~is_missing, None).str.strip(), errors="coerce")
    parsed = parsed.astype("Float64")
    failed = parsed.isna() & ~is_missing
    return parsed, failed.astype(bool)


@dataclass
class _IssueCollector:
    """Accumulates row-level quality issue codes, reported but never used to drop rows."""

    index: pd.Index
    codes: list[tuple[str, pd.Series]] = field(default_factory=list)

    def add(self, code: str, mask: pd.Series) -> None:
        self.codes.append((code, mask.fillna(False).astype(bool)))

    def as_column(self) -> pd.Series:
        joined = pd.Series("", index=self.index, dtype="string")
        for code, mask in self.codes:
            joined = joined.mask(mask, joined + code + ";")
        return joined.str.rstrip(";").replace("", pd.NA).astype("string")


def prepare_bookings(raw: pd.DataFrame, source_sha256: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Normalize, type and derive fields. Returns the prepared rows and profiling details.

    Row count and order are unchanged: one output row per source record.
    """
    n = len(raw)
    out = pd.DataFrame(index=raw.index)
    details: dict[str, Any] = {}
    issues = _IssueCollector(raw.index)

    row_number = pd.Series(range(1, n + 1), index=raw.index, dtype="Int64")
    out["source_record_key"] = (source_sha256[:16] + ":" + row_number.astype(str)).astype("string")
    out["source_row_number"] = row_number

    # Identifiers: raw text preserved, normalized copy alongside.
    id_details = {}
    for source, name in ID_COLUMNS.items():
        out[f"{name}_raw"] = raw[source].astype("string")
        out[name] = normalize_identifier(raw[source])
        id_details[name] = {
            "missing": int(out[name].isna().sum()),
            "raw_values_changed_by_normalization": int((out[name].fillna("") != raw[source]).sum()),
            "raw_values_with_literal_quotes": int(
                raw[source].str.strip().str.contains(r"^[\"']|[\"']$", regex=True).sum()
            ),
        }
        issues.add(f"missing_{name}", out[name].isna())
    details["identifiers"] = id_details

    # Date and time: parse failures are reported, never guessed.
    date_text = normalize_text(raw["Date"])
    time_text = normalize_text(raw["Time"])
    date_parsed = pd.to_datetime(date_text, format=DATE_FORMAT, errors="coerce")
    time_parsed = pd.to_datetime(time_text, format="%H:%M:%S", errors="coerce")
    date_failed = date_parsed.isna() & date_text.notna()
    time_failed = time_parsed.isna() & time_text.notna()
    issues.add("missing_date", date_text.isna())
    issues.add("date_parse_failed", date_failed)
    issues.add("missing_time", time_text.isna())
    issues.add("time_parse_failed", time_failed)
    details["parse_failures"] = {
        "Date": _failure_summary(date_failed, out["source_row_number"]),
        "Time": _failure_summary(time_failed, out["source_row_number"]),
    }
    details["missing_date_rows"] = int(date_text.isna().sum())
    details["missing_time_rows"] = int(time_text.isna().sum())

    booking_date = date_parsed.astype(DATETIME_DTYPE)
    time_of_day = time_parsed - time_parsed.dt.normalize()
    out["booking_ts"] = (booking_date + time_of_day).astype(DATETIME_DTYPE)
    out["booking_date"] = booking_date
    out["year_month"] = booking_date.dt.strftime("%Y-%m").astype("string")
    out["weekday_num"] = booking_date.dt.weekday.astype("Int64")
    out["weekday"] = out["weekday_num"].map(dict(enumerate(WEEKDAY_LABELS))).astype("string")
    out["hour"] = time_parsed.dt.hour.astype("Int64")
    out["is_weekend"] = (out["weekday_num"] >= 5).astype("boolean")

    # Status and outcomes. Status labels are matched case-insensitively to the
    # five documented statuses; anything else is kept and flagged.
    status_text = normalize_text(raw["Booking Status"])
    canonical = {s.casefold(): s for s in STATUS_ORDER}
    status = status_text.map(lambda v: canonical.get(v.casefold(), v) if isinstance(v, str) else v)
    out["booking_status"] = status.astype("string")
    out["is_known_status"] = out["booking_status"].isin(STATUS_ORDER).fillna(False).astype(bool)
    for flag, label in OUTCOME_FLAGS.items():
        out[flag] = (out["booking_status"] == label).fillna(False).astype(bool)
    out["is_cancelled"] = out["is_cancelled_by_customer"] | out["is_cancelled_by_driver"]
    issues.add("missing_status", out["booking_status"].isna())
    issues.add("unexpected_status", out["booking_status"].notna() & ~out["is_known_status"])

    # Categorical text (whitespace standardized; no label remapping).
    whitespace_changes = {}
    for source, name in TEXT_COLUMNS.items():
        if name == "booking_status":
            normalized = status_text  # before case mapping, so only whitespace changes count
        else:
            normalized = normalize_text(raw[source])
            out[name] = normalized
        changed = normalized.notna() & (normalized != raw[source])
        whitespace_changes[source] = int(changed.fillna(False).sum())
    details["text_values_changed_by_whitespace_normalization"] = whitespace_changes

    # Numeric measures stay missing when missing; never zero-filled.
    range_violations = {}
    for source, name in NUMERIC_COLUMNS.items():
        values, failed = parse_numeric(raw[source])
        out[name] = values
        details["parse_failures"][source] = _failure_summary(failed, out["source_row_number"])
        issues.add(f"{name}_parse_failed", failed)
        low, high = NUMERIC_RANGES[name]
        bad = pd.Series(False, index=raw.index)
        if low is not None:
            bad |= (values < low).fillna(False)
        if high is not None:
            bad |= (values > high).fillna(False)
        range_violations[name] = {"min": low, "max": high, "violations": int(bad.sum())}
        issues.add(f"{name}_out_of_range", bad)
    details["range_violations"] = range_violations

    # Source exception flags: validated against status; missing is not recorded zero.
    flag_validation = {}
    for source, (name, expected_status) in FLAG_COLUMNS.items():
        values, failed = parse_numeric(raw[source])
        failed |= (values.notna() & (values != values.round())).fillna(False)  # non-integer
        out[name] = values.where(~failed, pd.NA).astype("Int64")
        details["parse_failures"][source] = _failure_summary(failed, out["source_row_number"])
        on_status = (out["booking_status"] == expected_status).fillna(False)
        flag = out[name]
        mismatch = (on_status & (flag != 1).fillna(True)) | (~on_status & (flag == 1).fillna(False))
        issues.add(f"{name}_status_mismatch", mismatch)
        flag_validation[source] = {
            "expected_status": expected_status,
            "status_rows": int(on_status.sum()),
            "status_rows_flag_1": int((on_status & (flag == 1).fillna(False)).sum()),
            "status_rows_flag_missing": int((on_status & flag.isna()).sum()),
            "other_rows_flag_1": int((~on_status & (flag == 1).fillna(False)).sum()),
            "other_rows_flag_0": int((~on_status & (flag == 0).fillna(False)).sum()),
            "other_rows_flag_missing": int((~on_status & flag.isna()).sum()),
            "mismatched_rows": int(mismatch.sum()),
        }
    details["flag_validation"] = flag_validation

    reason_validation = {}
    for name, expected_status in REASON_STATUS.items():
        on_status = (out["booking_status"] == expected_status).fillna(False)
        present = out[name].notna()
        reason_validation[name] = {
            "expected_status": expected_status,
            "present_on_status": int((present & on_status).sum()),
            "missing_on_status": int((~present & on_status).sum()),
            "present_on_other_status": int((present & ~on_status).sum()),
        }
        issues.add(f"{name}_on_unexpected_status", present & ~on_status)
    details["reason_validation"] = reason_validation

    # Repeated identifiers and exact duplicates are flagged, never removed.
    for name in ID_COLUMNS.values():
        occurrences = out.groupby(name, dropna=True)[name].transform("size")
        out[f"{name}_occurrences"] = occurrences.astype("Int64")
        out[f"is_repeated_{name}"] = (out[f"{name}_occurrences"] > 1).fillna(False).astype(bool)
    duplicate_any = raw.duplicated(keep=False)
    out["is_exact_duplicate_row"] = duplicate_any.astype(bool)
    issues.add("exact_duplicate_row", duplicate_any)
    details["exact_duplicate_rows_beyond_first"] = int(raw.duplicated(keep="first").sum())
    details["rows_in_exact_duplicate_groups"] = int(duplicate_any.sum())

    out["quality_issues"] = issues.as_column()
    details["quality_issue_counts"] = {
        code: int(mask.sum()) for code, mask in issues.codes if int(mask.sum()) > 0
    }
    details["rows_with_any_quality_issue"] = int(out["quality_issues"].notna().sum())

    out = out[list(PREPARED_SCHEMA)]
    _assert_row_count(out, n, "derivation")
    return out, details


def _failure_summary(failed: pd.Series, row_numbers: pd.Series, examples: int = 5) -> dict:
    return {
        "count": int(failed.sum()),
        "example_source_rows": [int(x) for x in row_numbers[failed].head(examples)],
    }


def _assert_row_count(df: pd.DataFrame, expected: int, step: str) -> None:
    if len(df) != expected:
        raise PreparationError(
            f"Row count changed during {step}: expected {expected}, got {len(df)}"
        )


# ---------------------------------------------------------------------------
# Location lookup
# ---------------------------------------------------------------------------


def read_lookup(path: Path) -> pd.DataFrame | None:
    """Read an existing lookup verbatim as text, or return None if it does not exist yet."""
    if not path.exists():
        return None
    lookup = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False).astype("string")
    validate_lookup(lookup)
    return lookup


def validate_lookup(lookup: pd.DataFrame) -> None:
    """Reject lookups that could multiply rows or place unreviewed/implausible points."""
    columns = list(lookup.columns)
    if columns != list(LOOKUP_COLUMNS):
        raise LookupValidationError(
            f"Lookup columns must be exactly {list(LOOKUP_COLUMNS)}; found {columns}"
        )
    keys = lookup["original_location"].str.strip()
    if (keys == "").any():
        rows = _lookup_rows(keys == "")
        raise LookupValidationError(f"Lookup has blank original_location on rows {rows}")
    duplicated = keys.duplicated(keep=False)
    if duplicated.any():
        dupes = sorted(set(keys[duplicated]))
        raise LookupValidationError(f"Lookup has duplicate original_location keys: {dupes}")

    status = lookup["review_status"].str.strip()
    bad_status = ~status.isin(REVIEW_STATUSES)
    if bad_status.any():
        raise LookupValidationError(
            f"Lookup review_status must be one of {REVIEW_STATUSES}; "
            f"invalid on rows {_lookup_rows(bad_status)}"
        )

    lat, lat_failed = parse_numeric(lookup["latitude"])
    lon, lon_failed = parse_numeric(lookup["longitude"])
    if (lat_failed | lon_failed).any():
        raise LookupValidationError(
            f"Lookup has non-numeric coordinates on rows {_lookup_rows(lat_failed | lon_failed)}"
        )
    half = lat.isna() != lon.isna()
    if half.any():
        raise LookupValidationError(
            f"Lookup rows must have both or neither coordinate: rows {_lookup_rows(half)}"
        )
    has_coords = lat.notna()
    unresolved_with_coords = (status == "unresolved") & has_coords
    if unresolved_with_coords.any():
        raise LookupValidationError(
            "Unresolved lookup rows must have blank coordinates: "
            f"rows {_lookup_rows(unresolved_with_coords)}"
        )
    reviewed = status == MAPPABLE_REVIEW_STATUS
    no_evidence = reviewed & (~has_coords | (lookup["coordinate_source"].str.strip() == ""))
    if no_evidence.any():
        raise LookupValidationError(
            "Reviewed lookup rows need coordinates and a coordinate_source: "
            f"rows {_lookup_rows(no_evidence)}"
        )
    out_of_bounds = reviewed & ~_in_study_area(lat, lon)
    if out_of_bounds.any():
        raise LookupValidationError(
            f"Reviewed coordinates outside the study-area box lat {STUDY_AREA_LAT}, "
            f"lon {STUDY_AREA_LON}: rows {_lookup_rows(out_of_bounds)}"
        )


def _lookup_rows(mask: pd.Series) -> list[int]:
    """1-based data-row numbers (excluding the header) for error messages."""
    return [int(i) + 1 for i in mask[mask.fillna(False)].index[:10]]


def _in_study_area(lat: pd.Series, lon: pd.Series) -> pd.Series:
    inside = lat.between(*STUDY_AREA_LAT) & lon.between(*STUDY_AREA_LON)
    return inside.fillna(False).astype(bool)


def update_lookup(
    existing: pd.DataFrame | None, observed_labels: Iterable[str]
) -> tuple[pd.DataFrame, list[str]]:
    """Keep every existing row unchanged and append newly observed labels as unresolved.

    Returns the updated lookup and the labels that were added.
    """
    if existing is None:
        existing = pd.DataFrame({c: pd.Series(dtype="string") for c in LOOKUP_COLUMNS})
    known = set(existing["original_location"].str.strip())
    added = sorted(set(observed_labels) - known)
    new_rows = pd.DataFrame(
        {
            "original_location": added,
            "normalized_location": added,
            "latitude": "",
            "longitude": "",
            "city_or_zone": "",
            "coordinate_source": "",
            "review_status": "unresolved",
            "review_notes": NEW_LABEL_NOTE,
        },
        columns=list(LOOKUP_COLUMNS),
    ).astype("string")
    updated = pd.concat([existing, new_rows], ignore_index=True) if added else existing
    validate_lookup(updated)
    return updated, added


def attach_locations(prepared: pd.DataFrame, lookup: pd.DataFrame) -> pd.DataFrame:
    """Attach pickup and drop lookup fields with validated many-to-one left joins.

    A row is mapped for an endpoint only when its label's lookup row is reviewed
    and has coordinates. Unmapped rows keep NA coordinates and are never dropped.
    """
    validate_lookup(lookup)
    lat, _ = parse_numeric(lookup["latitude"])
    lon, _ = parse_numeric(lookup["longitude"])
    status = lookup["review_status"].str.strip()
    reference = pd.DataFrame(
        {
            "key": lookup["original_location"].str.strip().astype("string"),
            "normalized_location": _blank_to_na(lookup["normalized_location"]),
            "latitude": lat,
            "longitude": lon,
            "city_or_zone": _blank_to_na(lookup["city_or_zone"]),
            "review_status": status.astype("string"),
            "is_mapped": ((status == MAPPABLE_REVIEW_STATUS) & lat.notna()).astype(bool),
        }
    )
    result = prepared
    expected = len(prepared)
    for end in ("pickup", "drop"):
        renamed = reference.rename(
            columns={c: f"{end}_{c}" for c in reference.columns if c != "key"}
        )
        try:
            result = result.merge(
                renamed,
                how="left",
                left_on=f"{end}_location",
                right_on="key",
                validate="many_to_one",
                sort=False,
            ).drop(columns="key")
        except pd.errors.MergeError as exc:
            raise LookupValidationError(f"{end} lookup join is not many-to-one: {exc}") from exc
        _assert_row_count(result, expected, f"{end} location join")
        result[f"{end}_is_mapped"] = result[f"{end}_is_mapped"].fillna(False).astype(bool)
    result.index = prepared.index
    return result[list(FULL_SCHEMA)]


def _blank_to_na(values: pd.Series) -> pd.Series:
    stripped = values.str.strip()
    return stripped.mask(stripped == "", pd.NA).astype("string")


def geographic_coverage(prepared: pd.DataFrame) -> dict[str, Any]:
    """Eligible-row coverage per map layer (flows need both endpoints)."""
    total = len(prepared)
    pickup = prepared["pickup_is_mapped"]
    drop = prepared["drop_is_mapped"]
    layers = {
        "pickup_points": pickup,
        "drop_points": drop,
        "flows_both_endpoints": pickup & drop,
    }
    return {
        name: {
            "eligible_rows": int(mask.sum()),
            "share_of_rows": metrics.safe_rate(int(mask.sum()), total),
        }
        for name, mask in layers.items()
    }


# ---------------------------------------------------------------------------
# Writing, reloading and staleness
# ---------------------------------------------------------------------------


def meta_path_for(prepared_path: Path) -> Path:
    return prepared_path.with_name(prepared_path.stem + ".meta.json")


def write_prepared(prepared: pd.DataFrame, path: Path) -> None:
    out = prepared.copy()
    out["booking_ts"] = prepared["booking_ts"].dt.strftime(TIMESTAMP_FORMAT)
    out["booking_date"] = prepared["booking_date"].dt.strftime(DATE_FORMAT)
    _atomic_write(path, lambda tmp: out.to_csv(tmp, index=False, lineterminator="\n"))


def read_prepared_csv(path: Path) -> pd.DataFrame:
    """Reload prepared data with explicit types so it matches what was written."""
    header = list(pd.read_csv(path, nrows=0).columns)
    if header != list(FULL_SCHEMA):
        raise SchemaError(f"Prepared CSV columns differ from the expected schema: {path}")
    text_like = {"string", "datetime"}
    dtypes = {
        col: ("string" if kind in text_like else "boolean" if kind == "bool" else kind)
        for col, kind in FULL_SCHEMA.items()
    }
    na_values = {col: [""] for col in FULL_SCHEMA if col not in VERBATIM_COLUMNS}
    df = pd.read_csv(path, dtype=dtypes, keep_default_na=False, na_values=na_values)
    for col, kind in FULL_SCHEMA.items():
        if kind == "datetime":
            fmt = TIMESTAMP_FORMAT if col == "booking_ts" else DATE_FORMAT
            df[col] = pd.to_datetime(df[col], format=fmt, errors="raise").astype(DATETIME_DTYPE)
        elif kind == "bool":
            if df[col].isna().any():
                raise SchemaError(f"Prepared column {col} must not contain missing values")
            df[col] = df[col].astype(bool)
    return df


def load_prepared(
    path: Path = DEFAULT_PREPARED,
    *,
    source_path: Path | None = None,
    lookup_path: Path | None = None,
) -> pd.DataFrame:
    """Load prepared data after checking it is current.

    Raises StaleArtifactError when the metadata is missing, the prepared file was
    edited, the pipeline version changed, or (when paths are given) the source CSV
    or lookup no longer match the fingerprints recorded at preparation time.
    """
    problems = staleness_problems(path, source_path=source_path, lookup_path=lookup_path)
    if problems:
        raise StaleArtifactError(
            "Prepared data must be regenerated with preprocessing.py: " + "; ".join(problems)
        )
    return read_prepared_csv(path)


def staleness_problems(
    prepared_path: Path, *, source_path: Path | None = None, lookup_path: Path | None = None
) -> list[str]:
    meta_path = meta_path_for(prepared_path)
    if not prepared_path.exists():
        return [f"prepared file not found: {prepared_path}"]
    if not meta_path.exists():
        return [f"metadata file not found: {meta_path}"]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    problems = []
    if meta.get("pipeline_version") != PIPELINE_VERSION:
        problems.append(
            f"pipeline version {meta.get('pipeline_version')} != current {PIPELINE_VERSION}"
        )
    if meta.get("prepared_sha256") != sha256_file(prepared_path):
        problems.append("prepared file changed after it was written")
    if source_path is not None and meta.get("source_sha256") != sha256_file(source_path):
        problems.append("source CSV changed since preparation")
    if lookup_path is not None and meta.get("lookup_sha256") != sha256_file(lookup_path):
        problems.append("location lookup changed since preparation")
    return problems


def _atomic_write(path: Path, write: Any) -> None:
    """Write to a temporary sibling file, then replace the target in one step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _write_text(path: Path, text: str) -> None:
    _atomic_write(path, lambda tmp: tmp.write_text(text, encoding="utf-8", newline="\n"))


def check_output_paths(input_path: Path, outputs: dict[str, Path]) -> None:
    """Refuse output paths that would overwrite the raw input or collide with each other."""
    source = os.path.normcase(str(input_path.resolve()))
    seen: dict[str, str] = {}
    for name, path in outputs.items():
        resolved = os.path.normcase(str(path.resolve()))
        same_file = path.exists() and input_path.exists() and path.samefile(input_path)
        if resolved == source or same_file:
            raise OutputPathError(f"{name} path {path} would overwrite the raw input CSV")
        if resolved in seen:
            raise OutputPathError(f"{name} and {seen[resolved]} outputs share the path {path}")
        seen[resolved] = name


# ---------------------------------------------------------------------------
# Quality report
# ---------------------------------------------------------------------------

ASSUMPTIONS = [
    "One source row is one booking record. Booking ID is not assumed unique; "
    "source_record_key (source SHA-256 prefix + 1-based row number) identifies rows.",
    "Date and Time are treated as local booking time; no time-zone conversion is applied.",
    "Missing tokens (case-insensitive, after trimming): empty, 'null', 'nan', 'none'.",
    "Cancellation = Cancelled by Customer + Cancelled by Driver. "
    "No Driver Found and Incomplete are separate outcomes.",
    "Booking Value is in source currency units (INR is a proposed, unverified display label); "
    "it is not net revenue or profit.",
    "Avg VTAT and Avg CTAT keep their source names; their operational meaning is unverified.",
    "Location coordinates come only from reviewed lookup rows; unresolved labels stay unmapped.",
]


def build_report(
    *,
    prepared: pd.DataFrame,
    details: dict[str, Any],
    source: dict[str, Any],
    row_counts: list[dict[str, Any]],
    lookup: pd.DataFrame,
    lookup_added: list[str],
    lookup_path: Path,
    prepared_path: Path,
    reload_consistent: bool,
) -> dict[str, Any]:
    n = len(prepared)
    kpis = metrics.booking_kpis(prepared)
    interval = metrics.coverage_interval(prepared)
    gaps = metrics.coverage_gaps(prepared)
    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline_version": PIPELINE_VERSION,
        "source": source,
        "assumptions": ASSUMPTIONS,
        "row_counts": row_counts,
        "outputs": {
            "prepared_csv": _display_path(prepared_path),
            "lookup_csv": _display_path(lookup_path),
            "reload_consistent": reload_consistent,
        },
    }

    # Dates and calendar coverage.
    coverage: dict[str, Any] = {
        "rows_with_parsed_date": int(prepared["booking_date"].notna().sum()),
        "rows_missing_date": details["missing_date_rows"],
        "rows_missing_time": details["missing_time_rows"],
    }
    if interval is not None:
        dates = metrics.eligible_dates(interval[0], interval[1], excluded_dates=gaps)
        exposure = metrics.weekday_exposure(dates)
        coverage.update(
            {
                "first_date": interval[0].isoformat(),
                "last_date": interval[1].isoformat(),
                "distinct_dates": int(prepared["booking_date"].nunique()),
                "calendar_days_in_interval": (interval[1] - interval[0]).days + 1,
                "coverage_gap_dates": [d.isoformat() for d in gaps[:50]],
                "coverage_gap_count": len(gaps),
                "weekday_occurrences": {WEEKDAY_LABELS[i]: int(exposure[i]) for i in range(7)},
            }
        )
        weekday = metrics.weekday_demand(prepared, dates)
        coverage["weekday_demand"] = [
            {
                "weekday": row.weekday,
                "bookings": _int_or_none(row.bookings),
                "eligible_days": int(row.eligible_days),
                "avg_bookings_per_day": _round_or_none(row.avg_bookings_per_day, 2),
            }
            for row in weekday.itertuples()
        ]
    hours = prepared["hour"].value_counts().sort_index()
    coverage["bookings_by_hour"] = {int(h): int(c) for h, c in hours.items()}
    report["coverage"] = coverage

    # Identifiers.
    ids = {}
    for name in ID_COLUMNS.values():
        occ = prepared.groupby(name, dropna=True).size()
        repeated = occ[occ > 1]
        ids[name] = {
            **details["identifiers"][name],
            "distinct_normalized": int(len(occ)),
            "ids_appearing_more_than_once": int(len(repeated)),
            "excess_occurrences": int((repeated - 1).sum()),
            "rows_with_repeated_id": int(repeated.sum()),
        }
    repeated_rows = prepared.loc[prepared["is_repeated_booking_id"]]
    if len(repeated_rows):
        varies = repeated_rows.groupby("booking_id").agg(
            dates=("booking_date", "nunique"),
            customers=("customer_id", "nunique"),
            statuses=("booking_status", "nunique"),
        )
        ids["booking_id"]["repeated_id_groups_varying_in"] = {
            "date": int((varies["dates"] > 1).sum()),
            "customer_id": int((varies["customers"] > 1).sum()),
            "booking_status": int((varies["statuses"] > 1).sum()),
        }
    report["identifiers"] = ids
    report["exact_duplicates"] = {
        "rows_beyond_first": details["exact_duplicate_rows_beyond_first"],
        "rows_in_duplicate_groups": details["rows_in_exact_duplicate_groups"],
    }

    # Outcomes.
    composition = metrics.outcome_composition(prepared)
    report["status_distribution"] = [
        {
            "booking_status": r.booking_status,
            "bookings": int(r.bookings),
            "share": None if pd.isna(r.share) else float(r.share),
        }
        for r in composition.itertuples()
    ]
    report["outcome_rates"] = {
        "denominator_rows": kpis.total_bookings,
        "completion_rate": kpis.completion_rate,
        "cancellation_rate": kpis.cancellation_rate,
        "no_driver_rate": kpis.no_driver_rate,
        "incomplete_rate": kpis.incomplete_rate,
        "non_completion_rate": kpis.non_completion_rate,
        "unique_customer_ids": kpis.unique_customer_ids,
    }
    report["flag_validation"] = details["flag_validation"]
    report["reason_validation"] = details["reason_validation"]

    # Parsing, missingness and ranges.
    report["parse_failures"] = details["parse_failures"]
    report["range_violations"] = details["range_violations"]
    report["text_values_changed_by_whitespace_normalization"] = details[
        "text_values_changed_by_whitespace_normalization"
    ]
    measure_columns = [
        *NUMERIC_COLUMNS.values(),
        "payment_method",
        *REASON_STATUS,
    ]
    status_labels = prepared["booking_status"].fillna(metrics.MISSING_LABEL)
    by_status = {}
    for status, group in prepared.groupby(status_labels):
        by_status[str(status)] = {
            "rows": len(group),
            "present": {c: int(group[c].notna().sum()) for c in measure_columns},
        }
    report["missingness"] = {
        "missing_by_column": {c: int(prepared[c].isna().sum()) for c in measure_columns},
        "present_by_status": by_status,
    }
    report["quality_issues"] = {
        "rows_with_any_issue": details["rows_with_any_quality_issue"],
        "counts_by_code": details["quality_issue_counts"],
    }

    # Categories and values.
    vehicle = metrics.bookings_by_vehicle_type(prepared)
    mix = metrics.payment_mix(prepared)
    value = metrics.completed_booking_value(prepared)
    report["categories"] = {
        "vehicle_type": {
            str(k): int(v)
            for k, v in zip(vehicle["vehicle_type"], vehicle["bookings"], strict=True)
        },
        "payment_method_completed": {
            "eligible_records": mix.eligible_records,
            "completed_without_method": mix.completed_without_method,
            "counts": {
                str(k): int(v)
                for k, v in zip(
                    mix.table["payment_method"], mix.table["completed_bookings"], strict=True
                )
            },
        },
        "completed_booking_value": {
            "total_source_currency_units": value.total,
            "valid_records": value.valid_records,
            "completed_records": value.completed_records,
        },
    }

    # Geography.
    pickup_labels = set(prepared["pickup_location"].dropna())
    drop_labels = set(prepared["drop_location"].dropna())
    flows = metrics.od_flows(prepared)
    status_counts = lookup["review_status"].str.strip().value_counts()
    observed = pickup_labels | drop_labels
    coordinate_columns = [
        c
        for c in report["source"]["columns"]
        if re.search(r"\b(lat|latitude|lon|lng|longitude|geometry)\b", c, re.IGNORECASE)
    ]
    report["geography"] = {
        "source_coordinate_columns": coordinate_columns,
        "distinct_pickup_labels": len(pickup_labels),
        "distinct_drop_labels": len(drop_labels),
        "distinct_labels_both_endpoints": len(observed),
        "rows_missing_pickup": int(prepared["pickup_location"].isna().sum()),
        "rows_missing_drop": int(prepared["drop_location"].isna().sum()),
        "distinct_ordered_pairs": len(flows),
        "max_records_for_one_ordered_pair": int(flows["bookings"].max()) if len(flows) else 0,
        "rows_with_same_pickup_and_drop": int(
            flows.loc[flows["is_same_location"], "bookings"].sum()
        ),
        "top_pickups": _top_records(prepared, "pickup"),
        "top_drops": _top_records(prepared, "drop"),
        "lookup": {
            "rows": len(lookup),
            "by_review_status": {str(k): int(v) for k, v in status_counts.items()},
            "labels_added_this_run": len(lookup_added),
            "labels_in_lookup_not_in_source": sorted(
                set(lookup["original_location"].str.strip()) - observed
            ),
        },
        "layer_coverage": geographic_coverage(prepared),
    }
    report["sample_size"] = {
        "pickup_rows_per_label_min": int(prepared["pickup_location"].value_counts().min())
        if n
        else 0,
        "reliability_min_denominator": metrics.RELIABILITY_MIN_DENOMINATOR,
    }
    return report


def _top_records(prepared: pd.DataFrame, endpoint: metrics.Endpoint) -> list[dict[str, Any]]:
    table = metrics.top_locations(prepared, endpoint, n=5)
    return [{"location": r.location, "bookings": int(r.bookings)} for r in table.itertuples()]


def _int_or_none(value: Any) -> int | None:
    return None if pd.isna(value) else int(value)


def _round_or_none(value: Any, digits: int) -> float | None:
    return None if pd.isna(value) else round(float(value), digits)


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def _fmt(value: float | None) -> str:
    return "N/A" if value is None else f"{value:,}"


def _display_path(path: Path) -> str:
    """Project-relative path for reports, avoiding machine-specific absolute paths."""
    resolved = path.resolve()
    for base in (Path.cwd().resolve(), PROJECT_ROOT):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.name


def render_markdown(report: dict[str, Any]) -> str:
    src = report["source"]
    cov = report["coverage"]
    ids = report["identifiers"]
    geo = report["geography"]
    lines = [
        "# Data quality report — NCR ride bookings",
        "",
        f"Generated (UTC): {report['generated_at_utc']} · pipeline {report['pipeline_version']}",
        "",
        "## Source",
        "",
        f"- File: `{src['path']}` ({src['size_bytes']:,} bytes)",
        f"- SHA-256: `{src['sha256']}`",
        f"- Unchanged by this run: {src['unchanged_after_run']}",
        f"- Columns: {src['column_count']} (matches expected schema: {src['matches_expected']})",
        "",
        "## Assumptions",
        "",
        *[f"- {a}" for a in report["assumptions"]],
        "",
        "## Row counts by step",
        "",
        "| Step | Rows |",
        "|---|---:|",
        *[f"| {r['step']} | {r['rows']:,} |" for r in report["row_counts"]],
        "",
        f"Saved/reloaded prepared data identical: {report['outputs']['reload_consistent']}",
        "",
        "## Coverage",
        "",
        f"- Dates: {cov.get('first_date')} to {cov.get('last_date')}; "
        f"{cov.get('distinct_dates')} distinct dates of "
        f"{cov.get('calendar_days_in_interval')} calendar days; "
        f"{cov.get('coverage_gap_count')} coverage-gap dates",
        f"- Rows missing date / time: {cov['rows_missing_date']} / {cov['rows_missing_time']}",
        "",
        "| Weekday | Bookings | Eligible days | Avg per day |",
        "|---|---:|---:|---:|",
        *[
            f"| {w['weekday']} | {_fmt(w['bookings'])} | {w['eligible_days']} | "
            f"{_fmt(w['avg_bookings_per_day'])} |"
            for w in cov.get("weekday_demand", [])
        ],
        "",
        "## Identifiers",
        "",
        "| Field | Distinct (normalized) | Missing | IDs repeated | Excess occurrences "
        "| Raw values with literal quotes |",
        "|---|---:|---:|---:|---:|---:|",
        *[
            f"| {name} | {v['distinct_normalized']:,} | {v['missing']:,} | "
            f"{v['ids_appearing_more_than_once']:,} | {v['excess_occurrences']:,} | "
            f"{v['raw_values_with_literal_quotes']:,} |"
            for name, v in ids.items()
        ],
        "",
        "Exact full-row duplicates beyond first: "
        f"{report['exact_duplicates']['rows_beyond_first']}. "
        "Repeated Booking IDs are retained; see `is_repeated_booking_id`.",
    ]
    varying = ids["booking_id"].get("repeated_id_groups_varying_in")
    if varying:
        lines.append(
            f"Repeated Booking ID groups that differ in date: {varying['date']}, "
            f"customer: {varying['customer_id']}, status: {varying['booking_status']}."
        )
    rates = report["outcome_rates"]
    lines += [
        "",
        "## Booking outcomes",
        "",
        "| Status | Records | Share |",
        "|---|---:|---:|",
        *[
            f"| {s['booking_status']} | {s['bookings']:,} | {_pct(s['share'])} |"
            for s in report["status_distribution"]
        ],
        "",
        f"Completion rate {_pct(rates['completion_rate'])}; cancellation rate "
        f"{_pct(rates['cancellation_rate'])} (customer + driver); no-driver rate "
        f"{_pct(rates['no_driver_rate'])}; incomplete rate {_pct(rates['incomplete_rate'])}. "
        f"Denominator: {rates['denominator_rows']:,} rows.",
        "",
        "### Exception flags vs status",
        "",
        "| Flag | Status rows | Flag=1 on status | Flag missing off status | Mismatches |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| {name} | {v['status_rows']:,} | {v['status_rows_flag_1']:,} | "
            f"{v['other_rows_flag_missing']:,} | {v['mismatched_rows']:,} |"
            for name, v in report["flag_validation"].items()
        ],
        "",
        "Missing flags on other statuses are recorded as missing, not as zero.",
        "",
        "## Parsing and ranges",
        "",
        "| Column | Parse failures |",
        "|---|---:|",
        *[f"| {c} | {v['count']:,} |" for c, v in report["parse_failures"].items()],
        "",
        "| Measure | Allowed range | Violations |",
        "|---|---|---:|",
        *[
            f"| {c} | {v['min']} to {v['max'] if v['max'] is not None else '∞'} | "
            f"{v['violations']:,} |"
            for c, v in report["range_violations"].items()
        ],
        "",
        "## Missingness by status (present values)",
        "",
    ]
    by_status = report["missingness"]["present_by_status"]
    measure_cols = list(report["missingness"]["missing_by_column"])
    lines += [
        "| Status | Rows | " + " | ".join(measure_cols) + " |",
        "|---|---:|" + "---:|" * len(measure_cols),
        *[
            f"| {status} | {v['rows']:,} | "
            + " | ".join(f"{v['present'][c]:,}" for c in measure_cols)
            + " |"
            for status, v in by_status.items()
        ],
        "",
        f"Rows with any quality issue code: {report['quality_issues']['rows_with_any_issue']:,}",
        "",
        "## Categories",
        "",
        "Bookings by vehicle type (booking records, not vehicles): "
        + ", ".join(f"{k} {v:,}" for k, v in report["categories"]["vehicle_type"].items()),
        "",
    ]
    pay = report["categories"]["payment_method_completed"]
    val = report["categories"]["completed_booking_value"]
    lines += [
        f"Payment methods on completed rows ({pay['eligible_records']:,} eligible, "
        f"{pay['completed_without_method']:,} completed without a method): "
        + ", ".join(f"{k} {v:,}" for k, v in pay["counts"].items()),
        "",
        f"Completed booking value: {_fmt(val['total_source_currency_units'])} "
        f"source currency units over {val['valid_records']:,} "
        f"of {val['completed_records']:,} completed rows.",
        "",
        "## Geography",
        "",
        "- Coordinate-like source columns: "
        + (", ".join(geo["source_coordinate_columns"]) or "none (location names only)"),
        f"- Distinct labels: pickup {geo['distinct_pickup_labels']}, drop "
        f"{geo['distinct_drop_labels']}, both endpoints {geo['distinct_labels_both_endpoints']}",
        f"- Ordered pickup→drop pairs: {geo['distinct_ordered_pairs']:,}; largest pair has "
        f"{geo['max_records_for_one_ordered_pair']} records; same-label rows "
        f"{geo['rows_with_same_pickup_and_drop']}",
        f"- Lookup rows: {geo['lookup']['rows']} ({geo['lookup']['by_review_status']}); "
        f"added this run: {geo['lookup']['labels_added_this_run']}",
        "",
        "| Map layer | Eligible rows | Share |",
        "|---|---:|---:|",
        *[
            f"| {name} | {v['eligible_rows']:,} | {_pct(v['share_of_rows'])} |"
            for name, v in geo["layer_coverage"].items()
        ],
        "",
        "| Rank | Pickup | Records | Drop | Records |",
        "|---:|---|---:|---|---:|",
        *[
            f"| {i + 1} | {p['location']} | {p['bookings']:,} "
            f"| {d['location']} | {d['bookings']:,} |"
            for i, (p, d) in enumerate(zip(geo["top_pickups"], geo["top_drops"], strict=False))
        ],
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_preparation(
    input_path: Path = DEFAULT_INPUT,
    prepared_path: Path = DEFAULT_PREPARED,
    lookup_path: Path = DEFAULT_LOOKUP,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
) -> dict[str, Any]:
    """Run the full preparation and return the quality report."""
    json_report = reports_dir / "data_quality_report.json"
    md_report = reports_dir / "data_quality_report.md"
    check_output_paths(
        input_path,
        {
            "prepared": prepared_path,
            "prepared metadata": meta_path_for(prepared_path),
            "lookup": lookup_path,
            "JSON report": json_report,
            "Markdown report": md_report,
        },
    )

    source_sha = sha256_file(input_path)
    raw, schema = read_raw(input_path)
    row_counts = [{"step": "read raw CSV", "rows": len(raw)}]

    prepared, details = prepare_bookings(raw, source_sha)
    row_counts.append({"step": "normalize and derive fields", "rows": len(prepared)})

    existing_lookup = read_lookup(lookup_path)
    observed = set(prepared["pickup_location"].dropna()) | set(prepared["drop_location"].dropna())
    lookup, added = update_lookup(existing_lookup, observed)
    if existing_lookup is None or added:
        _atomic_write(lookup_path, lambda tmp: lookup.to_csv(tmp, index=False, lineterminator="\n"))

    prepared = attach_locations(prepared, lookup)
    row_counts.append({"step": "pickup and drop lookup joins", "rows": len(prepared)})

    write_prepared(prepared, prepared_path)
    reloaded = read_prepared_csv(prepared_path)
    row_counts.append({"step": "written and reloaded", "rows": len(reloaded)})
    try:
        pd.testing.assert_frame_equal(prepared.reset_index(drop=True), reloaded)
        reload_consistent = True
    except AssertionError as exc:
        raise PreparationError(f"Prepared CSV does not reload identically: {exc}") from exc

    source_sha_after = sha256_file(input_path)
    if source_sha_after != source_sha:
        raise PreparationError("The raw input CSV changed while preparation was running")

    meta = {
        "pipeline_version": PIPELINE_VERSION,
        "source_file": _display_path(input_path),
        "source_sha256": source_sha,
        "lookup_file": _display_path(lookup_path),
        "lookup_sha256": sha256_file(lookup_path),
        "prepared_sha256": sha256_file(prepared_path),
        "rows": len(prepared),
        "columns": list(FULL_SCHEMA),
    }
    _write_text(meta_path_for(prepared_path), json.dumps(meta, indent=2) + "\n")

    source = {
        "path": _display_path(input_path),
        "size_bytes": input_path.stat().st_size,
        "sha256": source_sha,
        "unchanged_after_run": True,
        **schema,
    }
    report = build_report(
        prepared=prepared,
        details=details,
        source=source,
        row_counts=row_counts,
        lookup=lookup,
        lookup_added=added,
        lookup_path=lookup_path,
        prepared_path=prepared_path,
        reload_consistent=reload_consistent,
    )
    _write_text(json_report, json.dumps(report, indent=2, default=str) + "\n")
    _write_text(md_report, render_markdown(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare NCR ride bookings for analysis.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="raw bookings CSV")
    parser.add_argument("--output", type=Path, default=DEFAULT_PREPARED, help="prepared CSV")
    parser.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP, help="location lookup CSV")
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    args = parser.parse_args(argv)
    try:
        report = run_preparation(args.input, args.output, args.lookup, args.reports_dir)
    except PreparationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rates = report["outcome_rates"]
    geo = report["geography"]
    print(f"Source: {report['source']['path']} sha256={report['source']['sha256'][:16]}…")
    print("Rows: " + " -> ".join(f"{r['rows']:,}" for r in report["row_counts"]))
    print(
        f"Completion {_pct(rates['completion_rate'])}, "
        f"cancellation {_pct(rates['cancellation_rate'])}"
    )
    print(
        f"Lookup: {geo['lookup']['rows']} labels {geo['lookup']['by_review_status']}, "
        f"{geo['lookup']['labels_added_this_run']} added this run"
    )
    print(
        "Flow-eligible rows (both endpoints mapped): "
        f"{geo['layer_coverage']['flows_both_endpoints']['eligible_rows']:,}"
    )
    print(f"Reports written to {_display_path(args.reports_dir)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
