"""The agentic pipeline: Investigator -> evidence check (code) -> Verifier -> scorer (code) -> Explainer.

    uv run python -m claim_triage.pipeline CLM-001653

The scorer is the same plain arithmetic as the rules-only baseline. Which documents a claim REQUIRES is policy and is enforced in code;
the Investigator's own remarks about missing documents are recorded and measured, but they do not change the evidence gate.
"""
from __future__ import annotations

import copy
import sys

from . import tracing
from .agents.explainer import Explainer
from .agents.investigator import Investigator
from .agents.verifier import Verifier, evidence_check
from .contracts import FinalResult
from .llm import LLM, load_models
from .retrieval import LLMReranker
from .scoring.rules import Ctx, missing_documents
from .scoring.scorer import load_weights, score, thresholds
from .tools import ToolBox

PLANS = ("routed", "all_haiku", "all_sonnet")


def plan_models(plan: str, models: dict | None = None) -> dict:
    """routed = the config as written; all_haiku / all_sonnet force one Claude model for the Investigator (to measure what the stronger model buys)."""
    models = copy.deepcopy(models or load_models())
    inv = models["agents"]["investigator"]
    if plan == "all_haiku":
        inv["cross_policy"] = inv["default"]
    elif plan == "all_sonnet":
        inv["default"] = inv["cross_policy"]
    elif plan != "routed":
        raise ValueError(f"unknown plan {plan!r}; choose from {PLANS}")
    return models


RETRIEVAL = {"none": None, "bm25": "bm25", "graph": "graph", "hybrid": "hybrid", "hybrid_rerank": "hybrid"}  # option -> retrieval arms switched on


class AgentPipeline:
    def __init__(self, box: ToolBox, W: dict | None = None, plan: str = "routed", models: dict | None = None, retrieval: str = "none"):
        if retrieval not in RETRIEVAL:
            raise ValueError(f"unknown retrieval option {retrieval!r}; choose from {list(RETRIEVAL)}")
        self.box, self.W, self.retrieval = box, W or load_weights(), retrieval
        models = plan_models(plan, models)
        self.reranker = LLMReranker(LLM("reranker", models)) if retrieval == "hybrid_rerank" else None
        box.configure_retrieval(RETRIEVAL[retrieval], self.reranker)  # "none" removes the knowledge tool: the agent cannot search at all
        self.investigator = Investigator(box, LLM("investigator", models))
        self.verifier = Verifier(box, LLM("verifier", models))
        self.explainer = Explainer(LLM("explainer", models))

    def assess(self, claim_id: str) -> FinalResult:
        with tracing.claim_trace(claim_id):  # a no-op unless Langfuse tracing is on
            return self._assess(claim_id)

    def _assess(self, claim_id: str) -> FinalResult:
        box, W = self.box, self.W
        ctx = Ctx(box, claim_id, thresholds(W))  # the claim and its fact sheet (also used to check the agents' claims in code)
        inv = self.investigator.run(claim_id)
        found = inv["result"]
        kept, agent_extra_missing, dropped = evidence_check(box, found.indicators, set(inv["meta"]["allowed"]), ctx.f, ctx.docs, found.missing_evidence)
        verified, reviews, vmeta = self.verifier.review(ctx.claim, ctx.f, kept)
        missing = missing_documents(ctx)  # required documents are policy: enforced in code
        final = score(claim_id, ctx.claim["case_id"], verified, missing, W)
        reasons, emeta = self.explainer.explain(final)
        final.reasons = reasons
        imeta = inv["meta"]
        rmeta = self.reranker.drain() if self.reranker else None  # the reranker runs inside the Investigator's search calls
        usage = [imeta, vmeta, emeta] + ([rmeta] if rmeta else [])
        final.tokens = sum(u.get("tokens", 0) for u in usage)
        final.cost_usd = round(sum(u.get("cost_usd") or 0 for u in usage), 5) if all(u.get("cost_usd") is not None or u.get("calls", 0) == 0 for u in usage) else None
        final.models_used = {"investigator": ", ".join(imeta["models"]), "verifier": ", ".join(vmeta.get("models", [])), "explainer": ", ".join(emeta.get("models", []))}
        if rmeta and rmeta.get("models"):
            final.models_used["reranker"] = ", ".join(rmeta["models"])
        searches = [c for c in box.calls if c["tool"] == "search_knowledge" and c["args"].get("claim_id") == claim_id]
        final.trace = dict(retrieval=dict(option=self.retrieval, searches=len(searches), queries=[c["args"].get("query") for c in searches], reranker=rmeta),
                           investigator=imeta, summary=found.summary, proposed=[i.code for i in found.indicators], dropped_by_code_check=dropped,
                           verifier=dict(vmeta, reviews=reviews), explainer=emeta, agent_reported_missing=[m.document for m in agent_extra_missing],
                           latency_ms=sum(u.get("latency_ms", 0) for u in usage))
        return final


def main() -> None:
    claim_id = sys.argv[1]
    box = ToolBox()
    r = AgentPipeline(box).assess(claim_id)
    tracing.flush()
    print(f"{claim_id}: trust {r.trust_score:.2f} -> {r.band_label} ({r.evidence_status} evidence)  models: {r.models_used}  tokens: {r.tokens}  cost: {r.cost_usd}")
    for line in r.reasons:
        print("  -", line)
    for line in r.outstanding_checks:
        print("  todo:", line)
    print("  next:", r.next_action)
    print("  tools used:", r.trace["investigator"]["tools"])
    box.close()


if __name__ == "__main__":
    main()
