# Rules-only baseline: evaluation report

Expected band: red for suspicious cases, review needed (amber) for missing-evidence cases, green for honest lookalikes, except that an honest claim from a high-fraud area is expected to be review needed (the area flag is policy). Weights are tuned on the dev split only; the held-out numbers are the ones to quote.

How this baseline was built (read before trusting the numbers):

- It uses no LLM: tools -> deterministic rules -> scorer. It reads only the tools' output, never the answer keys.
- The first dev run exposed that five 'missing evidence' scenarios also carried a strong fraud signal, so labelling them amber was inconsistent. The data generator was changed so that missing-evidence cases are genuinely ambiguous; this changed all splits equally.
- Two rule changes came from dev misses: an accident documented by a police report weakens the early-death signal, and the claimed damage is compared with every earlier inspection, not only the latest.
- Several fixes came from data-consistency checks (impossible dates, deaths followed by activity), not from scores.
- The remaining misses (a moderately inflated bill scored amber, and a weak ring plus an area flag scored red) were left untouched on purpose: they are real limits of fixed rules.
- The scenarios are still fairly clean, so there is little headroom: a score near 100% shows the rules fit this synthetic data, not that fraud detection is solved. Harder, ambiguous cases are needed before the agents can show added value.

| Metric | dev | heldout | Target |
|---|---|---|---|
| cases | 52 | 28 |  |
| band_accuracy | 0.5 | 0.5 |  |
| fraud_in_green | 0.5 | 0.5 | <= 0.05 |
| fraud_red_recall | 1.0 | 1.0 |  |
| fraud_flagged_recall | 0.5 | 0.5 |  |
| legit_in_red | 0.5 | 0.5 | <= 0.1 |
| legit_as_expected | 0.5 | 0.5 |  |
| amber_rate | 0.0 | 0.0 | <= 0.4 |
| missing_never_green | None | None | >= 1.0 |
| missing_as_expected | None | None |  |
| indicator_recall | 0.5 | 0.5 |  |
| false_indicator_rate | 0.0 | 0.0 |  |
| missing_item_recall | None | None |  |
| evidence_coverage | 1.0 | 1.0 | >= 1.0 |
| latency_ms_median | 9 | 9 |  |
| latency_ms_p95 | 15 | 16 |  |

## dev

Targets: fraud_in_green: MISS (0.5 vs <= 0.05); legit_in_red: MISS (0.5 vs <= 0.1); amber_rate: PASS (0.0 vs <= 0.4); missing_never_green: MISS (None vs >= 1.0); evidence_coverage: PASS (1.0 vs >= 1.0)

Confusion matrix (rows: expected, columns: predicted)

| expected \ predicted | red | amber | green |
|---|---|---|---|
| red | 13 | 0 | 0 |
| amber | 3 | 0 | 13 |
| green | 10 | 0 | 13 |

Per scenario

| Scenario | suspicious as expected | honest as expected | missing -> amber |
|---|---|---|---|
| D1 | 13/13 | 0/13 | 0/0 |
| D2 | 0/13 | 13/13 | 0/0 |

Fairness check on honest cases (share landing red / amber)

| Slice | n | red | amber |
|---|---|---|---|
| gender=F | 11 | 0.545 | 0.0 |
| gender=M | 14 | 0.429 | 0.0 |
| gender=withheld | 1 | 1.0 | 0.0 |
| area_flagged=False | 23 | 0.435 | 0.0 |
| area_flagged=True | 3 | 1.0 | 0.0 |

Cases flagged only because of area history: 0

## heldout

Targets: fraud_in_green: MISS (0.5 vs <= 0.05); legit_in_red: MISS (0.5 vs <= 0.1); amber_rate: PASS (0.0 vs <= 0.4); missing_never_green: MISS (None vs >= 1.0); evidence_coverage: PASS (1.0 vs >= 1.0)

Confusion matrix (rows: expected, columns: predicted)

| expected \ predicted | red | amber | green |
|---|---|---|---|
| red | 7 | 0 | 0 |
| amber | 2 | 0 | 7 |
| green | 5 | 0 | 7 |

Per scenario

| Scenario | suspicious as expected | honest as expected | missing -> amber |
|---|---|---|---|
| D1 | 7/7 | 0/7 | 0/0 |
| D2 | 0/7 | 7/7 | 0/0 |

Fairness check on honest cases (share landing red / amber)

| Slice | n | red | amber |
|---|---|---|---|
| gender=F | 6 | 0.667 | 0.0 |
| gender=M | 7 | 0.429 | 0.0 |
| gender=withheld | 1 | 0.0 | 0.0 |
| area_flagged=False | 12 | 0.417 | 0.0 |
| area_flagged=True | 2 | 1.0 | 0.0 |

Cases flagged only because of area history: 0
