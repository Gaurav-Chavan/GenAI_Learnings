"""Analyst trust rules, with a scripted fake LLM (no network)."""
from src.competitor_agent.analyst import (
    ExtractedFact, ExtractedNews, ExtractedProfile, analyze_competitor, build_evidence, finalize,
)

COMP = {"name": "Cursor"}
RAW = {
    "pages": [{"url": "https://cursor.com/pricing", "raw_content": "Free $0, Plus $8/mo"}],
    "web": [{"title": "Blog", "url": "https://blog.example.com/cursor",
             "content": "Pro is $20", "published": None}],
    "news": [{"title": "Funding", "url": "https://news.example.com/a", "content": "raised",
              "published": "2026-10-01"},
             {"title": "Launch", "url": "https://news.example.com/b", "content": "shipped",
              "published": "2026-11-03"}],
}


def extracted(**over):
    base = dict(
        pricing=ExtractedFact(value="Free $0; Plus $8/mo", source_url="https://cursor.com/pricing"),
        features=[ExtractedFact(value="Agent mode", source_url="https://blog.example.com/cursor"),
                  ExtractedFact(value=None)],
        positioning=ExtractedFact(value="AI-native editor", source_url="https://blog.example.com/cursor"),
        news=[ExtractedNews(headline="Funding", date="2026-10-01", source_url="https://news.example.com/a"),
              ExtractedNews(headline="Launch", date="2026-11-03", source_url="https://news.example.com/b")],
        conflicts=["Blog says Pro $20; official page says Plus $8 (trusted official)"],
    )
    base.update(over)
    return ExtractedProfile(**base)


def test_official_page_is_confirmed_with_todays_date_and_third_party_is_reported():
    p = finalize("Cursor", extracted(), RAW, today="2026-11-05")
    assert p.pricing.status == "confirmed" and p.pricing.as_of == "2026-11-05"
    assert p.positioning.status == "reported" and p.positioning.as_of is None
    assert [f.value for f in p.features] == ["Agent mode"]  # empty feature dropped


def test_official_site_makes_a_differently_named_domain_confirmed():
    raw = {**RAW, "pages": [{"url": "https://anthropic.com/pricing", "raw_content": "Pro $20"}]}
    ex = extracted(pricing=ExtractedFact(value="Pro $20", source_url="https://anthropic.com/pricing"))
    assert finalize("Claude Code", ex, raw).pricing.status == "reported"  # name match only
    p = finalize("Claude Code", ex, raw, site="https://anthropic.com")
    assert p.pricing.status == "confirmed"
    text, _ = build_evidence("Claude Code", raw, site="https://anthropic.com")
    assert "[OFFICIAL PAGE] URL: https://anthropic.com/pricing" in text


def test_analyze_competitor_trusts_the_site_gather_verified_not_the_claimed_one():
    pages = [{"url": "https://anthropic.com/pricing", "raw_content": "Pro $20"}]
    ex = extracted(pricing=ExtractedFact(value="Pro $20", source_url="https://anthropic.com/pricing"))
    claimed_only = {"name": "Claude Code", "url": "https://anthropic.com"}
    assert analyze_competitor(claimed_only, {**RAW, "pages": pages}, FakeLLM(ex)
                              ).pricing.status == "reported"
    verified = {**RAW, "pages": pages, "site": "https://anthropic.com"}
    assert analyze_competitor(claimed_only, verified, FakeLLM(ex)).pricing.status == "confirmed"


def test_citing_a_url_we_never_fetched_is_unverified_and_loses_its_source():
    ex = extracted(pricing=ExtractedFact(value="$5", source_url="https://made-up.com/p"))
    p = finalize("Cursor", ex, RAW, today="2026-11-05")
    assert p.pricing.status == "unverified" and p.pricing.source_url is None


def test_missing_value_is_not_found():
    p = finalize("Cursor", extracted(pricing=ExtractedFact()), RAW)
    assert p.pricing.status == "not_found"


def test_news_must_come_from_fetched_news_results_and_is_sorted_newest_first():
    ex = extracted(news=[
        ExtractedNews(headline="Old", date="2026-10-01", source_url="https://news.example.com/a"),
        ExtractedNews(headline="From a blog", date="2026-11-04", source_url="https://blog.example.com/cursor"),
        ExtractedNews(headline="New", date="2026-11-03", source_url="https://news.example.com/b"),
    ])
    p = finalize("Cursor", ex, RAW)
    assert [n.headline for n in p.news] == ["New", "Old"]


def test_undated_news_is_unverified():
    ex = extracted(news=[ExtractedNews(headline="X", date=None, source_url="https://news.example.com/a")])
    assert finalize("Cursor", ex, RAW).news[0].status == "unverified"


def test_conflicts_become_notes():
    assert "official page says Plus $8" in finalize("Cursor", extracted(), RAW).notes[0]


def test_evidence_is_tagged_official_vs_web_vs_news():
    text, urls = build_evidence("Cursor", RAW)
    assert "[OFFICIAL PAGE] URL: https://cursor.com/pricing" in text
    assert "[WEB] Blog" in text and "[NEWS] Funding (date: 2026-10-01)" in text
    assert "https://news.example.com/b" in urls


class FakeLLM:
    def __init__(self, result, fail_first=0):
        self.result, self.kwargs, self.fail_first, self.calls = result, None, fail_first, 0

    def with_structured_output(self, _schema, **kwargs):
        self.kwargs = kwargs
        return self

    def invoke(self, messages):
        self.calls += 1
        self.messages = messages
        if self.calls <= self.fail_first:
            raise RuntimeError("LengthFinishReasonError")
        return self.result


def many_web(n):
    return [{"title": f"t{i}", "url": f"https://w{i}.example.com", "content": "c",
             "published": None, "score": i / 100} for i in range(n)]


def test_evidence_is_bounded_and_keeps_the_most_relevant_results():
    raw = {**RAW, "web": many_web(20)}
    text, urls = build_evidence("Cursor", raw)
    web_urls = {u for u in urls if u.startswith("https://w") and u.endswith(".example.com")}
    assert len(web_urls) == 8 and "https://w19.example.com" in web_urls
    assert "https://w0.example.com" not in web_urls
    assert "https://cursor.com/pricing" in urls  # official pages are always included


def test_second_attempt_uses_less_evidence_after_a_failure():
    raw = {**RAW, "web": many_web(20)}
    llm = FakeLLM(extracted(), fail_first=1)
    analyze_competitor(COMP, raw, llm, today="2026-11-05")
    assert llm.calls == 2
    assert llm.messages[1][1].count("[WEB]") == 4  # SMALL limits


def test_two_failures_raise_so_the_node_can_keep_the_previous_profile():
    import pytest
    with pytest.raises(RuntimeError):
        analyze_competitor(COMP, RAW, FakeLLM(extracted(), fail_first=2))


def test_prompt_tags_do_not_leak_into_notes():
    ex = extracted(conflicts=["[OFFICIAL PAGE] says $8, [WEB] blog says $20"])
    assert finalize("Cursor", ex, RAW).notes == ["official page says $8, third-party page blog says $20"]


def test_analyze_node_keeps_previous_profile_when_reanalysis_fails(monkeypatch):
    from src.competitor_agent import nodes
    monkeypatch.setattr(nodes, "get_analyst_llm", lambda: None)
    monkeypatch.setattr(nodes, "analyze_competitor",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    previous = finalize("Cursor", extracted(), RAW, today="2026-11-05").model_dump()
    out = nodes._analyze_real(COMP, RAW, 2, previous)["profiles"]["Cursor"]
    assert out["pricing"]["value"] == "Free $0; Plus $8/mo"
    assert "kept the earlier result" in out["notes"][-1]
    first = nodes._analyze_real(COMP, RAW, 0, None)["profiles"]["Cursor"]  # nothing to keep
    assert first["pricing"]["status"] == "not_found" and first["notes"][0].startswith("Analysis failed")


def test_analyze_competitor_uses_json_schema_and_sends_evidence():
    llm = FakeLLM(extracted())
    profile = analyze_competitor(COMP, RAW, llm, today="2026-11-05")
    assert llm.kwargs == {"method": "json_schema"}
    assert "https://cursor.com/pricing" in llm.messages[1][1]
    assert profile.name == "Cursor" and profile.pricing.status == "confirmed"
