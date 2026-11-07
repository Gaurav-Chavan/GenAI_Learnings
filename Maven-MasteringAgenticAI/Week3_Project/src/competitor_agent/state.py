"""Graph state and the data schemas that flow through it.

State holds plain dicts (via .model_dump()) so the checkpointer serializes cleanly;
the Pydantic models validate data at the edges (Discovery output, Analyst output).
"""
import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

# How much we trust a fact. Drives how the briefing labels it.
FactStatus = Literal["confirmed", "reported", "unverified", "not_found"]


class Competitor(BaseModel):
    name: str
    url: str | None = Field(
        default=None,
        description="Official website homepage of the company that makes this product "
                    "(e.g. https://claude.com for Claude Code). Null if unsure.")
    rationale: str = ""
    evidence_urls: list[str] = Field(default_factory=list)  # sources that support the pick


class Fact(BaseModel):
    value: str | None = None
    source_url: str | None = None
    as_of: str | None = None  # when the source says this was true
    status: FactStatus = "not_found"


class NewsItem(BaseModel):
    headline: str
    date: str | None = None
    source_url: str | None = None
    status: FactStatus = "reported"


class CompetitorProfile(BaseModel):
    name: str
    pricing: Fact = Field(default_factory=Fact)
    features: list[Fact] = Field(default_factory=list)
    positioning: Fact = Field(default_factory=Fact)
    news: list[NewsItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # e.g. conflicts between sources


def merge_dicts(old: dict | None, new: dict | None) -> dict:
    """Reducer: parallel branches each add their own competitor key."""
    return {**(old or {}), **(new or {})}


class ResearchState(TypedDict, total=False):
    # input
    company: str
    competitor_definition: str  # what counts as a "competitor"

    # discovery + human checkpoint 1
    resolved_company: dict  # canonical name / description / website from discovery
    competitors: list[dict]
    discovery_note: str  # shown to the human reviewer (ambiguity, shortfalls)
    competitors_approved: bool

    # per-competitor data, keyed by competitor name (written by parallel branches)
    raw: Annotated[dict[str, dict], merge_dicts]
    profiles: Annotated[dict[str, dict], merge_dicts]

    # critic / retry loop
    gaps: dict[str, list[str]]  # competitor -> fields still missing
    warnings: dict[str, list[str]]  # competitor -> quality caveats for the briefing
    refresh: list[str]  # competitors to re-gather on a retry round
    retries: int

    # budget + audit trail
    tavily_calls: Annotated[int, operator.add]
    log: Annotated[list[str], operator.add]

    # output + human checkpoint 2
    briefing_md: str
    feedback: str  # human feedback that sends the writer back
    revisions: int
    briefing_approved: bool
    saved_path: str

    # orchestrator bookkeeping: the step that just ran
    last_step: str
