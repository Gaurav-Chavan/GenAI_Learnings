# NCR RidePulse — data dictionary

Covers `data/processed/rides_prepared.csv` (pipeline version 1.0.0) and `data/reference/location_lookup.csv`. Both are produced by `preprocessing.py` from the unchanged raw file `ncr_ride_bookings.csv`.

## Grain and identity

- **One row = one source booking record.** This is an assumption: the source `Booking ID` is *not* unique (1,224 IDs repeat in the current file), so it is never used to deduplicate.
- Row order matches the source file. Nothing is dropped, merged, or repaired.
- `source_record_key` = first 16 hex characters of the source file's SHA-256 + `:` + 1-based data-row number (header excluded). It identifies a row *within that exact source file version*; it is not a verified booking identity.

## Missing values

The raw file writes missing values as the literal text `null`. During preparation, these values count as missing, case-insensitively and after trimming whitespace: empty, `null`, `nan`, `none`. They become empty cells in the prepared CSV and NA after reload. Numeric measures are **never** zero-filled.

## Prepared booking columns

### Identity

| Column | Type | Derivation |
|---|---|---|
| `source_record_key` | text | See above |
| `source_row_number` | integer | 1-based data-row number in the source |
| `booking_id_raw` | text | Source `Booking ID` exactly as read (the current file wraps every ID in literal `"` characters) |
| `booking_id` | text / NA | `booking_id_raw` with surrounding whitespace and literal `"`/`'` quote characters stripped; missing tokens → NA |
| `customer_id_raw`, `customer_id` | text | Same treatment for `Customer ID`. Distinct customer IDs are identifiers, not verified people |

### Time (local booking time, no time-zone conversion)

| Column | Type | Derivation |
|---|---|---|
| `booking_ts` | datetime | `Date` (`YYYY-MM-DD`) + `Time` (`HH:MM:SS`); NA if either fails to parse |
| `booking_date` | date | Parsed `Date` |
| `year_month` | text | `YYYY-MM` from `booking_date` |
| `weekday_num` | integer | 0 = Monday … 6 = Sunday |
| `weekday` | text | Monday … Sunday |
| `hour` | integer | 0–23 from `Time` (available even if `Date` fails) |
| `is_weekend` | boolean / NA | Saturday or Sunday |

### Outcome

| Column | Type | Derivation |
|---|---|---|
| `booking_status` | text | Source status with whitespace trimmed. Matched case-insensitively to the five documented statuses; any other value is kept as-is and flagged `unexpected_status` |
| `is_known_status` | boolean | Status is one of the five documented values |
| `is_completed` | boolean | Status = Completed |
| `is_cancelled_by_customer` | boolean | Status = Cancelled by Customer |
| `is_cancelled_by_driver` | boolean | Status = Cancelled by Driver |
| `is_no_driver_found` | boolean | Status = No Driver Found |
| `is_incomplete` | boolean | Status = Incomplete |
| `is_cancelled` | boolean | Customer **or** driver cancellation. No Driver Found and Incomplete are *not* cancellations |

### Booking attributes

| Column | Type | Notes |
|---|---|---|
| `vehicle_type` | text | Booking vehicle category (Bike and eBike stay distinct). Not a count of physical vehicles |
| `pickup_location`, `drop_location` | text | Locality labels (names only; the source has no coordinates) |
| `payment_method` | text / NA | Present only on Completed and Incomplete rows in the current file |

### Measures (nullable decimals; never zero-filled)

| Column | Source | Present on (current file) | Notes |
|---|---|---|---|
| `avg_vtat` | Avg VTAT | all statuses except No Driver Found | Source name kept; meaning unverified (do not read as speed or travel time) |
| `avg_ctat` | Avg CTAT | Completed, Incomplete | Source name kept; meaning unverified |
| `booking_value` | Booking Value | Completed, Incomplete | Source currency units (INR proposed, unverified). Not net revenue or profit |
| `ride_distance` | Ride Distance | Completed, Incomplete | Unit not documented in source |
| `driver_rating` | Driver Ratings | Completed | 1–5 range check |
| `customer_rating` | Customer Rating | Completed | 1–5 range check |

Values outside plausibility ranges (negative values, or ratings outside 1–5) are flagged in `quality_issues`. They are not changed.

### Source exception flags and reasons

| Column | Source | Notes |
|---|---|---|
| `cancelled_by_customer_flag` | Cancelled Rides by Customer | Integer / NA. Expected `1` on the matching status. **Missing on other statuses means not recorded, not zero** |
| `cancelled_by_driver_flag` | Cancelled Rides by Driver | Same rule |
| `incomplete_flag` | Incomplete Rides | Same rule |
| `customer_cancel_reason` | Reason for cancelling by Customer | Text / NA |
| `driver_cancel_reason` | Driver Cancellation Reason | Text / NA |
| `incomplete_reason` | Incomplete Rides Reason | Text / NA |

Outcome booleans come from `booking_status`, not from these flags. The flags are checked against the status, and any disagreement is reported in the quality report.

### Quality flags

| Column | Meaning |
|---|---|
| `booking_id_occurrences` / `customer_id_occurrences` | How many rows share this normalized ID (NA when the ID is missing) |
| `is_repeated_booking_id` / `is_repeated_customer_id` | Occurrences > 1 |
| `is_exact_duplicate_row` | All 21 raw fields identical to another row (every copy is flagged; none removed) |
| `quality_issues` | `;`-separated codes, or NA. Codes: `missing_booking_id`, `missing_customer_id`, `missing_date`, `date_parse_failed`, `missing_time`, `time_parse_failed`, `missing_status`, `unexpected_status`, `<measure>_parse_failed`, `<measure>_out_of_range`, `<flag>_status_mismatch`, `<reason>_on_unexpected_status`, `exact_duplicate_row` |

### Geography (from the reviewed lookup; both endpoints)

Each of these columns exists twice, prefixed `pickup_` and `drop_`.

| Column | Meaning |
|---|---|
| `*_normalized_location` | Lookup display name |
| `*_latitude`, `*_longitude` | Representative locality point from the lookup (NA until sourced) |
| `*_city_or_zone` | Analyst-assigned zone (enrichment, not a source field) |
| `*_review_status` | `unresolved`, `proposed` or `reviewed` (NA if the label is missing from the lookup) |
| `*_is_mapped` | True only when the lookup row is `reviewed` **and** has coordinates |

The pickup and drop joins are two many-to-one left joins (`validate="many_to_one"`), and the row count is asserted to be unchanged after each. Unmapped rows stay in every non-map total.

## Location lookup (`data/reference/location_lookup.csv`)

| Column | Meaning |
|---|---|
| `original_location` | Source label: the unique join key |
| `normalized_location` | Display name (initially the same label) |
| `latitude`, `longitude` | Blank until sourced; both or neither |
| `city_or_zone` | Analyst zone assignment |
| `coordinate_source` | Source URL or reference (required for `reviewed`) |
| `review_status` | `unresolved` → `proposed` → `reviewed` (only a person sets `reviewed`) |
| `review_notes` | Interpretation notes, especially for ambiguous names (e.g. MG Road, Rajiv Chowk, Model Town) |

Validation rules (enforced in `preprocessing.validate_lookup`):
- The columns must be exactly the ones listed above.
- Keys must be unique after trimming whitespace.
- An `unresolved` row must have blank coordinates.
- A `reviewed` row needs coordinates, a `coordinate_source`, and a position inside the study-area sanity box: latitude 27.5–30.0, longitude 76.0–78.5. The box is wide enough for outlying labels such as Panipat, Meerut and Bhiwadi. It is a guard against typos, not a boundary definition.

Re-running preparation keeps every existing lookup row unchanged and appends new labels as `unresolved`. The lookup file is rewritten only when labels are added.

## Metric definitions (implemented in `metrics.py`)

The base cohort **B** is defined by the global filters: date range, vehicle type, weekday, hour, and optional pickup/drop labels. **Booking status is never a global filter.**

| Metric | Definition |
|---|---|
| Total bookings | Rows in B |
| Unique customer IDs | Distinct non-missing normalized `customer_id` in B |
| Completion rate | Completed / rows in B |
| Cancellation rate | (Cancelled by Customer + Cancelled by Driver) / rows in B |
| No-driver rate / incomplete rate | Their counts / rows in B |
| Non-completion rate | (rows in B − Completed) / rows in B |
| Payment mix | Completed rows in B with a recorded method, grouped by method; the eligible count is returned |
| Completed booking value | Sum of recorded `booking_value` on Completed rows; valid-record count returned; N/A if none recorded |
| Reason shares | Reason count / rows of that outcome with a recorded reason |
| Location reliability | Outcome rates per location over all outcomes; `sufficient_sample` when ≥ 50 records (a product guardrail, not a statistical test) |

Any rate with a zero denominator is N/A (`None`, or NA in tables). Aggregate rates are recomputed from counts and never averaged across subgroups.

**Calendar exposure.**
- Demand averages divide by *eligible calendar dates*: the selected date range, intersected with the dataset's coverage interval, restricted to the selected weekdays, minus known coverage gaps (dates with no source records at all, which are treated as unknown rather than zero demand).
- A date with zero bookings after vehicle or location filters still counts in the denominator.
- Weekdays with no eligible dates, and unselected hours, are masked (NA) rather than shown as zero.

## Known source characteristics (local file, SHA-256 `545118f7…`)

The local file reproduces `docs/PRIOR_DATA_AUDIT.md` exactly; see `reports/data_quality_report.md`. Notable features:
- Status counts are round numbers (93,000 / 10,500 / 27,000 / 10,500 / 9,000).
- Pickups per label fall in a narrow range (790–949).
- Normalized weekday demand varies by about 1.5%.

These patterns suggest the data may be synthetic or generated. Treat geographic and temporal differences as descriptive and small, and do not draw causal conclusions from them.
