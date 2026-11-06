"""The rules-only baseline: tools -> deterministic rules -> evidence check -> scorer. No LLM.

    uv run python -m claim_triage.baseline CLM-001653
"""
from __future__ import annotations

import sys

from .contracts import FinalResult
from .scoring.rules import RULES, Ctx, missing_documents
from .scoring.scorer import load_weights, score, thresholds
from .tools import ToolBox


def assess_claim(box: ToolBox, claim_id: str, W: dict | None = None) -> FinalResult:
    W = W or load_weights()
    ctx = Ctx(box, claim_id, thresholds(W))
    fired = [ind for _, rule in RULES if (ind := rule(ctx))]
    dropped = []
    for ind in fired:  # keep only evidence IDs that a tool really returned in this assessment
        ind.evidence_ids = [e for e in dict.fromkeys(ind.evidence_ids) if e in box.returned_ids]
        if not ind.evidence_ids:
            dropped.append(ind.code)
    result = score(claim_id, ctx.claim["case_id"], fired, missing_documents(ctx), W, dropped)
    if result.band == "green":
        result.reasons[0] += " Checks run: " + ", ".join(name for name, _ in RULES) + "."
    return result


def main() -> None:
    claim_id = sys.argv[1]
    box = ToolBox(graph=False)
    r = assess_claim(box, claim_id)
    print(f"{claim_id}: trust {r.trust_score:.2f} -> {r.band_label} ({r.evidence_status} evidence)")
    for line in r.reasons:
        print("  -", line)
    for line in r.outstanding_checks:
        print("  todo:", line)
    if r.counterfactual:
        print("  ", r.counterfactual)
    print("  next:", r.next_action)
    box.close()


if __name__ == "__main__":
    main()
