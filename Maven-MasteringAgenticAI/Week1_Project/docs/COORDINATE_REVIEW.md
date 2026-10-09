# Coordinate review — Phase 2A pilot (10 labels)

**Status (2026-10-09): 8 `reviewed`, 2 back to `unresolved`.** The user gave this instruction: "Use only confidently verified pilot coordinates. Keep ambiguous locations such as MG Road and Khandsa unresolved."
- **Accepted:** Barakhamba Road, Saket, Pragati Maidan, Cyber Hub, Noida Sector 62, Ghaziabad, IGI Airport and Panipat. The criterion was two sources agreeing within about 1.1 km, or an exact named OSM feature.
- **Not individually inspected.** The user has not checked each accepted point on a map; each row's notes say so. Spot-checking the links below is still recommended.
- **Withdrawn:** MG Road and Khandsa, which now have blank coordinates. Their candidate evidence stays in the evidence file.
- **Basemap:** OpenStreetMap standard tiles (option A) were approved the same day.

The proposals table below is kept as the review record.

## How these were sourced

- **Approval:** the user approved the Phase 2A pilot on 2026-10-09.
- **What was sent:** place-name queries only, such as "Saket, New Delhi". No booking or customer data was sent.
- **OpenStreetMap Nominatim:** one request at a time, at least 1.2 s apart, with a User-Agent naming the project. Every response was cached. These rules follow the [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/).
- **Wikidata:** used to cross-check coordinates (property P625). It answered 15 requests, then returned HTTP 429 on the 16th, which was MG Road's search. It wasn't queried again until one final, spaced lookup for Saket.
- **Request counts:**
  - Nominatim: 15 (11 in the main run, one of which repeated a query lost in a crash, plus 4 targeted follow-ups).
  - Wikidata: 18 (15 answered, 1 refused, and 2 for Saket).
- **Raw responses:** [`data/reference/coordinate_evidence/pilot_2026-10-09.json`](../data/reference/coordinate_evidence/pilot_2026-10-09.json).
- **Licences:** OSM data is © OpenStreetMap contributors (ODbL). Wikidata is CC0.
- **What a point means:** each point is a *representative locality point*. It is not where trips actually start, and it is not a route.

## Proposals

"Check" opens the proposed point on openstreetmap.org. "Δ" is the distance to an independent second source.

| Label | Proposed (lat, lon) | Zone | Primary source | Δ to cross-check | Check | Notes |
|---|---|---|---|---|---|---|
| Barakhamba Road | 28.62747, 77.22905 | Central Delhi | [OSM way 523568284](https://www.openstreetmap.org/way/523568284) | 0.19 km ([Wikidata](https://www.wikidata.org/wiki/Q125567805)) | [map](https://www.openstreetmap.org/?mlat=28.62747&mlon=77.22905#map=16/28.62747/77.22905) | Road in Connaught Place; point is one segment's centroid |
| Saket | 28.51918, 77.21301 | South Delhi | [Wikidata Q7402907](https://www.wikidata.org/wiki/Q7402907) | 1.07 km (OSM POI) | [map](https://www.openstreetmap.org/?mlat=28.51918&mlon=77.21301#map=15/28.51918/77.21301) | OSM has no Saket place node |
| Pragati Maidan | 28.616813, 77.243359 | Central Delhi | [Wikidata Q2723163](https://www.wikidata.org/wiki/Q2723163) | 0.32 km (OSM bus stop) | [map](https://www.openstreetmap.org/?mlat=28.616813&mlon=77.243359#map=16/28.616813/77.243359) | Exhibition grounds (venue now called Bharat Mandapam) |
| Cyber Hub | 28.49510, 77.08851 | Gurugram | [OSM way 1449268449](https://www.openstreetmap.org/way/1449268449) | — single source | [map](https://www.openstreetmap.org/?mlat=28.4951&mlon=77.08851#map=16/28.4951/77.08851) | DLF Cyber Hub, Sector 25A |
| Khandsa | 28.422, 76.99 | Gurugram | [Wikidata Q6400254](https://www.wikidata.org/wiki/Q6400254) | 0.58 km (OSM Khandsa Road) | [map](https://www.openstreetmap.org/?mlat=28.422&mlon=76.99#map=15/28.422/76.99) | ⚠ Coarse precision. Could mean the village *or* the Khandsa Road/Mandi area |
| Noida Sector 62 | 28.62114, 77.36435 | Noida | [OSM node 10811810934](https://www.openstreetmap.org/node/10811810934) | — single source | [map](https://www.openstreetmap.org/?mlat=28.62114&mlon=77.36435#map=15/28.62114/77.36435) | OSM `place=suburb` node |
| Ghaziabad | 28.67115, 77.41204 | Ghaziabad | [OSM node 2521085873](https://www.openstreetmap.org/node/2521085873) | 0.67 km ([Wikidata](https://www.wikidata.org/wiki/Q207098)) | [map](https://www.openstreetmap.org/?mlat=28.67115&mlon=77.41204#map=13/28.67115/77.41204) | City-level point; coarse for a whole city |
| IGI Airport | 28.55542, 77.08481 | South West Delhi | [OSM relation 19597141](https://www.openstreetmap.org/relation/19597141) | 0.26 km ([Wikidata](https://www.wikidata.org/wiki/Q821275)) | [map](https://www.openstreetmap.org/?mlat=28.55542&mlon=77.08481#map=14/28.55542/77.08481) | Aerodrome centroid; the terminals are several km apart |
| Panipat | 29.39127, 76.97717 | Panipat | [OSM node 245768111](https://www.openstreetmap.org/node/245768111) | 0.81 km ([Wikidata](https://www.wikidata.org/wiki/Q139706)) | [map](https://www.openstreetmap.org/?mlat=29.39127&mlon=76.97717#map=13/29.39127/76.97717) | City-level point; about 85 km north of central Delhi |
| MG Road | 28.47696, 77.06627 | Gurugram | [OSM way 28739056](https://www.openstreetmap.org/way/28739056) | — single source | [map](https://www.openstreetmap.org/?mlat=28.47696&mlon=77.06627#map=14/28.47696/77.06627) | ⚠ **Ambiguous.** Mehrauli–Gurgaon Road runs more than 10 km from Delhi into Gurugram, and Gurugram also has an MG Road metro station |

## How to review

For each row in `data/reference/location_lookup.csv`:

1. Open its **map** link and decide whether the point is a fair *representative* point for how this label is likely used in a Delhi-NCR ride booking.
2. Then make one of three edits:
   - **Accept:** change `review_status` from `proposed` to `reviewed`. Optionally add your initials or a decision to `review_notes`.
   - **Correct:** replace `latitude`/`longitude` with a better point, put its source URL in `coordinate_source`, then mark the row `reviewed`.
   - **Reject:** set `review_status` back to `unresolved` and clear `latitude`, `longitude` and `coordinate_source`.
3. Re-run the preparation step, because the app refuses prepared data that is older than the lookup:
   ```text
   uv run python preprocessing.py --input ncr_ride_bookings.csv
   ```

Validation rejects any `reviewed` row that lacks a source or falls outside the study-area box (latitude 27.5–30.0, longitude 76.0–78.5).

**Expected coverage if all 10 are reviewed:**
- 8,837 pickups (5.9%) and 8,424 drops (5.6%).
- 443 bookings (0.30%) with both endpoints mapped, across 88 directed connections; the busiest has 12 bookings.
- Without MG Road: 359 flow bookings across 71 connections.

**Actual coverage with the 8 reviewed labels (unfiltered):**
- 7,068 pickups (4.7%) and 6,758 drops (4.5%).
- 285 bookings (0.19%) with both ends mapped, across 55 directed connections. The busiest has 12 bookings; the longest arc is about 94 km.

## Basemap — decided: A (OpenStreetMap tiles)

The options considered are below.

| Option | Key or signup | Terms (checked 2026-10-09) | Trade-off |
|---|---|---|---|
| **A. OpenStreetMap standard tiles** (Pydeck `TileLayer`) | None | [Tile usage policy](https://operations.osmfoundation.org/policies/tiles/): low-volume use with a real browser User-Agent and Referer; show "© OpenStreetMap contributors"; no bulk downloading or prefetching | Busy, colourful street map behind the arcs; fine for a local demo |
| **B. CARTO Positron / Dark Matter** | **Your own free API key** | [Basemap terms](https://carto.com/legal/basemap-terms/) (updated 2026-09-29): "Customer must always use the Basemap Services with Customer's own unique API keys"; free for non-commercial use up to 5M tile requests per month; credit both CARTO and OSM. Raster tiles stopped working without a key on 2026-09-25 ([FAQ](https://docs.carto.com/faqs/carto-basemaps)) | Cleanest look for arcs. The key goes in `.streamlit/secrets.toml`, which stays out of git |
| **C. No basemap** | None | — | Points and arcs on a blank canvas. CLAUDE.md treats this as a *partial* map test only |

Pydeck's default CARTO style without a key no longer fits CARTO's terms, so it is not offered as an option.
