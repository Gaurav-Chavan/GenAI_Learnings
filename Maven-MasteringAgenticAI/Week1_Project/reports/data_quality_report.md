# Data quality report — NCR ride bookings

Generated (UTC): 2026-10-09T16:58:38Z · pipeline 1.0.0

## Source

- File: `ncr_ride_bookings.csv` (25,537,048 bytes)
- SHA-256: `545118f78c629cf8495770a2c16f839a5a3fbcbd0af7982c98309d5dc6db72e7`
- Unchanged by this run: True
- Columns: 21 (matches expected schema: True)

## Assumptions

- One source row is one booking record. Booking ID is not assumed unique; source_record_key (source SHA-256 prefix + 1-based row number) identifies rows.
- Date and Time are treated as local booking time; no time-zone conversion is applied.
- Missing tokens (case-insensitive, after trimming): empty, 'null', 'nan', 'none'.
- Cancellation = Cancelled by Customer + Cancelled by Driver. No Driver Found and Incomplete are separate outcomes.
- Booking Value is in source currency units (INR is a proposed, unverified display label); it is not net revenue or profit.
- Avg VTAT and Avg CTAT keep their source names; their operational meaning is unverified.
- Location coordinates come only from reviewed lookup rows; unresolved labels stay unmapped.

## Row counts by step

| Step | Rows |
|---|---:|
| read raw CSV | 150,000 |
| normalize and derive fields | 150,000 |
| pickup and drop lookup joins | 150,000 |
| written and reloaded | 150,000 |

Saved/reloaded prepared data identical: True

## Coverage

- Dates: 2024-01-01 to 2024-12-30; 365 distinct dates of 365 calendar days; 0 coverage-gap dates
- Rows missing date / time: 0 / 0

| Weekday | Bookings | Eligible days | Avg per day |
|---|---:|---:|---:|
| Monday | 21,644 | 53 | 408.38 |
| Tuesday | 21,391 | 52 | 411.37 |
| Wednesday | 21,413 | 52 | 411.79 |
| Thursday | 21,215 | 52 | 407.98 |
| Friday | 21,397 | 52 | 411.48 |
| Saturday | 21,542 | 52 | 414.27 |
| Sunday | 21,398 | 52 | 411.5 |

## Identifiers

| Field | Distinct (normalized) | Missing | IDs repeated | Excess occurrences | Raw values with literal quotes |
|---|---:|---:|---:|---:|---:|
| booking_id | 148,767 | 0 | 1,224 | 1,233 | 150,000 |
| customer_id | 148,788 | 0 | 1,206 | 1,212 | 150,000 |

Exact full-row duplicates beyond first: 0. Repeated Booking IDs are retained; see `is_repeated_booking_id`.
Repeated Booking ID groups that differ in date: 1219, customer: 1224, status: 731.

## Booking outcomes

| Status | Records | Share |
|---|---:|---:|
| Completed | 93,000 | 62.00% |
| Cancelled by Customer | 10,500 | 7.00% |
| Cancelled by Driver | 27,000 | 18.00% |
| No Driver Found | 10,500 | 7.00% |
| Incomplete | 9,000 | 6.00% |

Completion rate 62.00%; cancellation rate 25.00% (customer + driver); no-driver rate 7.00%; incomplete rate 6.00%. Denominator: 150,000 rows.

### Exception flags vs status

| Flag | Status rows | Flag=1 on status | Flag missing off status | Mismatches |
|---|---:|---:|---:|---:|
| Cancelled Rides by Customer | 10,500 | 10,500 | 139,500 | 0 |
| Cancelled Rides by Driver | 27,000 | 27,000 | 123,000 | 0 |
| Incomplete Rides | 9,000 | 9,000 | 141,000 | 0 |

Missing flags on other statuses are recorded as missing, not as zero.

## Parsing and ranges

| Column | Parse failures |
|---|---:|
| Date | 0 |
| Time | 0 |
| Avg VTAT | 0 |
| Avg CTAT | 0 |
| Booking Value | 0 |
| Ride Distance | 0 |
| Driver Ratings | 0 |
| Customer Rating | 0 |
| Cancelled Rides by Customer | 0 |
| Cancelled Rides by Driver | 0 |
| Incomplete Rides | 0 |

| Measure | Allowed range | Violations |
|---|---|---:|
| avg_vtat | 0 to ∞ | 0 |
| avg_ctat | 0 to ∞ | 0 |
| booking_value | 0 to ∞ | 0 |
| ride_distance | 0 to ∞ | 0 |
| driver_rating | 1 to 5 | 0 |
| customer_rating | 1 to 5 | 0 |

## Missingness by status (present values)

| Status | Rows | avg_vtat | avg_ctat | booking_value | ride_distance | driver_rating | customer_rating | payment_method | customer_cancel_reason | driver_cancel_reason | incomplete_reason |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Cancelled by Customer | 10,500 | 10,500 | 0 | 0 | 0 | 0 | 0 | 0 | 10,500 | 0 | 0 |
| Cancelled by Driver | 27,000 | 27,000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 27,000 | 0 |
| Completed | 93,000 | 93,000 | 93,000 | 93,000 | 93,000 | 93,000 | 93,000 | 93,000 | 0 | 0 | 0 |
| Incomplete | 9,000 | 9,000 | 9,000 | 9,000 | 9,000 | 0 | 0 | 9,000 | 0 | 0 | 9,000 |
| No Driver Found | 10,500 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Rows with any quality issue code: 0

## Categories

Bookings by vehicle type (booking records, not vehicles): Auto 37,419, Go Mini 29,806, Go Sedan 27,141, Bike 22,517, Premier Sedan 18,111, eBike 10,557, Uber XL 4,449

Payment methods on completed rows (93,000 eligible, 0 completed without a method): UPI 41,834, Cash 23,114, Uber Wallet 11,206, Credit Card 9,320, Debit Card 7,526

Completed booking value: 47,260,574.0 source currency units over 93,000 of 93,000 completed rows.

## Geography

- Coordinate-like source columns: none (location names only)
- Distinct labels: pickup 176, drop 176, both endpoints 176
- Ordered pickup→drop pairs: 30,564; largest pair has 17 records; same-label rows 0
- Lookup rows: 176 ({'unresolved': 168, 'reviewed': 8}); added this run: 0

| Map layer | Eligible rows | Share |
|---|---:|---:|
| pickup_points | 7,068 | 4.71% |
| drop_points | 6,758 | 4.51% |
| flows_both_endpoints | 285 | 0.19% |

| Rank | Pickup | Records | Drop | Records |
|---:|---|---:|---|---:|
| 1 | Khandsa | 949 | Ashram | 936 |
| 2 | Barakhamba Road | 946 | Basai Dhankot | 917 |
| 3 | Saket | 931 | Lok Kalyan Marg | 916 |
| 4 | Badarpur | 921 | Narsinghpur | 913 |
| 5 | Pragati Maidan | 920 | Cyber Hub | 912 |
