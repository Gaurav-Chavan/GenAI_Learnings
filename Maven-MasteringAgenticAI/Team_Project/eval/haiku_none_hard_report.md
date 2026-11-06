# Agentic pipeline (all_haiku, retrieval: none): evaluation report

Expected band: red for suspicious cases, review needed (amber) for missing-evidence cases, green for honest lookalikes, except that an honest claim from a high-fraud area is expected to be review needed (the area flag is policy). Weights are tuned on the dev split only; the held-out numbers are the ones to quote.

How this baseline was built (read before trusting the numbers):

- It uses no LLM: tools -> deterministic rules -> scorer. It reads only the tools' output, never the answer keys.
- The first dev run exposed that five 'missing evidence' scenarios also carried a strong fraud signal, so labelling them amber was inconsistent. The data generator was changed so that missing-evidence cases are genuinely ambiguous; this changed all splits equally.
- Two rule changes came from dev misses: an accident documented by a police report weakens the early-death signal, and the claimed damage is compared with every earlier inspection, not only the latest.
- Several fixes came from data-consistency checks (impossible dates, deaths followed by activity), not from scores.
- The remaining misses (a moderately inflated bill scored amber, and a weak ring plus an area flag scored red) were left untouched on purpose: they are real limits of fixed rules.
- The scenarios are still fairly clean, so there is little headroom: a score near 100% shows the rules fit this synthetic data, not that fraud detection is solved. Harder, ambiguous cases are needed before the agents can show added value.

| Metric | dev | Target |
|---|---|---|
| cases | 52 |  |
| band_accuracy | 0.712 |  |
| fraud_in_green | 0.5 | <= 0.05 |
| fraud_red_recall | 0.923 |  |
| fraud_flagged_recall | 0.5 |  |
| legit_in_red | 0.0 | <= 0.1 |
| legit_as_expected | 0.962 |  |
| amber_rate | 0.096 | <= 0.4 |
| missing_never_green | None | >= 1.0 |
| missing_as_expected | None |  |
| indicator_recall | 0.5 |  |
| false_indicator_rate | 0.0 |  |
| missing_item_recall | None |  |
| evidence_coverage | 1.0 | >= 1.0 |
| latency_ms_median | 13625 |  |
| latency_ms_p95 | 27004 |  |
| unavailable | 0 |  |
| tokens_per_case | 25622 |  |
| cost_usd_total | 0.171 |  |
| cost_usd_per_case | 0.00329 |  |
| tool_calls_mean | 4.69 |  |
| loop_guard_trips | 0 |  |
| required_proposed_recall | 0.5 |  |
| investigator_false_signal | 0 |  |
| verifier_removed_true_signal | 0 |  |
| verifier_removed_false_signal | 0 |  |
| explainer_fallbacks | 8 |  |
| agent_noticed_missing | None |  |

## dev

Targets: fraud_in_green: MISS (0.5 vs <= 0.05); legit_in_red: PASS (0.0 vs <= 0.1); amber_rate: PASS (0.096 vs <= 0.4); missing_never_green: MISS (None vs >= 1.0); evidence_coverage: PASS (1.0 vs >= 1.0)

Confusion matrix (rows: expected, columns: predicted)

| expected \ predicted | red | amber | green |
|---|---|---|---|
| red | 12 | 1 | 0 |
| amber | 0 | 3 | 13 |
| green | 0 | 1 | 22 |

Per scenario

| Scenario | suspicious as expected | honest as expected | missing -> amber |
|---|---|---|---|
| D1 | 12/13 | 12/13 | 0/0 |
| D2 | 0/13 | 13/13 | 0/0 |

Fairness check on honest cases (share landing red / amber)

| Slice | n | red | amber |
|---|---|---|---|
| gender=F | 11 | 0.0 | 0.091 |
| gender=M | 14 | 0.0 | 0.214 |
| gender=withheld | 1 | 0.0 | 0.0 |
| area_flagged=False | 23 | 0.0 | 0.043 |
| area_flagged=True | 3 | 0.0 | 1.0 |

Cases flagged only because of area history: 0
