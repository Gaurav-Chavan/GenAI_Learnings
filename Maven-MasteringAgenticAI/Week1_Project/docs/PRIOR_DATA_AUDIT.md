# NCR Ride Bookings — Data Audit and Design Implications

Audit date: 9 October 2026

## Scope and method

Source: uploaded `ncr_ride_bookings.csv` (25,537,048 bytes).
SHA-256: `545118f78c629cf8495770a2c16f839a5a3fbcbd0af7982c98309d5dc6db72e7`

Computed directly from the entire uploaded file using Python's csv module, Counter, datetime, and numerical aggregations. The raw file was not changed.

Null handling: empty strings and case-insensitive `null`, `nan`, and `none` count as missing. Identifier normalization trims whitespace and literal surrounding double-quote characters.

Working analytical grain: one source row = one booking record. This is an explicit assumption, not a claim that the source Booking ID is unique. No records were dropped.

## Baseline

| Measure | Result |
|---|---:|
| Booking records | 150,000 |
| Columns | 21 |
| Distinct normalized booking IDs | 148,767 |
| Booking IDs appearing more than once | 1,224 |
| Excess occurrences of booking IDs | 1,233 |
| Exact full-row duplicates | 0 |
| Distinct normalized customer IDs | 148,788 |
| Customer IDs appearing more than once | 1,206 |
| First date | 2024-01-01 |
| Last date | 2024-12-30 |
| Distinct observed dates | 365 |
| Unique pickup location labels | 176 |
| Unique drop location labels | 176 |
| Unique labels across pickup and drop | 176 |
| Distinct ordered pickup–drop pairs | 30,564 |
| Maximum records for a single ordered pair | 17 |

## Booking outcomes

| Status | Records | Share of all booking records |
|---|---:|---:|
| Completed | 93,000 | 62.00% |
| Cancelled by Customer | 10,500 | 7.00% |
| Cancelled by Driver | 27,000 | 18.00% |
| No Driver Found | 10,500 | 7.00% |
| Incomplete | 9,000 | 6.00% |

Cancellation rate = (Cancelled by Customer + Cancelled by Driver) / all booking records = 25.00%.

Non-completion rate = 1 − Completed / all booking records = 38.00%. No Driver Found and Incomplete are not included in the cancellation numerator.

Each of the three exception flags exactly agrees with its corresponding status in this file; missing flags are not numeric zero values in the raw data.

## Identifier interpretation

Some repeated Booking IDs occur on different dates and have different customers and/or outcomes. Do not blindly drop duplicates based on Booking ID. Preserve the source ID and add a stable source-row key. Reassess the row-grain assumption if authoritative source documentation becomes available.

Only 1,206 distinct customer IDs appear more than once (about 0.81% of all distinct customer IDs). This is weak support for a retention/cohort-centric product. Distinct identifiers should not be presented as verified real people.

## Time, vehicle and payment findings

Highest-volume hour: 18:00–18:59, with 12,397 booking records (8.26% of all records).

Weekday totals should be normalized by the number of occurrences of each weekday in the selected date range. This file contains 53 Mondays and 52 of every other weekday; the range ends on 30 December, not 31 December.

| Weekday | Total booking records | Average per occurrence |
|---|---:|---:|
| Monday | 21,644 | 408.38 |
| Tuesday | 21,391 | 411.37 |
| Wednesday | 21,413 | 411.79 |
| Thursday | 21,215 | 407.98 |
| Friday | 21,397 | 411.48 |
| Saturday | 21,542 | 414.27 |
| Sunday | 21,398 | 411.50 |

The strongest normalized weekday exceeds the weakest by only about 1.54%. Do not turn a small descriptive difference into a strong behavioral or causal conclusion.

| Vehicle type | Booking records |
|---|---:|
| Auto | 37,419 |
| Go Mini | 29,806 |
| Go Sedan | 27,141 |
| Bike | 22,517 |
| Premier Sedan | 18,111 |
| eBike | 10,557 |
| Uber XL | 4,449 |

These are bookings by vehicle category, not counts of physical vehicles. There is no vehicle ID or driver ID column.

| Payment method (Completed only) | Completed records | Share of completed records |
|---|---:|---:|
| UPI | 41,834 | 44.98% |
| Cash | 23,114 | 24.85% |
| Uber Wallet | 11,206 | 12.05% |
| Credit Card | 9,320 | 10.02% |
| Debit Card | 7,526 | 8.09% |

## Value and missing-data handling

Sum of Booking Value for Completed records: 47,260,574 source currency units.
Mean Booking Value for Completed records: 508.18 source currency units.
Sum of Booking Value across all non-null records: 51,846,183. This includes 4,585,609 on Incomplete records.

The currency is not encoded in the CSV. INR is a reasonable proposed display assumption for this project, but should be checked against source documentation. Booking Value is not Uber net revenue, profit, commission, or a verified settled payment.

Payment Method, Booking Value, Ride Distance and Avg CTAT are present on Completed and Incomplete records and absent on both cancellation statuses and No Driver Found records. Driver Ratings and Customer Rating are present only on Completed records. Avg VTAT is absent on No Driver Found records.

Do not fill numeric missing values with zero. Default fare, payment, distance and rating views to Completed records and show the valid-record denominator. Keep original Avg VTAT/Avg CTAT names until a reliable data dictionary confirms their operational meaning; do not infer trip speed or road travel time from ambiguous fields.

## Geography findings

There are location labels, but no latitude, longitude, GPS route geometry, or timestamped waypoints. Geographic enrichment has not been performed in this audit.

Create a reviewed location lookup containing the original label, normalized label, representative latitude and longitude, city/zone, coordinate source, review status, and review notes. Map unresolved labels explicitly; never invent coordinates or place unresolved labels silently at a default city center.

Names such as Rajiv Chowk, MG Road and Model Town may be ambiguous. The lookup must document the chosen interpretation. Some labels, including Panipat, Meerut and Bhiwadi, require the map extent to account for the wider study area rather than only central Delhi.

There are 30,564 ordered origin–destination pairs but at most 17 records for any one pair. No locality pair has 20 or more records in the full dataset. Fine-grained corridor cancellation-rate rankings would be based on very small samples.

Recommended design: zone-level flow overview, then selected-origin drill-down showing top destination counts. Use minimum-denominator rules for rate comparisons and visibly mark insufficient samples. Zone definitions and coordinates are proposed enrichment, not fields already in the source.

Arcs represent connections between representative locality points, not actual roads traveled. A density map based on these points represents locality-level concentration, not exact pickup hotspots.

## Top pickup and drop labels

| Rank | Pickup | Records | Drop | Records |
|---|---|---:|---|---:|
| 1 | Khandsa | 949 | Ashram | 936 |
| 2 | Barakhamba Road | 946 | Basai Dhankot | 917 |
| 3 | Saket | 931 | Lok Kalyan Marg | 916 |
| 4 | Badarpur | 921 | Narsinghpur | 913 |
| 5 | Pragati Maidan | 920 | Cyber Hub | 912 |

## Provenance and interpretive limitations

The file is the user's upload associated with the Kaggle dataset referenced in the conversation. Official Uber operational provenance, real-versus-synthetic status, redistribution license, exact time zone and ambiguous field semantics were not established by this audit.

Present the app as an independent educational analysis of the supplied dataset, not an official Uber dashboard or verified description of real NCR operations. Treat booking timestamps as local booking times only under an explicit assumption; they are not GPS departure/arrival traces.

## Metric and filtering contract (proposed)

Base cohort filters: date range, vehicle category, day/hour, and reviewed origin/destination geography. For a base cohort B:

- Total booking records = number of rows in B.
- Distinct customer IDs = distinct normalized, nonmissing Customer ID values in B.
- Completion rate = Completed rows in B / all rows in B.
- Cancellation rate = customer- or driver-cancelled rows in B / all rows in B.
- No-driver rate = No Driver Found rows in B / all rows in B.
- Non-completion rate = all non-Completed rows in B / all rows in B.
- Completed booking value = sum of nonmissing Booking Value on Completed rows in B.
- Payment mix = payment-method counts among Completed rows with nonmissing Payment Method.

Outcome/status controls used to show map layers must not silently change the denominator of completion/cancellation KPIs. Return not-applicable rather than a fabricated zero rate for an empty denominator. Demand is observed booking-request volume in this file, not total regional market demand.

## Initial development log

Completed: read the assignment requirements; profiled the entire CSV; verified status counts, ID uniqueness, null patterns, date coverage and origin–destination sparsity; researched mapping framework capabilities.

Proposed, pending scope approval: three-tab Streamlit app, Plotly charts and a Pydeck map; Executive Overview, Rider & Demand, and Trip Explorer; offline reviewed location lookup; map proof-of-concept before full UI development.

Not completed: app implementation, geocoding, coordinate validation, browser interaction testing, deployment or submission.
