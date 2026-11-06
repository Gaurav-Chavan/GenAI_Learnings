# Evaluation results

One pair of files per run: `<name>_cases.csv` (one row per case: expected band, predicted band, trust, cost, time) and `<name>_report.md` (the summary).
The Evaluation page of the dashboard reads the `*_cases.csv` files in this folder (not the subfolder). Never edit them by hand; re-run the harness instead.

| Name | What it is |
|---|---|
| `baseline`, `baseline_hard` | rules only (no AI), original 300 cases and the 80 hard cases, dev and held-out |
| `agents_sample`, `agents_sample2`, `agents_sample3` | the first 30-case agent sample and the two fixes to the second-opinion agent. We tuned on these, so they are a history, not evidence |
| `haiku_none_*`, `haiku_rerank_*` | the full small-model agent runs: `none` = no retrieval, `rerank` = with retrieval; `_std` = original cases, `_hard` = hard cases; `_heldout` suffix = held-out split, otherwise dev |
| `routed_rerank_x12` | the larger model (routed plan) with retrieval on scenarios X1 and X2, dev |
| `archive/` | superseded trial runs, kept for the record |

Reproduce a run with `uv run python -m claim_triage.evaluation.harness --help`. Agent runs cost money; the baseline is free.
