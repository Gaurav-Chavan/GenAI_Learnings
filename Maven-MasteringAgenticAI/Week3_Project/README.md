# Competitor Analysis Agent (Week 3, Project 3A)

A LangGraph multi-agent system: give it a company, it finds the top 3 competitors, gathers
fresh web and news data, extracts pricing / features / positioning / news, and compiles a
sourced briefing. A human approves the competitor list and the final briefing.

**Status:** the full pipeline runs live (Tavily, Claude, Nebius Qwen) behind a Streamlit UI
with both human checkpoints. Still to come: LLM-driven routing decisions in the orchestrator
and a multi-company eval harness.

## Flow
```
START -> orchestrator <-> discovery
                      <-> competitor_review   (human checkpoint 1: interrupt)
                      <-> gather x N          (parallel, one per competitor)
                      <-> analyze x N         (parallel)
                      <-> critic              (gaps -> back to gather, bounded retries)
                      <-> writer
                      <-> final_approval      (human checkpoint 2: interrupt)
                       -> save                (the only write action)
```

## Run
Uses [uv](https://docs.astral.sh/uv/); dependencies are locked in `uv.lock`.
```bash
uv sync
cp .env.example .env
uv run streamlit run app.py      # the UI (stub data until USE_STUBS=false in .env)
uv run python run_cli.py Cursor  # terminal, auto-approves both checkpoints
uv run pytest
```

## The UI
`app.py` is a thin view over `src/competitor_agent/session.py` (`ResearchSession`), which wraps
the graph and reports which phase the run is in:

| Phase | Screen |
|---|---|
| new | enter a company |
| awaiting_competitors | **Checkpoint 1**: edit the competitor table (name, official website, why), approve |
| awaiting_briefing | **Checkpoint 2**: read the briefing, then approve and save, request changes, or discard |
| failed | the error, and **Retry from the last checkpoint** (earlier work is kept) |
| done | saved path, download button, run details |

Progress lines stream while a step runs. State is checkpointed to SQLite and the run id is in the
URL (`?run=...`), so refreshing the page resumes the run. The second tab, "How the agent works",
shows the flow and the nine-field agent framework.

Behind an antivirus that re-signs HTTPS traffic (e.g. Avast) and seeing
`CERTIFICATE_VERIFY_FAILED`? Use the system certificate store: `uv sync --system-certs`
(or set `UV_SYSTEM_CERTS=1`).

Resume payloads for the checkpoints must be non-empty (an empty dict is ignored by LangGraph):
`{"approved": True}`, optionally with `competitors` or `feedback`.

See `PROMPTS_AND_ITERATIONS.md` for the build log.
