# GraphRAG vs. Vector RAG — Comparison Analysis

**Project 3 · GraphRAG for Organizational Knowledge** · run 2026-10-31 · 14 questions ·
raw results in [`eval/results.csv`](../eval/results.csv) / [`results.jsonl`](../eval/results.jsonl),
auto-generated tables in [`eval/summary.md`](../eval/summary.md).

## TL;DR

| | GraphRAG | Vector RAG |
|---|---|---|
| Correctness, LLM judge (avg) | **0.96** | 0.71 |
| Correctness, human-reviewed (avg) | **0.96** | 0.75 |
| Fully correct answers (human-reviewed) | **13 / 14** | 8 / 14 |
| Faithfulness (avg, non-refusals) | 1.00 | 1.00 |
| Entity recall (avg) | **1.00** | 0.76 |
| Right refusal behaviour | **14 / 14** | 13 / 14 |
| Median / p95 latency | 5.45 s / 5.91 s | **2.19 s / 3.85 s** |
| Tokens per question (avg) | 2,548 | **588** |

GraphRAG won 6 questions and tied 8; vector RAG won none on correctness. Vector RAG is
**~2.5× faster and ~4.3× cheaper** in tokens. Both systems were fully grounded
(faithfulness 1.0): vector RAG's failures were *incomplete* answers or a wrong refusal, not
invented facts.

**The pattern:** when the answer sits on one or two entities, vector RAG over well-written
entity cards is as good and much cheaper. When the answer has to be **assembled across many
entities** (counting, "more than one", "shared by") or needs **two hops** from the entity
the question names, GraphRAG wins, because a Cypher query reaches every matching node while
top-k similarity can only see k cards.

## Setup — making the comparison fair

| | GraphRAG | Vector RAG |
|---|---|---|
| Knowledge | Neo4j graph: 44 entities, 75 relationships, 6 doc chunks | 44 Chroma "entity cards": each node's properties **plus every relationship it takes part in** (both directions) |
| Retrieval | LangGraph: router → Text2Cypher (`gpt-4o`) → grader → entity-anchored fallback | Dense top-4 by cosine similarity (`text-embedding-3-small`) |
| Answer | `gpt-4o-mini`, shared prompt, refusal sentence, `[id]` citations | identical |

The baseline holds **the same facts as the graph** (a unit test checks that every
relationship appears on both endpoints' cards). A naive baseline over the 5–6 raw documents
would have lost almost everywhere — the documents never name who approved decisions or who
was on the audit team — which would compare *data access*, not retrieval. With fair cards,
the comparison isolates the retrieval pattern.

Scoring: `gpt-4o` judges correctness against a reference answer (1 / 0.5 / 0) and
faithfulness against the retrieved context; entity recall and distractor checks are string
matches; latency includes every LLM call in each pipeline.

## Results by question type

| Category | Questions | GraphRAG | Vector RAG | Why |
|---|---|---|---|---|
| Aggregation | Q3, Q4 | **1.00** | 0.25 | Needs all people/tools; top-4 shows only some |
| Multi-hop | Q2, Q6, Q14 | **1.00** | 0.67 | Second hop's entity card not retrieved |
| Filter + hop | Q8 | **1.00** | 0.50 | Lead isn't on Bob's card; inferred the wrong person |
| Semantic | Q10 | 1.00 | 0.50 → **1.00** after review | Judge error (see below) |
| Skill + hop | Q9 | 0.50 | 0.50 | GraphRAG: answer model dropped a retrieved project |
| Multi-hop + temporal | Q1 | 1.00 | 1.00 | The 2025 audit's card lists all people and tools |
| Provenance | Q5 | 1.00 | 1.00 | Churn-analysis card names its author |
| Path | Q7 | 1.00 | 1.00 | Eve's card + Snowflake's card both name the pricing project — the full path |
| Detail in text | Q11 | 1.00 | 1.00 | Number inside a document; both find the memo |
| Temporal + why | Q13 | 1.00 | 1.00 | One decision card holds both answers |
| Unanswerable | Q12 | 1.00 | 1.00 | Both refused |

## When GraphRAG wins — and why

1. **Aggregation across the whole graph (Q3, Q4).** "Who has worked on more than one
   project?" needs every Person–Project edge. GraphRAG ran
   `MATCH (p:Person)-[:WORKED_ON]->(proj) WITH p, collect(proj) AS projects WHERE size(projects) > 1`
   and got all four people. Vector RAG retrieved two team cards and two people cards (the
   question has no distinctive words to match on), so it found Bob and Frank and missed
   Alice and David. For Q4 ("tools used across multiple projects") it retrieved four tool
   cards — Vanta, AWS Audit Manager, Drata, Tableau — each used by a single project, missed
   Snowflake and Okta, and **wrongly refused**.
   *Top-k is a hard ceiling: no k fits "all entities that satisfy X".*

2. **Second hop from the named entity (Q14, Q6).** "Who does Bob report to, and what has
   that person approved?" Vector RAG found Bob's card ("Reports to: Grace Patel") but not
   Grace's card, so it answered half the question. GraphRAG's
   `(b)-[:REPORTS_TO]->(m)-[:APPROVED]->(d)` gets both hops in one query. Q6 is the same
   shape: Grace's card lists her decisions, but the projects they belong to are on the
   *decision* cards, which weren't retrieved.

3. **Filter + hop with role semantics (Q8).** "Which in-progress projects is Bob on, and
   who leads them?" Leads are recorded on the WORKED_ON edge as "Tech Lead". Vector RAG saw
   Bob and Grace (Executive Sponsor of Zero-Trust) and **named Grace as the leader** — a
   plausible but wrong inference that the faithfulness judge let pass because Grace's link
   to the project *was* in the context. GraphRAG filtered on
   `toLower(w.role) CONTAINS 'lead'` and returned David Kim and Frank Lee.

4. **Refusal precision.** GraphRAG's refusal on Q12 is backed by evidence: the Cypher query
   for a GCP decision returned 0 rows, and the anchor fallback found nothing relevant.
   Vector RAG refused correctly on Q12 but also refused on Q4, where the answer existed.

## When vector RAG is enough — and where it is better

- **Answers that live on one or two entities** (Q1, Q2, Q5, Q7, Q13): with rich entity
  cards, the best-matching card *is* the answer. Q1 ("last compliance audit") was designed
  as a GraphRAG showcase, but the 2025 audit's card lists every person and tool, and the
  model correctly ignored the 2024 audit card that was also retrieved. **Good chunk design
  closes much of the gap for lookup questions.**
- **Facts inside free text** (Q10, Q11): policy wording and numbers live in document
  content. GraphRAG's router sends these to its semantic path (vector seeds + chunks), so
  it is effectively vector RAG here too — only slower.
- **Latency and cost, always.** Vector RAG makes 1 LLM call per question; GraphRAG makes
  3–4 (router, Text2Cypher on `gpt-4o`, grader, answer). That is the 2.5× latency and
  4.3× token difference. GraphRAG still met the < 6 s p95 target (5.91 s), but only just.

## Failure analysis

| Q | System | Score | What happened | Root cause | Fix / mitigation |
|---|---|---|---|---|---|
| Q9 | GraphRAG | 0.5 | Listed Frank's 2025 audit but not his Zero-Trust project | **Generation**, not retrieval: the Cypher rows and sources included `proj_zerotrust`; `gpt-4o-mini` read "what projects" as "what SOC2 projects" | Stronger answer model, or a prompt rule to list every retrieved item for list questions |
| Q5 | GraphRAG | 1.0 | Cypher filtered decisions on "pricing redesign" (that's the project's name, not the decision's) → 0 rows | Text2Cypher name mismatch | Already handled: anchor fallback found the churn analysis + author |
| Q3 | Vector | 0.5 | Found 2 of 4 people | Top-k ceiling on aggregation | Not fixable by tuning k; needs structured retrieval |
| Q4 | Vector | 0 | Refused although the answer exists | Top-k ceiling; retrieved tools used by one project each | Same |
| Q6 | Vector | 0.5 | Projects missing for 2 decisions; cited a non-existent id `proj_zero_trust` | Second hop not retrieved; model guessed an id | Citation validator dropped the fake id (only real ids count) |
| Q8 | Vector | 0.5 | Named the Executive Sponsor as the leader | Lead's card not retrieved; plausible inference | — |
| Q14 | Vector | 0.5 | Found the manager but not her approvals | Second hop not retrieved | — |
| Q10 | Vector | 0.5 → 1 | Judge said the MFA date "2025-10-05" was incorrect | **Judge error** — that date is correct in the data | Reported both scores; human review overrides |

### About the judge

One of 28 judgments was wrong (vector Q10): the answer added a true detail (the MFA mandate
date) that the reference didn't mention, and the judge marked it as an error despite the
instruction that extra true details are fine. Every other non-1 score was checked by hand
against `results.jsonl` and agreed with. Human-reviewed vector correctness is therefore
0.75 (8/14 fully correct) rather than 0.71 (7/14). GraphRAG's numbers are unchanged.

## Iterations that mattered (GraphRAG)

Full log in [`docs/PLAN.md`](PLAN.md#phase-3-iteration-log-for-the-google-doc).

1. **Role values in the Text2Cypher prompt.** Q8 first failed because Cypher tested
   `role = 'Lead'` while leads are stored as "Tech Lead". Adding the real WORKED_ON role and
   HAS_SKILL level values to the prompt, plus a "match roles with CONTAINS" rule, fixed it.
2. **…which broke Q9.** Seeing the "Expert" level, the model turned "SOC2 expertise" into
   `level = 'Expert'`. A rule to filter on level only when the question names one restored
   it. *Lesson: every hint you give Text2Cypher can over-constrain another question —
   re-run the whole set after each prompt change.*
3. **Citations.** The answer model cited the results table instead of entities, or none
   at all, and grouped ids in one bracket (`[dec_x, proj_y]`). After fixing the prompt and
   the parser, all 13 of GraphRAG's non-refusals cite entity ids (vector RAG: 11 of 12).
4. **The fair baseline.** Designing entity cards with relationships was the single biggest
   decision for the comparison. Without it, vector RAG would have failed even Q1 and Q2,
   and the conclusion would have been "the graph has more data", not "graph retrieval is
   better for these question shapes".

## Limitations

- **Small graph** (44 entities). At this size a full graph fits in a prompt; the benefit of
  structured retrieval grows with graph size, but so does Text2Cypher's difficulty.
- **One run** at temperature 0. LLM outputs still vary slightly between runs; Q9-type
  generation slips may come and go.
- **We wrote the questions.** They deliberately cover graph-shaped questions (aggregation,
  hops). A question mix drawn from real users would likely contain more lookups, where the
  two systems tie.
- **Simple baseline.** Vector RAG used dense top-4 only — no hybrid BM25, reranking or
  query decomposition. Query decomposition in particular could recover some two-hop
  questions (Q14) at extra latency.
- **LLM judge.** One clear judge error in 28; scores are best read alongside the
  per-question human review above.

## Recommendation

Use **GraphRAG with a router**, as built: structured questions (who/which/how many,
relationships, "the last X") go to Text2Cypher, and text questions go to vector-anchored
retrieval. GraphRAG answers every question shape correctly, costs 2.5× the latency, and
has evidence-backed refusals. To close the latency gap:

1. Skip the grader when the Cypher rows already contain every entity type the question asks
   for (saves ~1 s on most graph questions).
2. Run the router and the embedding call in parallel.
3. Cache Text2Cypher output for repeated question shapes.
