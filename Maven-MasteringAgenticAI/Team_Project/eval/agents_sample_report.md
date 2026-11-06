# Agentic pipeline (routed): evaluation report

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
| cases | 30 |  |
| band_accuracy | 0.667 |  |
| fraud_in_green | 0.0 | <= 0.05 |
| fraud_red_recall | 0.1 |  |
| legit_in_red | 0.0 | <= 0.1 |
| legit_as_expected | 0.9 |  |
| amber_rate | 0.633 | <= 0.4 |
| missing_never_green | 1.0 | >= 1.0 |
| missing_as_expected | 1.0 |  |
| indicator_recall | 1.0 |  |
| false_indicator_rate | 0.0 |  |
| missing_item_recall | 1.0 |  |
| evidence_coverage | 1.0 | >= 1.0 |
| latency_ms_median | 11892 |  |
| latency_ms_p95 | 18905 |  |
| unavailable | 0 |  |
| tokens_per_case | 21241 |  |
| cost_usd_total | 1.2171 |  |
| cost_usd_per_case | 0.04057 |  |
| tool_calls_mean | 3.1 |  |
| loop_guard_trips | 0 |  |
| required_proposed_recall | 1.0 |  |
| investigator_false_signal | 0 |  |
| verifier_removed_true_signal | 0 |  |
| verifier_removed_false_signal | 0 |  |
| explainer_fallbacks | 5 |  |
| agent_noticed_missing | 0.9 |  |

## dev

Targets: fraud_in_green: PASS (0.0 vs <= 0.05); legit_in_red: PASS (0.0 vs <= 0.1); amber_rate: MISS (0.633 vs <= 0.4); missing_never_green: PASS (1.0 vs >= 1.0); evidence_coverage: PASS (1.0 vs >= 1.0)

Confusion matrix (rows: expected, columns: predicted)

| expected \ predicted | red | amber | green |
|---|---|---|---|
| red | 1 | 9 | 0 |
| amber | 0 | 10 | 1 |
| green | 0 | 0 | 9 |

Per scenario

| Scenario | suspicious -> red | honest as expected | missing -> amber |
|---|---|---|---|
| H1 | 1/1 | 1/1 | 1/1 |
| H2 | 0/1 | 1/1 | 1/1 |
| M1 | 0/1 | 0/1 | 1/1 |
| M2 | 0/1 | 1/1 | 1/1 |
| T1 | 0/1 | 1/1 | 1/1 |
| T2 | 0/1 | 1/1 | 1/1 |
| X1 | 0/1 | 1/1 | 1/1 |
| X2 | 0/1 | 1/1 | 1/1 |
| X3 | 0/1 | 1/1 | 1/1 |
| X4 | 0/1 | 1/1 | 1/1 |

Fairness check on honest cases (share landing red / amber)

| Slice | n | red | amber |
|---|---|---|---|
| gender=F | 8 | 0.0 | 0.0 |
| gender=M | 2 | 0.0 | 0.0 |
| area_flagged=False | 9 | 0.0 | 0.0 |
| area_flagged=True | 1 | 0.0 | 0.0 |

Cases flagged only because of area history: 2
