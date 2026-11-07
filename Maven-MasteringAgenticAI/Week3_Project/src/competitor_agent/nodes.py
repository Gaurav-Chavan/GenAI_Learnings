"""Graph nodes. The thin graph-facing layer; the logic lives in dedicated modules.

  discovery     -> discovery.py  (Tavily + Claude)
  gather        -> gather.py     (Tavily search / news / extract)
  analyze       -> analyst.py    (Nebius Qwen structured extraction)
  critic        -> critic.py     (deterministic rules)
  writer        -> writer.py     (code-rendered facts + guarded LLM narrative)
  orchestrator  -> here; rule-based routing, LLM decision points still to come

With USE_STUBS=true, discovery / gather / analyze / writer return fake-but-shaped data so
the control flow can be tested offline.
"""
from datetime import date
from typing import Any

from langgraph.graph import END
from langgraph.types import Command, Send, interrupt

from . import tools
from .config import get_settings
from .analyst import analyze_competitor
from .critic import review
from .discovery import discover
from .gather import gather_competitor
from .llm import get_analyst_llm, get_orchestrator_llm
from .prompts import DEFAULT_COMPETITOR_DEFINITION
from .state import Competitor, CompetitorProfile, Fact, NewsItem, ResearchState
from .writer import draft as draft_briefing
from .writer import revise as revise_briefing


# --------------------------------------------------------------------------- workers
def discovery(state: ResearchState) -> dict:
    """Agent 1: resolve the company and find the top-N competitors (Tavily + LLM)."""
    cfg = get_settings()
    company, top_n = state["company"], cfg.top_n

    if cfg.use_stubs:
        competitors = [
            Competitor(name=f"{company} Rival {i}", url=f"https://rival{i}.example.com",
                       rationale="stub rationale").model_dump()
            for i in range(1, top_n + 1)
        ]
        return {"competitors": competitors, "tavily_calls": 1,
                "log": [f"discovery[stub]: {len(competitors)} candidates"]}

    year = date.today().year
    results = tools.tavily_search(f"{company} top competitors and alternatives {year}",
                                  max_results=6)
    results += tools.tavily_search(f"{company} company overview what it does", max_results=4)
    seen, unique = set(), []
    for r in results:  # the two searches can overlap
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    if not unique:  # nothing to reason over: do not call the LLM, let the human supply the list
        return {"competitors": [], "tavily_calls": 2,
                "discovery_note": "Search returned no results. Please enter competitors manually.",
                "log": ["discovery: no search results, escalating to human"]}

    resolved, found, note = discover(
        company, state.get("competitor_definition") or DEFAULT_COMPETITOR_DEFINITION,
        unique, get_orchestrator_llm(), top_n,
    )
    return {
        "resolved_company": resolved.model_dump() if resolved else {},
        "competitors": [c.model_dump() for c in found],
        "discovery_note": note,
        "tavily_calls": 2,
        "log": [f"discovery: resolved '{company}' -> '{resolved.name if resolved else '?'}', "
                f"{len(found)}/{top_n} competitors from {len(unique)} sources"],
    }


def gather(payload: dict) -> dict:
    """Agent 2: fresh web + news data for ONE competitor (fanned out in parallel)."""
    c, rnd = payload["competitor"], payload["round"]
    name = c["name"]

    if get_settings().use_stubs:
        web = tools.tavily_search(f"{name} pricing features positioning", max_results=5)
        news = tools.tavily_search(f"{name} latest news", topic="news", days=30)
        pages = tools.tavily_extract([c.get("url") or "https://example.com"])
        return {
            "raw": {name: {"web": web, "news": news, "pages": pages, "round": rnd}},
            "tavily_calls": 3,
            "log": [f"gather[stub {name}] round {rnd}: {len(web)} web, {len(news)} news"],
        }

    raw, calls, errors = gather_competitor(c, rnd, payload.get("gaps", []), payload.get("previous"))
    summary = (f"{len(raw['web'])} web, {len(raw['news'])} news, {len(raw['pages'])} official "
               f"page(s) ({calls} calls)")
    return {
        "raw": {name: raw},
        "tavily_calls": calls,
        "log": [f"gather[{name}] round {rnd}: {summary}"]
        + [f"gather[{name}] degraded: {e}" for e in errors],
    }


def analyze(payload: dict) -> dict:
    """Agent 3: extract pricing / features / positioning / news for ONE competitor."""
    name = payload["competitor"]["name"]
    rnd = payload["round"]

    if not get_settings().use_stubs:
        return _analyze_real(payload["competitor"], payload.get("raw") or {}, rnd,
                             payload.get("profile"))

    # Stub of a real failure mode: the 3rd competitor's pricing is missing on round 0,
    # found after the Critic sends it back. Exercises the retry loop in the skeleton.
    price_found = not (name.endswith("3") and rnd == 0)
    profile = CompetitorProfile(
        name=name,
        pricing=Fact(
            value="$20/mo (stub)" if price_found else None,
            source_url="https://example.com/pricing" if price_found else None,
            as_of="2026-11-05" if price_found else None,
            status="confirmed" if price_found else "not_found",
        ),
        features=[Fact(value=f"Stub feature {i}", source_url="https://example.com/features",
                       status="confirmed") for i in (1, 2, 3)],
        positioning=Fact(value="Stub positioning statement",
                         source_url="https://example.com", status="confirmed"),
        news=[NewsItem(headline="Stub news item", date=date.today().isoformat(),
                       source_url="https://example.com/news")],
    )
    return {"profiles": {name: profile.model_dump()}, "log": [f"analyze[{name}] round {rnd}"]}


def _analyze_real(competitor: dict, raw: dict, rnd: int, previous: dict | None) -> dict:
    """Real Analyst call (analyze_competitor already tries full then reduced evidence).

    If it still fails, NEVER throw away earlier work: keep the previous round's profile when
    there is one; only a first-round failure degrades to an empty profile, which the Critic
    flags as a gap on every field."""
    name = competitor["name"]
    try:
        profile = analyze_competitor(competitor, raw, get_analyst_llm())
        return {"profiles": {name: profile.model_dump()},
                "log": [f"analyze[{name}] round {rnd}: pricing={profile.pricing.status}, "
                        f"positioning={profile.positioning.status}, "
                        f"{len(profile.features)} features, {len(profile.news)} news"]}
    except Exception as e:  # noqa: BLE001 - LLM / parsing / network errors alike
        err = f"{type(e).__name__}: {str(e)[:160]}"
    if previous:
        kept = {**previous, "notes": [*previous.get("notes", []),
                                      f"Re-analysis in round {rnd} failed; kept the earlier result."]}
        return {"profiles": {name: kept},
                "log": [f"analyze[{name}] round {rnd} FAILED, kept previous profile: {err}"]}
    empty = CompetitorProfile(name=name, notes=[f"Analysis failed: {err}"])
    return {"profiles": {name: empty.model_dump()},
            "log": [f"analyze[{name}] FAILED, degraded to empty: {err}"]}


def critic(state: ResearchState) -> dict:
    """Deterministic quality review: retriable gaps vs caveats for the briefing."""
    gaps, warnings = review(state.get("profiles", {}))
    return {"gaps": gaps, "warnings": warnings,
            "log": [f"critic: gaps={gaps or 'none'}"
                    + (f", warnings={sum(map(len, warnings.values()))}" if warnings else "")]}


def writer(state: ResearchState) -> dict:
    """Compile the briefing; applies human feedback as a guarded revision."""
    if not get_settings().use_stubs:
        llm = get_orchestrator_llm()
        if state.get("feedback") and state.get("briefing_md"):
            md, note = revise_briefing(state, llm)
        else:
            md, note = draft_briefing(state, llm)
        return {"briefing_md": md, "log": [note]}

    lines = [f"# Competitor Analysis: {state['company']}", ""]
    if state.get("feedback"):
        lines += [f"_Revised per feedback: {state['feedback']}_", ""]
    for name, p in state["profiles"].items():
        price = p["pricing"]
        lines += [
            f"## {name}",
            f"- **Pricing:** {price['value'] or 'not found'} ({price['status']})",
            f"- **Positioning:** {p['positioning']['value'] or 'not found'}",
            "- **Features:** " + "; ".join(f["value"] for f in p["features"]),
            "- **Recent news:** " + "; ".join(n["headline"] for n in p["news"]),
            "",
        ]
    return {"briefing_md": "\n".join(lines), "log": ["writer: draft ready"]}


def save(state: ResearchState) -> dict:
    """Write action; reachable only after the human approved the briefing."""
    path = tools.save_briefing(state["company"], state["briefing_md"])
    return {"saved_path": path, "log": [f"save: wrote {path}"]}


# --------------------------------------------------------------- human checkpoints
def competitor_review(state: ResearchState) -> Command:
    """Human checkpoint 1: confirm or edit the competitor list before spending searches.

    Also the fallback when discovery found nothing: the human supplies the list.
    Resume value: {"approved": True, "competitors": [{"name": ..., "url": ...}]} where
    `competitors` is optional (omit to keep the proposed list). NEVER resume with {}:
    LangGraph treats an empty resume value as "no value" and re-interrupts.
    """
    decision = interrupt({
        "checkpoint": "approve_competitors",
        "resolved_company": state.get("resolved_company", {}),
        "competitors": state.get("competitors", []),
        "note": state.get("discovery_note", ""),
    })
    competitors = decision.get("competitors") or state.get("competitors", [])
    return Command(
        goto="orchestrator",
        update={
            "competitors": [Competitor(**c).model_dump() for c in competitors],
            "competitors_approved": True,
            "log": [f"competitor_review: approved {[c['name'] for c in competitors]}"],
        },
    )


def final_approval(state: ResearchState) -> Command:
    """Human checkpoint 2: approve the briefing before the write action.

    Resume value: {"approved": bool, "feedback": str}.
    """
    decision = interrupt({"checkpoint": "approve_briefing", "briefing_md": state["briefing_md"]})
    approved = bool(decision.get("approved"))
    return Command(
        goto="orchestrator",
        update={
            "briefing_approved": approved,
            "feedback": decision.get("feedback", ""),
            "log": [f"final_approval: approved={approved}"],
        },
    )


# ------------------------------------------------------------------- orchestrator
def _sends(
    node: str, state: ResearchState, names: list[str] | None = None, rnd: int | None = None
) -> list[Send]:
    rnd = state.get("retries", 0) if rnd is None else rnd
    return [
        Send(node, {
            "competitor": c,
            "round": rnd,
            "gaps": state.get("gaps", {}).get(c["name"], []),  # what the Critic found missing
            "previous": state.get("raw", {}).get(c["name"]),  # evidence gathered so far
            "raw": state.get("raw", {}).get(c["name"]),  # what the Analyst reads
            "profile": state.get("profiles", {}).get(c["name"]),  # last good analysis, if any
        })
        for c in state["competitors"]
        if names is None or c["name"] in names
    ]


def orchestrator(state: ResearchState) -> Command:
    """Supervisor: looks at the step that just finished and decides what happens next.

    Rule-based in the skeleton. LLM decision points (marked) come in a later step:
    after `critic` (retry vs accept) and after `final_approval` (revise vs stop).
    """
    cfg = get_settings()
    last = state.get("last_step", "start")

    def go(goto: Any, step: str, **update: Any) -> Command:
        return Command(goto=goto, update={"last_step": step, **update})

    if last == "start":
        return go("discovery", "discovery")
    if last == "discovery":
        return go("competitor_review", "competitor_review")
    if last == "competitor_review":
        return go(_sends("gather", state), "gather", refresh=[])
    if last == "gather":
        return go(_sends("analyze", state, state.get("refresh") or None), "analyze")
    if last == "analyze":
        return go("critic", "critic")
    if last == "critic":
        # DECISION POINT (LLM later): retry the gaps, or accept and write?
        gaps = state.get("gaps", {})
        within_budget = state.get("tavily_calls", 0) < cfg.max_tavily_calls
        if gaps and state.get("retries", 0) < cfg.max_retries and within_budget:
            names, next_round = list(gaps), state.get("retries", 0) + 1
            return go(_sends("gather", state, names, rnd=next_round), "gather",
                      refresh=names, retries=next_round)
        note = f"orchestrator: accepting with gaps {gaps}" if gaps else "orchestrator: data complete"
        return go("writer", "writer", log=[note])
    if last == "writer":
        return go("final_approval", "final_approval")
    if last == "final_approval":
        if state.get("briefing_approved"):
            return go("save", "save")
        # DECISION POINT (LLM later): revise per feedback, or give up?
        if state.get("revisions", 0) < cfg.max_revisions and state.get("feedback"):
            return go("writer", "writer", revisions=state.get("revisions", 0) + 1)
        return go(END, "done", log=["orchestrator: briefing rejected, nothing saved"])
    return go(END, "done")
