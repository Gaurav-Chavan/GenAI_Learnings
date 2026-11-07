"""Offline tests for the real-Tavily code path (the Tavily client is faked)."""
import pytest

from src.competitor_agent import tools


@pytest.fixture(autouse=True)
def _real_mode(monkeypatch):
    monkeypatch.setenv("USE_STUBS", "false")


def _fake_tool(monkeypatch, name, *, result=None, exc=None):
    import langchain_tavily

    class Fake:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def invoke(self, args):
            if exc:
                raise exc
            return result

    monkeypatch.setattr(langchain_tavily, name, Fake)


@pytest.mark.parametrize("message", ["429 Too Many Requests", "Read timed out", "502 Bad Gateway"])
def test_transient_errors_are_retryable(monkeypatch, message):
    _fake_tool(monkeypatch, "TavilySearch", exc=Exception(message))
    with pytest.raises(tools.TransientToolError):
        tools.tavily_search("q")


@pytest.mark.parametrize("message", ["Invalid API key", "usage limit exceeded", "401 Unauthorized"])
def test_permanent_errors_are_not_retried(monkeypatch, message):
    _fake_tool(monkeypatch, "TavilySearch", exc=Exception(message))
    with pytest.raises(tools.PermanentToolError):
        tools.tavily_search("q")


def test_error_dict_returned_by_the_tool_is_raised(monkeypatch):
    _fake_tool(monkeypatch, "TavilySearch", result={"error": "Invalid API key"})
    with pytest.raises(tools.PermanentToolError):
        tools.tavily_search("q")


def test_empty_results_are_not_an_error(monkeypatch):
    _fake_tool(monkeypatch, "TavilySearch", result={"results": []})
    assert tools.tavily_search("q") == []


def test_results_are_normalised_and_clipped(monkeypatch):
    _fake_tool(monkeypatch, "TavilySearch", result={"results": [
        {"title": "T", "url": "https://a.com", "content": "x" * 10_000,
         "published_date": "2026-11-01", "score": 0.9}]})
    [r] = tools.tavily_search("q", topic="news", days=7)
    assert r["published"] == "2026-11-01"
    assert len(r["content"]) == tools.MAX_CONTENT_CHARS


def test_rfc1123_news_dates_are_normalised_to_iso(monkeypatch):
    _fake_tool(monkeypatch, "TavilySearch", result={"results": [
        {"title": "T", "url": "https://a.com", "content": "c",
         "published_date": "Tue, 03 Nov 2026 01:18:30 GMT"},
        {"title": "U", "url": "https://b.com", "content": "c", "published_date": "garbage"}]})
    a, b = tools.tavily_search("q", topic="news")
    assert a["published"] == "2026-11-03" and b["published"] is None


def test_extract_omits_failed_urls(monkeypatch):
    _fake_tool(monkeypatch, "TavilyExtract", result={
        "results": [{"url": "https://ok.com", "raw_content": "page"}],
        "failed_results": [{"url": "https://bad.com", "error": "blocked"}]})
    assert tools.tavily_extract(["https://ok.com", "https://bad.com"]) == [
        {"url": "https://ok.com", "raw_content": "page"}]


def test_extract_with_no_fetchable_page_is_an_empty_result_not_a_crash(monkeypatch):
    # Real SDK behaviour (seen for us.pg.com/pricing): when every URL fails it RETURNS this text.
    msg = "No extracted results found for '['https://us.pg.com/pricing']'. Suggestions: ..."
    _fake_tool(monkeypatch, "TavilyExtract", result=msg)
    assert tools.tavily_extract(["https://us.pg.com/pricing"]) == []


def test_other_text_returned_by_the_sdk_is_treated_as_an_error(monkeypatch):
    _fake_tool(monkeypatch, "TavilyExtract", result="429 Too Many Requests")
    with pytest.raises(tools.TransientToolError):
        tools.tavily_extract(["https://a.com"])
    _fake_tool(monkeypatch, "TavilySearch", result="Invalid API key")
    with pytest.raises(tools.PermanentToolError):
        tools.tavily_search("q")


@pytest.mark.parametrize("days,expected", [(None, None), (1, "day"), (7, "week"), (30, "month"), (90, "year")])
def test_days_map_to_tavily_time_range(days, expected):
    assert tools._days_to_range(days) == expected
