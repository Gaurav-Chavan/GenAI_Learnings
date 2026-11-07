"""Critic: deterministic quality review of every competitor profile.

It separates two things:
  gaps      fields worth ANOTHER search round (the orchestrator decides whether to retry)
  warnings  things a retry cannot fix; they are shown to the human in the briefing

Gap keys match what Gather knows how to search for: pricing, positioning, features, news.
Rules are plain code on purpose: cheap, testable and identical on every run. The judgement
call (retry or accept?) belongs to the orchestrator, not here.
"""
from datetime import date

MIN_FEATURES = 3
MAX_NEWS_AGE_DAYS = 60
ANALYSIS_FAILED = "Analysis failed"


def review_profile(p: dict, today: date) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    warnings: list[str] = []

    if any(n.startswith(ANALYSIS_FAILED) for n in p.get("notes", [])):
        return ["pricing", "positioning", "features", "news"], ["The analysis step failed."]

    pricing = p["pricing"]
    if pricing["status"] in ("not_found", "unverified"):
        gaps.append("pricing")
    elif pricing["status"] == "reported":
        gaps.append("pricing")  # worth one more try at the official page...
        warnings.append("Pricing comes from a third-party source, not the company's own site.")

    if p["positioning"]["status"] in ("not_found", "unverified"):
        gaps.append("positioning")

    if len(p["features"]) < MIN_FEATURES:
        gaps.append("features")

    dated = [n["date"] for n in p["news"] if n.get("date")]
    if not dated:
        gaps.append("news")
    else:
        age = (today - date.fromisoformat(max(dated))).days
        if age > MAX_NEWS_AGE_DAYS:
            gaps.append("news")
            warnings.append(f"Latest news item is {age} days old.")

    if p.get("notes"):
        warnings.append("Sources disagreed on at least one fact; see the notes.")
    return gaps, warnings


def review(profiles: dict[str, dict], today: date | None = None
           ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    today = today or date.today()
    gaps: dict[str, list[str]] = {}
    warnings: dict[str, list[str]] = {}
    for name, p in profiles.items():
        g, w = review_profile(p, today)
        if g:
            gaps[name] = g
        if w:
            warnings[name] = w
    return gaps, warnings
