"""Agent 3: turn raw evidence into a structured, sourced CompetitorProfile.

The LLM extracts; plain code decides how far each fact is trusted. A fact's status is never
chosen by the model:
  not_found   no value
  unverified  value present but its source_url is not one of the URLs we actually fetched
  confirmed   value comes from the company's own site
  reported    value comes from a third party
"""
from datetime import date

from pydantic import BaseModel, Field

from .gather import is_first_party
from .prompts import ANALYST_HUMAN, ANALYST_SYSTEM
from .state import CompetitorProfile, Fact, NewsItem

MAX_FEATURES, MAX_NEWS = 6, 5
SNIPPET_CHARS, PAGE_CHARS, NEWS_CHARS = 700, 6000, 500
MAX_WEB, MAX_NEWS_IN = 8, 6  # evidence blocks shown to the model on the first attempt
SMALL = (4, 4)  # ...and on the second attempt, if the first failed


class ExtractedFact(BaseModel):
    value: str | None = None
    source_url: str | None = None
    as_of: str | None = None


class ExtractedNews(BaseModel):
    headline: str
    date: str | None = None
    source_url: str | None = None


class ExtractedProfile(BaseModel):
    pricing: ExtractedFact
    features: list[ExtractedFact]
    positioning: ExtractedFact
    news: list[ExtractedNews]
    conflicts: list[str] = Field(default_factory=list)


def _best(items: list[dict], limit: int) -> list[dict]:
    """Top results by Tavily relevance score (evidence is merged across retry rounds, so
    it can grow; the prompt must stay bounded or the model can run away)."""
    return sorted(items, key=lambda r: r.get("score") or 0, reverse=True)[:limit]


def build_evidence(name: str, raw: dict, max_web: int = MAX_WEB, max_news: int = MAX_NEWS_IN,
                   site: str | None = None) -> tuple[str, set[str]]:
    """Tagged evidence text plus the set of URLs the model is allowed to cite."""
    blocks: list[str] = []
    urls: set[str] = set()
    for p in raw.get("pages", []):
        tag = "OFFICIAL PAGE" if is_first_party(p["url"], name, site) else "WEB"
        blocks.append(f"[{tag}] URL: {p['url']}\n{p['raw_content'][:PAGE_CHARS]}")
        urls.add(p["url"])
    for r in _best(raw.get("web", []), max_web):
        if r["url"] in urls:
            continue
        tag = "OFFICIAL PAGE" if is_first_party(r["url"], name, site) else "WEB"
        blocks.append(f"[{tag}] {r['title']}\nURL: {r['url']}\n{r['content'][:SNIPPET_CHARS]}")
        urls.add(r["url"])
    for r in _best(raw.get("news", []), max_news):
        blocks.append(f"[NEWS] {r['title']} (date: {r.get('published') or 'unknown'})\n"
                      f"URL: {r['url']}\n{r['content'][:NEWS_CHARS]}")
        urls.add(r["url"])
    return "\n\n".join(blocks), urls


def _plain(note: str) -> str:
    """The prompt's evidence tags must not leak into text a human reads."""
    return (note.replace("[OFFICIAL PAGE]", "official page")
                .replace("[WEB]", "third-party page").replace("[NEWS]", "news article"))


def _status(value: str | None, source_url: str | None, known: set[str], name: str,
            site: str | None = None) -> str:
    if not value:
        return "not_found"
    if not source_url or source_url not in known:
        return "unverified"
    return "confirmed" if is_first_party(source_url, name, site) else "reported"


def _fact(ex: ExtractedFact, known: set[str], name: str, fetched_pages: set[str],
          today: str, site: str | None = None) -> Fact:
    status = _status(ex.value, ex.source_url, known, name, site)
    source = ex.source_url if status in ("confirmed", "reported") else None
    as_of = ex.as_of
    if status == "confirmed" and not as_of and source in fetched_pages:
        as_of = today  # we read the live page today
    return Fact(value=ex.value, source_url=source, as_of=as_of, status=status)


def finalize(name: str, ex: ExtractedProfile, raw: dict, today: str | None = None,
             limits: tuple[int, int] = (MAX_WEB, MAX_NEWS_IN),
             site: str | None = None) -> CompetitorProfile:
    today = today or date.today().isoformat()
    _, known = build_evidence(name, raw, *limits, site=site)  # only URLs the model saw
    pages = {p["url"] for p in raw.get("pages", [])}
    news_urls = {r["url"] for r in raw.get("news", [])}

    features = [_fact(f, known, name, pages, today, site) for f in ex.features if f.value]
    news = []
    for n in ex.news:
        if n.source_url not in news_urls:  # news must come from a news result we fetched
            continue
        news.append(NewsItem(
            headline=n.headline, date=n.date, source_url=n.source_url,
            status="confirmed" if is_first_party(n.source_url, name, site) else "reported"
            if n.date else "unverified",
        ))
    news.sort(key=lambda n: n.date or "", reverse=True)

    return CompetitorProfile(
        name=name,
        pricing=_fact(ex.pricing, known, name, pages, today, site),
        features=features[:MAX_FEATURES],
        positioning=_fact(ex.positioning, known, name, pages, today, site),
        news=news[:MAX_NEWS],
        notes=[_plain(c) for c in ex.conflicts],
    )


def analyze_competitor(competitor: dict, raw: dict, llm, today: str | None = None
                       ) -> CompetitorProfile:
    """Two attempts: full evidence, then a smaller evidence set. A model that ran away or
    failed to parse on a big prompt often succeeds on a smaller one."""
    name, site = competitor["name"], raw.get("site")  # verified by Gather, not merely claimed
    structured = llm.with_structured_output(ExtractedProfile, method="json_schema")
    last_error: Exception | None = None
    for limits in ((MAX_WEB, MAX_NEWS_IN), SMALL):
        evidence, _ = build_evidence(name, raw, *limits, site=site)
        try:
            extracted: ExtractedProfile = structured.invoke([
                ("system", ANALYST_SYSTEM),
                ("human", ANALYST_HUMAN.format(name=name, evidence=evidence)),
            ])
            return finalize(name, extracted, raw, today, limits, site)
        except Exception as e:  # noqa: BLE001 - length limit, parse and network errors alike
            last_error = e
    raise last_error  # type: ignore[misc]
