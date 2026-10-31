# Build Plan — GraphRAG for Organizational Knowledge

**Deliverables (handout, Project 3):** knowledge graph with 20+ nodes · GraphRAG vs. vector
RAG on 10+ queries · analysis of when each wins · Google Doc · ≤5 min demo video · GitHub.
**Bonus:** chatbot UI. **Final deadline:** Nov 11, 2026.

## Decisions

| Area | Decision |
|---|---|
| LLM / embeddings | OpenAI: `gpt-4o-mini` answers (both systems), `gpt-4o` for Cypher + judge, `text-embedding-3-small` |
| Graph DB | Neo4j Aura Free; database name from `NEO4J_DATABASE` (Aura pauses when idle — resume before demo) |
| Dataset | Original `mock_data.py` untouched + moderate extension in `mock_data_ext.py` (~44 nodes) |
| UI | Streamlit with GraphRAG / Vector / Side-by-side modes |

## Graph schema

```
(Person)-[:WORKED_ON {role}]->(Project)-[:USED_TOOL]->(Tool)
(Person)-[:HAS_SKILL {level}]->(Skill)
(Person)-[:AUTHORED]->(Document)-[:ABOUT]->(Project)
(Person)-[:APPROVED]->(Decision)-[:DECISION_FOR]->(Project)
(Person)-[:MEMBER_OF]->(Team)            # extension
(Person)-[:REPORTS_TO]->(Person)         # extension
(Decision)-[:BASED_ON]->(Document)       # extension
(DocChunk)-[:PART_OF]->(Document)
```

Every org node also carries a shared `:Entity` label with `id`, display `name`,
`description` (text from `verbalize.node_text`) and `embedding`. Chunks use `:DocChunk`
(not `:Chunk`) so they never mix with the tutorial's nodes or `project_chunks` index.

Indexes: unique `Entity.id` and `DocChunk.id` · vector indexes `entity_embeddings` and
`chunk_embeddings` (1536-dim, cosine) · full-text index `entity_names` on `Entity.name`
(exact-name entity linking). Dates (`started`, `ended`, `date`) are stored as Neo4j `Date`.

**Built:** 44 nodes (Person 8, Project 5, Skill 6, Tool 7, Document 6, Decision 5, Team 7),
75 relationships, 6 chunks. Neo4j Browser shows **50 nodes / 81 relationships** in total,
because it also counts the 6 `:DocChunk` nodes and their 6 `PART_OF` links.

## Dataset extension (`mock_data_ext.py`)

| Addition | Purpose |
|---|---|
| 7 `Team` nodes + `MEMBER_OF` | team-level questions |
| `proj_audit_2024` — Q3 2024 SOC2 audit (Completed, 2024-07 → 2024-09) | "the **last** audit" needs date ordering |
| `p_henry` — Henry Okafor, Compliance Analyst (2024 audit only) | distractor for vector RAG |
| `tool_vanta` — Vanta (used by 2024 audit) | distractor tool |
| `doc_audit_2024` — 2024 audit report (by Alice; 3 medium findings; Vanta) | |
| `dec_switch_drata` — Replace Vanta with Drata (2025-06-10; Alice + Grace; for 2025 audit; BASED_ON 2024 report) | "why" chains |
| `REPORTS_TO`: Bob, Frank, David, Carol → Grace; Henry → Alice; Alice → Eve | reporting lines |
| `BASED_ON`: tiered pricing → churn analysis, pricing memo; MFA → security policy; Drata switch → 2024 report | provenance |

## Systems under test

**Vector RAG baseline (fair):** every node verbalized into a text card (properties + its
relationships) + all documents → Chroma, same embeddings/LLM/prompt, top-k = 4.

Built in `vector_rag.py`: 44 cards (37–555 chars, median ~250), one card = one chunk,
cosine similarity. Both systems answer through `generation.py` (same prompt, `gpt-4o-mini`,
same refusal sentence, `[id]` citations), so only retrieval differs.

First sanity run (before formal eval):

| Question | Vector RAG result |
|---|---|
| Q1 last audit people + tools | ✅ correct — the 2025 project card holds everything (2024 audit retrieved too, but ignored) |
| Q3 people on >1 project | ❌ found Frank, Bob; missed Alice, David — aggregation needs more than top-4 cards |
| Q11 NRR lift | ✅ 22% over 12 months |
| Q12 GCP migration | ✅ refused |
| Q14 Bob's manager's approvals | ❌ half — found Grace, but her card wasn't retrieved, so no approvals (2 hops) |

**GraphRAG (LangGraph, `graph_rag.py`):**

```
router ─graph──► write_cypher ─► run_cypher ─rows─► grade ─sufficient─► answer
  │                  ▲              │                 └─insufficient─┐
  │                  └─error (1 retry)                               │
  │                                 └─no rows / still failing─► anchor ─► answer
  ├─semantic─────────────────────────────────────────────────► anchor ─► answer
  └─out_of_scope──────────────────────────────────────────────────────► answer (refuse)
```

- **router** (`gpt-4o-mini`, structured output): graph / semantic / out_of_scope.
- **write_cypher** (`gpt-4o`): schema + live entity-name catalog + WORKED_ON role and
  HAS_SKILL level values + 7 few-shots (patterns, not eval questions).
- **run_cypher**: regex rejects write clauses, then runs in a READ transaction (second guard).
  Rows become a results table source plus each referenced entity's description (citable ids).
- **grade** (`gpt-4o-mini`): do the rows answer every part? If not → anchor adds context.
- **anchor**: full-text (exact names, 3) + vector (4) seed entities → 1-hop neighbourhoods
  as `[id]`-tagged triples + 2 closest document chunks.
- **answer**: shared `generation.py` prompt; result carries route path, Cypher, tokens for
  all LLM calls, and the subgraph (nodes/edges) for the UI.

### Phase 3 iteration log (for the Google Doc)

| Run | Problem seen | Fix |
|---|---|---|
| 1 | Anchor query crashed: `ORDER BY i` after an aggregation | Return `i AS seed_rank` and order by it |
| 1 | Q2 had no citations; Q3 cited the results table `[graph_query_result]` | Answer prompt: every fact needs a citation; cite entity ids inside tables |
| 2 | Q8 wrong: Cypher matched `role: 'Lead'` exactly, but leads are "Tech Lead" | Catalog now lists real role/level values; rule: match roles with CONTAINS |
| 2 | Q6/Q7 citations not counted: model wrote `[dec_x, proj_y]` | Citation parser accepts comma-separated ids in one bracket |
| 3 | Q9 regressed: with levels visible, "SOC2 expertise" became `level = 'Expert'` (dropped Frank, Henry) | Rule: filter on level only when the question names a level |
| 3 | Q1 Cypher duplicated rows (two OPTIONAL MATCHes, no DISTINCT); first call 10 s | Rule: collect(DISTINCT …); `warm_up()` loads catalog/connection at start |
| 4 (full re-run) | — | All 14 answers correct on manual review; Q5 recovered via anchor fallback (Cypher used a wrong decision name), Q12 refused correctly |

Warm latency in the full re-run: 3.1–6.9 s per question (median ~5.6 s). Graph-route
questions cost 5.2–6.9 s (router + Text2Cypher on `gpt-4o` + grader + answer), so p95 is
just above the 6 s target; semantic-route questions take 3–4.5 s. Formal metrics come in
Phase 4 — if latency matters there, skipping the grader when Cypher rows look complete is
the obvious lever.

## Evaluation (14 questions)

| # | Category | Question | Expected |
|---|---|---|---|
| 1 | Multi-hop + temporal | Who worked on the last compliance audit and what tools did they use? | 2025 audit: Alice, Frank, Bob; AWS Audit Manager, Drata, Okta (not Henry/Vanta) |
| 2 | Multi-hop | What pricing decisions were made and who approved them? | 3-tier usage pricing (2025-05-20); Eve, Carol |
| 3 | Aggregation | Who has worked on more than one project? | Bob (3), David (2), Frank (2), Alice (2 after extension) |
| 4 | Aggregation | Which tools are used across multiple projects? | Snowflake, Okta |
| 5 | Provenance | What analysis motivated the pricing redesign and who wrote it? | Churn Analysis Q2 2025; David Kim |
| 6 | Multi-hop | Which decisions did Grace approve, for which projects? | MFA mandate (Zero-Trust), PyTorch (ML Recs), Drata switch (2025 audit) |
| 7 | Path | How is Eve Johnson connected to Snowflake? | Eve → Pricing (Exec Sponsor) → Snowflake |
| 8 | Filter + hop | Which in-progress projects is Bob on, and who leads them? | ML Recs (David), Zero-Trust (Frank) |
| 9 | Skill + hop | Who has SOC2 expertise and what did they work on? | Alice (Expert), Frank (Intermediate), Henry (Intermediate) + projects |
| 10 | Semantic | What's our policy on production access? | MFA via Okta, device posture, quarterly pentests |
| 11 | Detail in text | What NRR lift did the pricing proposal project? | 22% over 12 months (vector expected to win) |
| 12 | Unanswerable | Who approved the migration to GCP? | Refuse |
| 13 | Temporal + why | What did we use for audit evidence before Drata, and why did we switch? | Vanta; Drata-switch rationale |
| 14 | Multi-hop | Who does Bob report to, and what has that person approved? | Grace → MFA, PyTorch, Drata switch |

Expected answers for the graph-dependent rows are pinned by offline tests in
`tests/test_mock_data_ext.py`, so a data edit that changes a gold answer fails there first.

Metrics per question × system: entity recall (auto), correctness (LLM judge vs. expected),
faithfulness (LLM judge vs. retrieved context), correct refusal, latency, tokens.
LangSmith tracing on.

## Phase 4 — evaluation (done)

`evaluate.py` runs both systems on `eval/queries.yaml` (warm start), judges correctness and
faithfulness with `gpt-4o`, and writes `eval/results.csv`, `results.jsonl`, `summary.md`.
Run of 2026-10-31: GraphRAG 0.96 avg correctness (13/14), vector 0.71 by the judge /
0.75 human-reviewed (one judge error on vector Q10). GraphRAG p95 5.91 s — inside the
6 s target. Analysis: [comparison_analysis.md](comparison_analysis.md).

## Phase 5 — Streamlit UI (done)

`app.py`, three pages:
- **Chat**: GraphRAG / Vector RAG / Side-by-side (both run in parallel); per answer — stats
  line, "How this answer was found" (pipeline path, Cypher, every source with score and
  cited flag), interactive subgraph (cited nodes enlarged), 👍/👎 → `feedback.jsonl`.
- **Evaluation**: metric deltas, all-metrics table, correctness by category chart,
  per-question table, answer-pair inspector with judge reasons and Cypher.
- **Knowledge graph**: the full graph with a node-type filter, plus the schema text.

UI decisions: own vis-network component instead of `streamlit-agraph` (no `key`, so
repeated graphs collide); warm-up cached with `st.cache_resource`; clear error if Aura is
paused.

## Phases

1. `mock_data_ext.py` + `ingest.py` → verify (44 nodes, 75 rels, 6 chunks, 3 indexes online)
2. `verbalize.py` + `vector_rag.py` (Chroma)
3. `graph_rag.py` (LangGraph pipeline)
4. `eval/queries.yaml` + `eval/run_eval.py` → `results.csv` + `docs/comparison_analysis.md`
5. `app.py` (Streamlit: modes, Cypher panel, context, citations, graph view, eval page, feedback)
6. README, Google Doc, ≤5 min video
