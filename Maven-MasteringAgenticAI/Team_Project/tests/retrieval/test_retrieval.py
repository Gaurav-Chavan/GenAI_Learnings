"""Retrieval tests. Fusion, reranking and the evidence rules run offline with fakes; the point-in-time checks run on the live graph (skipped if unreachable)."""
from __future__ import annotations

import json
from datetime import date

import duckdb
import pytest

from claim_triage.data.config import paths
from claim_triage.llm import LLMResult
from claim_triage.retrieval import HybridRetriever, LLMReranker, rrf
from claim_triage.tools import ToolBox
from claim_triage.tools import toolbox as tb


# ---------- fusion ----------

def test_rrf_lifts_items_that_two_arms_agree_on():
    fused = rrf({"keyword": ["a", "b", "c"], "graph": ["c", "d"]})
    assert [k for k, _, _ in fused][0] == "c"  # first in neither list, but in both
    assert dict((k, arms) for k, _, arms in fused)["c"] == ["keyword", "graph"]


def test_rrf_ignores_repeats_and_handles_empty_lists():
    assert rrf({}) == []
    fused = rrf({"keyword": ["a", "a", "b"]})
    assert [k for k, _, _ in fused] == ["a", "b"]


# ---------- retriever with a fake box ----------

class FakeBox:
    silver = None
    _result = ToolBox._result

    def __init__(self, knowledge=(), text=(), graph=(), history=()):
        self.rows = {id(tb.CYPHER_KNOWLEDGE): list(knowledge), id(tb.CYPHER_PRECEDENT_TEXT): list(text), id(tb.CYPHER_PRECEDENT_GRAPH): list(graph),
                     id(tb.CYPHER_PROVIDER_HISTORY): list(history)}
        self.asked = []

    def _rows(self, con, sql, *params):
        return [dict(claim_id=params[0], line="health", filed_date=date(2026, 7, 1), procedure_code="P04", description="Knee surgery")]

    def _read_graph(self, cypher, **params):
        self.asked.append((cypher, params))
        return self.rows[id(cypher)]


def know(kind, ident, line=None):
    return dict(kind=kind, id=ident, title=ident, text=f"text of {ident}", line=line, score=1.0)


def prec(ident, outcome="cleared", reasons=None):
    return dict(id=ident, line="health", text=f"claim {ident}", outcome=outcome, decided_date=date(2026, 1, 1), reasons=reasons or [])


def test_patterns_and_clauses_are_context_only_precedents_are_evidence():
    box = FakeBox(knowledge=[know("pattern", "BILL_OUTLIER_VS_PEERS"), know("clause", "CL-HEALTH-04", "health")], graph=[prec("CLM-000010", "confirmed_fraud", ["same provider"])])
    out = HybridRetriever(box).search("CLM-000001", "bill too high", 5)
    assert {r["id"] for r in out["records"]} == {"BILL_OUTLIER_VS_PEERS", "CL-HEALTH-04", "CLM-000010"}
    assert out["evidence_ids"] == ["CLM-000010"]


def test_provider_track_record_comes_first_and_its_provider_id_is_evidence():
    box = FakeBox(knowledge=[know("pattern", "BILL_OUTLIER_VS_PEERS")], history=[dict(provider_id="PRV-007", name="X Hospital", decided=9, confirmed_fraud=5)])
    out = HybridRetriever(box).search("CLM-000001", "bill", 5)
    first = out["records"][0]
    assert first["kind"] == "history" and first["confirmed_fraud"] == 5 and "5 of 9" in first["text"]
    assert out["evidence_ids"] == ["PRV-007"]


def test_no_track_record_when_the_provider_has_no_decided_claims_or_in_keyword_only_mode():
    quiet = FakeBox(history=[dict(provider_id="PRV-007", name="X", decided=0, confirmed_fraud=0)])
    assert not [r for r in HybridRetriever(quiet).search("CLM-000001", "bill", 5)["records"] if r["kind"] == "history"]
    busy = FakeBox(history=[dict(provider_id="PRV-007", name="X", decided=9, confirmed_fraud=5)])
    assert not [r for r in HybridRetriever(busy, mode="bm25").search("CLM-000001", "bill", 5)["records"] if r["kind"] == "history"]


def test_clauses_for_other_lines_are_left_out():
    box = FakeBox(knowledge=[know("clause", "CL-MOTOR-01", "motor"), know("clause", "CL-ALL-02", "all"), know("clause", "CL-HEALTH-04", "health")])
    ids = {r["id"] for r in HybridRetriever(box).search("CLM-000001", "damage", 5)["records"]}
    assert ids == {"CL-ALL-02", "CL-HEALTH-04"}


def test_precedent_found_by_both_keyword_and_graph_ranks_first_and_remembers_why():
    box = FakeBox(text=[prec("CLM-000020"), prec("CLM-000021")], graph=[prec("CLM-000021", "confirmed_fraud", ["paid to the same account as another customer"])])
    out = HybridRetriever(box).search("CLM-000001", "knee", 5)
    first = out["records"][0]
    assert first["id"] == "CLM-000021" and set(first["arms"]) == {"keyword:precedents", "graph:precedents"}
    assert first["why"] == ["paid to the same account as another customer"]


def test_modes_switch_arms_on_and_off():
    full = dict(knowledge=[know("pattern", "P")], text=[prec("CLM-000020")], graph=[prec("CLM-000021")])
    bm25 = HybridRetriever(FakeBox(**full), mode="bm25").search("CLM-000001", "knee", 5)
    graph = HybridRetriever(FakeBox(**full), mode="graph").search("CLM-000001", "knee", 5)
    assert set(bm25["retrieval"]["arm_sizes"]) == {"keyword:knowledge", "keyword:precedents"}
    assert set(graph["retrieval"]["arm_sizes"]) == {"graph:precedents"} and [r["id"] for r in graph["records"]] == ["CLM-000021"]
    with pytest.raises(ValueError):
        HybridRetriever(FakeBox(), mode="dense")


def test_queries_are_cleaned_and_searches_use_the_claims_filing_date():
    box = FakeBox()
    HybridRetriever(box).search("CLM-000001", "nominee AND (changed) OR *", 5)
    cypher, params = box.asked[0]
    assert params["q"] == "nominee AND changed OR" and params["as_of"] == date(2026, 7, 1)  # no Lucene operators; the agent cannot choose the date
    assert all("as_of" in p and p["as_of"] == date(2026, 7, 1) for _, p in box.asked)


# ---------- reranker ----------

class FakeLLM:
    def __init__(self, content=None, error=None):
        self.content, self.error = content, error

    def chat(self, messages, variant="default", tools=None, json_mode=False, max_tokens=None):
        if self.error:
            raise self.error
        return LLMResult(content=self.content, tool_calls=[], model="fake/model", fell_back=False, latency_ms=1, input_tokens=100, output_tokens=10, cost_usd=0.001)


CANDS = [dict(kind="pattern", id="A", text="a"), dict(kind="clause", id="B", text="b"), dict(kind="precedent", id="CLM-000010", text="c", outcome="cleared", why=[])]


def test_reranker_reorders_and_completes_a_partial_order():
    rr = LLMReranker(FakeLLM(json.dumps({"order": [3, 1]})))
    order, status = rr.rerank({}, CANDS)
    assert order == [2, 0, 1] and status == "ok"  # candidate 2 (B) was left out by the model, so it goes last
    assert rr.drain()["calls"] == 1 and rr.drain()["calls"] == 0  # usage is reported once, then reset


def test_reranker_ignores_invented_numbers():
    order, status = LLMReranker(FakeLLM(json.dumps({"order": [9, 2, 2, 0]}))).rerank({}, CANDS)
    assert order == [1, 0, 2] and status == "ok"


@pytest.mark.parametrize("llm", [FakeLLM(None), FakeLLM("no json"), FakeLLM(json.dumps({"order": []})), FakeLLM(error=ValueError("boom"))])
def test_reranker_failure_keeps_the_fused_order_and_says_so(llm):
    try:
        order, status = LLMReranker(llm).rerank({}, CANDS)
    except ValueError:
        pytest.fail("a reranker failure must not escape")
    assert order is None and status.startswith("degraded")


def test_retriever_keeps_fused_order_when_the_reranker_degrades():
    box = FakeBox(knowledge=[know("pattern", "P1"), know("pattern", "P2")])
    out = HybridRetriever(box, LLMReranker(FakeLLM("no json"))).search("CLM-000001", "x", 5)
    assert [r["id"] for r in out["records"]] == ["P1", "P2"] and out["retrieval"]["rerank"].startswith("degraded")


# ---------- the tool inside the toolbox ----------

@pytest.fixture(scope="module")
def cfg(built_dataset):
    return built_dataset[0]


def test_no_retrieval_option_removes_the_tool(cfg):
    box = ToolBox(cfg, graph=False)
    assert "search_knowledge" in box.tools
    box.configure_retrieval(None)
    assert "search_knowledge" not in box.tools and all(s["function"]["name"] != "search_knowledge" for s in box.specs())
    box.close()


@pytest.fixture(scope="module")
def live(cfg):
    b = ToolBox(cfg, graph=True)
    try:
        b.driver()
        if not b._read_graph("MATCH (n:Pattern) RETURN count(n) AS n")[0]["n"]:
            pytest.skip("knowledge nodes are not loaded in the graph (run load_neo4j --knowledge-only)")
    except Exception as exc:  # noqa: BLE001
        b.close()
        pytest.skip(f"Neo4j not reachable: {type(exc).__name__}")
    yield b
    b.close()


def test_live_precedents_are_decided_before_the_claim_was_filed_and_never_unlabelled(live, cfg):
    gold = duckdb.connect(str(paths(cfg)["gold"]), read_only=True)
    claims = [r[0] for r in gold.execute("select claim_id from claim_features order by claim_id limit 40").fetchall()]
    gold.close()
    checked = 0
    for mode in ("hybrid", "bm25", "graph"):
        live.configure_retrieval(mode)
        for cid in claims[::8]:
            filed = live._rows(live.silver, "select filed_date from claims where claim_id = ?", cid)[0]["filed_date"].isoformat()
            for rec in live.call("search_knowledge", claim_id=cid, query="hospital bill nominee damage accident", limit=10)["records"]:
                if rec["kind"] == "precedent":
                    checked += 1
                    assert rec["outcome"] in ("cleared", "confirmed_fraud") and rec["decided_date"] <= filed and rec["id"] != cid
    assert checked > 0


def test_live_search_returns_the_matching_pattern(live):
    live.configure_retrieval("hybrid")
    gold = duckdb.connect(str(paths(live.cfg)["gold"]), read_only=True)
    term = gold.execute("select claim_id from claim_features where line = 'term' limit 1").fetchone()[0]
    gold.close()
    out = live.call("search_knowledge", claim_id=term, query="nominee changed shortly before death", limit=5)
    assert "NOMINEE_CHANGE_NEAR_EVENT" in [r["id"] for r in out["records"]]
    assert not set(out["evidence_ids"]) & {r["id"] for r in out["records"] if r["kind"] in ("pattern", "clause", "signal")}  # context, never evidence
