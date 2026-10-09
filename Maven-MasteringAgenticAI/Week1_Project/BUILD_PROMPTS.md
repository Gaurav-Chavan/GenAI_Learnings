# NCR RidePulse — copy-ready Claude Code prompts

Send ONE prompt at a time. Review the result and approve the next phase deliberately. Start Phase 0 in the VS Code extension's Plan mode. Use Manual edit approval for implementation. Keep the raw CSV in the project root; do not attach its full contents as text.

## Prompt 0 — understand and plan

```text
Read @CLAUDE.md and @PROJECT_BRIEF.md, then inspect docs/DEVLOG.md and docs/PRIOR_DATA_AUDIT.md.

We are building NCR RidePulse: an independent Delhi-NCR ride analytics app using Python, uv, Streamlit, pandas, Plotly, and Pydeck. I am developing through the Claude Code VS Code extension. The raw input is ./ncr_ride_bookings.csv and must remain unchanged.

The three tabs are Executive Overview, Rider & Demand, and Trip Explorer. The signature feature is a selectable origin-destination connection map, with hotspot and reliability views. Follow the brief's data, denominator, and geographic integrity rules.

For now, inspect and plan only. Do not modify project files, install packages, generate the app, or geocode locations.

Inspect existing files and the CSV header. Profile the CSV with read-only commands if the available permissions allow it; otherwise identify the pending checks without bypassing restrictions. Do not load the entire CSV into the conversation.

Return:
1. Your understanding of the user problem and three tabs.
2. What you actually verified about the local data/environment versus what remains to be tested.
3. A minimal file/module plan using the existing uv project, if present.
4. The Phase 1 preparation and metrics tasks and acceptance checks.
5. A map-first plan with explicit coordinate sourcing/review and browser-test gates.
6. Essential blockers or decisions requiring my approval.

Do not assume location names contain GPS coordinates. Prior audit numbers are regression targets to reproduce, not dashboard constants. Stop and wait for my approval of Phase 1.
```

## Prompt 1 — implement only the data foundation

Use after reviewing the plan and granting the necessary edit/command permissions.

```text
The plan is approved for Phase 1 only. Read the brief and current development log again, then implement the data foundation.

Create preprocessing.py, metrics.py, the appropriate tests, reports, and an unresolved location-lookup template. Use the existing uv environment. Do not build the Streamlit UI or obtain coordinates yet.

Support this command from the project root:
uv run python preprocessing.py --input ncr_ride_bookings.csv

Write data/processed/rides_prepared.csv, reports/data_quality_report.json, reports/data_quality_report.md, and data/reference/location_lookup.csv. Never alter the source CSV or overwrite existing reviewed lookup values.

Inspect actual column names. Preserve all source records, repeated Booking IDs, missing numerical values, and original identifiers. Derive normalized IDs, source-row identity, typed date/time fields, and outcome flags. Implement the shared metric/filter contract and calendar-exposure normalization from PROJECT_BRIEF.md. Reject row-multiplying lookup joins and report source/version discrepancies.

The lookup initially contains every distinct source location label with unresolved review status and blank coordinates. No invented geographic values.

Create DATA_DICTIONARY.md and README instructions appropriate to this phase. Update docs/DEVLOG.md with the actual prompt, decisions, commands, outcomes, and limitations.

Run preparation against the local CSV, run uv run python -m pytest -q, and run uv run ruff check . where execution is available. Compare actual statistics with the prior audit without hardcoding application outputs. Do not relax a test to conceal a mismatch.

Return changed files, commands actually executed, pass/fail/skip results, an actual-data baseline summary, unresolved issues, and exactly what I should verify. Stop before Phase 2.
```

## Prompt 2A — approve the geographic approach

```text
Phase 1 is accepted. Plan Phase 2's geographic proof of concept before using any coordinate service.

Read the generated lookup and quality report. Propose approximately 8–12 actual source labels for a geographically varied pilot. Identify ambiguous names and propose the coordinate sources, review workflow, analytical zone convention, and basemap provider. Check current provider terms and key requirements using available official sources. Do not send any booking/customer records to a service.

Do not invent coordinates, infer geocoding results from memory, or bulk-geocode labels. Explain which research/browser capabilities are actually available. Identify anything I must supply or approve.

Show the verification checklist and the intended map/detail interaction. Stop for approval of sourcing before network lookup or full map implementation.
```

## Prompt 2B — build the small reviewed map

Use only after approving 2A's specific method. Approval of this prompt is limited to that method and pilot.

```text
The proposed pilot coordinate-sourcing and basemap method is approved. Proceed with Phase 2 only using that method.

Populate source-documented pilot entries, keeping proposed and reviewed statuses honest. Flag ambiguous interpretations for my review rather than treating them as confirmed. Do not overwrite reviewed entries. Re-run preparation and validate many-to-one joins and geographic coverage.

Build a minimal app.py and maps.py focused on Trip Explorer. Use real booking aggregates and the reviewed pilot locations only. Show a large Pydeck connection map, origin selection/dropdown, requested-versus-completed flow control, top-N connections, tooltips, and a selected-area detail panel. Keep executive/reliability denominators independent of the flow-status control. Label the pilot's partial coverage and approximate locality positions prominently.

Verify the installed Streamlit selection API, explicit layer IDs, pickable point/arc layers, and stale-selection handling. Include an unmapped-count display and a table/empty-state fallback. No invented coordinates or silently substituted sample bookings.

Run tests. Start the app when permitted and report whether map tiles and selection behavior were actually verified in a browser. If browser access or approved coordinates are unavailable, state the blocker and do not claim the map milestone passed. Give the exact manual checks I need to perform.

Update README and DEVLOG, then stop for review. Do not build the other two tabs yet.
```

## Prompt 3 — complete the map experience

```text
The pilot map is accepted. Continue Phase 3 under the approved geographic method.

Expand the lookup review across the actual location labels. Preserve source evidence and reviewed work; explicitly report unresolved entries and their booking coverage. Do not silently place unresolved labels at a default point.

Complete the three Trip Explorer modes from the brief: directional trip flows, pickup/drop demand hotspots, and service reliability. Include the reviewed zone-level overview and locality drill-down, top-N coverage, the 50-record rate-comparison guardrail, low-sample handling, legends, self/intra-zone flow handling, and map-driven detail controls with dropdown alternatives.

Recompute rates from aggregate counts and apply each map layer's correct coordinate-eligibility rule. Unmapped rows must still contribute to relevant non-map totals. Do not let completed-flow filtering turn location completion rates into 100%.

Test and document the result, including actual browser verification versus remaining manual checks. Measure rather than promise performance. Report mapped and unresolved coverage separately from top-N omissions. Stop before Phase 4.
```

## Prompt 4 — complete the dashboard

```text
Phase 3 is accepted. Build Phase 4: Executive Overview and Rider & Demand exactly as specified in PROJECT_BRIEF.md, preserving the tested Trip Explorer.

Reuse one shared base-filter and metric layer. Include all six executive KPIs, all booking outcomes, volume/completion trends, top pickups/drops, the normalized weekday-hour heatmap, normalized weekdays, hourly requests, vehicle-category bookings, and completed-booking payment mix.

Use a polished, restrained layout, consistent visual meanings, visible filter context, a reset action, units/denominators, and useful empty states. Add a deterministic Explain this view summary based only on computed values. No chatbot, fabricated deltas, causal promises, or hardcoded figures.

Run regression tests and check cross-tab consistency, calendar denominators, status-filter isolation, stale map selections, and empty results. Update README and DEVLOG with actual verification. Stop before final polish/deployment.
```

## Prompt 5 — quality and demo preparation

```text
Phase 4 is accepted. Complete Phase 5 without adding features or changing the architecture.

Review data integrity, aggregation, filter consistency, cache invalidation, failure states, map provider configuration/attribution, coordinate provenance, accessible labels, and browser interactions. Fix documented defects and run automated checks. Do not report browser testing as passed unless actually performed.

Prepare README setup/run instructions using uv, a requirements-to-feature checklist, a screenshot capture checklist, and docs/DEMO_SCRIPT.md for a video of five minutes or less. Update DEVLOG with prompts, decisions, bugs, fixes, actual test evidence, and remaining limitations. Do not invent screenshots, recordings, test results, or deployment links.

Propose a safe GitHub publication checklist: include code and uv.lock, exclude secrets/environments/caches, and do not publish the source CSV before redistribution permission is verified and I approve. Do not push or deploy yet.

Return a final pass/fail/not-tested checklist and the exact remaining actions required for a credible live demo.
```

## Focused debugging prompt

```text
We are working on NCR RidePulse under CLAUDE.md and PROJECT_BRIEF.md.
Current phase: [phase]
Command/action: [exact command or click sequence]
Expected result: [expected behavior]
Actual result: [observed behavior]
Error or screenshot: [paste traceback or attach screenshot]

Reproduce and diagnose the problem before editing. Make the smallest relevant fix, preserve the agreed scope and data rules, add a regression test where practical, and rerun the relevant checks. Report what was actually verified. Do not rebuild the app or change frameworks to bypass this error.
```
