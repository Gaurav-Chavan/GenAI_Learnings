"""Discovery logic with a scripted fake LLM (no network, no API keys)."""
from src.competitor_agent.discovery import DiscoveryResult, ResolvedCompany, discover, validate
from src.competitor_agent.state import Competitor

RESULTS = [
    {"title": "Best Acme alternatives", "url": "https://a.com/1",
     "content": "Top rivals to Acme include Globex, Initech and Umbrella Corp."},
    {"title": "Acme overview", "url": "https://a.com/2", "content": "Acme makes anvils."},
]


def result(*names, resolved="Acme", note="", site="https://a.com"):
    return DiscoveryResult(
        company=ResolvedCompany(name=resolved, description="anvils", ambiguity_note=note),
        competitors=[Competitor(name=n, url=site, rationale="r", evidence_urls=["https://a.com/1"])
                     for n in names],
    )


class FakeLLM:
    """Returns scripted DiscoveryResults, one per call; records how many calls were made."""

    def __init__(self, *scripted):
        self.scripted, self.calls = list(scripted), 0

    def with_structured_output(self, _schema, **kwargs):
        assert kwargs.get("method") == "json_schema"  # guards the Sonnet 5.5 requirement
        return self

    def invoke(self, _messages):
        self.calls += 1
        return self.scripted.pop(0)


def test_valid_list_passes_with_no_repair():
    llm = FakeLLM(result("Globex", "Initech", "Umbrella Corp"))
    resolved, comps, note = discover("acme", "def", RESULTS, llm, 3)
    assert [c.name for c in comps] == ["Globex", "Initech", "Umbrella Corp"]
    assert llm.calls == 1 and note == ""


def test_self_duplicate_and_hallucinated_names_are_rejected():
    r = result("Acme", "Globex", "globex", "Hooli Quantum")
    comps, problems = validate(r, "Acme", RESULTS, 3)
    assert [c.name for c in comps] == ["Globex"]
    joined = " ".join(problems)
    assert "itself" in joined and "more than once" in joined and "evidence" in joined
    assert "only 1 valid" in joined


def test_repair_attempt_fixes_the_list():
    llm = FakeLLM(result("Acme", "Globex", "Initech"),
                  result("Globex", "Initech", "Umbrella Corp"))
    _, comps, note = discover("Acme", "def", RESULTS, llm, 3)
    assert llm.calls == 2
    assert len(comps) == 3 and note == ""


def test_still_short_after_repair_returns_partial_with_note_for_the_human():
    llm = FakeLLM(result("Globex", "Hooli Quantum"), result("Globex", "Hooli Quantum"))
    _, comps, note = discover("Acme", "def", RESULTS, llm, 3)
    assert llm.calls == 2  # exactly one repair, no loop
    assert [c.name for c in comps] == ["Globex"]
    assert note.startswith("Please review")


def test_ambiguity_note_is_surfaced():
    llm = FakeLLM(result("Globex", "Initech", "Umbrella Corp", note="Also a cartoon brand"))
    _, _, note = discover("Acme", "def", RESULTS, llm, 3)
    assert "Also a cartoon brand" in note


def test_official_website_is_normalised_to_its_origin():
    from src.competitor_agent.discovery import clean_website
    assert clean_website("https://www.claude.com/pricing?x=1") == "https://claude.com"
    assert clean_website("claude.com") == "https://claude.com"  # scheme added


def test_malformed_or_low_value_websites_are_dropped():
    from src.competitor_agent.discovery import clean_website
    assert clean_website(None) is None and clean_website("  ") is None
    assert clean_website("javascript:alert(1)") is None
    assert clean_website("localhost") is None
    assert clean_website("https://www.reddit.com/r/cursor") is None


def test_website_is_kept_even_if_the_evidence_never_mentions_the_domain():
    # discovery evidence is roundups about the target; verification happens later, in Gather
    llm = FakeLLM(result("Globex", "Initech", "Umbrella Corp", site="https://globex-corp.com"))
    _, comps, note = discover("Acme", "def", RESULTS, llm, 3)
    assert all(c.url == "https://globex-corp.com" for c in comps) and note == ""


def test_note_lists_competitors_without_a_proposed_website():
    llm = FakeLLM(result("Globex", "Initech", "Umbrella Corp", site=None))
    _, comps, note = discover("Acme", "def", RESULTS, llm, 3)
    assert "No official website proposed for Globex, Initech, Umbrella Corp" in note


def test_evidence_urls_not_in_search_results_are_dropped():
    r = result("Globex", "Initech", "Umbrella Corp")
    r.competitors[0].evidence_urls = ["https://made-up.com", "https://a.com/2"]
    comps, _ = validate(r, "Acme", RESULTS, 3)
    assert comps[0].evidence_urls == ["https://a.com/2"]
