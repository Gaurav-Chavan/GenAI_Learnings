# Data 360 — Design

The data foundation for Insurance Claim Triage. Companion to [DESIGN.md](DESIGN.md).
Status: design agreed step by step on 2026-10-31.
**Built (Day 1):** seeded generator, privacy split (vault + tokens), silver and gold layers, quality and privacy checks, data dictionary, Neo4j graph load with BM25 indexes, 18 tests. Run the data build with `uv run python -m claim_triage.data.build` (about 15 s), load the graph with `uv run python -m claim_triage.data.load_neo4j` (refuses a non-empty database unless `--clear`), tests with `uv run pytest`.
**Built (Day 1, part 2):** the read-only `ToolBox` (11 tools at that point, 13 now, see `tools.md`), the rules-only baseline (rules, scorer, evaluation harness, `eval/baseline_report.md`), 62 tests (206 now).
**Built since (Day 2):** the three agents, hybrid retrieval, the dashboard, the learning loop and the 80 hard evaluation cases; see [DESIGN.md](DESIGN.md) section 0.
**Not built:** the MCP wrapper (stretch). Synthetic data only.

## 1. Goals

1. **The reviewer only ever knows a customer ID.** Nothing in the dashboard, the AI agents, the tools
   or the logs can identify a person.
2. **A 360° view of every customer:** profile, demographics, area risk and a full event history,
   so the system can flag, for example, a claim from an area with a high rate of confirmed fraud.
3. **A strong foundation:** layered, tested, reproducible. A bad table must not quietly reach the agents.

## 2. Decision log

| Step | Decision |
|---|---|
| 1 Privacy | No one in the app can re-identify a customer. Identity sits in a separate locked **vault**. Phone, bank account, address and vehicle registration become keyed-hash **tokens**. Customer ID looks like `CUST-000123`, with numbers assigned in random order so they don't reveal signup order |
| 2 Profile | Age band, gender, occupation category, income band, PIN code location. Only **area risk** and **income-vs-cover mismatch** may influence the score; everything else is context. Gender is never scored |
| 3 Area risk | Per PIN, per line (health / term / motor), last 24 months, **reviewer-confirmed fraud only**. A high-fraud area flags a claim as **review needed, never red** |
| 4 Events | One timeline per customer, 2–3 years, including ordinary honest changes. Only what was known on the filing date may be used |
| 5 Storage | Raw → privacy split → silver → gold in **DuckDB** files; Neo4j loaded from gold; a failed check **stops the pipeline** |
| 6 Synthetic data | ~600 customers, ~1,500 past claims, ~25 PINs, 3–4 hotspots, ~300 evaluation claims (35% suspicious, 45% honest lookalikes, 20% missing evidence) with hotspot decoys; realistic Indian-style fake personal data |

## 3. Layers

```
1 RAW      generator output, includes fake personal data
   │  privacy split — the only step that touches personal data
   ├─► VAULT   names, phones, addresses, IDs         vault.duckdb    (nothing in the app reads it)
   └─► SILVER  tokens + coarse bands, typed, checked silver.duckdb   (the only layer the app sees)
          │  build
          ▼
3 GOLD     customer_360 · area_risk · graph · BM25   gold.duckdb → Neo4j
4 LABELS   hidden fraud labels and answer keys        labels.duckdb  (only the evaluation harness reads it)
```

| Layer | Holds | Readable by |
|---|---|---|
| Raw | Everything the generator produced, including fake personal data | Privacy split only; **deleted after the split** unless `--keep-raw` |
| Vault | Name, phone, address, email, ID numbers, exact DOB, exact PIN-to-person link | Vault module only; never the UI, agents, tools or traces |
| Silver | Pseudonymous, typed tables (customers, policies, claims, events, providers, areas, documents) | Pipeline, tools, UI |
| Gold | `customer_360` (profile + history features), `area_risk`, graph projection, BM25 index | Tools, agents (through tools), UI |
| Labels | Scenario, fraud flag, expected band, expected indicators and evidence IDs | Evaluation harness only |

## 4. Privacy model

- **Vault:** the only place real-looking identity exists. Separate file, separate module, no code path
  from the dashboard or the agents.
- **Tokens:** `token = HMAC(secret_key, normalised value)`. The same phone number always gives the
  same token, so the graph can still link customers who share one. The reviewer sees `PH-3F9`, never the
  number. Same for bank account, address and vehicle registration. The key is kept in `.env`, never committed.
- **Customer ID:** `CUST-000123`, numbered in random order.
- **Quasi-identifiers:** PIN code + gender + age band + occupation + income band can point to one person.
  The backend uses the PIN to compute area risk, but the **reviewer screen shows only the district**.
  The enforced guard is **k = 5 on location + gender + age band**: a customer whose (district, gender, age band) group
  has fewer than 5 customers is generalised step by step (district → state, age band → under 40 / 40+, gender → withheld).
  Occupation and income band stay visible as context, so the *full* combination is **not** 5-anonymous with only 600
  customers (about 94% of customers sit in a smaller group). The build reports that number; closing the gap needs a
  much larger population or coarser bands.
- **Free text:** document snippets are scrubbed of names and numbers before they reach silver.
- **LLMs and logs:** agents receive only silver/gold data. Prompts and traces are checked for vault values.

## 5. Customer profile and demographics

| Field | Reviewer sees | Scored? |
|---|---|---|
| customer_id | Yes | No |
| age_band (e.g. 30–39) | Yes | No |
| gender | Yes (context) | **Never**; used only for fairness checks |
| occupation_category | Yes | No |
| income_band | Yes | Only through the income-vs-cover mismatch signal |
| state, district | Yes | Only through area risk |
| PIN code | **No** (backend only) | Only through area risk |
| urban / semi-urban / rural class | Yes | No |
| acquisition channel (agent / online / bank) | Yes | No |
| customer since (tenure band), KYC status | Yes | No |

**Derived in gold (as of each claim's filing date):** claims in the last 12 and 36 months, claim amount
vs premiums paid, lapsed policies, number of active policies, days since last nominee / phone / address /
bank change, number of other customers sharing each token.

**Never stored outside the vault:** name, phone, address, email, ID numbers, exact date of birth,
exact location, religion, caste.

## 5b. The graph in Neo4j

```
(Person)-[:HOLDS]->(Policy)-[:HAS_CLAIM]->(Claim)-[:AT_PROVIDER]->(Provider)
(Claim)-[:SUPPORTED_BY]->(Document)  (Claim)-[:FOR_CONDITION]->(Condition)  (Claim)-[:PAID_TO]->(BankAccount)
(Claim)-[:FOR_VEHICLE]->(Vehicle)    (Policy)-[:COVERS]->(Vehicle)          (Policy)-[:HAS_NOMINEE {relation}]->(Nominee)
(Person)-[:USES]->(Contact | BankAccount)                                    (Person)-[:LIVES_IN]->(District)
```

Built: 10,793 nodes (600 Person, 1,392 Policy, 2,157 Claim, 3,739 Document, 72 Provider, 420 Vehicle, 339 Nominee, 1,365 Contact,
666 BankAccount, 11 Condition, 6 District, plus 12 Pattern and 14 Clause knowledge nodes). Person nodes carry only reviewer-safe
values (no PIN, name, phone or address). BM25 full-text indexes: `document_text`, `claim_text`, `provider_names`, and `knowledge_text`
(over Pattern, Clause and ApprovedSignal nodes). Graph checks: counts match the tables, no personal values on any Person, and the
canary scan finds no vault value anywhere in the graph.

Knowledge nodes (`knowledge/fraud_patterns.yaml`, `knowledge/policy_clauses.yaml`) are loaded with `load_neo4j --knowledge-only`, which
adds or refreshes them without touching claim data. `ApprovedSignal` nodes (from the Knowledge Base tab) have their constraint and index
but none exist yet. Earlier claims with a reviewer-confirmed outcome act as precedents; see [DESIGN.md](DESIGN.md) section 6.

## 6. Area risk

- **Levels:** PIN → district → state.
- **Rate:** `confirmed_fraud_claims / decided_claims` over the last 24 months, **per line**, using only
  reviewer-confirmed outcomes (never our own flags, to avoid a self-reinforcing loop).
- **Sparse PINs:** `rate_pin = (fraud + m · rate_district) / (n + m)` with `m ≈ 15`, so a PIN with few
  claims leans on its district.
- **High area:** shrunk rate ≥ 2× the portfolio rate and enough past claims behind it (configurable).
- **Effect:** indicator `HIGH_FRAUD_AREA` (weight 0.25 in config, no confidence scaling once thresholds
  are met), so on its own a claim lands in **review needed (≈ 0.75), never red**.
- **Shown to the reviewer in plain words:** "Claims from this area are confirmed as fraud about 3× more
  often than average (41 past claims)".
- **Time rule:** statistics use only claims decided before the current claim was filed, and exclude it.
- **Fairness checks:** flag rate by gender, age band and district; area-only flags monitored; evaluation
  deliberately includes honest claims from hotspots and fraud from quiet areas.
- **Income-vs-cover mismatch:** indicator `COVER_VS_INCOME_MISMATCH` (small weight in config) when cover
  is far above what the income band supports, with per-line thresholds.

## 7. Event history

One append-only `events` table: `event_id, customer_id, policy_id, claim_id, event_type, event_date,
details (small structured payload), entity_tokens`.

| Group | Event types |
|---|---|
| Policy life | policy_issued, policy_lapsed, policy_terminated (death); premium and rider events are planned, not built |
| Detail changes | nominee_changed, phone_changed, address_changed, bank_changed, vehicle_owner_changed |
| Claim life | claim_filed, documents_submitted, claim_inspected, claim_decided, claim_paid, claim_reopened |
| Medical / vehicle | hospital_admitted, hospital_discharged, accident_reported, vehicle_inspected, vehicle_repaired |

Honest customers also get ordinary changes and occasional lapses, so signals are not obvious.
**Point-in-time rule:** every feature is computed as of the claim's filing date.

## 8. Synthetic data

- ~600 customers with correlated age, occupation and income; 1–3 policies each across health, term, motor.
- 25 PINs in 6 districts and 3 states; 4 hotspot PINs where the generator makes confirmed fraud 5× as likely.
  Area risk is derived from the generated history, never typed in. Hotspot detection is noisy at this volume
  (6 of the 8 hotspot cells, 4 PINs × health and motor, are flagged; cells with fewer than 15 claims in the window
  correctly fall back to the district rate).
- ~1,500 past claims with reviewer outcomes (these build the area statistics).
- ~300 evaluation claims across the 10 scenarios in `DESIGN.md` §4, with a hidden answer key each:
  35% suspicious, 45% honest lookalikes, 20% missing evidence; 20 dev / 10 held-out per scenario.
- Hotspot decoys: honest claims from hotspots and fraud from quiet areas.
- Fake personal data in realistic Indian formats (Faker, `en_IN`), clearly labelled synthetic.
- One seed; a dataset hash (printed by the build) lets any teammate verify they rebuilt identical data.
- **Built size:** 600 customers, 1,405 policies, 2,161 claims (1,576 closed history claims with outcomes), 12,460 events,
  3,665 documents, 300 evaluation claims (100 held out; exactly 10 per scenario).
- **Realism rules (enforced by checks):** everyone is an adult when they sign up and when a policy starts; a **death ends the
  customer's activity** (all policies terminated on the death date, no later claims or events, `deceased` flag in customer_360);
  past term claims are critical-illness rider claims (living benefit), so history never contains a death.

## 9. Data-quality and privacy checks (a failed check stops the pipeline)

| Check | Layer |
|---|---|
| Types, required fields, allowed values | Silver |
| Unique IDs; every claim → real policy; every event → real customer | Silver |
| Date logic (policy start ≤ claim; discharge ≥ admission, etc.) | Silver |
| Every quasi-identifier group has ≥ 5 customers (else generalise) | Silver → reviewer view |
| **No vault value appears** in silver/gold, tool outputs, agent prompts or traces (canary scan) | All |
| App code cannot open the vault or labels files | Code |
| No feature or area statistic uses data after the claim's filing date | Gold |
| Area risk reproduces from the closed-claim history | Gold |
| Same seed → identical dataset hash | Raw |
| Graph node/edge counts match gold tables | Neo4j |

## 10. Documentation

- `docs/data_dictionary.md`, generated from the table schemas: every table and column, meaning,
  layer, visibility and whether it is scored.
- Every gold table records its `as_of` date and its source tables (lineage).

## 11. Repository additions

```
src/claim_triage/data/
  config.py  reference.py  tokens.py   settings, geography/procedures/bands, keyed-hash tokens
  generate.py        raw layer (seeded, 10 scenarios)
  privacy_split.py   vault + tokens + silver + reviewer view
  quality.py         checks for every layer, canary scan, signal report
  build_gold.py      customer_360, claim_features, area risk, provider stats
  dictionary.py      generates docs/data_dictionary.md
  build.py           runs the whole pipeline
  graph_conn.py      Neo4j credentials (env, Neo4j-*.txt or .env; never printed or committed)
  load_neo4j.py      graph + BM25 indexes from silver/gold, with graph checks
src/claim_triage/tools/
  toolbox.py         11 validated, read-only, point-in-time tools over silver, gold and the graph
data/   raw.duckdb  vault.duckdb  silver.duckdb  gold.duckdb  labels.duckdb   (git-ignored)
docs/data_dictionary.md
tests/data/          privacy, integrity, point-in-time, reproducibility tests
```

## 12. Status and impact on the two-day plan

Done on Day 1: scaffold, generator, privacy split, silver, gold, checks, dictionary, tests.
Still to do: Neo4j load (needs the Aura credentials and a decision about the existing Week 2 graph in the same
instance), vetted tools over gold, then the rules-only baseline and retrieval numbers, which move to the start of Day 2.
If time runs short, cut from the lower end of the DESIGN.md cut line; the privacy split and the quality checks stay.

## 13. Known limits (be honest about these in the demo)

- **Little headroom for the agents.** The rules-only baseline scores 99.5% (dev) and 99% (held-out) band accuracy with 0% of fraud landing in
  green. That shows the rules fit this clean synthetic data, not that fraud detection is solved. Add harder, ambiguous cases (partial signals,
  conflicting documents, near-threshold amounts) before comparing the agents with the baseline.
- **The hard tier (added 31 Oct) is a set of designed probes, not a sample of real difficulty.** 80 extra cases (table `labels_hard`, IDs `HARD-xxxx`, same
  dev/held-out split rule) in two scenarios: **D1** a large bill (2.7-3.5x peers) where the discharge summary does or does not document a medical reason
  (complication, ICU stay), and **D2** a slightly high bill (1.5-1.9x, below the rule threshold) at a hospital with 4-5 reviewer-confirmed frauds in the last
  24 months versus one with 0-1. D1 can only be solved by reading text and D2 only by looking up past decisions, so they show what those abilities are worth,
  not how often they matter. A suspicious D2 case is expected in review (amber), not red. Everything in the tier happens after the last standard claim
  (filed 27-31 Oct; the track record is filed 16-18 Oct and decided by 26 Oct), so the 300 standard fact sheets are unchanged (checked by fingerprint: 0 of 300
  differ). An earlier attempt that used older dates shifted 129 of 300 fact sheets and let one fraud case slip into green for the rules baseline; that is why
  the dates matter. The rules-only baseline scores 50% on the hard dev split (every honest D1 case red, every suspicious D2 case green).
- **Honest claims from a high-fraud area are always "review needed".** That is the agreed policy, but it means the area flag, not the claim,
  drives their review. The report counts these cases ("flagged only because of area history").
- **Area risk is a noisy estimator** at 25 PINs and about 1,500 closed claims; hotspots were made 5x to be detectable.
- **k-anonymity is partial** (see section 4).
- **The vault is a separate file, not encrypted.** A real deployment needs encryption, access control and key management.
- **The token key** defaults to a dev key derived from the seed unless `CLAIM_TRIAGE_TOKEN_KEY` is set.
- **A leak found while building retrieval (fixed).** In the "relatives in one household" scenario the generator gave an existing customer a new
  surname after their earlier claims were written. The old surname stayed in 95 claim descriptions (17 customers), was not recorded as personal
  data, and so was neither scrubbed nor caught by the canary. Former names are now stored in the vault (`former_name`), scrubbed from claim text
  and documents, and covered by the canary; `tests/data/test_name_scrub.py` guards it. Lesson: the canary can only find values it knows about, so
  any generator step that changes a personal value must record the old one too.
- Everything is synthetic; no real personal data was used.
