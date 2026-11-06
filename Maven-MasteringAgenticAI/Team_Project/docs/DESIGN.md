# Insurance Claim Triage — Design Plan

Reviewer dashboard that gives every insurance claim a **trust score**, a colour band and
reasons. An agentic system (Hybrid RAG: Graph + BM25) investigates; a **human reviewer decides
every claim**; reviewer decisions flow back into the knowledge base under a governed gate.

Status: designed step by step on 2026-10-31 and built as an MVP by one person in about two days (Oct 31 – Nov 1). Section 0 says what is built,
what is measured and what is not; the rest of the document describes the design and, where marked **Built**, what actually exists.

Builds on: `Insurance_Fraud_Agentic_AI_PRD_FINAL.docx` (contracts, guardrails, acceptance
tests), `Fraud Detection in Insurance claims.docx` (3-layer detection), and the Week 2
OrgGraph RAG project in `GenAI_Learnings` (Neo4j + LangGraph + eval harness).

---

## 0. Status at a glance

| Component | Status | Evidence |
|---|---|---|
| Data 360: 600 customers, privacy split (vault + tokens), k-anonymity, canary scan, area risk | Built, tested | 47 quality checks, [DATA_360.md](DATA_360.md) |
| Graph + 13 read-only, validated, point-in-time tools (incl. `get_claim_documents`, `search_knowledge`) | Built, tested | [tools.md](tools.md) |
| Rules-only baseline (no AI) | Built, **measured** | original 300 cases: 99.5% dev, 99% held-out right band, 0% fraud in green |
| 3 LLM agents: Investigator, Verifier, Explainer | Built; **measured on the full dev and held-out sets** (small model) | original (dev 200 / held-out 100): 91.5–93% right band vs 99–99.5% for the rules; hard (dev 52 / held-out 28): 71% without / 100% with retrieval vs 50%; on the original cases 0 fraud in green and 0 honest in red; dev and held-out agree; with the larger model for cross-policy claims on X1+X2 dev (40 cases): 95% vs 65–78% for the small model (rules 100%), at ~18× the cost per claim |
| Hybrid retrieval: BM25 + graph, RRF, rerank, provider track record | Built; **measured** | original cases: no real difference (dev +1.5, held-out −1 point); hard cases: 71% → 100% on both dev and held-out (the bad-hospital cases need it) |
| Hard tier: 80 designed cases (explained-in-text bills, bad-provider track record) | Built | rules score 50% on them |
| Dashboard: review queue, claim page with decision panel and audit trail, Knowledge Base tab, Evaluation page | Built, tested headlessly | 206 tests |
| Learning loop: pending → safety check → active / blocked | Built, tested on the live index | a good signal activates, a spammy one is blocked |
| **Not built / not done:** dense retrieval arm, MCP wrapper, LangGraph orchestration, LangSmith tracing (local JSONL traces instead; optional Langfuse tracing was added later), routed-model run on X3, X4 and held-out (X1 and X2 dev done), 3-run stability, judge model scores, fairness checks for the agents | — | needs paid runs or more time |

The honest headline: the system works end to end and its guard rails are tested, but **on the clean original data the plain rules beat the agents**
(99–99.5% right band against 91.5–93% for the agents on the dev and held-out splits, which agree; the agents ran on the small model). On that data the agents never put
fraud in green or an honest claim in red, but they send 14–16 of 70 dev frauds (6 of 30 held-out) to "review needed" instead of red, mostly on cross-policy claims,
where those runs used the small model instead of the larger one; a run with the larger model on the two weakest scenarios (X1, X2, dev only) reached 95% against 65–78% for the small model and 100% for the rules, at about 18 times the cost per claim. The agents win on the 80-case hard tier (71% without retrieval and 100% with it on both splits, against
50% for the rules); those cases were designed to need reading text and looking up a hospital's record. Agent runs cost about $10.4 in total.

**Explainer finding (from the Langfuse traces).** In every saved run the Explainer's answer limit was 900 tokens, and the small model's hidden thinking often used all of it, so the call came back empty and the original code-made sentences were kept (about 3 in 10 rewrites; the Evaluation page now shows the count per run). Scores and bands are not affected, because the Explainer never touches them. The limit is now 2,500 and was re-checked on three claims; the full runs were not repeated, so the saved reports still show the old fallback counts.

## 1. Decision log

| Step | Decision |
|---|---|
| 1 Score | 0–1 **trust score** (high = looks legitimate). Heuristic, not a fraud probability |
| 2 Scope | Health + Term + Motor; 6 within-policy + 4 cross-policy scenarios; synthetic data; text snippets, not PDFs |
| 3 Store | Layered data (raw → privacy split → silver → gold, in DuckDB) feeds Neo4j (graph + BM25 full-text + vector index). Full data design: [DATA_360.md](DATA_360.md) |
| 4 Retrieval | Vetted read-only Cypher tools (MCP); BM25 + graph, RRF fusion, **LLM listwise rerank (10 → 5)**; dense arm as an ablation flag |
| 5 Agents | Deterministic orchestrator; **3 LLM agents**: one **Investigator** (bounded ReAct loop; always the Health, Term or Motor checklist, plus the cross-policy checklist when the person holds more than one policy), a **Verifier** (evidence-ID check in code, then a devil's advocate on a different model family) and an **Explainer**; pure-Python scorer using noisy-OR |
| 6 Reviewer | Every decision is logged for audit. A separate **Knowledge Base tab** lets the reviewer tick the signals worth keeping and click **Update knowledge base**; ticked signals are quarantined, pass a regression gate, then go live. They are agent context only, never read by the scorer |
| 7 Eval | Custom harness (`evaluation/harness.py`) plus pytest; local JSONL traces, plus optional Langfuse tracing (`tracing.py`, off unless its keys are set; the LangSmith key is not wired); 20 dev / 10 held-out per scenario; headline = fraud landing in green |
| 8 Stack | Streamlit, LiteLLM, Neo4j, DuckDB, Pydantic, `uv`, pytest (a plain-code orchestrator; LangGraph and LangSmith were descoped; Langfuse is an optional tracing extra, `uv sync --group tracing`) |
| 9 Models | Investigator: Claude Haiku 5.5 (Sonnet 5.5 when the cross-policy checklist is loaded); Verifier: GPT-4o-mini; Explainer and reranker: Haiku 5.5; evaluation judge: GPT-4o; each falls back to the other vendor. Set in `config/models.yaml`; keys in `API_Keys.txt` (git-ignored) |
| 10 Hard cases | Two designed probes in their own label table (`labels_hard`, 80 cases): **D1** a large bill whose discharge summary does or does not give a medical reason; **D2** a slightly high bill at a hospital with or without a record of reviewer-confirmed frauds. Filed after the last original claim so the original 300 are unchanged. A "near the line" tier was offered and not chosen |
| 11 Dashboard | Streamlit, four pages (Review queue, Claim, Knowledge base, Evaluation). A reviewer's decision is Agree / Change the band (reason required) / Hold (does not close the claim); every decision is audited, and disagreeing with the system is recorded, never blocked |
| 12 Budget | After about $5 of agent runs the owner paused paid runs, then approved a small-model evaluation of dev ($1.80) and held-out ($0.89) and a larger-model run on two scenarios ($2.80): about $10.4 in total. Everything else reads saved results or uses free checks |

## 2. Score and bands

- `trust = 1 − risk`; `risk = 1 − Π(1 − weight_i × confidence_i)` over verified indicators (noisy-OR).
- **Red** `trust < 0.3` · **Review needed** `0.3 ≤ trust ≤ 0.8` · **Green** `trust > 0.8`.
- Red and review-needed must show **reasons with evidence IDs**; review-needed also lists the checks still outstanding. Green shows a short "checks passed" summary.
- **Evidence gate:** if critical evidence is missing, `trust` is capped at 0.80, so the claim can never be green, and the missing items are listed.
- **Hard rule:** a confirmed exact duplicate caps `trust` at 0.29 (red).
- An indicator without an evidence ID that a tool actually returned is dropped before scoring.
- Green is **not** an approval. The reviewer decides every claim. UI wording never says "fraud proven".
- Weights live in `config/weights.yaml`, are illustrative, and are tuned on the dev split only.

## 3. Architecture

```
Claim in (Pydantic validation)
  → Customer-360 lookup: all of this person's policies and claims
  → deterministic rules + graph aggregates            (cheap, runs first)
  → hybrid retrieval: BM25 + graph expansion → RRF → LLM rerank (10 → 5)
        sources: fraud-pattern cards, policy clauses, reviewer precedents
  → Investigator (bounded ReAct; Health | Term | Motor checklist, + cross-policy checklist if > 1 policy; ≤ 7 tool calls, ≤ 9 with cross-policy)
  → verifier: evidence-ID check in code, then devil's-advocate LLM
  → scorer (pure Python) + evidence gate → trust, band
  → explainer: reasons from indicators + counterfactual ("turns green if X is supplied")
  → reviewer queue → claim page → decision + reason   (Streamlit; saved to `app.duckdb` for audit)
  → Knowledge Base page: reviewer ticks findings from decided claims, writes a note → "Update knowledge base"
  → ticked signals saved as PENDING → safety check on the live index → ACTIVE (searchable by agents) or BLOCKED
```

Agent roster: **3 LLM agents** — the **Investigator** (one agent whose checklists (skill files) are loaded as needed: the claim's line
always, cross-policy when the person holds more than one policy), the **Verifier** and the **Explainer**; all three run on every claim.
There are also **2 LLM helpers** (reranker, evaluation judge) and **4 code engines** (orchestrator, rules, scorer, tool server).

Rules for the LLM: it extracts indicators and writes narrative. It never invents a score,
fabricates evidence, or treats a retrieved pattern as proof (a pattern is a hypothesis).

## 4. Data and scenarios

Synthetic only, fixed seed, Indian-format but fake and masked identifiers (vehicle regs,
phones, policy numbers). Hidden ground-truth labels live outside Neo4j and outside every tool.
Include benign lookalikes and missing-data controls for every scenario.

| ID | Scenario | Indicator code |
|---|---|---|
| H1 | Duplicate health claim | `DUPLICATE_CLAIM` |
| H2 | Inflated billing vs provider peers | `BILL_OUTLIER_VS_PEERS` |
| T1 | Nominee changed shortly before death claim | `NOMINEE_CHANGE_NEAR_EVENT` |
| T2 | Conflicting death dates across documents | `DOC_DATE_MISMATCH` |
| M1 | Repeat claim for same vehicle/incident | `REPEAT_INCIDENT_CLAIM` |
| M2 | Claimed damage visible in earlier inspection | `DAMAGE_IN_PRIOR_INSPECTION` |
| X1 | Non-disclosure: health claim for a condition in earlier records, soon after inception | `PRE_EXISTING_NEAR_INCEPTION` |
| X2 | Early term death claim contradicting application or recent health claims | `EARLY_DEATH_VS_HEALTH_HISTORY` |
| X3 | Same accident claimed under motor and health, inconsistent facts | `CROSS_POLICY_INCIDENT_MISMATCH` |
| X4 | Unrelated customers sharing hospital/garage/phone/bank account | `SHARED_ENTITY_CLUSTER` |

Size: about 600 customers and ~1,500 past claims (they build the area-risk statistics), plus ~300
evaluation claims. Per scenario: 20 dev / 10 held-out including controls. Details: [DATA_360.md](DATA_360.md).

## 5. Graph schema

**Built** (see [DATA_360.md](DATA_360.md) §5b; only reviewer-safe values, tokens instead of phones, accounts and addresses):

```
(Person)-[:HOLDS]->(Policy)-[:HAS_CLAIM]->(Claim)-[:AT_PROVIDER]->(Provider)
(Claim)-[:SUPPORTED_BY]->(Document)   (Claim)-[:FOR_CONDITION]->(Condition)   (Claim)-[:PAID_TO]->(BankAccount)
(Claim)-[:FOR_VEHICLE]->(Vehicle)     (Policy)-[:COVERS]->(Vehicle)           (Policy)-[:HAS_NOMINEE {relation}]->(Nominee)
(Person)-[:USES]->(Contact | BankAccount)                                     (Person)-[:LIVES_IN]->(District)
(:Pattern) (:Clause) (:ApprovedSignal)        knowledge nodes, searched by BM25 and never linked to a person
```

BM25 (Neo4j full-text) indexes: `document_text`, `claim_text`, `provider_names`, `knowledge_text` (patterns, clauses, approved signals). Neo4j Aura Free has
no GDS library, so ring detection is plain Cypher shared-neighbour counts (enough at this size). The original design's `Incident`, `Signal` and
`SIMILAR_TO` nodes were not needed.

## 6. Retrieval

1. **Anchor:** exact BM25 match on identifiers (claim, policy, provider, vehicle).
2. **Expand:** 1–2 graph hops from the anchors via vetted tools: `get_person_timeline`,
   `find_shared_entities`, `get_provider_stats`, `find_similar_claims`,
   `get_vehicle_inspections`, `get_policy`. All read-only, parameterised, argument-validated,
   record-limited, never exposing labels. Text2Cypher is not in the decision path.
3. **Text search:** BM25 over pattern cards, clauses and precedents using the claim's own text.
4. **Fuse:** reciprocal rank fusion of the graph and BM25 lists, top 10.
5. **Rerank:** LLM listwise rerank behind an interface (a cross-encoder can replace it later), keep 5.
   On failure, fall back to fused order and log degraded mode.
6. **Ablation flags:** BM25-only, graph-only, hybrid, hybrid + dense, with and without rerank.

**Built (31 Oct):** one tool, `search_knowledge(claim_id, query, limit)`, in `src/claim_triage/retrieval/`. Arms: BM25 over pattern cards, clauses
and approved signals; BM25 over earlier decided claims of the same line; a graph arm over earlier decided claims linked by another customer's
payout account, the same vehicle, or the same provider (and procedure). Fused by reciprocal rank fusion (k = 60), top 10 reranked by Haiku, top 5
returned; if the reranker fails the fused order is kept and the result says `degraded`. Rules that keep it honest: only precedents decided
before the claim's filing date count (the agent cannot choose the date); patterns and clauses are context and are never returned as evidence IDs;
the Investigator is told a precedent never creates an indicator by itself. Switch with `--retrieval none|bm25|graph|hybrid|hybrid_rerank`
(`none` removes the tool). The dense arm is not built. In graph and hybrid mode `search_knowledge` also returns a **provider track record** (how many of the
provider's claims decided in the last 24 months a reviewer confirmed as fraud), and the Investigator can read a claim's own documents with
`get_claim_documents`. Measured with the small model on dev and held-out (no retrieval → with retrieval): original cases 91.5% → 93% (dev) and 93% → 92% (held-out), no real difference; hard cases 71% → 100% on both splits.
The hard-case gain is the bad-hospital probe, which only retrieval can see; the checklist tells the agent what to do with the record, so this shows the
information matters, not that the agent discovers the pattern alone.

## 7. Contracts (freeze first)

Pydantic, stable field names. `insurance_type ∈ {health, term, motor}`, `event_type ∈ {claim, application}`.

- `CaseContext`: `case_id, insurance_type, event_type, submitted_fields, history_records, retrieved_patterns, precedents`
- `InvestigationResult`: `case_id, agent_type, indicators[{code, severity, confidence, evidence_ids, explanation, counter_evidence}], missing_evidence[], summary, trace_id`
- `FinalResult`: adds `trust_score, band, evidence_status, reasons[], next_action, outstanding_checks[], counterfactual, models_used, tokens, cost_usd`
- `KnowledgeSignal`: `signal_id, source_case_id, indicator_code, plain_text, evidence_ids, reviewer, note, status (pending|active|blocked), gate_result, created_at`
- `DecisionLog` (audit only, never retrieved): `case_id, reviewer, decision, reason, system_band, disagrees_with_system, created_at`

A case ID is distinct from a claim ID.

## 8. Reviewer workflow and the Knowledge Base tab

The Streamlit dashboard (`uv run streamlit run src/claim_triage/app/dashboard.py`) has **four pages**:

- **Review queue:** every assessed claim, least trusted first, with band, trust, customer ID (only), first reason, findings, missing documents and
  reviewed status; filters for band, line, "not reviewed" and a search box.
- **Claim:** score, band and evidence status; reasons; each finding with a plain name, confidence, counter-evidence and the evidence it cites; what is still
  to check; a **Look at a record** viewer that opens any cited claim, document, event, provider or policy; the claim and the customer as a reviewer may see them
  (coarse bands only); and the **decision panel**: Agree / Change the band (reason required) / Hold (reason required, claim stays open) / Escalate (name the person and give a reason; the claim stays
  open and moves to that person). Optional **tags** say what was wrong with the system's output, and an **owner** can be set on the claim page; the queue
  shows an "Assigned to" column and an "Assigned to me" filter. **Every decision is logged for audit**, including whether it disagreed with the system. The page offers the saved agent run as an alternative view where one exists. (The mini
  Customer-360 graph from the first design was not built.)
- **Knowledge base:** the reviewer chooses what the system should learn, and nothing is learned silently (below).
- **Evaluation:** every run so far with a plain note on what it does and does not prove, the hard-case comparison, one run in detail, fairness for the rules
  baseline and the list of what is not measured. It shows totals only, never an individual claim's expected answer.

**How the Knowledge base tab works**

1. It lists the findings from claims the reviewer has **already decided** (a hold is not a decision), in plain words, each with
   its claim and the reviewer's decision. Area and cover findings are not offered: they are recalculated for every claim, so there is nothing to learn.
2. The reviewer **ticks a checkbox** next to each signal worth keeping and writes a short note
   (required).
3. The reviewer clicks **Update knowledge base**. Each ticked signal is saved as `pending`, a
   regression gate re-runs the frozen evaluation subset with it included, and the tab shows the
   result per signal: added, or blocked with the reason (for example "made 3 test cases worse").
4. Only when it passes does the signal become `active`, and its note enters the BM25 index.

**Built (31 Oct):** the tab, the signal store (`app/kb.py`) and the safety check (`learning/gate.py`, `process.py`, `kb_writer.py`). The check is honest
about its scope: a signal only informs what agents can look up, so it cannot be tested for its effect on decisions without paid runs. What it does test, for
free and against the real search index: (1) the signal is safe (real evidence IDs, no personal data, nothing that reads as an instruction, no certainty
claims, not a near-copy of an active signal); (2) with the signal in the index, none of 36 frozen test searches (`knowledge/gate_queries.yaml`, 3 per finding
type) loses its right pattern card from the top 3, and the signal does not appear in the top 3 of more than 2 searches about unrelated findings. A seeded
spammy signal is blocked by the second test (it appeared in 12 unrelated searches); a good one is activated. If the graph is unreachable the signal stays
pending (fails closed). Approved signals are written as `ApprovedSignal` nodes (the only graph write the app can do) and are visible to every claim from their
approval date; the scorer never reads them. Not measured yet: whether activating signals improves agent decisions (needs the paid evaluation).

Guardrails:
- **Disagreement rule:** if the reviewer's decision contradicts the band (for example approving a
  red claim), signals from that case need a longer note and carry an audit tag.
- Approved signals reach agents as retrieved context (elsewhere in this document they are called
  "precedents"). **The scorer never reads them.**
- Human feedback never overwrites synthetic source records.

## 9. Evaluation

Frozen KB snapshot per run, so results are reproducible. Tune on dev only; report on held-out.
Targets are demo targets, not production claims.

| Level | Metric | Target |
|---|---|---|
| Retrieval | Pattern recall@5, MRR; BM25 vs graph vs hybrid (± dense, ± rerank) | Hybrid ≥ each single arm |
| Trajectory | Right skill chosen; required-tool coverage; tool calls/case; loop-guard trips | 100%; ≥ 90%; ≤ 6; 0 |
| Grounding | Indicators with a real evidence ID; reason faithfulness | 100%; ≥ 0.95 |
| Decision | **Fraud landing in green (headline)**; legitimate landing in red; amber rate | ≤ 5%; ≤ 10%; ≤ 40% |
| Safety | Missing-evidence cases never green; prompt-injection in documents succeeds | 100%; 0 |
| Stability | Same band across 3 runs; paraphrase robustness | ≥ 90% |
| Learning loop | Gate blocks a seeded bad precedent; before/after on similar cases | Blocks it; improves, no regression |
| Agents | Investigator calls the tools its checklist requires; Verifier removes bad findings without removing good ones; Explainer stays faithful to the verified findings | ≥ 90%; Report; ≥ 0.95 |
| Model plan | all-Haiku vs routed (Haiku, Sonnet for cross-policy) vs all-Sonnet investigator: what the stronger model buys | Report |
| Cost | p95 latency, tokens and USD per case | Report |

Also report a **rules-only baseline** next to the agentic system, so the agents' added value is measured, not assumed.
Judge: a different model from the agents, with human spot-checks (Week 2 had 1 judge error in 28).
Week 2 lesson: re-run the whole set after every prompt change; hints can over-constrain other cases.

## 10. Stack and repo layout

Python 3.14 (3.11 or newer works), `uv`, Pydantic, LiteLLM, DuckDB, Neo4j (BM25 via its full-text index), Streamlit, pytest. Install with `uv sync --native-tls` behind TLS inspection.

Models (via LiteLLM, set in `config/models.yaml`): Investigator **`claude-haiku-5-5`**, or **`claude-sonnet-5-5`** when the cross-policy
checklist is loaded; Verifier **`gpt-4o-mini`** (a different model family gives an independent second opinion); Explainer and reranker
**`claude-haiku-5-5`**; evaluation judge **`gpt-4o`**. Each falls back to the other vendor. Keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`LANGSMITH_API_KEY`) are read by `apikeys.py` from `API_Keys.txt` or `.env`, both git-ignored, and are never printed.
Retry only transient and provider failures; enforce timeouts; log the model used; if all fail, return "analysis unavailable" with no
fabricated score.

Learned in the first smoke test: Claude 5.5 models accept only their default temperature (so no temperature 0 for Claude; the stability
check measures the effect), LiteLLM tool calling needs the `mcp` package, and behind TLS inspection Python must use the Windows
certificate store (`truststore`).

```
config/{data,models,weights}.yaml       knowledge/{fraud_patterns,policy_clauses,gate_queries}.yaml
src/claim_triage/
  contracts.py  llm.py  apikeys.py  pipeline.py  baseline.py
  data/        generator, privacy split, silver/gold, quality checks, Neo4j loader (see DATA_360.md)
  tools/       toolbox.py (13 read-only tools)
  scoring/     rules.py, scorer.py (plain arithmetic; never reads signals)
  retrieval/   hybrid.py, fuse.py, rerank.py (search_knowledge)
  agents/      investigator.py, verifier.py, explainer.py, base.py
  skills/      common.md, health.md, term.md, motor.md, cross_policy.md
  learning/    gate.py, process.py, kb_writer.py (the safety check; the only graph writes)
  evaluation/  harness.py (the only package that reads the hidden answer keys)
  app/         dashboard.py, review_queue.py, claim_view.py, kb.py, eval_view.py, evidence.py, plain.py, store.py, backfill.py
tests/         206 tests (data, tools, scoring, agents with a scripted fake model, retrieval, learning loop, dashboard pages)
docs/{tools,data_dictionary}.md   docs/DEMO.md   README.md
```

## 11. Build log and what is left

The original two-day plan is kept below for the record. What happened: Day 1 and part of Day 2 went on the data foundation (privacy split, quality checks,
a name-leak found and fixed), the tools and the baseline; then the agents, retrieval, the hard tier, the dashboard and the learning loop were built in
order, with the evaluation shrunk to small samples when the budget for paid runs ran out. **Left to do:** the routed-model run on X3, X4 and the held-out split (on X1 and X2 dev it closed most of the gap: 95% vs 65–78%, at ~18× the cost), the 3-run stability check, the judge model, fairness for the agents, the dense arm and the MCP wrapper. The small-model evaluation cost $1.80 on dev and $0.89 on held-out; the larger-model run on X1 and X2 cost $2.80.

*Original plan:*

**Day 1 — Sat Oct 31: data, graph, tools, baseline**
1. Scaffold: `uv` project, frozen contracts, config, LiteLLM smoke test, Neo4j connection (~1 h)
2. Data 360 pipeline: seeded generator, privacy split (vault + tokens), silver and gold tables, quality checks, Neo4j load (see DATA_360.md §12; ~4 h, so tools and the rules-only baseline shift)
3. Vetted Cypher tools as plain Python + tests; rules → scorer; **rules-only baseline eval** (~1.5 h)
4. BM25 + graph retrieval, RRF, pattern cards, retrieval ablation numbers (~1.5 h)

Exit criteria: CLI scores a case end to end with rules only, and baseline plus retrieval numbers exist.

**Day 2 — Sun Nov 1: agents, UI, eval, loop**
1. Investigator (4 checklists), Verifier, Explainer, pipeline, local traces (planned with LangSmith; dropped, and optional Langfuse tracing added later) (~2 h)
2. Streamlit: review queue, case page, decision panel with interrupt/resume, plus the Knowledge base tab with checkboxes and the Update button (~2 h)
3. Full eval harness with dev/held-out and judge; mini regression gate; Evaluation page (~1.5 h)
4. README, demo script, buffer (~1 h)

**Cut line (drop in this order if behind):** MCP server wrapper (keep the tools as Python
functions) → dense arm → reranker (keep RRF) → within-policy scenarios down to one per line
(keep all 4 cross-policy, they are the point) → counterfactual polish → full poisoning test
(keep one seeded bad-precedent test).

**Stretch:** MCP wrapper, dense ablation arm, cross-encoder rerank, LLM extraction from
snippets, calibration plot, second-reviewer approval.

## 12. Risks and open items

- **The agents lose to the rules on the original data** (section 0) and win only on hard cases designed to favour reading and retrieval. The held-out run agrees with dev; an earlier 30/30 sample was tuned on, so it is not evidence.
- **Keys:** provided in `API_Keys.txt` and `Neo4j-*.txt` (git-ignored). The API keys appeared in a chat transcript: **rotate them and set spending limits**.
- **Synthetic data only.** The privacy design (vault, tokens, k-anonymity, canary) is tested, but the vault is an unencrypted file and k-anonymity on all quasi-identifiers is not achievable at 600 customers.
- **Aura Free pauses when idle:** resume before runs; the dataset rebuilds from the seed.
- **LLM variance:** GPT calls use temperature 0, Claude 5.5 cannot (default only), so variance is higher: a 3-run stability check measures it; scoring is deterministic.
- **Eval cost:** run the evaluation concurrently; default to the held-out set, with dev runs on a subset.
- Safe degradation: tracing (the optional Langfuse hook) can never block a run, and its errors are swallowed; if retrieval
  fails, say so rather than pretending retrieval worked.
