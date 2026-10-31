"""GraphRAG: a LangGraph pipeline over the Neo4j org knowledge graph.

    uv run python -m orggraph_rag.graph_rag "Who approved the pricing change?"
    uv run python -m orggraph_rag.graph_rag --mermaid      # print the pipeline diagram

Flow
    router ──graph──────► write_cypher ─► run_cypher ──rows──► grade ──sufficient──► answer
       │                       ▲              │                  │
       │                       └──error (1 retry)                └──not sufficient──┐
       │                                      └──no rows / still failing──► anchor ─┤
       ├──semantic─────────────────────────────────────────────────────► anchor ───► answer
       └──out_of_scope──────────────────────────────────────────────────────────────► answer (refuse)

- write_cypher: Text2Cypher with the schema, the entity-name catalog and few-shot examples.
- run_cypher:   rejects write clauses, then runs in a READ transaction (Aura enforces it).
- grade:        LLM check that the rows answer every part of the question.
- anchor:       entity-anchored retrieval - full-text (exact names) + vector seeds, their
                1-hop neighbourhoods as triples, plus the closest document chunks.
- answer:       the same prompt/model/refusal as the vector baseline (generation.py).
"""

import argparse
import json
import re
import time
from functools import lru_cache
from typing import Any, Dict, List, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from neo4j import RoutingControl
from neo4j.exceptions import Neo4jError
from pydantic import BaseModel

from .config import get_chat_llm, get_driver, get_embeddings, settings
from .generation import RAGResult, Source, generate
from .ingest import CHUNK_VECTOR_INDEX, ENTITY_FULLTEXT_INDEX, ENTITY_VECTOR_INDEX
from .prompts import CYPHER_PROMPT, CYPHER_RETRY_FEEDBACK, GRADER_PROMPT, ROUTER_PROMPT

MAX_CYPHER_ATTEMPTS = 2
MAX_ROWS = 50
QUERY_RESULT_ID = "graph_query_result"
ENTITY_SEEDS, FULLTEXT_SEEDS, CHUNK_SEEDS = 4, 3, 2

_WRITE_CLAUSE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV|GRANT|DENY|REVOKE)\b|\b(apoc|dbms)\.",
    re.IGNORECASE,
)
_LUCENE_SPECIAL = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


# ---------------------------------------------------------------- helpers

def is_read_only(cypher: str) -> bool:
    return not _WRITE_CLAUSE.search(cypher)


def strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:cypher)?\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", text).strip()


def lucene_query(text: str) -> str:
    """Question -> safe full-text query (special chars and AND/OR/NOT operators removed)."""
    return " ".join(_LUCENE_SPECIAL.sub(" ", text).lower().split())


def collect_ids(value: Any, known: frozenset) -> List[str]:
    """Every known entity id found anywhere inside a (nested) query result value."""
    if isinstance(value, str):
        return [value] if value in known else []
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        return [i for v in value for i in collect_ids(v, known)]
    return []


def read_query(cypher: str, **params) -> List[Dict[str, Any]]:
    records = get_driver().execute_query(
        cypher, params, database_=settings.neo4j_database, routing_=RoutingControl.READ
    ).records
    return [r.data() for r in records]


@lru_cache
def entity_ids() -> frozenset:
    return frozenset(r["id"] for r in read_query("MATCH (n:Entity) RETURN n.id AS id"))


@lru_cache
def entity_catalog() -> str:
    rows = read_query("""
        MATCH (n:Entity)
        WITH [l IN labels(n) WHERE l <> 'Entity'][0] AS kind, n.name AS name ORDER BY name
        RETURN kind, collect(name) AS names ORDER BY kind
    """)
    values = read_query("""
        MATCH ()-[w:WORKED_ON]->() WITH collect(DISTINCT w.role) AS roles
        MATCH ()-[h:HAS_SKILL]->() RETURN roles, collect(DISTINCT h.level) AS levels
    """)[0]
    return "\n".join(
        [f"- {r['kind']}: {'; '.join(r['names'])}" for r in rows]
        + [f"- WORKED_ON.role values: {'; '.join(sorted(values['roles']))}",
           f"- HAS_SKILL.level values: {'; '.join(sorted(values['levels']))}"]
    )


def descriptions(ids: List[str]) -> List[Source]:
    rows = read_query("MATCH (n:Entity) WHERE n.id IN $ids RETURN n.id AS id, n.description AS text", ids=ids)
    by_id = {r["id"]: r["text"] for r in rows}
    return [Source(id=i, text=by_id[i]) for i in ids if i in by_id]


def merge_sources(*groups: List[Source]) -> List[Source]:
    seen, merged = set(), []
    for source in (s for g in groups for s in g):
        if source.id not in seen:
            seen.add(source.id)
            merged.append(source)
    return merged


def add_usage(state: "GraphState", message) -> Dict[str, int]:
    usage = dict(state.get("usage") or {"input_tokens": 0, "output_tokens": 0})
    meta = getattr(message, "usage_metadata", None) or {}
    usage["input_tokens"] += meta.get("input_tokens", 0)
    usage["output_tokens"] += meta.get("output_tokens", 0)
    return usage


def subgraph(sources: List[Source]) -> Dict[str, list]:
    """Nodes/edges behind the answer (for the UI graph view)."""
    known = entity_ids()
    ids = {s.id for s in sources if s.id in known}
    for s in sources:
        ids.update(i for i in re.findall(r"\[([a-z]+_[a-z0-9_]+)\]", s.text) if i in known)
    rows = read_query("""
        MATCH (n:Entity) WHERE n.id IN $ids
        WITH collect(n) AS ns
        UNWIND ns AS a
        OPTIONAL MATCH (a)-[r]->(b:Entity) WHERE b IN ns
        RETURN a.id AS id, a.name AS name, [l IN labels(a) WHERE l <> 'Entity'][0] AS kind,
               collect(CASE WHEN b IS NULL THEN null ELSE {target: b.id, type: type(r)} END) AS edges
    """, ids=sorted(ids))
    return {
        "nodes": [{"id": r["id"], "name": r["name"], "kind": r["kind"]} for r in rows],
        "edges": [{"source": r["id"], **e} for r in rows for e in r["edges"]],
    }


# ---------------------------------------------------------------- pipeline state + nodes

class GraphState(TypedDict, total=False):
    question: str
    started: float
    route: str
    cypher: str
    cypher_error: str
    attempts: int
    rows: List[Dict[str, Any]]
    sufficient: bool
    sources: List[Source]
    path: List[str]
    usage: Dict[str, int]
    result: RAGResult


class Route(BaseModel):
    route: Literal["graph", "semantic", "out_of_scope"]


class Sufficiency(BaseModel):
    sufficient: bool


def route_question(state: GraphState) -> GraphState:
    out = (ROUTER_PROMPT | get_chat_llm().with_structured_output(Route, include_raw=True)).invoke(
        {"question": state["question"]}
    )
    route = out["parsed"].route if out["parsed"] else "graph"
    return {"route": route, "path": state.get("path", []) + [f"router:{route}"], "usage": add_usage(state, out["raw"])}


def write_cypher(state: GraphState) -> GraphState:
    feedback = ""
    if state.get("cypher_error"):
        feedback = CYPHER_RETRY_FEEDBACK.format(cypher=state["cypher"], error=state["cypher_error"])
    response = (CYPHER_PROMPT | get_chat_llm(settings.cypher_model)).invoke({
        "question": state["question"],
        "entity_catalog": entity_catalog(),
        "error_feedback": feedback,
    })
    return {
        "cypher": strip_fences(response.content),
        "attempts": state.get("attempts", 0) + 1,
        "cypher_error": "",
        "path": state["path"] + ["write_cypher"],
        "usage": add_usage(state, response),
    }


def run_cypher(state: GraphState) -> GraphState:
    cypher = state["cypher"]
    if not is_read_only(cypher):
        return {"cypher_error": "Write operations are not allowed; write a read-only query.", "rows": [],
                "path": state["path"] + ["run_cypher:rejected"]}
    try:
        rows = read_query(cypher)[:MAX_ROWS]
    except Neo4jError as e:
        return {"cypher_error": e.message or str(e), "rows": [], "path": state["path"] + ["run_cypher:error"]}

    sources = []
    if rows:
        table = "\n".join(json.dumps(row, default=str) for row in rows)
        ids = list(dict.fromkeys(collect_ids(rows, entity_ids())))
        sources = [Source(id=QUERY_RESULT_ID, text=f"Graph query results:\n{table}")] + descriptions(ids)
    return {"rows": rows, "sources": sources, "path": state["path"] + [f"run_cypher:{len(rows)} rows"]}


def after_cypher(state: GraphState) -> str:
    if state.get("cypher_error"):
        return "write_cypher" if state["attempts"] < MAX_CYPHER_ATTEMPTS else "anchor"
    return "grade" if state["rows"] else "anchor"


def grade(state: GraphState) -> GraphState:
    context = "\n\n".join(s.text for s in state["sources"])
    out = (GRADER_PROMPT | get_chat_llm().with_structured_output(Sufficiency, include_raw=True)).invoke(
        {"question": state["question"], "context": context}
    )
    sufficient = bool(out["parsed"] and out["parsed"].sufficient)
    return {"sufficient": sufficient, "path": state["path"] + [f"grade:{'sufficient' if sufficient else 'insufficient'}"],
            "usage": add_usage(state, out["raw"])}


def anchor(state: GraphState) -> GraphState:
    question = state["question"]
    vector = get_embeddings().embed_query(question)

    seed_ids: List[str] = []
    if text := lucene_query(question):
        seed_ids += [r["id"] for r in read_query(f"""
            CALL db.index.fulltext.queryNodes('{ENTITY_FULLTEXT_INDEX}', $text) YIELD node, score
            RETURN node.id AS id LIMIT {FULLTEXT_SEEDS}
        """, text=text)]
    seed_ids += [r["id"] for r in read_query(f"""
        CALL db.index.vector.queryNodes('{ENTITY_VECTOR_INDEX}', {ENTITY_SEEDS}, $v) YIELD node, score
        RETURN node.id AS id
    """, v=vector)]
    seed_ids = list(dict.fromkeys(seed_ids))

    neighbourhoods = read_query("""
        UNWIND range(0, size($ids) - 1) AS i
        MATCH (s:Entity {id: $ids[i]})
        OPTIONAL MATCH (s)-[r]-(n:Entity)
        WITH i, s, r, n ORDER BY type(r), n.name
        RETURN i AS seed_rank, s.id AS id, s.name AS name, s.description AS description,
               collect(CASE WHEN n IS NULL THEN null ELSE
                   {outgoing: startNode(r) = s, type: type(r), props: properties(r), id: n.id, name: n.name}
               END) AS edges
        ORDER BY seed_rank
    """, ids=seed_ids)

    anchor_sources = []
    for row in neighbourhoods:
        lines = [row["description"], "Relationships:"]
        for e in row["edges"]:
            props = "".join(f" {k}: {v}" for k, v in e["props"].items())
            arrow = f"-[{e['type']}{props}]->"
            lines.append(f"- {row['name']} {arrow} {e['name']} [{e['id']}]" if e["outgoing"]
                         else f"- {e['name']} [{e['id']}] {arrow} {row['name']}")
        anchor_sources.append(Source(id=row["id"], text="\n".join(lines)))

    chunk_sources = [Source(id=r["id"], text=r["text"], score=round(r["score"], 3)) for r in read_query(f"""
        CALL db.index.vector.queryNodes('{CHUNK_VECTOR_INDEX}', {CHUNK_SEEDS}, $v) YIELD node, score
        MATCH (node)-[:PART_OF]->(d:Document)
        RETURN d.id AS id, node.text AS text, score
    """, v=vector)]

    return {
        "sources": merge_sources(state.get("sources", []), anchor_sources, chunk_sources),
        "path": state["path"] + [f"anchor:{len(seed_ids)} seeds"],
    }


def answer_node(state: GraphState) -> GraphState:
    sources = state.get("sources", []) if state["route"] != "out_of_scope" else []
    result = generate(
        "graph", state["question"], sources, state["started"],
        route=state["route"], path=state["path"] + ["answer"],
        cypher=state.get("cypher"), rows=len(state.get("rows") or []),
        subgraph=subgraph(sources) if sources else {"nodes": [], "edges": []},
    )
    usage = state.get("usage") or {}
    result.input_tokens += usage.get("input_tokens", 0)
    result.output_tokens += usage.get("output_tokens", 0)
    return {"result": result}


def build_pipeline():
    g = StateGraph(GraphState)
    g.add_node("router", route_question)
    g.add_node("write_cypher", write_cypher)
    g.add_node("run_cypher", run_cypher)
    g.add_node("grade", grade)
    g.add_node("anchor", anchor)
    g.add_node("answer", answer_node)

    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router",
        lambda s: {"graph": "write_cypher", "semantic": "anchor", "out_of_scope": "answer"}[s["route"]],
        ["write_cypher", "anchor", "answer"],
    )
    g.add_edge("write_cypher", "run_cypher")
    g.add_conditional_edges("run_cypher", after_cypher, ["write_cypher", "anchor", "grade"])
    g.add_conditional_edges("grade", lambda s: "answer" if s["sufficient"] else "anchor", ["answer", "anchor"])
    g.add_edge("anchor", "answer")
    g.add_edge("answer", END)
    return g.compile()


PIPELINE = build_pipeline()


def warm_up() -> None:
    """Open the Neo4j connection and load the cached catalog once, so the first user
    question isn't charged for start-up work (call at app/eval start)."""
    entity_ids()
    entity_catalog()
    get_embeddings().embed_query("warm up")


def answer(question: str) -> RAGResult:
    state = PIPELINE.invoke({"question": question, "started": time.perf_counter(), "path": []})
    return state["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", help="question to answer")
    parser.add_argument("--mermaid", action="store_true", help="print the pipeline as a Mermaid diagram")
    args = parser.parse_args()

    if args.mermaid:
        print(PIPELINE.get_graph().draw_mermaid())
    if args.question:
        result = answer(args.question)
        print("Path:   ", " -> ".join(result.extra["path"]))
        if result.extra.get("cypher"):
            print("Cypher: ", result.extra["cypher"].replace("\n", "\n         "))
        print("Sources:", [s.id for s in result.sources])
        print(f"\n{result.answer}\n")
        print(f"citations={result.citations}  latency={result.latency_s}s  "
              f"tokens={result.input_tokens}+{result.output_tokens}  "
              f"subgraph={len(result.extra['subgraph']['nodes'])} nodes")
    if not (args.mermaid or args.question):
        parser.print_help()


if __name__ == "__main__":
    main()
