from datetime import date

from src.competitor_agent.critic import review, review_profile

TODAY = date(2026, 11, 5)


def fact(value="v", status="confirmed", url="https://x.com", as_of=None):
    return {"value": value, "source_url": url if value else None, "as_of": as_of, "status": status}


def profile(**over):
    p = {
        "name": "X",
        "pricing": fact("Free $0"),
        "positioning": fact("Editor"),
        "features": [fact(f"f{i}") for i in range(3)],
        "news": [{"headline": "h", "date": "2026-11-01", "source_url": "https://n.com",
                  "status": "reported"}],
        "notes": [],
    }
    p.update(over)
    return p


def test_a_complete_profile_has_no_gaps_or_warnings():
    assert review_profile(profile(), TODAY) == ([], [])


def test_missing_or_unverified_pricing_is_a_gap():
    for status in ("not_found", "unverified"):
        gaps, _ = review_profile(profile(pricing=fact(None, status)), TODAY)
        assert "pricing" in gaps


def test_third_party_pricing_is_a_gap_and_a_warning():
    gaps, warnings = review_profile(profile(pricing=fact("$20", "reported")), TODAY)
    assert "pricing" in gaps and any("third-party" in w for w in warnings)


def test_third_party_positioning_is_acceptable():
    assert review_profile(profile(positioning=fact("x", "reported")), TODAY)[0] == []


def test_too_few_features_is_a_gap():
    gaps, _ = review_profile(profile(features=[fact("only one")]), TODAY)
    assert gaps == ["features"]


def test_no_news_and_stale_news_are_gaps():
    assert review_profile(profile(news=[]), TODAY)[0] == ["news"]
    old = [{"headline": "h", "date": "2026-06-01", "source_url": "u", "status": "reported"}]
    gaps, warnings = review_profile(profile(news=old), TODAY)
    assert gaps == ["news"] and "157 days" in warnings[0]


def test_undated_news_does_not_count_as_fresh():
    undated = [{"headline": "h", "date": None, "source_url": "u", "status": "unverified"}]
    assert review_profile(profile(news=undated), TODAY)[0] == ["news"]


def test_source_conflicts_become_a_warning_not_a_gap():
    gaps, warnings = review_profile(profile(notes=["blog says $20, official says $8"]), TODAY)
    assert gaps == [] and "disagreed" in warnings[0]


def test_failed_analysis_flags_everything():
    gaps, _ = review_profile(profile(notes=["Analysis failed: timeout"]), TODAY)
    assert set(gaps) == {"pricing", "positioning", "features", "news"}


def test_review_only_lists_competitors_with_findings():
    gaps, warnings = review({"A": profile(), "B": profile(news=[])}, TODAY)
    assert list(gaps) == ["B"] and warnings == {}
