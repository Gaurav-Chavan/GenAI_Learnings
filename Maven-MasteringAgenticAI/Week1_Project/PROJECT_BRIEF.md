# NCR RidePulse — product and implementation brief

Status: agreed product direction translated into implementation requirements. Application code, local environment tests, and reviewed geographic enrichment have not been supplied in this starter pack. Build incrementally with approval at each phase.

## 1. Product purpose

Build **NCR RidePulse — Ride Demand, Reliability & Trip Intelligence**: an independent educational analytics application using the user's Delhi-NCR ride-booking CSV, associated with the Kaggle dataset at https://www.kaggle.com/datasets/anilrohan/uber-data-india.

The primary user is a city operations analyst or manager exploring where and when bookings occur, which outcomes prevent completion, and which areas deserve further investigation. The app must be more useful than a collection of charts: shared filters and a selectable connection map should support exploration from recorded demand to booking outcomes.

Do not describe it as an official Uber application, verified company operational data, live monitoring, or a proven optimization system. Dataset provenance, currency, time-zone semantics, redistribution permission, and some field definitions remain to be verified. Distinguish descriptive observations, assumptions, and hypotheses.

## 2. Stack and scope

Use Python + uv + Streamlit + pandas + Plotly + Pydeck. Use pytest and Ruff for checks. Resolve compatible package versions locally, lock them with uv, and record installed versions.

Keep the raw CSV in the project root as the user requested. Prepare data with a repeatable local script; load the prepared booking-level dataset in the app; compute summaries after filters. Prepared CSV is sufficient initially. No database, separate web API, JavaScript frontend, paid LLM integration, machine-learning training, or live geocoding is required.

Proposed module structure, to be created during the appropriate phases:

```text
ncr-ridepulse/
  ncr_ride_bookings.csv          # user input, unchanged
  CLAUDE.md
  PROJECT_BRIEF.md
  BUILD_PROMPTS.md
  START_HERE.md
  pyproject.toml
  uv.lock
  .python-version
  app.py                        # UI entry point
  preprocessing.py              # CLI and reusable preparation functions
  metrics.py                    # pure calculations and shared filtering
  maps.py                       # aggregation, geographic validation, layers
  data/
    reference/location_lookup.csv
    processed/rides_prepared.csv
  reports/
    data_quality_report.json
    data_quality_report.md
  docs/
    DEVLOG.md
    PRIOR_DATA_AUDIT.md
    DATA_DICTIONARY.md
    DEMO_SCRIPT.md
  tests/
  README.md
```

Small structural adjustments are acceptable when explained; do not replace the agreed stack without approval.

## 3. Source inspection and preparation

Read the actual CSV header and profile the full dataset programmatically. Do not paste the full file into a chat context. The expected source fields are:

```text
Date, Time, Booking ID, Booking Status, Customer ID, Vehicle Type,
Pickup Location, Drop Location, Avg VTAT, Avg CTAT,
Cancelled Rides by Customer, Reason for cancelling by Customer,
Cancelled Rides by Driver, Driver Cancellation Reason,
Incomplete Rides, Incomplete Rides Reason, Booking Value,
Ride Distance, Driver Ratings, Customer Rating, Payment Method
```

Required rules:
- Preserve the raw source and original identifiers. Keep one row per source booking record and add a deterministic key tied to source-file SHA-256 and the 1-based record index. This is a source-record key, not a newly verified booking identity.
- Normalize surrounding whitespace and literal surrounding quote characters on identifier fields. Retain string types and missing IDs as missing, not the string 'nan'.
- Standardize missing representations and text whitespace. Apply only explicit, justified label mappings. Keep Bike and eBike distinct.
- Parse dates, times, and numeric columns with explicit reporting of failures. Derive booking timestamp, date, year-month, weekday label/order, hour, and weekend flag. Treat timestamps as local booking time only under a stated assumption; do not invent a time-zone conversion.
- Generate outcome flags from normalized Booking Status. Validate source exception flags against their relevant statuses; do not interpret every missing raw flag as recorded zero.
- Keep missing numerical measures missing. Flag unexpected statuses, invalid ranges, inconsistent fields, exact duplicates, and repeated IDs; do not silently remove records or repair them to match expected statistics.
- Keep ambiguous timing field names unchanged. Explain field definitions and derivations in DATA_DICTIONARY.md.
- Produce prepared booking-level data and JSON/Markdown quality reports. Include source fingerprint, actual coverage, row counts before/after each step, ID checks, status distribution, parse failures, missingness by status, and geography coverage.
- Make repeated preparation safe: preserve reviewed lookup entries; add newly observed labels as unresolved; do not regenerate the whole lookup and erase review work. Reject duplicate lookup keys and output paths that would overwrite raw input.
- Reload prepared CSV with explicit identifier, numeric, boolean, and date handling so saved/reloaded results agree. Detect stale artifacts using source/lookup fingerprints and require regeneration when relevant inputs change.

### Prior audit: reproduce locally, do not hardcode

`docs/PRIOR_DATA_AUDIT.md` is a previous audit of the uploaded file, not evidence that the local file is identical. It reported:

| Check | Prior result |
|---|---:|
| Booking records | 150,000 |
| Source columns | 21 |
| Distinct normalized Booking IDs | 148,767 |
| Exact full-row duplicates | 0 |
| Distinct normalized Customer IDs | 148,788 |
| Completed | 93,000 |
| Cancelled by Customer | 10,500 |
| Cancelled by Driver | 27,000 |
| No Driver Found | 10,500 |
| Incomplete | 9,000 |
| Completion rate | 62% |
| Cancellation rate | 25% |
| Observed date range | 2024-01-01 through 2024-12-30 |
| Distinct location labels across both endpoints | 176 |
| Distinct ordered locality pairs | 30,564 |
| Maximum records for one ordered locality pair | 17 |

Recompute these from the local input. Investigate discrepancies; do not alter data to make a test pass. Scope snapshot-specific regression expectations to the audited source version and keep general logic tests independent of that snapshot.

## 4. Shared metric and filter contract

Base cohort B is selected by date range, vehicle category, weekday/hour, and optional pickup/drop geography. Start with all available dates and vehicle types. Provide a reset action and visible active-filter summary.

- Total bookings = row count of B, with a tooltip explaining the booking-record assumption.
- Unique customer IDs = distinct normalized, nonmissing customer IDs in B; not verified individual people.
- Completion rate = completed rows / all rows in B.
- Cancellation rate = (customer-cancelled + driver-cancelled rows) / all rows in B.
- Customer cancellations and driver cancellations = their respective counts, with optional shares of B.
- No-driver and incomplete outcomes remain separate. Non-completion is not synonymous with cancellation.
- Payment mix = completed rows with a recorded payment method, grouped by that method; show the eligible count.
- Optional completed booking value = sum of recorded values on completed rows, with valid-record count and a documented currency assumption if needed. Do not label it Uber net revenue or profit.
- Recompute aggregate rates from aggregate counts, not an unweighted average of subgroup percentages.
- Return N/A for empty rate denominators and all-missing numerical measures. Show zero for an actual zero count.

Do not put Booking Status in the global filter bar in the initial MVP. Status controls used for map flows must not redefine executive or reliability denominators. Map selections drill into a local detail cohort; show that scope explicitly.

Demand means observed booking requests in this dataset, not all travel demand in NCR. Demand charts include every outcome unless a clearly labeled local control says otherwise.

For weekday averages and weekday-hour cells, use the number of eligible calendar occurrences in the selected inclusive date range, not the number of dates surviving vehicle/location filters. Respect selected weekdays and the known coverage interval. Keep zero-booking eligible days in the denominator; flag unknown coverage gaps rather than claiming they are measured zero demand. Mask unselected hours/days or no-exposure cells instead of labeling them zero demand.

## 5. Tab requirements

### Tab 1 — Executive Overview

Question: How is the selected region performing?

Six KPI cards: total booking records; unique customer IDs; completion rate; cancellation rate; customer cancellations; driver cancellations.

Below: booking-outcome composition covering all five statuses; daily/weekly booking-volume trend and a separately labeled completion-rate view; top five pickups; top five drops; one computed, factual takeaway. Avoid duplicated vehicle charts here. No fabricated comparison deltas.

### Tab 2 — Rider & Demand

Question: When are bookings requested and what do customers choose?

Hero chart: Monday-to-Sunday by 00-to-23 weekday-hour heatmap showing average bookings per eligible weekday occurrence. Include a clear unit and denominator explanation. Supporting views: normalized weekday bars, hourly demand, bookings by vehicle category, and payment mix on completed bookings.

Label vehicle analysis as bookings by vehicle type, never fleet counts. Do not prioritize retention, loyalty, lifetime value, or individual customer profiling for this MVP. Avoid causal claims based on small descriptive differences.

### Tab 3 — Trip Explorer

Question: Where do requested or completed trips connect, and where do booking outcomes suggest further investigation?

Give one large map roughly two-thirds of the tab width, with a selected-area/connection detail panel beside it. Use a single map panel with a mode selector:

1. Trip flows: origin-destination arcs with width based on counts. Toggle requested flows (all outcomes) versus completed flows. Use reviewed zone-to-zone overview where available, plus selected-locality top-destination drill-down. Keep directionality. Default to about 20 connections, with a bounded top-N control; do not render every raw pair.
2. Demand hotspots: aggregate pickup or drop counts at representative locality points. Clearly label locality-level concentration. Offer weighted density or sized point rendering as appropriate and tested.
3. Service reliability: point size indicates booking volume; color represents a selected failure rate. Calculate rates on the full base/location cohort, independent of the completed-flow toggle. Apply a documented minimum denominator, initially 50 records, and distinguish insufficient-sample areas. This is a product guardrail, not statistical proof.

Selecting a point or arc should update details: booking count, completion/cancellation/no-driver rates, top destinations, and cancellation reasons with explicit reason-share denominators. When selecting an arc, label route scope; selecting a location uses pickup scope unless otherwise labeled. Provide a dropdown alternative and a clear/reset selection control. Clear stale selections when filters remove the selected object. Prefer meaningful stable object IDs over relying on row positions across reruns.

Show tooltips with origin, destination, count, and relevant metrics; a legend for size/color/direction; active filters; map coverage; and a table alternative. Separate 'records omitted because unmapped' from 'records not drawn because of top-N'. Represent same-location or same-zone flows as a separate count/table or point treatment rather than invisible zero-length arcs.

Implement interactions using the installed Streamlit/Pydeck API; verify `st.pydeck_chart` selections, explicit layer IDs, pickable layers, and session state behavior against official documentation. Do not assume HeatmapLayer itself provides meaningful point selections; use selectable point overlays or the dropdown as necessary.

## 6. Geographic enrichment and map acceptance gate

The source is expected to have names, not coordinates or route geometry. Confirm this locally. Create `data/reference/location_lookup.csv` with:

```text
original_location,normalized_location,latitude,longitude,
city_or_zone,coordinate_source,review_status,review_notes
```

Keep one row per original source label, with a unique documented join key. Do not blindly merge similar place names. Document ambiguous interpretations, the source URL/reference, and actual review state. Proposed values are not reviewed merely because code generated them. Newly extracted labels start unresolved with blank coordinates.

A point must have suitable source evidence, acceptable coordinate bounds, and the required review status before appearing in the analytical map. Confirm Delhi-NCR interpretations and wider-area locations instead of forcing every label into central Delhi. City/zone definitions are analyst enrichment, not source-provided boundaries. Explain their mapping and use documented representative zone points.

Attach pickup and drop lookup fields using two validated many-to-one left joins and assert unchanged row count. Unmapped bookings remain in non-map calculations. A flow needs both endpoints; pickup demand/reliability needs only pickup coordinates; drop demand needs only drop coordinates. Report the eligible-row coverage for each layer separately.

Begin the map proof of concept with a small, sourced and reviewed subset (approximately 8–12 labels), clearly marked as partial coverage. Obtain approval for the coordinate-sourcing and basemap approach first. No random coordinates, unapproved bulk geocoding, or silent provider changes. No request should send booking IDs or customer IDs. If coordinates or research tools are unavailable, implement the lookup validation and explicit empty state, explain what is missing, and do not claim the map milestone is complete.

Use a basemap whose current usage terms and credentials fit the project. Document attribution, network requirements, and any key configuration. Test map tiles and selections in a real browser. A fallback data table keeps analytics usable if tiles fail; it does not count as the map being successfully demonstrated.

Always display: 'Representative locality coordinates. Connections show origin–destination relationships, not actual roads traveled.'

## 7. UI, performance, and scope discipline

Use a polished, restrained visual design with clear hierarchy, consistent spacing, aligned KPI cards, readable labels, and a prominent map. A dark/neutral map with distinct origin/destination accents is a proposed design, not a requirement to mimic Uber branding. Keep status colors consistent and do not rely on color alone.

Cache prepared-data loading using version/fingerprint inputs. Aggregate before sending map data to the browser. Keep full-data calculations out of repeated per-chart copies. Show loading, missing-input, invalid-schema, empty-filter, missing-lookup, partial-map, and tile-failure states without silently substituting demo data.

A deterministic 'Explain this view' summary is in scope; an LLM call is deferred. Generate statements only from computed results and explain filter context. Do not invent causal explanations or guaranteed operational improvements.

## 8. Verification and delivery

Minimum automated checks: correct status denominators; empty and all-missing cases; quoted IDs; retained repeated Booking IDs; numeric missingness; parsing failures; Monday-Sunday ordering; calendar-day normalization including zero-booking filtered days; directional OD grouping; unchanged row count after geographic joins; duplicate-lookup rejection; missing-coordinate coverage per layer; preserved reviewed entries on repeated preparation; prepared-data reload consistency; and source-version-aware baseline regression.

Use small synthetic test fixtures only in the test suite. Real-data checks must say when the real CSV is absent or differs from the audit. Test logic independently rather than reusing the function under test to create its expected answer.

Browser acceptance: app loads; each filter and reset works; map tiles render; point/arc selection and dropdown update the correct detail scope; unmapped/top-N coverage is visible; empty results are handled; small samples are labeled; no terminal/browser errors. Distinguish automated checks from manual browser checks and report untested items explicitly.

Maintain README setup/run instructions, data assumptions, source/license caveats, and known limitations. Append real prompts, decisions, errors, fixes, and test evidence to DEVLOG. Prepare a five-minute-or-less demo script and a screenshot checklist; do not claim screenshots or recordings exist before capturing them.

Assignment-derived delivery requirements (Week 1 handout, pages 2 and 5): document the problem and step-by-step vibe-coding process with screenshots; submit project documentation covering dataset, prompts, iterations and learning; show a working demo video of five minutes or less; share the GitHub codebase link. In-app AI interactions are optional (page 3). The detailed product and test requirements above are this project's agreed/proposed design, not extra requirements attributed to the handout.

## 9. Build sequence

Phase 0: inspect and plan only.
Phase 1: preparation, metric functions, tests, quality report, unresolved lookup template.
Phase 2: approve geographic sourcing; prove a small reviewed connection map and detail interaction.
Phase 3: expand reviewed lookup coverage and complete the three map modes.
Phase 4: complete Executive Overview and Rider & Demand, sharing the tested filter/metric layer.
Phase 5: polish, browser checks, documentation, and demo rehearsal.

Stop at each milestone for review. Do not mark a phase complete based on code generation alone.

## Official implementation references

These are technical references, not dataset provenance. Check them against installed versions:
- Claude Code VS Code: https://code.claude.com/docs/en/vs-code
- CLAUDE.md: https://code.claude.com/docs/en/memory
- Claude Code workflow: https://code.claude.com/docs/en/best-practices
- uv projects: https://docs.astral.sh/uv/guides/projects/
- uv minimal project: https://docs.astral.sh/uv/concepts/projects/init/
- uv dependency groups: https://docs.astral.sh/uv/concepts/projects/dependencies/
- uv Python pinning: https://docs.astral.sh/uv/concepts/python-versions/
- Streamlit Pydeck selections: https://docs.streamlit.io/develop/api-reference/charts/st.pydeck_chart
