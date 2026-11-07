"""Agent 2: gather fresh web + news evidence for ONE competitor.

Round 0 runs the full set of searches. On a retry round (the Critic found gaps) it runs only
the queries that target the missing fields, and merges the new evidence into what we already
had, so good earlier evidence is never thrown away.

Failure policy (partial data beats no data):
  - a transient tool error is retried with backoff; if it keeps failing, that one call is
    skipped and recorded in `errors`, and the others carry on
  - a permanent error (bad key, exhausted plan) propagates immediately
  - if EVERY call failed, raise, so the graph-level retry policy takes over
"""
import time
from collections import Counter
from urllib.parse import urlparse

from . import tools
from .tools import PermanentToolError, TransientToolError

PRICING_PATH_HINTS = ("pricing", "plans", "price")
# Words too generic to identify a company's own domain ("Claude Code" must not match codegen.com).
GENERIC_TOKENS = {"code", "desktop", "studio", "cloud", "app", "apps", "pro", "platform", "ai"}
# Video / social / Q&A pages are not citable evidence for a business briefing.
LOW_VALUE_DOMAINS = ("youtube.com", "youtu.be", "reddit.com", "quora.com", "facebook.com",
                     "instagram.com", "tiktok.com", "pinterest.com", "x.com", "twitter.com")
ATTEMPTS = 3


def name_tokens(name: str) -> list[str]:
    words = "".join(ch.lower() if ch.isalnum() else " " for ch in name).split()
    return [w for w in words if len(w) >= 4 and w not in GENERIC_TOKENS]


def registered_domain(url: str) -> str:
    """docs.github.com -> github.com (good enough for .com/.ai/.io; not for co.uk)."""
    return ".".join(urlparse(url).netloc.lower().split(":")[0].split(".")[-2:])


def is_first_party(url: str, name: str, site: str | None = None) -> bool:
    """True when the URL is on the company's own site.

    `site` is the official website Discovery found and validated; its registered domain is
    authoritative (it is how "Claude Code" is recognised on claude.com). Without it we fall
    back to a heuristic: a distinctive word of the product name appears in the domain."""
    if site and registered_domain(url) == registered_domain(site):
        return True
    domain = urlparse(url).netloc.lower()
    return any(t in domain for t in name_tokens(name))


def is_low_value(url: str) -> bool:
    """Exact domain or subdomain match ('x.com' must not match 'netflix.com')."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in LOW_VALUE_DOMAINS)


def verify_site(claimed: str | None, results: list[dict]) -> str | None:
    """Trust Discovery's proposed official website only once this competitor's own search
    results contain a page on that domain (a made-up domain never shows up there)."""
    if not claimed:
        return None
    domain = registered_domain(claimed)
    if any(registered_domain(r["url"]) == domain for r in results):
        parts = urlparse(claimed)
        return f"{parts.scheme}://{parts.netloc}"
    return None


def probe_site(claimed: str | None, name: str, pages: list[dict]) -> str | None:
    """Second way to verify the claimed site: we fetched <claimed>/pricing and that page, on
    the claimed domain, actually talks about the product (SEO blogs often crowd the official
    domain out of search results, so search alone is not reliable)."""
    if not claimed:
        return None
    words = name_tokens(name) or [name.lower().split()[0]]
    domain = registered_domain(claimed)
    for p in pages:
        if (registered_domain(p["url"]) == domain
                and any(w in (p.get("raw_content") or "").lower() for w in words)):
            parts = urlparse(claimed)
            return f"{parts.scheme}://{parts.netloc}"
    return None


def pick_pricing_urls(name: str, results: list[dict], site: str | None = None,
                      limit: int = 1) -> list[str]:
    """Official pricing page candidates, best first:
    1. a first-party URL with a pricing-like path, seen in the search results
    2. the competitor's official site (validated by Discovery) + /pricing
    3. /pricing on the first-party domain that appears most in the results
    """
    found: list[str] = []
    for r in results:
        path = urlparse(r["url"]).path.lower()
        if (is_first_party(r["url"], name, site) and any(h in path for h in PRICING_PATH_HINTS)
                and r["url"] not in found):
            found.append(r["url"])
    if not found and site:
        parts = urlparse(site)
        if parts.scheme and parts.netloc:
            found.append(f"{parts.scheme}://{parts.netloc}/pricing")
    if not found:
        domains = Counter(registered_domain(r["url"]) for r in results
                          if is_first_party(r["url"], name, site))
        found += [f"https://{d}/pricing" for d, _ in domains.most_common(1)]
    return found[:limit]


def _merge(old: list[dict], new: list[dict]) -> list[dict]:
    """Append new results, skipping duplicates and user-generated / video sites."""
    seen = {r["url"] for r in old}
    return old + [r for r in new if r["url"] not in seen and not is_low_value(r["url"])]


def gather_competitor(
    competitor: dict,
    rnd: int,
    gaps: list[str],
    previous: dict | None,
    *,
    search=None,
    extract=None,
    sleep=time.sleep,
) -> tuple[dict, int, list[str]]:
    """Returns (raw evidence dict, number of Tavily calls made, error messages)."""
    search = search or tools.tavily_search
    extract = extract or tools.tavily_extract
    name = competitor["name"]
    prev = previous or {}
    errors: list[str] = []
    calls = 0
    attempted = 0
    succeeded = 0

    def call(fn, *args, **kwargs):
        nonlocal calls, attempted, succeeded
        attempted += 1
        for attempt in range(1, ATTEMPTS + 1):
            calls += 1
            try:
                out = fn(*args, **kwargs)
                succeeded += 1
                return out
            except PermanentToolError:
                raise
            except TransientToolError as e:
                if attempt == ATTEMPTS:
                    errors.append(f"{fn.__name__}: {e}")
                    return None
                sleep(2 ** (attempt - 1))
        return None

    retry = rnd > 0
    want = set(gaps) if retry else {"pricing", "positioning", "features", "news"}

    web: list[dict] = list(prev.get("web", []))
    news: list[dict] = list(prev.get("news", []))
    pages: list[dict] = list(prev.get("pages", []))
    pricing_urls: list[str] = list(prev.get("pricing_urls", []))
    # the official site: only trusted once our own searches for this competitor land on it
    site: str | None = prev.get("site")
    site_probed: bool = bool(prev.get("site_probed"))  # do not re-fetch a claim that failed

    if {"positioning", "features"} & want:
        q = f"{name} what it is homepage" if retry else f"{name} features and positioning"
        found = call(search, q, max_results=8 if retry else 5) or []
        site = site or verify_site(competitor.get("url"), found)
        web = _merge(web, found)

    if "pricing" in want:
        q = f"{name} pricing per month plans cost" if retry else f"{name} official pricing plans"
        found = call(search, q, max_results=8 if retry else 5) or []
        claimed = competitor.get("url")
        site = site or verify_site(claimed, found)
        web = _merge(web, found)
        if not site and claimed and not site_probed:
            origin = f"{urlparse(claimed).scheme}://{urlparse(claimed).netloc}"
            probe = call(extract, [origin + "/pricing"]) or []
            site = probe_site(claimed, name, probe)
            site_probed = True
            if site:  # the page doubles as the official pricing evidence
                pages = _merge(pages, probe)
                pricing_urls = list(dict.fromkeys(pricing_urls + [p["url"] for p in probe]))
        urls = [u for u in pick_pricing_urls(name, found, site, limit=2 if retry else 1)
                if u not in {p["url"] for p in pages}]
        if urls:
            pricing_urls = list(dict.fromkeys(pricing_urls + urls))
            pages = _merge(pages, call(extract, urls) or [])

    if "news" in want:
        # a retry widens the window and changes the angle: announcements, launches, funding
        q, days = (f"{name} announcement launch funding", 90) if retry else (f"{name} news", 30)
        news = _merge(news, call(search, q, topic="news", days=days, max_results=5) or [])

    if attempted and succeeded == 0:
        raise TransientToolError(f"all {attempted} Tavily calls failed for {name}: {errors}")

    raw = {"web": web, "news": news, "pages": pages, "pricing_urls": pricing_urls,
           "site": site, "site_probed": site_probed, "round": rnd}
    return raw, calls, errors
