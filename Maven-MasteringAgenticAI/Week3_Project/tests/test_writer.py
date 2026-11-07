"""Writer guardrails with a scripted fake LLM (no network)."""
from types import SimpleNamespace

from src.competitor_agent.writer import (
    ComparisonRow, Narrative, assemble, draft, facts_text, known_urls, revise, ungrounded_money,
)


def fact(value, status="confirmed", url="https://a.com/pricing", as_of="2026-11-05"):
    return {"value": value, "source_url": url if value else None, "as_of": as_of, "status": status}


PROFILES = {
    "Alpha": {
        "name": "Alpha",
        "pricing": fact("Free $0; Pro $20/mo"),
        "positioning": fact("Editor for pros", "reported", "https://blog.com/alpha", None),
        "features": [fact("Agents", url="https://a.com/f"), fact("Chat", url="https://a.com/f")],
        "news": [{"headline": "Raised funding", "date": "2026-11-01",
                  "source_url": "https://news.com/1", "status": "reported"}],
        "notes": ["Blog says $25; official page says $20"],
    },
    "Beta": {
        "name": "Beta", "pricing": fact(None, "not_found"), "positioning": fact("CLI tool"),
        "features": [], "news": [], "notes": [],
    },
}
STATE = {"company": "Cursor", "profiles": PROFILES, "gaps": {"Beta": ["pricing"]},
         "warnings": {"Beta": ["Latest news item is 99 days old."]}, "retries": 2,
         "resolved_company": {"name": "Cursor", "description": "AI editor by Anysphere"}}


class FakeLLM:
    def __init__(self, result):
        self.result, self.messages = result, None

    def with_structured_output(self, _schema, **kwargs):
        assert kwargs.get("method") == "json_schema"
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.result


def narrative(summary=("Alpha costs $20/mo.",), pricing="Free; $20/mo"):
    return Narrative(
        executive_summary=list(summary),
        comparison=[ComparisonRow(name="Alpha", pricing=pricing, positioning="Pro editor",
                                  best_for="Teams"),
                    ComparisonRow(name="Beta", pricing="Not found", positioning="CLI", best_for="Devs"),
                    ComparisonRow(name="Ghost", pricing="x", positioning="x", best_for="x")],
        implications=["Price against $20/mo."],
    )


def test_ungrounded_money_detects_invented_figures():
    assert ungrounded_money("costs $20 or $99", "Pro $20/mo") == {"$99"}
    assert ungrounded_money("costs $ 20", "Pro $20/mo") == set()


def test_a_comma_or_full_stop_after_a_figure_is_not_part_of_the_figure():
    # Seen live: "Free $0, Pro $20" produced the figure "$0," and a valid line was dropped.
    assert ungrounded_money("Free $0, Pro $20, Max $200.", "Free $0; Pro $20/mo; Max $200/mo") == set()
    assert ungrounded_money("costs $1,500 a year", "Plan $1,500/yr") == set()
    assert ungrounded_money("costs $1,500, or $99", "Plan $1,500/yr") == {"$99"}
    assert ungrounded_money("about $4.50.", "price $4.50 per month") == set()


def test_draft_keeps_grounded_lines_and_drops_invented_figures():
    llm = FakeLLM(narrative(summary=("Alpha costs $20/mo.", "Beta costs $5/mo.")))
    md, note = draft(STATE, llm, today="2026-11-05")
    assert "Alpha costs $20/mo." in md and "Beta costs $5/mo." not in md
    assert "1 unsupported line(s) dropped" in note


def test_invented_figure_in_a_table_cell_is_replaced():
    md, note = draft(STATE, FakeLLM(narrative(pricing="$999/mo")), today="2026-11-05")
    assert "$999" not in md and "$999" in note.replace("'$999'", "$999")  # named in the log
    assert "| Alpha | Free $0; Pro $20/mo |" in md  # falls back to the real price text


def test_fallback_table_cells_are_cut_at_a_plan_boundary_not_mid_word():
    from src.competitor_agent.writer import _short
    plans = "Free $0; Student $0; Pro $10/mo; Pro+ $39/mo; Max $100/mo; Business $19/user/mo; Enterprise $39/user/mo"
    out = _short(plans, 60)
    assert out == "Free $0; Student $0; Pro $10/mo; Pro+ $39/mo; Max $100/mo …"
    assert _short("short text", 60) == "short text"
    assert _short("one two three four five six seven eight nine ten", 20) == "one two three four …"
    assert _short("x" * 50, 20) == "x" * 20 + " …"  # no separator at all


def test_unknown_competitor_rows_are_ignored():
    md, _ = draft(STATE, FakeLLM(narrative()), today="2026-11-05")
    assert "Ghost" not in md


def test_assemble_renders_facts_with_labels_links_and_sources():
    md = assemble(STATE, None, today="2026-11-05")
    assert "**Pricing** (official source, as of 2026-11-05, [source](https://a.com/pricing))" in md
    assert "**Positioning** (third-party source, [source](https://blog.com/alpha))" in md
    assert "**Pricing:** not found" in md
    assert "- 2026-11-01: Raised funding ([source](https://news.com/1))" in md
    assert "Blog says $25; official page says $20" in md
    assert "Beta: could not fully confirm **pricing** after 2 retry round(s)." in md
    assert "Beta: Latest news item is 99 days old." in md
    sources = md.split("## Sources")[1]  # listed in order of first citation, de-duplicated
    assert sources.count("https://a.com/pricing") == 1
    assert "https://blog.com/alpha" in sources and "https://news.com/1" in sources


def test_llm_failure_still_produces_a_facts_only_briefing():
    class Boom:
        def with_structured_output(self, *a, **k):
            raise RuntimeError("api down")

    md, note = draft(STATE, Boom(), today="2026-11-05")
    assert "narrative failed" in note and "## Competitor profiles" in md
    assert "## Executive summary" not in md


def test_facts_text_labels_every_fact_and_known_urls_collects_citations():
    t = facts_text(PROFILES)
    assert "pricing [official source, as of 2026-11-05]" in t and "pricing [not found]" in t
    assert known_urls(PROFILES) >= {"https://a.com/pricing", "https://news.com/1"}


def _state_with_briefing(feedback="shorter"):
    md = assemble(STATE, None, today="2026-11-05")
    return {**STATE, "briefing_md": md, "feedback": feedback}, md


def test_revision_is_applied_when_it_adds_nothing_new():
    state, md = _state_with_briefing()
    llm = FakeLLM(SimpleNamespace(content=[{"type": "text", "text": "# Shorter\nPro is $20/mo"}]))
    out, note = revise(state, llm)
    assert out.startswith("# Shorter") and note == "writer: revision applied"


def test_revision_with_an_invented_figure_is_rejected_and_old_text_kept():
    state, md = _state_with_briefing()
    out, note = revise(state, FakeLLM(SimpleNamespace(content="Pro costs $49")))
    assert out.startswith(md.strip()) and "Revision not applied" in out and "REJECTED" in note


def test_revision_with_a_new_url_is_rejected():
    state, md = _state_with_briefing()
    out, note = revise(state, FakeLLM(SimpleNamespace(content="see https://evil.example/x")))
    assert "REJECTED" in note and "https://evil.example/x" in note
