"""The agent's four tools.

Reads (autonomous):  tavily_search (general), tavily_search (news), tavily_extract
Write (human-gated): save_briefing  -- only ever called after checkpoint 2 approval

With USE_STUBS=true the read tools return deterministic fake data (used by the tests).
With USE_STUBS=false they call Tavily, and failures are classified so the graph can react:
  TransientToolError (ConnectionError) -> LangGraph RetryPolicy retries with backoff
  PermanentToolError (RuntimeError)    -> not retried; the node fails fast and loudly
An empty result is NOT an error: it returns [] and the Critic / orchestrator handle the gap.
"""
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .config import get_settings

MAX_CONTENT_CHARS = 6000  # keep checkpointed state small


class TransientToolError(ConnectionError):
    """Rate limit, timeout, 5xx: worth retrying."""


class PermanentToolError(RuntimeError):
    """Bad key, exhausted plan, malformed request: retrying will not help."""


def _classify(err: Any) -> Exception:
    text = f"{type(err).__name__}: {err}".lower()
    permanent = ("invalid api key", "unauthorized", "401", "403", "usage limit",
                 "432", "433", "exceeds your plan", "badrequest", "400")
    if any(s in text for s in permanent):
        return PermanentToolError(str(err))
    return TransientToolError(str(err))


def _days_to_range(days: int | None) -> str | None:
    if days is None:
        return None
    return "day" if days <= 1 else "week" if days <= 7 else "month" if days <= 31 else "year"


def _clip(text: str | None) -> str:
    return (text or "")[:MAX_CONTENT_CHARS]


def _iso_date(value: str | None) -> str | None:
    """Tavily news dates are RFC-1123 ('Tue, 03 Nov 2026 01:18:30 GMT'); normalise to ISO."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def tavily_search(
    query: str, *, topic: str = "general", max_results: int = 5, days: int | None = None
) -> list[dict]:
    """Search the web (topic='general') or news (topic='news', optionally last `days`).

    Returns [{title, url, content, published, score}]; [] when nothing matched.
    """
    if get_settings().use_stubs:
        return [
            {
                "title": f"[stub {topic}] result {i + 1} for: {query}",
                "url": f"https://example.com/{topic}/{i + 1}",
                "content": f"Stub content {i + 1} about {query}.",
                "published": str(date.today()),
                "score": 1.0,
            }
            for i in range(min(max_results, 3))
        ]

    from langchain_tavily import TavilySearch

    try:
        raw = TavilySearch(
            max_results=max_results,
            topic=topic,
            time_range=_days_to_range(days),
            search_depth="basic",
        ).invoke({"query": query})
    except Exception as e:  # noqa: BLE001 - classify everything the SDK can raise
        raise _classify(e) from e
    if isinstance(raw, str):  # the SDK returns some failures as a message, not an exception
        raise _classify(raw)
    if isinstance(raw, dict) and raw.get("error"):
        raise _classify(raw["error"])

    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": _clip(r.get("content")),
            "published": _iso_date(r.get("published_date")),
            "score": r.get("score"),
        }
        for r in (raw or {}).get("results", [])
    ]


def tavily_extract(urls: list[str]) -> list[dict]:
    """Pull full page content (used for official pricing pages).

    Returns [{url, raw_content}] for pages that worked; failed URLs are simply omitted.
    """
    if get_settings().use_stubs:
        return [{"url": u, "raw_content": f"Stub page content for {u}"} for u in urls]

    from langchain_tavily import TavilyExtract

    try:
        raw = TavilyExtract(extract_depth="basic").invoke({"urls": urls})
    except Exception as e:  # noqa: BLE001
        raise _classify(e) from e
    if isinstance(raw, str):
        # When NONE of the requested pages could be fetched (404, blocked, no such page: a
        # company may simply have no /pricing page) the SDK returns this text instead of a dict.
        # That is an ordinary "nothing extracted", not an error.
        if raw.startswith("No extracted results"):
            return []
        raise _classify(raw)
    if isinstance(raw, dict) and raw.get("error"):
        raise _classify(raw["error"])

    return [
        {"url": r.get("url", ""), "raw_content": _clip(r.get("raw_content"))}
        for r in (raw or {}).get("results", [])
    ]


def save_briefing(company: str, markdown: str) -> str:
    """WRITE action. Persist the approved briefing; returns the file path."""
    out_dir = Path(get_settings().briefings_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = "".join(c.lower() if c.isalnum() else "-" for c in company).strip("-")
    path = out_dir / f"{slug}-{date.today()}.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path)
