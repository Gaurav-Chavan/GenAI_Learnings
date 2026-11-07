"""Streamlit UI for the Competitor Analysis Agent.

    uv run streamlit run app.py

The page is a thin view over `ResearchSession`: it asks which phase the run is in and shows
the matching screen. The two human checkpoints are real LangGraph interrupts, so the run is
paused (not polling) until you answer. Progress is stored in SQLite, and the run id is kept in
the URL, so a browser refresh picks the run up where it was.
"""
import sqlite3
import time
import uuid

import pandas as pd
import streamlit as st
from langgraph.checkpoint.sqlite import SqliteSaver

from src.competitor_agent.config import get_settings
from src.competitor_agent.graph import build_graph
from src.competitor_agent.session import ResearchSession

st.set_page_config(page_title="Competitor Analysis Agent", page_icon="🔎", layout="wide")

NODE_LABEL = {
    "discovery": "Finding competitors",
    "gather": "Gathering web and news evidence",
    "analyze": "Extracting pricing, features, positioning, news",
    "critic": "Checking data quality",
    "writer": "Writing the briefing",
    "save": "Saving the briefing",
    "competitor_review": "Competitor list confirmed",
    "final_approval": "Briefing decision recorded",
    "orchestrator": "Orchestrator",
}


@st.cache_resource
def get_graph():
    conn = sqlite3.connect(get_settings().checkpoint_db, check_same_thread=False)
    return build_graph(SqliteSaver(conn))


def get_session() -> ResearchSession:
    """One LangGraph thread per run; the id lives in the URL so a refresh resumes the run."""
    run_id = st.query_params.get("run")
    if not run_id:
        run_id = str(uuid.uuid4())
        st.query_params["run"] = run_id
    return ResearchSession(get_graph(), run_id)


def md(text: str) -> str:
    """Streamlit markdown renders `$...$` as LaTeX, which garbles price ranges like
    "$10/mo to $280/mo". Escape dollar signs for display only; the saved file is untouched."""
    return text.replace("$", "\\$")


def queue(action: str, label: str, payload: dict | str | None = None) -> None:
    """Run a graph step on the next pass, before any form is drawn, so a stale checkpoint
    form never sits above the progress log while the step runs."""
    st.session_state["pending"] = (action, label, payload)
    st.rerun()


def new_run() -> None:
    st.query_params["run"] = str(uuid.uuid4())
    st.session_state.pop("error", None)
    st.rerun()


def drive(events, label: str) -> None:
    """Run the graph, showing each step as it finishes, then redraw for the new phase."""
    started = time.time()
    st.session_state.pop("error", None)
    with st.status(label, expanded=True) as status:
        try:
            for ev in events:
                if ev.node == "orchestrator" and not ev.lines:
                    continue
                for line in ev.lines or [NODE_LABEL.get(ev.node, ev.node)]:
                    st.write(f"`{time.time() - started:5.1f}s`  {line}")
            status.update(label=f"Finished in {time.time() - started:.0f}s", state="complete",
                          expanded=False)
        except Exception as e:  # noqa: BLE001 - show it; the last checkpoint is still intact
            st.session_state["error"] = f"{type(e).__name__}: {str(e)[:400]}"
            status.update(label="A step failed", state="error")
    st.rerun()


# ------------------------------------------------------------------------------ sidebar
cfg = get_settings()
session = get_session()
snap = session.snapshot()

with st.sidebar:
    st.title("🔎 Competitor Analysis")
    if cfg.use_stubs:
        st.warning("**Stub mode**: fake data, no API calls. Set `USE_STUBS=false` in `.env` "
                   "and restart for live research.")
    else:
        st.success("**Live mode**: Tavily search, Claude and Nebius.")

    disabled = snap.phase not in ("new", "done")
    company = st.text_input("Company or product", value=snap.values.get("company", "Cursor"),
                            max_chars=80, disabled=disabled)
    if snap.phase == "new":
        if st.button("Run analysis", type="primary", width="stretch",
                     disabled=not company.strip()):
            queue("start", "Researching competitors", company)
    else:
        if st.button("New analysis", width="stretch"):
            new_run()

    st.caption(f"Budget: {cfg.max_tavily_calls} Tavily calls, {cfg.max_retries} retry rounds, "
               f"{cfg.max_revisions} revisions. Top {cfg.top_n} competitors.")
    st.caption(f"Run id: `{session.thread_id[:8]}`")

tab_run, tab_framework = st.tabs(["Run", "How the agent works"])


# ------------------------------------------------------------------------------ run tab
def competitor_checkpoint() -> None:
    cp = snap.checkpoint
    st.subheader("Checkpoint 1 of 2: confirm the competitors")
    resolved = cp.get("resolved_company") or {}
    if resolved:
        st.markdown(f"**Target:** {resolved.get('name')}: {resolved.get('description', '')}")
    if cp.get("note"):
        st.warning(cp["note"])
    st.caption("This is the cheapest place to fix a mistake: nothing has been researched yet. "
               "Edit, add or remove rows, then approve. The website is used to recognise "
               "the company's own pages as official.")

    original = {c["name"]: c for c in cp["competitors"]}
    frame = pd.DataFrame(cp["competitors"], columns=["name", "url", "rationale"]).fillna("")
    edited = st.data_editor(
        frame, num_rows="dynamic", width="stretch", hide_index=True,
        key=f"editor-{session.thread_id}",
        column_config={"name": "Competitor", "url": "Official website", "rationale": "Why"},
    )
    rows = [r for r in edited.to_dict("records") if str(r["name"]).strip()]
    names = [str(r["name"]).strip().lower() for r in rows]
    problems = []
    if not rows:
        problems.append("Add at least one competitor.")
    if len(rows) > 5:
        problems.append("At most 5 competitors (each one costs search calls).")
    if len(set(names)) != len(names):
        problems.append("Competitor names must be unique.")
    if any(str(r["url"]).strip() and not str(r["url"]).strip().startswith(("http://", "https://"))
           for r in rows):
        problems.append("Websites must start with http:// or https://.")
    for p in problems:
        st.error(p)

    if st.button("Approve and research", type="primary", disabled=bool(problems)):
        competitors = [{
            "name": str(r["name"]).strip(),
            "url": str(r["url"]).strip() or None,
            "rationale": str(r["rationale"]).strip(),
            "evidence_urls": original.get(str(r["name"]).strip(), {}).get("evidence_urls", []),
        } for r in rows]
        queue("resume", "Researching the competitors",
              {"approved": True, "competitors": competitors})


def briefing_checkpoint() -> None:
    cp = snap.checkpoint
    used, limit = snap.values.get("revisions", 0), cfg.max_revisions
    st.subheader("Checkpoint 2 of 2: review the briefing")
    st.caption("Nothing has been saved. Approving is the only thing that writes a file.")
    gaps = {k: v for k, v in snap.values.get("gaps", {}).items() if v}
    if gaps:
        st.warning("Unresolved gaps after retries: " + "; ".join(
            f"**{n}**: {', '.join(g)}" for n, g in gaps.items()))

    left, right = st.columns([7, 3], gap="large")  # wide briefing: the comparison table wraps badly in a narrow column
    with right:
        if st.button("Approve and save", type="primary", width="stretch"):
            queue("resume", "Saving", {"approved": True})
        feedback = st.text_area(
            "Or request changes", height=120, key=f"fb-{session.thread_id}-{used}",
            placeholder="e.g. Shorter summary, focus on pricing differences.",
            help="The Writer rewrites using only facts already collected. A rewrite that "
                 "adds a new price or link is rejected and the previous version is kept.")
        if st.button("Revise", width="stretch", disabled=not feedback.strip() or used >= limit):
            queue("resume", "Revising", {"approved": False, "feedback": feedback.strip()})
        st.caption(f"Revisions used: {used} of {limit}.")
        if st.button("Discard, save nothing", width="stretch"):
            queue("resume", "Closing the run", {"approved": False})
    with left:
        with st.container(border=True):
            st.markdown(md(cp["briefing_md"]))


def finished() -> None:
    saved = snap.values.get("saved_path")
    briefing = snap.values.get("briefing_md", "")
    if saved:
        st.success(f"Briefing saved to `{saved}`")
        st.download_button("Download briefing (.md)", briefing,
                           file_name=saved.replace("\\", "/").split("/")[-1])
    else:
        st.info("You discarded the briefing, so nothing was saved.")
    with st.container(border=True):
        st.markdown(md(briefing))


def run_details() -> None:
    v = snap.values
    if not v.get("log"):
        return
    with st.expander("Run details: what the agent did", expanded=False):
        a, b, c = st.columns(3)
        a.metric("Tavily calls", f"{v.get('tavily_calls', 0)} / {cfg.max_tavily_calls}")
        b.metric("Retry rounds", f"{v.get('retries', 0)} / {cfg.max_retries}")
        c.metric("Revisions", f"{v.get('revisions', 0)} / {cfg.max_revisions}")
        st.code("\n".join(v["log"]), language=None)


with tab_run:
    pending =st.session_state.pop("pending", None)
    if pending:  # a step was requested: run it now, drawing nothing else (drive() reruns after)
        action, label, payload = pending
        events = {"start": lambda: session.start(payload), "resume": lambda: session.resume(payload),
                  "retry": session.retry}[action]()
        drive(events, label)
    if st.session_state.get("error"):
        st.error(f"A step failed after its automatic retries: {st.session_state['error']}")
    if snap.phase == "new":
        st.header("Competitor analysis, with you in the loop")
        st.markdown(
            "Enter a company in the sidebar. The agent finds its top competitors, **pauses for "
            "you to confirm them**, researches each one in parallel (pricing, features, "
            "positioning, recent news), and writes a sourced briefing that **you approve "
            "before anything is saved**."
        )
    elif snap.phase == "awaiting_competitors":
        competitor_checkpoint()
    elif snap.phase == "awaiting_briefing":
        briefing_checkpoint()
    elif snap.phase == "failed":
        st.error("The run stopped on an error. Your earlier work is saved at the last checkpoint.")
        if snap.error:
            st.code(snap.error, language=None)
        if st.button("Retry from the last checkpoint", type="primary"):
            queue("retry", "Retrying")
    elif snap.phase == "done":
        finished()
    run_details()


# ---------------------------------------------------------------------- framework tab
FLOW = """
digraph G {
  rankdir=LR; bgcolor="transparent"; nodesep=0.3;
  node [shape=box, style="rounded,filled", fillcolor="#eef2f7", color="#6b7a90", fontname="Helvetica"];
  edge [color="#6b7a90", fontname="Helvetica", fontsize=10];
  Discovery -> Confirm -> Gather -> Analyze -> Critic -> Writer -> Approve -> Save;
  Critic -> Gather [label="gaps: retry\\n(bounded)", style=dashed];
  Approve -> Writer [label="feedback", style=dashed];
  Confirm [label="YOU:\\nconfirm competitors", fillcolor="#fde9c9", color="#c9892b"];
  Approve [label="YOU:\\napprove briefing", fillcolor="#fde9c9", color="#c9892b"];
  Save [label="Save\\n(only write action)", fillcolor="#e0f0e3", color="#4d8a5a"];
}
"""

with tab_framework:
    st.header("The agent, in the Part 2 framework")
    st.markdown(
        "**One line:** Given a company, an agent team finds its top competitors, researches "
        "each on live web and news data, and drafts a sourced briefing that a human approves "
        "before it is saved."
    )
    st.graphviz_chart(FLOW, width="stretch")

    framework = [
        ("1. Goal",
         "A sourced competitor briefing (pricing, features, positioning, recent news) for the "
         f"top {cfg.top_n} competitors of one company, in minutes instead of hours."),
        ("2. Surface",
         "This Streamlit app. The same graph also runs from the terminal (`run_cli.py`)."),
        ("3. Steps",
         "**Orchestrator** (supervisor) routes between: **Discovery** (Tavily + Claude, validated "
         "by code) → *you confirm* → **Gather** and **Analyze** per competitor, in parallel "
         "(Tavily search / news / extract; Nebius Qwen extracts) → **Critic** (rules) → "
         "**Writer** (Claude) → *you approve* → **Save**."),
        ("4. Tools: read vs write",
         "**Read:** `tavily_search` (web and news), `tavily_extract` (full official pages). "
         "**Write:** `save_briefing`, the only one, reachable only after you approve."),
        ("5. Memory",
         "Per run: the LangGraph state, checkpointed to SQLite after every step (this is what "
         "lets a failed run resume and a refresh pick the run back up). No memory across runs."),
        ("6. Never do",
         "Invent a price or link (every dollar figure in the narrative must appear in the "
         "evidence, and a cited URL must have been fetched); label a third-party claim as "
         "official (status is assigned by code from the source domain, never by the model); "
         "save without approval; exceed the call budget."),
        ("7. Human in the loop",
         "Two checkpoints, both real pauses: **before spending searches** (confirm or edit the "
         "competitors) and **before the write** (approve, request changes, or discard)."),
        ("8. Failure handling",
         "Transient tool errors: retried with backoff, then that one call is skipped and the "
         "rest carry on. Permanent errors (bad key, quota) fail fast. A node that still fails: "
         "retried by the graph, and the run can resume from its last checkpoint. A failed "
         "re-analysis never overwrites earlier good data. Missing fields trigger up to "
         f"{cfg.max_retries} targeted retry rounds; then the briefing states the gap instead "
         "of guessing. If the Writer's LLM fails you still get a facts-only briefing."),
        ("9. Success measure",
         "Targets: official pricing confirmed from the company's own page for most competitors; "
         "every fact carries a source link and an as-of date; no figure in the narrative that "
         f"is absent from the evidence; a run in a few minutes and within {cfg.max_tavily_calls} "
         "Tavily calls. *A multi-company eval harness is not built yet, so these are tracked "
         "per run in “Run details”, not yet scored.*"),
    ]
    for title, body in framework:
        with st.container(border=True):
            left, right = st.columns([1, 4])
            left.markdown(f"**{title}**")
            right.markdown(body)
