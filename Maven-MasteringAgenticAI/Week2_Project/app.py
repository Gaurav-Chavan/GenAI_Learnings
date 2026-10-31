"""OrgGraph RAG - Streamlit chatbot UI.

    uv run streamlit run app.py

Pages
  Chat             ask GraphRAG, vector RAG, or both side by side; see the route, Cypher,
                   sources and the subgraph behind every answer; thumbs up/down feedback.
  Evaluation       results of `python -m orggraph_rag.evaluate` (eval/results.jsonl).
  Knowledge graph  the whole org graph, filterable by node type.
"""

import hmac
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# Hosts that install from requirements.txt (e.g. Streamlit Community Cloud running this app from a
# repo subfolder) don't install the package itself, so make src/ importable directly.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yaml

from orggraph_rag import graph_rag, vector_rag
from orggraph_rag.config import PROJECT_ROOT, settings
from orggraph_rag.evaluate import system_summary
from orggraph_rag.generation import RAGResult
from orggraph_rag.prompts import GRAPH_SCHEMA
from orggraph_rag.ui_graph import KIND_COLORS, graph_html

st.set_page_config(page_title="OrgGraph RAG", page_icon="🕸️", layout="wide")

SYSTEMS = {"graph": ("GraphRAG", graph_rag.answer), "vector": ("Vector RAG", vector_rag.answer)}
MODES = {"Side-by-side": ["graph", "vector"], "GraphRAG": ["graph"], "Vector RAG": ["vector"]}
FEEDBACK_FILE = PROJECT_ROOT / "feedback.jsonl"
RESULTS_FILE = settings.eval_dir / "results.jsonl"


@st.cache_resource(show_spinner="Connecting to Neo4j and warming up…")
def startup() -> int:
    graph_rag.warm_up()
    vector_rag.ensure_index()  # first run on a new host: builds chroma_db/ (~44 embedding calls)
    vector_rag.retrieve("warm up", k=1)
    return graph_rag.read_query("MATCH (n:Entity) RETURN count(n) AS n")[0]["n"]


@st.cache_data
def example_questions() -> list[str]:
    queries = yaml.safe_load((settings.eval_dir / "queries.yaml").read_text(encoding="utf-8"))
    return [q["question"] for q in queries]


def run_systems(question: str, systems: list[str]) -> dict[str, RAGResult | str]:
    """Run the chosen systems in parallel; an error is kept as a string for display."""
    def run(system):
        try:
            result = SYSTEMS[system][1](question)
            if "subgraph" not in result.extra:  # vector RAG: show which entities it retrieved
                result.extra["subgraph"] = graph_rag.subgraph(result.sources)
            return result
        except Exception as e:
            return f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=len(systems)) as pool:
        futures = {s: pool.submit(run, s) for s in systems}
        return {s: f.result() for s, f in futures.items()}


def log_feedback(key: str, question: str, system: str, result: RAGResult):
    score = st.session_state.get(key)
    if score is None:
        return
    record = {"time": datetime.now().isoformat(timespec="seconds"), "question": question, "system": system,
              "thumbs_up": score == 1, "answer": result.answer, "citations": result.citations}
    with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    st.toast("Thanks — feedback saved.")


def render_result(result: RAGResult | str, system: str, question: str, key: str):
    if isinstance(result, str):
        st.error(f"{SYSTEMS[system][0]} failed: {result}")
        return

    st.markdown(result.answer)
    stats = [f"⏱ {result.latency_s:.1f}s", f"{result.input_tokens + result.output_tokens:,} tokens",
             f"{len(result.citations)} citations"]
    if system == "graph":
        stats.append(f"route: {result.extra.get('route')}")
    st.caption(" · ".join(stats))

    with st.expander("How this answer was found"):
        if system == "graph":
            st.markdown("**Pipeline path:** " + " → ".join(f"`{step}`" for step in result.extra["path"]))
            if result.extra.get("cypher"):
                st.markdown("**Generated Cypher**")
                st.code(result.extra["cypher"], language="cypher")
        else:
            st.markdown(f"**Top-{result.extra.get('top_k')} entity cards by cosine similarity**")
        st.markdown("**Sources given to the answer model**")
        for source in result.sources:
            score = f" · score {source.score:.3f}" if source.score is not None else ""
            cited = " · ✅ cited" if source.id in result.citations else ""
            st.markdown(f"`[{source.id}]`{score}{cited}")
            st.text(source.text)

    subgraph = result.extra.get("subgraph") or {"nodes": []}
    if subgraph["nodes"]:
        with st.expander(f"Graph behind the answer ({len(subgraph['nodes'])} nodes)"):
            components.html(graph_html(subgraph["nodes"], subgraph["edges"], height=360,
                                       highlight=result.citations), height=370)

    st.feedback("thumbs", key=key, on_change=log_feedback, args=(key, question, system, result))


# ---------------------------------------------------------------- pages

def chat_page():
    st.title("🕸️ OrgGraph RAG")
    st.caption("Ask about people, teams, projects, skills, tools, documents and decisions. "
               "Compare GraphRAG (Neo4j + LangGraph) with a vector RAG baseline on the same question.")

    with st.sidebar:
        mode = st.radio("Answer with", list(MODES), help="Side-by-side runs both systems in parallel.")
        st.markdown("**Try a question**")
        for i, q in enumerate(example_questions()):
            if st.button(q, key=f"example_{i}", width="stretch"):
                st.session_state.pending = q
        if st.button("Clear chat", type="secondary", width="stretch"):
            st.session_state.messages = []

    messages = st.session_state.setdefault("messages", [])
    for i, msg in enumerate(messages):
        with st.chat_message("user"):
            st.markdown(msg["question"])
        with st.chat_message("assistant"):
            if len(msg["results"]) == 1:
                (system, result), = msg["results"].items()
                render_result(result, system, msg["question"], key=f"fb_{i}_{system}")
            else:
                for col, (system, result) in zip(st.columns(2), msg["results"].items()):
                    with col:
                        st.subheader(SYSTEMS[system][0])
                        render_result(result, system, msg["question"], key=f"fb_{i}_{system}")

    question = st.chat_input("Ask about the organization…") or st.session_state.pop("pending", None)
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        asking = " and ".join(SYSTEMS[s][0] for s in MODES[mode])
        with st.chat_message("assistant"), st.spinner(f"Asking {asking}…"):
            results = run_systems(question, MODES[mode])
        messages.append({"question": question, "results": results})
        st.rerun()


def evaluation_page():
    st.title("📊 GraphRAG vs. vector RAG")
    if not RESULTS_FILE.exists():
        st.info("No evaluation results yet. Run:")
        st.code("uv run python -m orggraph_rag.evaluate", language="bash")
        return

    rows = [json.loads(line) for line in RESULTS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    graph_rows = [r for r in rows if r["system"] == "graph"]
    vector_rows = [r for r in rows if r["system"] == "vector"]
    run_time = datetime.fromtimestamp(RESULTS_FILE.stat().st_mtime)
    st.caption(f"{len(graph_rows)} questions · judged by `{settings.judge_model}` · run {run_time:%Y-%m-%d %H:%M}. "
               "Re-run with `uv run python -m orggraph_rag.evaluate`.")

    g, v = system_summary(graph_rows), system_summary(vector_rows)
    st.markdown("**GraphRAG** (change vs. vector RAG)")
    cols = st.columns(4)
    for col, (metric, label, unit) in zip(cols, [("Correctness (avg)", "Correctness", ""),
                                                 ("Faithfulness (avg)", "Faithfulness", ""),
                                                 ("Entity recall (avg)", "Entity recall", ""),
                                                 ("Latency median (s)", "Median latency", " s")]):
        delta = round(g[metric] - v[metric], 2) if g[metric] is not None and v[metric] is not None else None
        col.metric(label, f"{g[metric]}{unit}", delta=f"{delta:+}{unit}" if delta else "±0",
                   delta_color="inverse" if unit else "normal", help=f"Vector RAG: {v[metric]}{unit}")

    st.subheader("All metrics")
    st.dataframe(pd.DataFrame({"GraphRAG": g, "Vector RAG": v}).astype(str), width="stretch")

    st.subheader("Correctness by question type")
    df = pd.DataFrame(rows)
    by_cat = (df.groupby(["category", "system"], as_index=False)["correctness"].mean()
              .replace({"system": {"graph": "GraphRAG", "vector": "Vector RAG"}}))
    chart = alt.Chart(by_cat).mark_bar().encode(
        x=alt.X("correctness:Q", title="Average correctness", scale=alt.Scale(domain=[0, 1])),
        y=alt.Y("category:N", title=None, sort=list(dict.fromkeys(df["category"]))),
        yOffset="system:N",
        color=alt.Color("system:N", title=None, legend=alt.Legend(orient="bottom"),
                        scale=alt.Scale(domain=["GraphRAG", "Vector RAG"], range=["#2a78d6", "#eb6834"])),
        tooltip=["category", "system", alt.Tooltip("correctness:Q", format=".2f")],
    )
    st.altair_chart(chart, width="stretch")

    st.subheader("Per question")
    vector_by_id = {r["id"]: r for r in vector_rows}
    table = []
    for r in graph_rows:
        vr = vector_by_id[r["id"]]
        winner = "GraphRAG" if r["correctness"] > vr["correctness"] else (
            "Vector RAG" if vr["correctness"] > r["correctness"] else "Tie")
        table.append({"Q": r["id"], "Category": r["category"], "Question": r["question"],
                      "GraphRAG": r["correctness"], "Vector RAG": vr["correctness"], "Winner": winner,
                      "GraphRAG s": r.get("latency_s"), "Vector s": vr.get("latency_s")})
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")

    st.subheader("Inspect an answer pair")
    qid = st.selectbox("Question", [r["id"] for r in graph_rows],
                       format_func=lambda i: f"{i} · {next(r['question'] for r in graph_rows if r['id'] == i)}")
    for col, r in zip(st.columns(2), [next(x for x in graph_rows if x["id"] == qid), vector_by_id[qid]]):
        with col:
            st.markdown(f"**{SYSTEMS[r['system']][0]}** · correctness {r['correctness']} · "
                        f"faithfulness {r.get('faithfulness', '–')}")
            st.markdown(r.get("answer") or r.get("error", ""))
            st.caption(f"Judge: {r.get('correctness_reason', '')}")
            if r.get("cypher"):
                st.code(r["cypher"], language="cypher")


def graph_page():
    st.title("🗺️ Knowledge graph")
    nodes = graph_rag.read_query("""
        MATCH (n:Entity) RETURN n.id AS id, n.name AS name, [l IN labels(n) WHERE l <> 'Entity'][0] AS kind
    """)
    edges = graph_rag.read_query("""
        MATCH (a:Entity)-[r]->(b:Entity) RETURN a.id AS source, b.id AS target, type(r) AS type
    """)
    kinds = st.multiselect("Node types", list(KIND_COLORS), default=list(KIND_COLORS))
    shown = {n["id"] for n in nodes if n["kind"] in kinds}
    st.caption(f"{len(nodes)} entities · {len(edges)} relationships in Neo4j "
               f"(plus {len(vector_rag_docs())} document chunks used for vector search)")
    components.html(graph_html([n for n in nodes if n["id"] in shown],
                               [e for e in edges if e["source"] in shown and e["target"] in shown],
                               height=640), height=650)
    with st.expander("Graph schema (as given to Text2Cypher)"):
        st.code(GRAPH_SCHEMA, language="text")


@st.cache_data
def vector_rag_docs() -> list[str]:
    return [r["id"] for r in graph_rag.read_query("MATCH (c:DocChunk) RETURN c.id AS id")]


def password_ok() -> bool:
    """Optional gate for public deployments: only active when APP_PASSWORD is set."""
    if not settings.app_password or st.session_state.get("authenticated"):
        return True
    st.title("🕸️ OrgGraph RAG")
    with st.form("login"):
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Enter"):
            if hmac.compare_digest(password, settings.app_password):
                st.session_state.authenticated = True
                st.rerun()
            st.error("Wrong password.")
    return False


# ---------------------------------------------------------------- main

if not password_ok():
    st.stop()

try:
    entity_count = startup()
except Exception as e:
    st.error(f"Could not reach Neo4j or OpenAI: {e}\n\n"
             "Check `.env` (or your host's Secrets), and resume the Aura instance if it is paused, then reload.")
    st.stop()

with st.sidebar:
    st.caption(f"🟢 Neo4j connected · {entity_count} entities")

st.navigation([
    st.Page(chat_page, title="Chat", icon="💬", default=True),
    st.Page(evaluation_page, title="Evaluation", icon="📊", url_path="evaluation"),
    st.Page(graph_page, title="Knowledge graph", icon="🗺️", url_path="graph"),
]).run()
