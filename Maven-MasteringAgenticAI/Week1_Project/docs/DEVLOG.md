# NCR RidePulse — development log

## Starter handoff — 2026-10-09

Prepared: project brief, recurring Claude Code rules, staged prompts, and a copy of the prior CSV audit.

Approved direction from the project conversation: Python/Streamlit/Plotly/Pydeck; three tabs (Executive Overview, Rider & Demand, Trip Explorer); repeatable preprocessing; map-first implementation; reviewed location lookup.

User environment stated: VS Code with the Claude Code extension; uv installed; raw CSV to be kept in the project folder.

Not performed by this starter pack: setup on the user's machine, implementation of the data pipeline or app, coordinate sourcing/review, local test runs, browser checks, deployment, screenshots, video recording, or GitHub publication.

Prior audit: `PRIOR_DATA_AUDIT.md` describes a separate audit of the uploaded CSV. Reproduce and compare it locally before treating those values as local verification.

Next gate: Phase 0 inspection/plan, followed by explicit approval for Phase 1.

---

### 2026-10-09 / Phase 0 / Inspect and plan

**User prompt:** BUILD_PROMPTS.md Prompt 0, lightly reworded (the three tabs were described in more detail). Inspect and plan only; no file changes, installs or geocoding.

**Approval and scope:** Read-only inspection. No project files changed.

**Observed inputs:**
- `ncr_ride_bookings.csv`: 25,537,048 bytes, SHA-256 `545118f78c629cf8495770a2c16f839a5a3fbcbd0af7982c98309d5dc6db72e7`. This is identical to the prior audit.
- 21 columns, as expected.
- uv 0.12.23.
- The existing `.venv` uses CPython 3.14.8, and there was no `.python-version` file. `pyproject.toml` requires ≥3.12.
- Installed: pandas 3.0.6, numpy 2.5.3, plotly 7.1.0, pydeck 0.9.3, streamlit 1.65.0, pytest 9.1.1, ruff 0.16.10, pyarrow 25.0.1.
- No `.gitignore` existed. `Readme.md` was empty.

**Decision and rationale:** A read-only profile (stdlib `csv`, run from a session scratchpad script) reproduced every audit baseline value. It also found that all 150,000 Booking and Customer IDs carry literal surrounding quotes, and that missing values are the literal text `null`. The plan was approved with these recommendations:
- Keep Python 3.14 and pin it.
- Add a `.gitignore` that excludes the raw CSV and derived booking-level data.
- Write the README into the existing `Readme.md`.

**Changed files:** None.

**Commands executed and results:**
- `uv --version`
- `Get-FileHash` on the CSV
- The read-only profiling script (exit 0)
- `uv lock --check --offline` (exit 0)
- `uv sync --locked --dry-run --offline` ("Would make no changes")

**Browser/manual verification:** None (no UI).

**Open issues / next approval:** Phase 1 approval. Granted: the user replied "Proceed with your recommendation" and sent Prompt 1.

### 2026-10-09 / Phase 1 / Data foundation

**User prompt:** BUILD_PROMPTS.md Prompt 1 (as written), preceded by "Proceed with your recommendation."

**Approval and scope:** Phase 1 only:
- Preprocessing, metrics, tests, reports and an unresolved lookup template
- A data dictionary and README
- No Streamlit UI and no coordinates

**Observed inputs:** Same source SHA-256 as Phase 0. The lockfile is unchanged; no packages were added or upgraded.

**Decision and rationale:**
- **Python version:** pinned to 3.14 (`uv python pin 3.14`) to match the existing environment rather than rebuilding it for 3.12.
- **Reading:** every source cell is read as verbatim text first, then typed explicitly. Raw IDs are kept alongside the normalized IDs.
- **Row identity:** `source_record_key` = first 16 hex characters of the SHA-256 + `:` + row number.
- **Status-dependent fields:**
  - Outcome booleans come from the normalized status.
  - The source exception flags are validated against the status. A missing flag is kept as missing, not 0.
- **Numeric measures** are nullable `Float64`, and range violations are only flagged. Every issue is recorded in a row-level `quality_issues` code list; no rows are dropped.
- **Lookup:**
  - Read as verbatim text and validated: exact columns, unique keys after trimming, the review-status vocabulary, both-or-neither coordinates, no coordinates on unresolved rows, and reviewed rows needing a source and a position inside a coarse NCR sanity box.
  - New labels are appended as unresolved. The file is only rewritten when labels are added, so reviewed work is preserved.
- **Geographic joins:** two `merge(validate="many_to_one")` left joins with a row-count assertion. `*_is_mapped` is true only for reviewed rows with coordinates.
- **Reload and staleness:**
  - The prepared CSV is reloaded with explicit dtypes and compared with `assert_frame_equal` on every run.
  - A `.meta.json` sidecar records the source, lookup and prepared fingerprints plus the pipeline version. `load_prepared()` refuses stale or edited outputs.
  - Output paths that resolve to the raw input are refused.
- **Metrics:**
  - Pure functions; rates return None (N/A) for an empty denominator; rate columns use nullable `Float64`.
  - Calendar exposure is built from calendar dates, intersected with coverage, restricted to the selected weekdays, minus coverage gaps.
  - Weekdays with no eligible dates and unselected hours are masked, not shown as zero.
- **Prior-audit values** live only in `tests/baselines/prior_audit_545118f7.json`. That regression test runs only when the source SHA matches, and skips with an explanation otherwise.
- **pandas 3 behaviours checked first:** the default string dtype; `to_datetime` gives `datetime64[us]`; `to_csv` writes CRLF on Windows unless told otherwise. The pipeline sets `lineterminator="\n"` and casts datetimes to `datetime64[us]` explicitly.

**Changed files:**
- Created: `preprocessing.py`, `metrics.py`, `.python-version`, `.gitignore`, `tests/conftest.py`, `tests/test_preprocessing.py`, `tests/test_lookup.py`, `tests/test_metrics.py`, `tests/test_real_data.py`, `tests/baselines/prior_audit_545118f7.json`, `docs/DATA_DICTIONARY.md`
- Generated: `data/reference/location_lookup.csv`, `data/processed/rides_prepared.csv`, `data/processed/rides_prepared.meta.json`, `reports/data_quality_report.json`, `reports/data_quality_report.md`
- Modified: `pyproject.toml` (Ruff and pytest configuration only), `Readme.md` (was empty), `docs/DEVLOG.md`

**Commands executed and results:**
- `uv python pin 3.14` → pinned. A sync dry-run afterwards still reported no changes.
- `uv run python preprocessing.py --input ncr_ride_bookings.csv` → exit 0, about 17 s. Rows 150,000 at every step; 176 lookup labels added as unresolved; 0 flow-eligible rows.
- A re-run → exit 0 with 0 labels added. The lookup file was not rewritten (its timestamp is unchanged).
- `uv run python -m pytest -q` → **63 passed, 0 failed, 0 skipped**, about 21 s. This includes the real-data invariants and the prior-audit regression.
- `uv run ruff check .` → first run found 7 issues (long lines; `zip()` calls without `strict=`). After fixing those and running `ruff format` on the new files: **All checks passed**.
- A mutation check in a scratchpad copy:
  - Counting No Driver Found as a cancellation → 1 test failed.
  - Zero-filling missing numerics → 19 failed.
  - Using days-with-bookings as the weekday denominator → 3 failed.
  
  So the tests detect these rule violations.
- The raw CSV's SHA-256 and size are unchanged after all runs.

**Recomputed vs prior audit:** every audited value matched, with no discrepancies:
- Rows, columns and ID statistics; exact duplicates
- Status counts and rates; date range
- Weekday totals, occurrences and averages; busiest hour
- Vehicle counts and completed-ride payment mix
- Booking-value totals and mean
- Label counts, pair counts, largest pair, and top 5 pickups/drops

New observations:
- All 1,224 repeated-Booking-ID groups have different Customer IDs, and 1,219 have different dates. That supports keeping them as distinct records.
- No quality-issue codes were raised on the local file.

**Browser/manual verification:** Not applicable (no UI yet).

**Bugs and fixes:**
- The test helper first mapped keyword arguments to column names; it was replaced with an explicit `fields` dict.
- Rate lists containing `None` became `float64`/`NaN`; they now use explicit nullable `Float64`.
- The baseline JSON was first written one folder too high (`Maven-MasteringAgenticAI/tests/…`). That misplaced file was deleted, and the file was recreated under `Week1_Project/tests/baselines/`.
- One combined verification command was rejected at the permission prompt and re-run as separate commands. Permission settings were not changed.

**Learning:** Reproducing the audit against a fingerprinted source turned "the numbers look right" into a version-scoped regression test. The deliberate-bug check showed the tests actually guard the business rules.

**Open issues / next approval:**
- The lookup has 0 coordinates, so no map layer has any eligible rows yet.
- Unverified: currency, time zone, the meaning of VTAT/CTAT, provenance and licence. The data may be synthetic.
- Next: Phase 2A. Propose 8–12 pilot labels, coordinate sources, a review workflow, a zone convention and a basemap. Needs approval before any network lookup.

### 2026-10-09 / Stage 3 / Streamlit dashboard MVP

**User prompt:** "We have successfully completed Stage 1 … and Stage 2 (Map Proof of Concept) … I am approving Stage 3: Streamlit Dashboard Development." The prompt asked for:
- `app.py` with a global filter sidebar and three tabs.
- Reuse of the existing metric and map functions, with `st.cache_data` for loading.
- Validation through Streamlit, pytest and Ruff, plus KPI, filter, reset and map checks.

**Approval and scope:** Stage 3 only. No database, chatbot, new framework or architecture change.

**Observed inputs — the Stage 2 discrepancy:** Before building anything, I checked the claimed Stage 2 work. It does not exist in this project:
- There is no `maps.py`, `app.py`, wireframe or UI spec anywhere under `Desktop\Maven`.
- There is no git branch or stash containing it, and no Stage 2 entry in this log.
- `data/reference/location_lookup.csv` is unchanged since Phase 1: 176 `unresolved` rows, 0 reviewed, blank coordinates.

I followed the prompt's own fallback rule ("If any advanced map mode is not ready, show the working mode and report the remaining work. Do not silently substitute mock data"). The Trip Explorer is built as a table-based explorer with an explicit "map not available" state. No coordinates were sourced and no basemap was chosen; Phase 2A approval is still required for both.

**Decision and rationale:**
- **Module split.**
  - `app.py` holds the layout only.
  - New `charts.py` holds the Plotly figure builders and formatting.
  - New `maps.py` holds the Trip Explorer aggregation.
  
  The brief already planned a `maps.py`. `charts.py` is a small addition so figure code can be tested and doesn't sit inside the UI file. No KPI logic is duplicated in `app.py`.
- **Loading.**
  - The app loads the prepared data with `preprocessing.load_prepared`, which runs the fingerprint and staleness checks, and keeps 26 of the 55 columns.
  - It's wrapped in `st.cache_data`, keyed on each input file's modification time and size.
  - The raw CSV is only fingerprint-checked, never read for analysis.
  - Measured: about 2.5 s for a cold load; about 0.05 s per rerun to copy the cached frame; about 0.07 s for filtering plus all metrics.
- **Filters.**
  - Sidebar widgets are tied to session-state keys (`f_*`), with defaults set before the widgets are created.
  - Reset is an `on_click` callback that writes the defaults back and also clears the Trip Explorer selection.
  - An empty multiselect means "all".
  - A half-selected date range runs from the start date to the end of coverage, with a note.
  - Booking status is not a global filter. Requested vs completed is a local control for trip flows only.
- **Demand normalization** reuses `metrics.eligible_dates` (calendar exposure). Unselected hours and weekdays without eligible dates are masked, not shown as zero.
- **Weekly trend.** I added `avg_bookings_per_day` to `metrics.volume_trend`. The last week of 2024 has a single day (Monday 30 December), and plotting that week's raw total would show a false collapse.
- **KPI cards.** The shares of customer and driver cancellations appear with `delta_arrow="off"` and `delta_color="off"`, so they can't be read as changes between periods. There are no fabricated deltas.
- **Insight.** One deterministic statement: the largest non-completion outcome, its share of all bookings and its share of the non-completed bookings.
- **Trip Explorer.**
  - The coverage banner reports reviewed labels and mappable rows per layer (currently 0).
  - Records cut by the top-N limit and unmapped records are counted separately.
  - Same-location records are counted separately.
  - Sparse routes carry a warning.
  - Reliability rankings put locations below 50 records last and label them.
  - The details panel is driven by a dropdown, with stable string IDs for locations and connections. A selection that disappears after a filter change is cleared with a notice.
- **Charts** use the dataviz skill's validated palette:
  - slot 1 blue for pickups and single-series charts; slot 2 orange for drops
  - status colours plus grey for the five outcomes, always with text labels
  - a one-hue blue ramp for the heatmap
  - no dual axes; table views available in expanders.
- **Config.** `.streamlit/config.toml` sets only the accent colour and `gatherUsageStats = false`. Light or dark mode follows the viewer's setting.

**Changed files:**
- Created: `app.py`, `charts.py`, `maps.py`, `.streamlit/config.toml`, `tests/test_app.py`, `tests/test_maps.py`, `tests/test_charts.py`
- Modified:
  - `metrics.py`: new `avg_bookings_per_day` column in `volume_trend`.
  - `tests/conftest.py`: the shared helpers `trip`, `lookup_frame`, `reviewed` and `unresolved` moved here from `test_lookup.py`.
  - `tests/test_lookup.py`: imports those helpers.
  - `tests/test_metrics.py`: a weekly-average assertion.
  - `Readme.md`, `docs/DEVLOG.md`.

**Commands executed and results:**
- Read the installed Streamlit 1.65 signatures and docstrings: `tabs` (`on_change`, `key`), `multiselect` (`persist_state`, `bind`), `dataframe` selection, `metric` (`delta_arrow`), `pydeck_chart` selection.
- Headless smoke run with `streamlit.testing.v1.AppTest` against the real prepared data. No exceptions. Unfiltered KPIs: 150,000 · 148,788 · 62.0% · 25.0% · 10,500 · 27,000.
- `uv run ruff check .`: 3 long lines on the first run, fixed; then **All checks passed**. `ruff format --check`: 22 files already formatted.
- `uv run python -m pytest -q`: **92 passed, 0 failed, 0 skipped**, about 37 s. The 29 new tests are 7 for maps, 11 for charts and 11 headless app tests.
- `uv run streamlit run app.py --server.headless true --server.port 8501` (in the background):
  - `/_stcore/health` returned `200 ok`; `/` returned 200.
  - The server log showed no errors.
  - The server was then stopped and port 8501 freed.
  - The script itself runs only when a browser session connects, which did not happen here.

**Verified against the audit (headless app tests):**
- **KPIs:** the six unfiltered KPIs match the audit.
- **Vehicle filter (Auto):** gives 37,419 in the KPI card, the heatmap data and the Trip Explorer caption, so all three tabs update together.
- **Reset:** restores 150,000 and the default widget values.
- **Empty cohort:** pickup = drop = Saket shows 0 / N/A and an info message per tab, with no errors.
- **Saket location detail:** shows 931 bookings.
- **Stale selection:** cleared with a notice.
- **Remaining views:** every Trip Explorer mode, the daily trend and the connection detail scope render without exceptions.

**Browser/manual verification:** **Not performed.** No browser tool was available in this session. Untested:
- visual layout and the roughly 70/30 split at desktop width
- chart appearance, hover tooltips and label overlap
- light and dark themes
- multiselect search typing
- date-picker interaction and the real click flow
- any browser console errors
- map tiles and map selections (no map exists to test)

**Bugs and fixes:**
- My smoke script used a relative `AppTest.from_file` path, which `AppTest` resolves against the calling file. I switched to an absolute path.
- A test module imported helpers from another test module; they were moved to `conftest.py`.

**Learning:** Checking a stated premise against the files on disk before building caught a missing prior stage. Streamlit's `AppTest` lets the widget, filter and reset logic be tested without a browser, but it is not evidence that the visual result is right.

**Open issues / next approval:**
1. Browser review of the three tabs (checklist in the Stage 3 summary).
2. Phase 2A: approve coordinate sourcing, the review workflow and the basemap, then build the pydeck map into the Trip Explorer's existing panel.
3. Confirm the currency label and the remaining provenance questions.

### 2026-10-09 / Stage 3 follow-up / Browser screenshot and "why no map?"

**User prompt:** a screenshot of the Trip Explorer tab, with "Why I am not getting map for 3rd tab ?"

**Observed:** this was the first real-browser evidence. The header, filter summary, tabs, map-status banner and mode selector all render. There is no map because the lookup had 0 reviewed coordinates. That is the designed empty state, not a bug.

**Response:** explained the cause and proposed the Phase 2A pilot: 10 labels, OSM and Wikidata sources, and a basemap choice.

### 2026-10-09 / Phase 2A / Pilot coordinate sourcing

**User prompt:** "Approved", in reply to the Phase 2A proposal (pilot labels, OSM/Wikidata sources, basemap choice).

**Approval and scope:** name-only lookups for 10 pilot labels, written as `proposed`. Reviewing them, choosing the basemap and building the map remain separate steps.

**Policy checks (2026-10-09):**
- **Nominatim:** at most 1 request/s, identifying User-Agent, results cached, attribution, no systematic or bulk queries.
- **CARTO basemap terms (updated 2026-09-29):** your own API key is now required. Raster tiles stopped working without a key on 2026-09-25. Free for non-commercial use up to 5M tiles/month, crediting CARTO and OSM.
- **OSM tile policy:** no keys exist; a real User-Agent and Referer are required; show "© OpenStreetMap contributors"; no bulk downloading or prefetching.

**What was done:**
- Scratchpad scripts, not project code, ran the lookups.
- **Nominatim:** 15 requests at least 1.2 s apart. One query (MG Road) was sent twice: the first script saved each label only after both of its lookups finished, so MG Road's result was lost when Wikidata refused.
- **Wikidata:** 15 requests answered, then HTTP 429 on the 16th (MG Road's search). I briefly misreported this to the user as a refusal of the first request; corrected after checking the cache timestamps. The script was rewritten to cache each response immediately and stop Wikidata calls after any refusal. Two spaced requests later resolved Saket.
- **Proposed:** 10 rows written to `location_lookup.csv`. The script refused to change any row that wasn't `unresolved` and asserted the other 166 rows were unchanged.
- **Cross-checks:**
  - Seven labels have a second source 0.19–1.07 km away.
  - Cyber Hub, Noida Sector 62 and MG Road are single-source.
  - MG Road is flagged ambiguous, and Khandsa has coarse precision with an ambiguous meaning.
- **Evidence:** raw responses saved to `data/reference/coordinate_evidence/pilot_2026-10-09.json`.
- **App:** the map banner now reports proposed labels "awaiting review" instead of "sourcing awaiting approval". `MapCoverage.proposed_labels` was added.

**Changed files:**
- `data/reference/location_lookup.csv`: 10 rows → `proposed`.
- `data/reference/coordinate_evidence/pilot_2026-10-09.json`: new.
- `docs/COORDINATE_REVIEW.md`: new.
- `maps.py`, `app.py`, `tests/test_maps.py`, `docs/DEVLOG.md`.

**Commands and results:**
- `uv run python preprocessing.py --input ncr_ride_bookings.csv`: exit 0. Lookup {unresolved 166, proposed 10}; flow-eligible rows 0, which is correct because proposed points are not mapped.
- `uv run python -m pytest -q`: **92 passed**.
- `uv run ruff check .`: **All checks passed**; formatting clean.

**Expected coverage once reviewed (computed):**
- All 10 labels: pickups 8,837 (5.9%), drops 8,424 (5.6%), flows 443 (0.30%) across 88 directed pairs, busiest pair 12.
- Without MG Road: flows 359 across 71 pairs.

**Open issues / next approval:**
1. The user reviews the 10 rows (`docs/COORDINATE_REVIEW.md`).
2. The user chooses a basemap: A (OSM tiles, no key), B (CARTO with their own key) or C (none, partial).
3. Then Phase 2B: build the Pydeck map in the Trip Explorer.

### 2026-10-09 / Phase 2B + Phase 3 (map) / Interactive connection map on OpenStreetMap

**User prompt (summary):**
- "Let's accelerate development … I approve OpenStreetMap as the initial basemap. Please proceed with Phase 2B and Phase 3 without waiting for all 176 locations to be reviewed."
- Priority 1, the map: "Use only confidently verified pilot coordinates. Keep ambiguous locations such as MG Road and Khandsa unresolved." Also: wider-NCR and long-distance support, arcs, counts, tooltips, location selection, transparent coverage, and a visually impressive map.
- Priorities 2–3: the three-tab app and global filters (already present from Stage 3).
- "Do not pause for additional design approvals unless there is a critical blocker."

**Approval and scope:**
- Accepting the 8 confident pilot points as `reviewed` and resetting MG Road and Khandsa follows the user's instruction.
- Each accepted row's notes record that the user did not inspect that point individually.
- No new coordinate sourcing was done for the other 166 labels. I read "Phase 3" here as completing the three map modes on the current coverage; bulk sourcing of the remaining labels would need its own review workflow.

**Investigation (installed Streamlit 1.65 / pydeck 0.9.3):**
- With `map_style=None`, Streamlit's front end substitutes a CARTO Positron or Dark Matter style using a Streamlit-supplied key. CARTO's terms now require your own key, so that default was avoided.
- The pydeck chart passes `mapStyle` to react-map-gl v5 / Mapbox GL v1 as a string, or as `mapStyle[0]`. A dict style would arrive as `undefined`, so it is not usable.
- Mapbox GL v1's `normalizeStyleURL` returns non-`mapbox://` URLs unchanged, so no token is needed.
- `TileLayer`'s default sub-layer is GeoJSON, and a raster `renderSubLayers` needs a JavaScript function pydeck's JSON cannot carry.
- Tooltip `html` templates escape the substituted values.
- `st.pydeck_chart(on_select=…)` needs an explicit `id` on every layer and returns selections grouped by layer id.

**Decisions:**
- **Basemap:**
  - `static/osm_basemap_style.json` is a Mapbox-GL v8 style with a single raster source, `https://tile.openstreetmap.org/{z}/{x}/{y}.png`, desaturated so the arcs stand out, with OSM attribution.
  - It is served by `server.enableStaticServing = true`.
  - The deck uses `map_provider="maplibre"` with `map_style="app/static/osm_basemap_style.json"`.
  - "Basemap © OpenStreetMap contributors" is also shown under every map.
- **`maps.py`:**
  - `reviewed_points` reads coordinates only from reviewed rows.
  - `mapped_flows` builds top-N arcs between reviewed endpoints, with an optional origin drill-down. It counts records drawn, cut by the top-N limit, unmapped and same-location separately; the top-N cut only ever ranks mapped connections.
  - `location_nodes`, `fit_view` (a view that fits all points, so the 94 km Panipat arcs stay visible), `selection_target`, and the `flow_deck` / `hotspot_deck` / `reliability_deck` builders.
  - Layer ids: `flows`, `locations`, `hotspots`, `reliability`, `labels`.
  - Every layer gets the same tooltip fields (`tt_title`, `tt_line1`–`3`), so one escaped HTML template works for all of them.
  - Layer data is passed as plain-Python records, avoiding pandas-3 serialization issues.
- **Colours:**
  - Arcs fade from blue (pickup) to orange (drop); width runs from 1.5 to 10 px by bookings.
  - The selected location is amber.
  - Reliability uses a one-hue red ramp on a fixed 0 to max(50%, top rate) domain, so the ramp doesn't stretch small differences. Locations below 50 records are grey.
- **`app.py` (Trip Explorer):**
  - The coverage banner is now "Partial map — pilot coverage".
  - Each mode draws its map, legend and drawn/omitted counts, followed by charts and tables that cover all locations.
  - A map click runs an `on_select` callback that writes into the details panel's session state.
  - The map's key is a hash of the deck JSON, so an old click selection can't survive a change of filters, mode or focus.
  - Choosing a location shows only its outgoing arcs.
  - The connection dropdown now lists the 50 busiest connections, plus every mapped one, plus the current selection.
  - The scope switch is seeded through session state, so a map click can set it without Streamlit's duplicate-default warning.

**Changed files:**
- `data/reference/location_lookup.csv`: 8 rows → `reviewed`; MG Road and Khandsa → `unresolved`, coordinates cleared.
- New: `static/osm_basemap_style.json`.
- Modified: `.streamlit/config.toml`, `maps.py`, `app.py`, `tests/test_maps.py`, `tests/test_app.py`, `docs/COORDINATE_REVIEW.md`, `Readme.md`, `docs/DEVLOG.md`.

**Commands and results:**
- `uv run python preprocessing.py --input ncr_ride_bookings.csv`: exit 0. Lookup {reviewed 8, unresolved 168}; flow-eligible rows 285.
- Headless smoke run:
  - The deck spec has `mapStyle=app/static/osm_basemap_style.json` and `mapProvider=maplibre`.
  - Layers: 20 arcs, 8 locations, 8 labels. View centred at (28.94, 77.19), zoom 8.91, pitch 40.
  - Selecting Panipat drew 7 arcs, all leaving Panipat.
- `uv run python -m pytest -q`: first run 109 passed and 1 failed. The cross-tab test still looked for the old "Shown:" caption; it was updated to the new "Drawn: … of N records in this view" wording, and the check that Trip Explorer's count equals the filtered total is unchanged. Then **110 passed**.
- `uv run ruff check .`: **All checks passed**; format check clean (23 files).
- `uv run streamlit run app.py --server.port 8501`: failed with "Port 8501 is not available". The user's own `streamlit run app.py` (started 22:01) was holding the port, and it was not stopped.
  - Against that instance, `/app/static/osm_basemap_style.json` returned the HTML page, because the server was started before static serving was enabled.
  - A verification server on port 8502 returned health 200, and the style file came back as `application/json` (621 bytes) with the OSM tile URL and attribution. The server was then stopped and the port freed.
- One OSM tile request, `https://tile.openstreetmap.org/8/182/107.png`, sent with a User-Agent naming the project: 200, `image/png`, 36,295 bytes. This proves the tiles are reachable from this machine, not that they render in the browser.

**Real coverage (unfiltered):** pickups 7,068 (4.7%), drops 6,758 (4.5%), flows 285 (0.19%) across 55 directed connections. Busiest connection 12 bookings; longest arc about 94 km (Panipat).

**Browser verification:** **Not performed by Claude**; no browser tool was available. Untested:
- OSM tiles actually drawing under the arcs, and the muted look
- hover tooltips
- clicking an arc or point and seeing the details panel update
- the zoom buttons
- legend rendering, layout, and dark mode

**Open issues / next approval:**
1. The user restarts their Streamlit server and runs the browser checklist.
2. Source and review coordinates for more labels to grow coverage; that needs its own review workflow.
3. Optionally, a zone-level overview once more labels are mapped.

### 2026-10-09 / Phase 2B bug fixes from the user's browser screenshots

**Bug 1 — `AttributeError: module 'maps' has no attribute 'reviewed_points'`** (screenshot)
- **Cause:** the user's server process (PID 13096, started 22:01) had hot-reloaded the new `app.py` but kept the pre-change `maps` module it loaded at startup. `maps.py` on disk defines the function, and a fresh import found it.
- **Fix:** restart the server. No code change was needed.

**Bug 2 — "Map is not shown properly"** (screenshot)
- **Symptoms:**
  - The OSM basemap and attribution loaded correctly.
  - The arcs rendered as huge fan-shaped sheets across North India.
  - The location dots and labels were invisible.
- **Cause:** pydeck serializes *every* bare string keyword argument as a `@@=` JavaScript expression. The deck JSON contained `widthUnits: "@@=pixels"`, `radiusUnits: "@@=pixels"` and `fontFamily: "@@=system-ui, …"`, which deck.gl evaluates as accessors returning undefined:
  - arc widths became invalid;
  - dot radii fell back to metres (about 15 m, so invisible);
  - the text layer's font was invalid.
- **Why tests missed it:** the earlier headless tests checked layer ids, data and the style URL, not property types.
- **Fix:** wrap literal settings with `pdk.types.String` (helper `maps._literal`). The pitch was also reduced from 40° to 30° for readability.
- **Regression test:** `test_only_data_accessors_are_serialized_as_expressions` fails if any non-`get*` layer property is sent as an `@@=` expression in the flow, hotspot or reliability decks.
- **Results:** `pytest -q` 111 passed; `ruff check .` clean.
- **Browser re-check by the user is still pending.**

### 2026-10-10 / Project documentation for reviewers

**User prompts:**
- "Project is Perfect. Can you create a document of this Project. Include the Architectural Diagram as well. Tell me the sections which will be included in document before you proceed."
- Then: "The reviewers need google doc, so create that. No need for markdown file." The user declined creating it as a Claude Doc.

**Decision:**
- This session has no Google Drive/Docs connector, so the deliverable is a Google-Docs-friendly Word file. It becomes a Google Doc when uploaded to Drive and opened with Google Docs.
- Formatting: Arial, built-in headings, fixed DXA table widths, and PNG images.
- Content follows the 15-part outline agreed with the user.
- Items only the user can supply are yellow-highlighted placeholders: final-app screenshots, demo video link and GitHub URL.

**Changed files:** `docs/NCR_RidePulse_Project_Documentation.docx` (new); `docs/DEVLOG.md`.

**How it was built:**
- **Diagram:** drawn with Pillow from the project venv.
- **Document:** built with `python-docx` in a throwaway environment (`uv run --no-project --with python-docx`), so `pyproject.toml` and `uv.lock` are unchanged.
- **Certificate error:** PyPI downloads failed with "invalid peer certificate: UnknownIssuer". `--system-certs` was used so uv checks certificates against the Windows trust store; verification is still on.

**Verification:**
- Microsoft Word opened the file read-only and exported a PDF. PyMuPDF rendered the 12 pages, which were reviewed visually.
- Fixes made after that review: the architecture page layout was tightened, and a stray `*not*` was removed.
- Not verified: the Google Docs import itself; the user has to upload the file.

---

## Entry template — append after each iteration

### Date / phase / short title

**User prompt:** Record the actual prompt or reference the matching BUILD_PROMPTS.md prompt with any changes.

**Approval and scope:** What was approved; what is deferred.

**Observed inputs:** Source fingerprint, relevant file/version, and actual environment details when known.

**Decision and rationale:** What changed and why.

**Changed files:** Actual paths only.

**Commands executed and results:** Exact commands, exit status or clear result, and test pass/fail/skip counts. Mark commands not run.

**Browser/manual verification:** What was actually observed and by whom. Mark untested interactions explicitly.

**Bugs and fixes:** Symptom, cause, minimal fix, regression evidence.

**Learning:** One or two useful observations about the workflow.

**Open issues / next approval:** Remaining work and the next milestone.
