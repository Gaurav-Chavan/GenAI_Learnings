# Evaluation summary

Run 2026-10-31 00:56 · answer model `gpt-4o-mini` · Cypher model `gpt-4o` · judge `gpt-4o` · 14 questions

## Overall

| Metric | GraphRAG | Vector RAG |
|---|---|---|
| Correctness (avg) | 0.96 | 0.71 |
| Fully correct | 13/14 | 7/14 |
| Faithfulness (avg) | 1 | 1 |
| Entity recall (avg) | 1.00 | 0.76 |
| Refusal behaviour OK | 14/14 | 13/14 |
| Distractor mentions | 0 | 0 |
| Non-refusals with citations | 13/13 | 11/12 |
| Latency median (s) | 5.45 | 2.19 |
| Latency p95 (s) | 5.91 | 3.85 |
| Tokens per question (avg) | 2548.29 | 588.21 |

## By category (avg correctness)

| Category | Questions | GraphRAG | Vector RAG |
|---|---|---|---|
| Multi-hop + temporal | Q1 | 1 | 1 |
| Multi-hop | Q2, Q6, Q14 | 1 | 0.67 |
| Aggregation | Q3, Q4 | 1 | 0.25 |
| Provenance | Q5 | 1 | 1 |
| Path | Q7 | 1 | 1 |
| Filter + hop | Q8 | 1 | 0.50 |
| Skill + hop | Q9 | 0.50 | 0.50 |
| Semantic | Q10 | 1 | 0.50 |
| Detail in text | Q11 | 1 | 1 |
| Unanswerable | Q12 | 1 | 1 |
| Temporal + why | Q13 | 1 | 1 |

## Per question

| Q | Category | GraphRAG correct / faithful / recall | Vector correct / faithful / recall | Winner | GraphRAG path |
|---|---|---|---|---|---|
| Q1 | Multi-hop + temporal | 1 / 1 / 1.00 | 1 / 1 / 1.00 | Tie | router:graph -> write_cypher -> run_cypher:1 rows -> grade:sufficient -> answer |
| Q2 | Multi-hop | 1 / 1 / 1.00 | 1 / 1 / 1.00 | Tie | router:graph -> write_cypher -> run_cypher:1 rows -> grade:sufficient -> answer |
| Q3 | Aggregation | 1 / 1 / 1.00 | 0.50 / 1 / 0.50 | GraphRAG | router:graph -> write_cypher -> run_cypher:4 rows -> grade:sufficient -> answer |
| Q4 | Aggregation | 1 / 1 / 1.00 | 0 / – / 0.00 | GraphRAG | router:graph -> write_cypher -> run_cypher:2 rows -> grade:sufficient -> answer |
| Q5 | Provenance | 1 / 1 / 1.00 | 1 / 1 / 1.00 | Tie | router:graph -> write_cypher -> run_cypher:0 rows -> anchor:4 seeds -> answer |
| Q6 | Multi-hop | 1 / 1 / 1.00 | 0.50 / 1 / 0.67 | GraphRAG | router:graph -> write_cypher -> run_cypher:1 rows -> grade:sufficient -> answer |
| Q7 | Path | 1 / 1 / 1.00 | 1 / 1 / 1.00 | Tie | router:graph -> write_cypher -> run_cypher:1 rows -> grade:sufficient -> answer |
| Q8 | Filter + hop | 1 / 1 / 1.00 | 0.50 / 1 / 0.50 | GraphRAG | router:graph -> write_cypher -> run_cypher:2 rows -> grade:sufficient -> answer |
| Q9 | Skill + hop | 0.50 / 1 / 1.00 | 0.50 / 1 / 1.00 | Tie | router:graph -> write_cypher -> run_cypher:3 rows -> grade:sufficient -> answer |
| Q10 | Semantic | 1 / 1 / 1.00 | 0.50 / 1 / 1.00 | GraphRAG | router:semantic -> anchor:5 seeds -> answer |
| Q11 | Detail in text | 1 / 1 / 1.00 | 1 / 1 / 1.00 | Tie | router:semantic -> anchor:4 seeds -> answer |
| Q12 | Unanswerable | 1 / – / – | 1 / – / – | Tie | router:graph -> write_cypher -> run_cypher:0 rows -> anchor:4 seeds -> answer |
| Q13 | Temporal + why | 1 / 1 / 1.00 | 1 / 1 / 1.00 | Tie | router:semantic -> anchor:4 seeds -> answer |
| Q14 | Multi-hop | 1 / 1 / 1.00 | 0.50 / 1 / 0.25 | GraphRAG | router:graph -> write_cypher -> run_cypher:1 rows -> grade:sufficient -> answer |

**Wins:** GraphRAG 6 · Vector RAG 0 · Tie 8

## Judge notes on answers that were not fully correct

- **Q3 · Vector RAG (0.50)**: The assistant correctly identifies Frank Lee and Bob Martinez but omits Alice Chen and David Kim.
- **Q4 · Vector RAG (0)**: The assistant incorrectly claims the information is unavailable, while the reference provides specific tools used across projects.
- **Q6 · Vector RAG (0.50)**: The assistant correctly identifies the decisions but omits the projects for two of them.
- **Q8 · Vector RAG (0.50)**: The assistant correctly identifies the projects Bob is on but incorrectly names the leader of the Zero-Trust Security Rollout.
- **Q9 · GraphRAG (0.50)**: The assistant omitted Frank Lee's involvement in the Zero-Trust Security Rollout.
- **Q9 · Vector RAG (0.50)**: The assistant correctly identifies Alice Chen, Frank Lee, and Henry Okafor with their SOC2 expertise and projects, but omits Frank Lee's Zero-Trust Security Rollout project and adds an irrelevant person, Bob Martinez.
- **Q10 · Vector RAG (0.50)**: The assistant's answer includes all key facts from the reference but adds an incorrect date for the MFA mandate.
- **Q14 · Vector RAG (0.50)**: The assistant correctly identifies who Bob reports to but fails to list the decisions approved by Grace Patel.
