"""Hybrid retrieval for the Investigator: what do our patterns, clauses and earlier reviewer decisions say about a claim like this one?

Three arms, all point in time (an earlier decision counts only if it was made before this claim was filed):
  * keyword (BM25) over pattern cards, policy clauses and approved signals;
  * keyword (BM25) over earlier decided claims of the same line (precedents);
  * graph: earlier decided claims linked to this one through a shared payout account, vehicle, provider and procedure, or provider.
The arms are merged by reciprocal rank fusion and, if a reranker is attached, the top candidates are reordered by a small model.
Patterns and clauses are context, never evidence: only precedent claim IDs (and the provider ID of a track-record summary) are returned as evidence IDs.
In graph and hybrid mode the result also starts with the provider's track record: how many of its claims decided in the last 24 months were confirmed as fraud.
"""
from __future__ import annotations

import re

from ..tools.toolbox import CYPHER_KNOWLEDGE, CYPHER_PRECEDENT_GRAPH, CYPHER_PRECEDENT_TEXT, CYPHER_PROVIDER_HISTORY, ToolError
from .fuse import rrf

MODES = ("hybrid", "bm25", "graph")
POOL = 10  # fused candidates handed to the reranker
ARM_LIMIT = 20


def _lucene_safe(query: str) -> str:
    return " ".join(re.sub(r"[^A-Za-z0-9\s-]", " ", query).split())


class HybridRetriever:
    def __init__(self, box, reranker=None, mode: str = "hybrid"):
        if mode not in MODES:
            raise ValueError(f"unknown retrieval mode {mode!r}; choose from {MODES}")
        self.box, self.reranker, self.mode = box, reranker, mode

    def _claim(self, claim_id: str) -> dict:
        rows = self.box._rows(self.box.silver, "select claim_id, line, filed_date, procedure_code, description from claims where claim_id = ?", claim_id)
        if not rows:
            raise ToolError(f"no claim {claim_id}")
        return rows[0]

    def search(self, claim_id: str, query: str, limit: int) -> dict:
        claim, q = self._claim(claim_id), _lucene_safe(query)
        if not q:
            raise ToolError("'query' must contain letters or digits")
        as_of, read = claim["filed_date"], self.box._read_graph
        cands: dict[str, dict] = {}
        arms: dict[str, list[str]] = {}

        def add(arm: str, rows: list[dict], kind: str) -> None:
            keys = []
            for r in rows:
                key = f"{kind}:{r['id']}"
                if kind == "precedent":
                    cands.setdefault(key, dict(kind="precedent", id=r["id"], title=f"Earlier {r['line']} claim", text=r["text"] or "", outcome=r["outcome"],
                                               decided_date=r["decided_date"], why=[]))
                    cands[key]["why"] = sorted(set(cands[key]["why"]) | set(r.get("reasons") or []))
                else:
                    cands.setdefault(key, dict(kind=r["kind"].lower(), id=r["id"], title=r["title"], text=r["text"], why=[]))
                keys.append(key)
            arms[arm] = keys

        if self.mode in ("hybrid", "bm25"):
            kb_as_of = getattr(self.box, "kb_as_of", as_of)
            rows = [r for r in read(CYPHER_KNOWLEDGE, q=q, as_of=as_of, kb_as_of=kb_as_of, limit=ARM_LIMIT) if r["line"] in (None, "all", claim["line"])]
            add("keyword:knowledge", rows, "knowledge")
            add("keyword:precedents", read(CYPHER_PRECEDENT_TEXT, q=q, as_of=as_of, line=claim["line"], cid=claim_id, limit=ARM_LIMIT), "precedent")
        if self.mode in ("hybrid", "graph"):
            add("graph:precedents", read(CYPHER_PRECEDENT_GRAPH, as_of=as_of, cid=claim_id, limit=ARM_LIMIT), "precedent")

        fused = rrf(arms)
        for key, score, found_by in fused:
            cands[key]["rrf"], cands[key]["arms"] = round(score, 4), found_by
        ordered = [cands[key] for key, _, _ in fused[:POOL]]

        status = "off"
        if self.reranker is not None and self.mode == "hybrid":
            context = {"claim_id": claim_id, "line": claim["line"], "procedure": claim["procedure_code"], "description": claim["description"], "query": q}
            order, status = self.reranker.rerank(context, ordered)
            if order is not None:
                ordered = [ordered[i] for i in order]
        records = [{**c, "text": c["text"][:400]} for c in ordered[:limit]]
        ids = [c["id"] for c in records if c["kind"] == "precedent"]  # patterns and clauses are context, not evidence
        if self.mode in ("hybrid", "graph"):  # the graph can also answer "what is this provider's record?", which no single precedent shows
            row = read(CYPHER_PROVIDER_HISTORY, cid=claim_id, as_of=as_of)
            if row and row[0]["decided"]:
                h = row[0]
                records.insert(0, dict(kind="history", id=h["provider_id"], title="Provider track record", confirmed_fraud=h["confirmed_fraud"], decided=h["decided"], why=[],
                                       text=f"{h['provider_id']}: {h['confirmed_fraud']} of {h['decided']} claims decided in the last 24 months were confirmed as fraud by a reviewer."))
                ids.append(h["provider_id"])
        out = self.box._result("search_knowledge", {"claim_id": claim_id, "query": q, "limit": limit}, records, ids)
        out["retrieval"] = dict(mode=self.mode, rerank=status, candidates=len(fused), as_of=as_of.isoformat(), arm_sizes={a: len(k) for a, k in arms.items()})
        return out
