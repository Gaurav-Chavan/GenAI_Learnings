"""Gather logic with fake search/extract callables (no network)."""
import pytest

from src.competitor_agent.gather import (
    gather_competitor, is_first_party, name_tokens, pick_pricing_urls,
)
from src.competitor_agent.tools import PermanentToolError, TransientToolError

COMP = {"name": "GitHub Copilot", "url": None}


def r(url, title="t"):
    return {"title": title, "url": url, "content": "c", "published": None, "score": 1}


class Recorder:
    """Fake tavily_search / tavily_extract that log every call."""

    def __init__(self, search_results=None, fail=None):
        self.queries, self.extracts = [], []
        self.search_results = search_results or {}
        self.fail = fail  # callable(query) -> exception or None

    def search(self, query, **kw):
        self.queries.append((query, kw.get("topic", "general")))
        if self.fail and (err := self.fail(query)):
            raise err
        for key, res in self.search_results.items():
            if key in query:
                return res
        return [r(f"https://blog.example.com/{len(self.queries)}")]

    def extract(self, urls):
        self.extracts.append(urls)
        return [{"url": u, "raw_content": "page"} for u in urls]


def run(rec, rnd=0, gaps=(), previous=None, comp=COMP):
    return gather_competitor(comp, rnd, list(gaps), previous, search=rec.search,
                             extract=rec.extract, sleep=lambda s: None)


def test_name_tokens_ignore_generic_words():
    assert name_tokens("Claude Code") == ["claude"]
    assert name_tokens("Devin Desktop") == ["devin"]
    assert name_tokens("GitHub Copilot") == ["github", "copilot"]


def test_first_party_detection():
    assert is_first_party("https://github.com/features/copilot/plans", "GitHub Copilot")
    assert not is_first_party("https://codegen.com/alternatives/cursor", "Claude Code")
    assert is_first_party("https://claude.com/pricing", "Claude Code")


def test_pricing_url_needs_first_party_and_pricing_path():
    results = [r("https://blog.example.com/github-copilot-pricing"),  # third party
               r("https://github.com/features/copilot"),  # first party, no pricing path
               r("https://github.com/features/copilot/plans")]
    assert pick_pricing_urls("GitHub Copilot", results) == [
        "https://github.com/features/copilot/plans"]
    # no pricing-like path anywhere: fall back to /pricing on the first-party domain, never a blog
    assert pick_pricing_urls("GitHub Copilot", results[:2]) == ["https://github.com/pricing"]
    assert pick_pricing_urls("GitHub Copilot", results[:1]) == []  # third party only: no guess


def test_pricing_url_derived_from_the_most_common_first_party_domain():
    results = [r("https://code.claude.com/docs/overview"), r("https://claude.com/product/claude-code"),
               r("https://claude.com/blog/x"), r("https://blog.example.com/claude-code-pricing")]
    assert pick_pricing_urls("Claude Code", results) == ["https://claude.com/pricing"]


def test_low_value_domains_are_dropped_with_exact_domain_matching():
    from src.competitor_agent.gather import _merge, is_low_value
    assert is_low_value("https://www.youtube.com/watch?v=1")
    assert is_low_value("https://old.reddit.com/r/x")
    assert not is_low_value("https://www.netflix.com/")  # 'x.com' must not match 'netflix.com'
    merged = _merge([], [r("https://youtube.com/a"), r("https://netflix.com/b"), r("https://ok.com")])
    assert [m["url"] for m in merged] == ["https://netflix.com/b", "https://ok.com"]


def test_official_site_from_discovery_is_trusted_even_when_the_name_does_not_match():
    # Claude Code is made by Anthropic; its pricing lives on claude.com / anthropic.com
    assert not is_first_party("https://www.anthropic.com/pricing", "Claude Code")
    assert is_first_party("https://www.anthropic.com/pricing", "Claude Code", "https://anthropic.com")
    assert is_first_party("https://docs.anthropic.com/x", "Claude Code", "https://anthropic.com")
    assert not is_first_party("https://blog.example.com/anthropic", "Claude Code",
                              "https://anthropic.com")


def test_pricing_url_uses_the_official_site_origin_as_the_fallback():
    assert pick_pricing_urls("Acme Tools", [], "https://acmetools.com/product/x") == [
        "https://acmetools.com/pricing"]
    assert pick_pricing_urls("Acme Tools", [], None) == []  # no site, no guess


def test_pricing_page_on_the_official_site_is_found_in_results_without_a_name_match():
    results = [r("https://mem0.ai/blog/claude-code-pricing"), r("https://anthropic.com/pricing")]
    assert pick_pricing_urls("Claude Code", results) == []
    assert pick_pricing_urls("Claude Code", results, "https://anthropic.com") == [
        "https://anthropic.com/pricing"]


def test_claimed_site_is_trusted_only_if_our_own_results_land_on_it():
    from src.competitor_agent.gather import verify_site
    results = [r("https://docs.anthropic.com/x"), r("https://blog.example.com/y")]
    assert verify_site("https://anthropic.com", results) == "https://anthropic.com"
    assert verify_site("https://made-up.com", results) is None
    assert verify_site(None, results) is None


def test_gather_verifies_the_site_and_uses_it_to_find_the_pricing_page():
    comp = {"name": "Claude Code", "url": "https://anthropic.com"}
    rec = Recorder({"official pricing": [r("https://mem0.ai/blog/claude-code-pricing"),
                                         r("https://anthropic.com/pricing")]})
    raw, _, _ = run(rec, comp=comp)
    assert raw["site"] == "https://anthropic.com"
    assert rec.extracts == [["https://anthropic.com/pricing"]]
    # a wrong claim is never trusted, and no pricing page is guessed
    bad = {"name": "Claude Code", "url": "https://made-up.com"}
    raw2, _, _ = run(Recorder({"official pricing": [r("https://mem0.ai/x")]}), comp=bad)
    assert raw2["site"] is None and raw2["pricing_urls"] == []


def test_site_is_verified_by_fetching_its_pricing_page_when_search_results_are_all_blogs():
    comp = {"name": "Windsurf", "url": "https://windsurf.com"}

    class Rec(Recorder):
        def extract(self, urls):
            self.extracts.append(urls)
            return [{"url": u, "raw_content": "Windsurf plans: Free, Pro $20"} for u in urls]

    rec = Rec({"official pricing": [r("https://blog.example.com/windsurf-pricing")]})
    raw, calls, _ = run(rec, comp=comp)
    assert raw["site"] == "https://windsurf.com"
    assert rec.extracts == [["https://windsurf.com/pricing"]]  # fetched once, not twice
    assert [p["url"] for p in raw["pages"]] == ["https://windsurf.com/pricing"]


def test_probe_rejects_a_page_that_does_not_mention_the_product_and_is_not_repeated():
    comp = {"name": "Windsurf", "url": "https://parked-domain.com"}
    rec = Recorder({"official pricing": [r("https://blog.example.com/p")]})  # extract -> "page"
    raw, _, _ = run(rec, comp=comp)
    assert raw["site"] is None and raw["pages"] == [] and raw["site_probed"] is True
    rec2 = Recorder({"pricing per month": [r("https://blog.example.com/p")]})
    run(rec2, rnd=1, gaps=["pricing"], previous=raw, comp=comp)
    assert rec2.extracts == []  # the failed claim is not fetched again


def test_retry_round_keeps_the_site_verified_earlier():
    previous = {"web": [], "news": [], "pages": [], "pricing_urls": [], "site": "https://anthropic.com"}
    rec = Recorder({"pricing per month": [r("https://blog.example.com/p")]})
    raw, _, _ = run(rec, rnd=1, gaps=["pricing"], previous=previous,
                    comp={"name": "Claude Code", "url": None})
    assert raw["site"] == "https://anthropic.com"
    assert rec.extracts == [["https://anthropic.com/pricing"]]  # derived from the kept site


def test_round_zero_runs_all_queries_and_extracts_official_pricing_page():
    rec = Recorder({"official pricing": [r("https://github.com/features/copilot/plans")]})
    raw, calls, errors = run(rec)
    assert [t for _, t in rec.queries].count("news") == 1 and len(rec.queries) == 3
    assert rec.extracts == [["https://github.com/features/copilot/plans"]]
    assert raw["pricing_urls"] == ["https://github.com/features/copilot/plans"]
    assert calls == 4 and errors == []


def test_retry_round_only_runs_queries_for_the_gaps_and_keeps_old_evidence():
    previous = {"web": [r("https://old.example.com")], "news": [r("https://news.example.com")],
                "pages": [], "pricing_urls": []}
    rec = Recorder({"pricing per month": [r("https://github.com/pricing")]})
    raw, calls, _ = run(rec, rnd=1, gaps=["pricing"], previous=previous)
    assert len(rec.queries) == 1 and "pricing" in rec.queries[0][0]
    urls = {w["url"] for w in raw["web"]}
    assert "https://old.example.com" in urls and "https://github.com/pricing" in urls
    assert raw["news"] == previous["news"]  # news untouched


def test_transient_error_is_retried_then_succeeds():
    attempts = {"n": 0}

    def fail(query):
        if "news" in query and attempts["n"] < 2:
            attempts["n"] += 1
            return TransientToolError("429")

    raw, calls, errors = run(Recorder(fail=fail))
    # 3 searches (no official pricing page found, so no extract) + 2 extra news attempts
    assert errors == [] and calls == 3 + 2


def test_one_call_failing_for_good_degrades_instead_of_crashing():
    rec = Recorder(fail=lambda q: TransientToolError("timeout") if "news" in q else None)
    raw, calls, errors = run(rec)
    assert raw["news"] == [] and raw["web"]
    assert len(errors) == 1 and "timeout" in errors[0]


def test_every_call_failing_raises_so_the_graph_retry_policy_takes_over():
    with pytest.raises(TransientToolError):
        run(Recorder(fail=lambda q: TransientToolError("down")))


def test_permanent_error_propagates_immediately():
    rec = Recorder(fail=lambda q: PermanentToolError("Invalid API key"))
    with pytest.raises(PermanentToolError):
        run(rec)
    assert len(rec.queries) == 1
