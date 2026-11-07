"""Writer: compile the briefing.

Faithfulness by construction:
  - CODE renders every fact (per-competitor sections, status labels, links, sources, data
    quality notes), so those cannot be paraphrased wrongly or lose their citation.
  - The LLM writes only the executive summary, the comparison cells and the implications,
    and any dollar figure in its text must appear in the facts, otherwise that line is dropped.
  - A human-requested revision is a full LLM rewrite, accepted only if it adds no new dollar
    figure and no new URL; otherwise the previous version is kept and the human is told.
"""
import re
from datetime import date

from pydantic import BaseModel, Field

from .prompts import REVISE_HUMAN, REVISE_SYSTEM, WRITER_HUMAN, WRITER_SYSTEM

# digits with optional thousands groups: "$0," in "Free $0, Pro $20" is the figure "$0", not "$0,"
_MONEY = re.compile(r"\$\s?\d+(?:,\d{3})*(?:\.\d+)?")
_URL = re.compile(r"https?://[^\s)>\]]+")

STATUS_LABEL = {
    "confirmed": "official source",
    "reported": "third-party source",
    "unverified": "unverified",
    "not_found": "not found",
}


class ComparisonRow(BaseModel):
    name: str
    pricing: str
    positioning: str
    best_for: str


class Narrative(BaseModel):
    executive_summary: list[str] = Field(description="3 to 5 bullets")
    comparison: list[ComparisonRow]
    implications: list[str] = Field(description="2 to 4 bullets")


# ------------------------------------------------------------------ fact helpers
def _label(fact: dict) -> str:
    bits = [STATUS_LABEL[fact["status"]]]
    if fact.get("as_of") and fact["status"] != "not_found":
        bits.append(f"as of {fact['as_of']}")
    return ", ".join(bits)


def facts_text(profiles: dict[str, dict]) -> str:
    """The facts the LLM may use, each with its source label."""
    out = []
    for name, p in profiles.items():
        out += [
            f"## {name}",
            f"pricing [{_label(p['pricing'])}]: {p['pricing']['value'] or 'NOT FOUND'}",
            f"positioning [{_label(p['positioning'])}]: {p['positioning']['value'] or 'NOT FOUND'}",
            "features: " + "; ".join(f["value"] for f in p["features"]),
        ]
        out += [f"news {n['date'] or 'undated'}: {n['headline']}" for n in p["news"]]
        out += [f"source conflict: {n}" for n in p["notes"]]
        out.append("")
    return "\n".join(out)


def known_urls(profiles: dict[str, dict]) -> set[str]:
    urls: set[str] = set()
    for p in profiles.values():
        for f in [p["pricing"], p["positioning"], *p["features"]]:
            if f.get("source_url"):
                urls.add(f["source_url"])
        urls |= {n["source_url"] for n in p["news"] if n.get("source_url")}
    return urls


def _money(text: str) -> set[str]:
    return {m.replace(" ", "") for m in _MONEY.findall(text)}


def ungrounded_money(text: str, allowed_text: str) -> set[str]:
    return _money(text) - _money(allowed_text)


def _text(message) -> str:
    """Anthropic returns either a string or a list of content blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


# ------------------------------------------------------------------ narrative (LLM)
def make_narrative(company: str, profiles: dict[str, dict], llm
                   ) -> tuple[Narrative, int, set[str]]:
    """Returns (grounded narrative, lines dropped, the unsupported figures that caused it)."""
    facts = facts_text(profiles)
    structured = llm.with_structured_output(Narrative, method="json_schema")
    raw: Narrative = structured.invoke([
        ("system", WRITER_SYSTEM.format(company=company)),
        ("human", WRITER_HUMAN.format(facts=facts)),
    ])
    dropped = 0
    bad_figures: set[str] = set()

    def keep(line: str) -> bool:
        nonlocal dropped
        bad = ungrounded_money(line, facts)
        dropped += bool(bad)
        bad_figures.update(bad)
        return not bad

    rows = []
    for r in raw.comparison:
        if r.name in profiles:
            if not keep(r.pricing):  # blank cell -> assemble() shows the real price text instead
                r = r.model_copy(update={"pricing": ""})
            rows.append(r)
    narrative = Narrative(
        executive_summary=[s for s in raw.executive_summary if keep(s)],
        comparison=rows,
        implications=[s for s in raw.implications if keep(s)],
    )
    return narrative, dropped, bad_figures


# ------------------------------------------------------------------ assembly (code)
def _link(url: str | None, text: str = "source") -> str:
    return f"[{text}]({url})" if url else ""


def _short(text: str, limit: int = 110) -> str:
    """Fit a table cell without cutting a plan or a word in half: cut at the last "; " (our
    price lists use it between plans), else at the last space, and mark the cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("; ", " "):
        if sep in cut:
            return cut[:cut.rindex(sep)].rstrip(" ;,") + " …"
    return cut + " …"


def _fact_line(label: str, fact: dict) -> str:
    if not fact["value"]:
        return f"**{label}:** not found"
    src = f", {_link(fact['source_url'])}" if fact.get("source_url") else ""
    return f"**{label}** ({_label(fact)}{src}): {fact['value']}"


def assemble(state: dict, narrative: Narrative | None, today: str | None = None) -> str:
    today = today or date.today().isoformat()
    company = state["company"]
    profiles = state["profiles"]
    resolved = state.get("resolved_company") or {}
    rows = {r.name: r for r in (narrative.comparison if narrative else [])}

    md = [f"# Competitor Analysis: {resolved.get('name') or company}", "",
          f"_Generated {today} from live web and news search. Competitors: "
          f"{', '.join(profiles)}. Every fact is labelled official or third-party and linked._", ""]
    if resolved.get("description"):
        md += [resolved["description"], ""]
    if state.get("feedback") and not state.get("briefing_md"):
        md += [f"_Reviewer note: {state['feedback']}_", ""]

    if narrative and narrative.executive_summary:
        md += ["## Executive summary", *[f"- {s}" for s in narrative.executive_summary], ""]

    md += ["## Comparison at a glance", "",
           "| Competitor | Pricing | Positioning | Best for |", "|---|---|---|---|"]
    for name, p in profiles.items():
        r = rows.get(name)
        pricing = (r.pricing if r and r.pricing else _short(p["pricing"]["value"] or "not found"))
        positioning = r.positioning if r else _short(p["positioning"]["value"] or "not found")
        md.append(f"| {name} | {pricing} | {positioning} | {r.best_for if r else ''} |".replace("\n", " "))
    md.append("")

    if narrative and narrative.implications:
        md += [f"## What this means for {resolved.get('name') or company}",
               *[f"- {s}" for s in narrative.implications], ""]

    md += ["## Competitor profiles", ""]
    sources: list[str] = []

    def cite(url: str | None) -> None:
        if url and url not in sources:
            sources.append(url)

    for name, p in profiles.items():
        md += [f"### {name}", "", _fact_line("Positioning", p["positioning"]), "",
               _fact_line("Pricing", p["pricing"]), ""]
        cite(p["positioning"].get("source_url"))
        cite(p["pricing"].get("source_url"))
        if p["features"]:
            md.append("**Core features**")
            for f in p["features"]:
                md.append(f"- {f['value']} ({STATUS_LABEL[f['status']]}, {_link(f['source_url'])})")
                cite(f.get("source_url"))
            md.append("")
        if p["news"]:
            md.append("**Recent news**")
            for n in p["news"]:
                md.append(f"- {n['date'] or 'undated'}: {n['headline']} ({_link(n['source_url'])})")
                cite(n.get("source_url"))
            md.append("")
        if p["notes"]:
            md += ["**Source conflicts**", *[f"- {n}" for n in p["notes"]], ""]

    quality = []
    for name in profiles:
        for g in state.get("gaps", {}).get(name, []):
            quality.append(f"- {name}: could not fully confirm **{g}** after {state.get('retries', 0)} retry round(s).")
        quality += [f"- {name}: {w}" for w in state.get("warnings", {}).get(name, [])]
    md += ["## Data quality notes", *(quality or ["- No gaps or warnings."]), ""]

    md += ["## Sources", *[f"{i}. {u}" for i, u in enumerate(sources, 1)]]
    return "\n".join(md) + "\n"


# ------------------------------------------------------------------ public API
def draft(state: dict, llm, today: str | None = None) -> tuple[str, str]:
    """Returns (markdown, log message). Falls back to a facts-only briefing if the LLM fails."""
    try:
        narrative, dropped, bad = make_narrative(state["company"], state["profiles"], llm)
        note = f"writer: draft ready ({dropped} unsupported line(s) dropped"
        note += f", figures not in the facts: {sorted(bad)})" if dropped else ")"
    except Exception as e:  # noqa: BLE001 - never lose the briefing over the prose
        narrative, note = None, f"writer: narrative failed ({type(e).__name__}); facts-only draft"
    return assemble(state, narrative, today), note


def revise(state: dict, llm) -> tuple[str, str]:
    """Apply human feedback. Returns (markdown, log message); keeps the old text if unsafe."""
    current = state["briefing_md"]
    facts = facts_text(state["profiles"])
    out = _text(llm.invoke([
        ("system", REVISE_SYSTEM),
        ("human", REVISE_HUMAN.format(feedback=state["feedback"], briefing=current, facts=facts)),
    ])).strip()

    allowed = facts + "\n" + current
    new_money = ungrounded_money(out, allowed)
    new_urls = set(_URL.findall(out)) - set(_URL.findall(current)) - known_urls(state["profiles"])
    if not out or new_money or new_urls:
        reason = sorted(new_money | new_urls) or ["empty output"]
        return (current + f"\n\n> Revision not applied: it introduced unsupported content {reason}. "
                f"Feedback was: {state['feedback']}\n"), f"writer: revision REJECTED ({reason})"
    return out + "\n", "writer: revision applied"
