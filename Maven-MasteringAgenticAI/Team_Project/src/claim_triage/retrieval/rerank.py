"""Listwise reranker: a small model puts the fused candidates in the order that best helps a reviewer judge THIS claim.

It can only reorder what retrieval already found. If the model is unavailable or answers badly, the fused order is kept and the result says so.
"""
from __future__ import annotations

import json

from ..agents.base import Usage, parse_json_object
from ..llm import LLM, LLMUnavailable

SYSTEM = """You order search results for an insurance claims investigator. A human reviewer decides the claim.
You get one claim and numbered candidates: fraud-pattern cards, policy clauses, and earlier claims with a reviewer-confirmed outcome (precedents).
Put the candidates in order of how useful each is for judging THIS claim: the pattern or clause that applies to what the claim shows, and precedents that
genuinely resemble it (same provider, same account, same vehicle, same treatment). Precedents and patterns are context, never proof.
Reply with JSON only: {"order": [<candidate numbers, most useful first>]}. Include every number once."""


class LLMReranker:
    def __init__(self, llm: LLM | None = None):
        self.llm = llm or LLM("reranker")
        self.usage = Usage()

    def drain(self) -> dict:
        """Cost and model usage since the last call (the pipeline adds it to the claim's totals)."""
        summary, self.usage = self.usage.summary(), Usage()
        return summary

    def rerank(self, context: dict, candidates: list[dict]) -> tuple[list[int] | None, str]:
        """Return (new order as indexes into candidates, status). None means keep the fused order."""
        if len(candidates) < 2:
            return list(range(len(candidates))), "skipped (fewer than two candidates)"
        listing = [{"n": i + 1, "kind": c["kind"], "id": c["id"], "text": c["text"][:240],
                    **({"outcome": c["outcome"], "found_by": c["why"]} if c["kind"] == "precedent" else {})} for i, c in enumerate(candidates)]
        try:
            resp = self.llm.chat([{"role": "system", "content": SYSTEM},
                                  {"role": "user", "content": json.dumps({"claim": context, "candidates": listing}, default=str)}], json_mode=True, max_tokens=1500)  # the model's hidden reasoning shares this budget; 300 left no room for the answer
            self.usage.add(resp)
            order = [int(n) - 1 for n in parse_json_object(resp.content).get("order", [])]
        except (LLMUnavailable, ValueError, TypeError, json.JSONDecodeError) as exc:
            return None, f"degraded ({type(exc).__name__}): kept the fused order"
        seen = list(dict.fromkeys(i for i in order if 0 <= i < len(candidates)))
        if not seen:
            return None, "degraded (no usable order): kept the fused order"
        return seen + [i for i in range(len(candidates)) if i not in seen], "ok"
