# NCR RidePulse — Ride Demand, Reliability & Trip Intelligence

An independent, educational analytics project built on a Delhi-NCR ride-booking CSV associated with the Kaggle dataset [anilrohan/uber-data-india](https://www.kaggle.com/datasets/anilrohan/uber-data-india). It is **not** an official Uber application and does not describe verified Uber operations.

## Status

**Done:**
- **Phase 1 (data foundation):** repeatable preprocessing, shared metric functions, tests, data-quality reports and an unresolved location-lookup template.
- **Stage 3 (Streamlit dashboard MVP):** the three tabs in `app.py`.

- **Phase 2 (pilot map):** an interactive Pydeck connection map in the Trip Explorer, drawn on OpenStreetMap tiles. It currently covers **8 of 176** location labels (pilot coverage, clearly labelled in the app).

**Not done:**
- **Coordinates for the other 168 labels.** Only the 8 reviewed pilot locations are on the map; MG Road, Khandsa and 166 more are unmapped, so the map shows a small slice of the data. See [docs/COORDINATE_REVIEW.md](docs/COORDINATE_REVIEW.md).
- **Browser checks.** Only partly done; see Known limitations.

See `PROJECT_BRIEF.md` §9 for the phase plan.

## Setup

Requires [uv](https://docs.astral.sh/uv/). The project pins Python 3.14 (`.python-version`); `pyproject.toml` allows ≥3.12. Dependencies are locked in `uv.lock`.

```text
uv sync
```

Put the raw data file `ncr_ride_bookings.csv` in the project root. It is git-ignored and must not be published until redistribution rights are confirmed.

## Commands (run from the project root)

```text
uv run python preprocessing.py --input ncr_ride_bookings.csv
uv run python -m pytest -q
uv run ruff check .
uv run streamlit run app.py
```

Run preprocessing before starting the app. The app reads only the prepared data, never the raw CSV. It refuses to start, and tells you how to regenerate the data, if the prepared file is missing, has been edited, or is older than the current lookup or source file. Open the local URL that Streamlit prints, which is usually http://localhost:8501.

`preprocessing.py` options: `--output` (prepared CSV), `--lookup` (lookup CSV), `--reports-dir`. It refuses any output path that would overwrite the input.

## What preparation produces

| Output | Purpose | In git? |
|---|---|---|
| `data/processed/rides_prepared.csv` | One typed row per source booking record | No (derived booking-level data) |
| `data/processed/rides_prepared.meta.json` | Source/lookup/prepared fingerprints; `load_prepared()` uses them to reject stale data | No |
| `data/reference/location_lookup.csv` | One row per location label; reviewed rows are preserved on re-runs | Yes |
| `reports/data_quality_report.{json,md}` | Fingerprint, row counts per step, ID checks, statuses, parse failures, missingness by status, geography coverage | Yes |

Column and metric definitions are in [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).

## Code layout

- `preprocessing.py` — reading, normalization, derived fields, validation, lookup maintenance, validated geographic joins, typed reload and stale-data checks, reports, and the CLI.
- `metrics.py` — pure functions: the base-cohort filter, KPIs, outcome mix, payment mix, calendar-exposure demand normalization, trends, origin–destination flows, location reliability, and reason breakdowns. No UI code and no hardcoded values.
- `maps.py` — Trip Explorer geography:
  - map coverage, top-N connections, locality hotspots, and reliability rankings with a 50-record guardrail;
  - mapped flows that count records cut by the top-N limit separately from unmapped and same-location records;
  - a map view fitted to all reviewed points;
  - reading map clicks;
  - Pydeck map builders for arcs, hotspots and reliability.
- `static/osm_basemap_style.json` — a style file that uses the OpenStreetMap standard tiles, muted so the arcs stand out. Streamlit serves it because `server.enableStaticServing = true` is set. Pydeck's default CARTO basemap is deliberately not used, because CARTO's current terms require your own API key.
- `charts.py` — Plotly figure builders and number formatting. Each entity has a fixed colour; booking outcomes always carry text labels.
- `app.py` — the Streamlit layout only: cached data loading, sidebar filters held in session state, and the three tabs.
- `.streamlit/config.toml` — the accent colour; anonymous usage statistics are switched off.
- `tests/` — synthetic fixtures for logic tests:
  - `test_real_data.py` checks the local CSV and runs the prior-audit regression only when the file's SHA-256 matches the audited version.
  - `test_app.py` drives `app.py` headlessly with Streamlit's `AppTest`. That covers widgets, reruns and session state, but not a real browser.

## Dashboard guide

**Sidebar (applies to every tab).** Filters: booking date range, vehicle type, day of week, booking hour, pickup location and drop location. An empty multiselect means "all". **Reset all filters** restores the full dataset. Booking status is deliberately not a global filter. The line under the title shows the active filters and how many records remain.

**Tabs:**
1. **Executive Overview**
   - Six KPI cards.
   - All five booking outcomes, with one computed operational insight.
   - Weekly or daily booking volume, with completion rate shown as a separate chart.
   - Top 5 pickup and drop locations.
2. **Rider & Demand**
   - A weekday × hour heatmap of average bookings per eligible calendar occurrence. Cells outside the selection are blank, not shown as zero.
   - Bookings by day of week (normalized) and by hour.
   - Bookings by vehicle category.
   - Payment mix on completed bookings.
3. **Trip Explorer**
   - A banner showing map coverage: how many labels are reviewed and how many records can be mapped.
   - A map (about 70% of the width) with three modes:
     - **Trip flows:** directional arcs, blue at the pickup end and orange at the drop end, with width showing bookings. A top-N slider (default 20) and a requested-vs-completed toggle apply only to the flows. Selecting a location shows only the connections leaving it.
     - **Demand hotspots:** circle size shows pickup or drop volume.
     - **Service reliability:** circle colour shows the chosen failure rate; locations with fewer than 50 records are grey.
   - Hover over any point or arc for a tooltip; click it to load it into the details panel. Each map has a legend and states how many records were drawn, cut by the top-N limit, unmapped, or same-location.
   - Charts and tables below the map cover every location, including those not on the map.
   - A details panel for a pickup location or a connection: outcome rates, top destinations, and cancellation reasons with their denominators. A selection that the filters remove is cleared automatically.

## Data assumptions

- One source row is one booking record. Repeated Booking IDs are kept; `source_record_key` identifies rows.
- Date and Time are local booking time; no time-zone conversion is applied.
- Cancellation means customer plus driver cancellations. No Driver Found and Incomplete are separate outcomes.
- Missing measures stay missing; nothing is zero-filled.
- Booking Value is in source currency units. INR is a proposed label that has not been verified. It is not revenue or profit.
- Avg VTAT and Avg CTAT keep their source names because their meaning has not been verified.
- Demand means booking requests recorded in this file, not total travel demand in NCR.

## Known limitations

- The source has location names only. Map coordinates come from a sourced, reviewed lookup; coordinates are never invented. So far 8 of 176 labels are reviewed, which covers about 4.7% of pickups and 0.19% of trips with both ends mapped (285 bookings across 55 connections).
- The basemap needs internet access to `tile.openstreetmap.org`. The OSM tile policy applies: light use only, with "© OpenStreetMap contributors" shown on the map.
- Streamlit reads `.streamlit/config.toml` only at startup. If you started the app before static serving was enabled, restart it, or the basemap style won't load.
- By default Streamlit listens on all network interfaces. For a local-only demo, add `--server.address localhost`.
- The data's provenance, licence, time zone, currency and some field meanings are unverified.
- The data's distributions look synthetic: round status totals, near-uniform location volumes, and weekday differences of about 1.5%. Differences between areas and times should be read as small and descriptive.
- Locality pairs are sparse (the largest pair has 17 records), so per-route rates are not reliable evidence.
- **Browser checks are partial.** The user's screenshot confirmed the header, tabs and Trip Explorer banner render. Not yet verified: map tiles drawing, clicking points or arcs, tooltips, layout at different widths, and dark mode. The app tests are headless (`AppTest`), and those can't click on a Pydeck map.
