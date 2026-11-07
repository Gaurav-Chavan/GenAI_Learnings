"""Agent 1: competitor discovery.

search (Tavily) -> LLM structured output -> deterministic validation -> one repair attempt.
The LLM proposes; plain code checks the proposal (grounding, duplicates, self-reference,
count) so a hallucinated or malformed list never reaches the rest of the pipeline.
If validation still fails after the repair, we return what is valid plus a note, and the
human checkpoint fixes the rest.
"""
import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from .gather import is_low_value
from .prompts import DISCOVERY_HUMAN, DISCOVERY_REPAIR, DISCOVERY_SYSTEM
from .state import Competitor


class ResolvedCompany(BaseModel):
    name: str = Field(description="Canonical company or product name")
    description: str = Field(description="One line: what it is and who it is for")
    website: str | None = None
    ambiguity_note: str = Field(default="", description="Only if the name is ambiguous")


class DiscoveryResult(BaseModel):
    company: ResolvedCompany
    competitors: list[Competitor]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def format_evidence(results: list[dict], limit_chars: int = 700) -> str:
    return "\n\n".join(
        f"[{i}] {r['title']}\nURL: {r['url']}\n{r['content'][:limit_chars]}"
        for i, r in enumerate(results, 1)
    )


def _grounded(name: str, evidence_text: str) -> bool:
    """The name (or its distinctive first word) must appear in the evidence."""
    n = _norm(name)
    if n and n in evidence_text:
        return True
    first = n.split(" ")[0] if n else ""
    return len(first) >= 4 and first in evidence_text


def clean_website(url: str | None) -> str | None:
    """Normalise a proposed official website to its origin, or None if it is malformed or a
    video / social / Q&A site.

    Discovery cannot verify the domain: its evidence is roundups ABOUT the target, so the
    competitors' own domains are usually not in it. The claim is therefore only a candidate;
    Gather confirms it against the competitor's own search results before trusting it."""
    if not url or not url.strip():
        return None
    url = url.strip()
    parts = urlparse(url if "://" in url else f"https://{url}")
    host = parts.netloc.lower().split(":")[0]
    if parts.scheme not in ("http", "https") or "." not in host:
        return None
    site = f"https://{host.removeprefix('www.')}"
    return None if is_low_value(site) else site


def validate(
    result: DiscoveryResult, company: str, results: list[dict], top_n: int
) -> tuple[list[Competitor], list[str]]:
    """Return (valid competitors, problems). Never raises."""
    evidence_text = _norm(" ".join(f"{r['title']} {r['content']}" for r in results))
    known_urls = {r["url"] for r in results}
    self_names = {_norm(company), _norm(result.company.name)}

    valid: list[Competitor] = []
    problems: list[str] = []
    seen: set[str] = set()
    for c in result.competitors:
        key = _norm(c.name)
        if not key:
            problems.append("a competitor had an empty name")
        elif key in self_names:
            problems.append(f"'{c.name}' is the target company itself")
        elif key in seen:
            problems.append(f"'{c.name}' is listed more than once")
        elif not _grounded(c.name, evidence_text):
            problems.append(f"'{c.name}' does not appear in the provided evidence")
        else:
            seen.add(key)
            c.evidence_urls = [u for u in c.evidence_urls if u in known_urls]
            c.url = clean_website(c.url)
            valid.append(c)
    if len(valid) > top_n:
        valid = valid[:top_n]
    if len(valid) < top_n:
        problems.append(f"only {len(valid)} valid competitors; exactly {top_n} are required")
    return valid, problems


def discover(
    company: str, definition: str, results: list[dict], llm, top_n: int
) -> tuple[ResolvedCompany | None, list[Competitor], str]:
    """Returns (resolved company, competitors, note for the human reviewer)."""
    # json_schema = the provider's native structured output (schema-guaranteed). The default,
    # forced tool calling, is not supported on Claude Sonnet 5.5 and can silently not fire.
    structured = llm.with_structured_output(DiscoveryResult, method="json_schema")
    messages = [
        ("system", DISCOVERY_SYSTEM.format(top_n=top_n, definition=definition)),
        ("human", DISCOVERY_HUMAN.format(company=company, evidence=format_evidence(results))),
    ]
    result: DiscoveryResult = structured.invoke(messages)
    competitors, problems = validate(result, company, results, top_n)

    if problems:  # one repair attempt, with the concrete problems spelled out
        messages += [
            ("ai", result.model_dump_json()),
            ("human", DISCOVERY_REPAIR.format(problems="\n".join(f"- {p}" for p in problems),
                                              top_n=top_n)),
        ]
        repaired: DiscoveryResult = structured.invoke(messages)
        r_comps, r_problems = validate(repaired, company, results, top_n)
        if len(r_comps) >= len(competitors):  # keep the better of the two attempts
            result, competitors, problems = repaired, r_comps, r_problems

    notes = []
    if result.company.ambiguity_note:
        notes.append(f"Name ambiguity: {result.company.ambiguity_note}")
    if problems:
        notes.append("Please review: " + "; ".join(problems))
    no_site = [c.name for c in competitors if not c.url]
    if no_site:
        notes.append("No official website proposed for " + ", ".join(no_site)
                     + " (official-source detection falls back to name matching)")
    return result.company, competitors, " | ".join(notes)
