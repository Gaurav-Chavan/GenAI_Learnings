"""UI controller on stub data: phases, resume rules and recovery after a failure."""
import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.competitor_agent import nodes
from src.competitor_agent.graph import build_graph
from src.competitor_agent.session import ResearchSession


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_STUBS", "true")
    monkeypatch.setenv("BRIEFINGS_DIR", str(tmp_path))


def new_session():
    return ResearchSession(build_graph(MemorySaver()))


def run(events):
    return list(events)


def test_new_session_has_nothing_to_show():
    assert new_session().snapshot().phase == "new"


def test_phases_follow_the_two_human_checkpoints(tmp_path):
    s = new_session()
    events = run(s.start("Cursor"))
    assert "discovery" in [e.node for e in events]
    snap = s.snapshot()
    assert snap.phase == "awaiting_competitors"
    assert len(snap.checkpoint["competitors"]) == 3

    run(s.resume({"approved": True}))
    snap = s.snapshot()
    assert snap.phase == "awaiting_briefing" and snap.checkpoint["briefing_md"]
    assert not snap.values.get("saved_path")  # nothing written before approval

    run(s.resume({"approved": True}))
    snap = s.snapshot()
    assert snap.phase == "done" and snap.values["saved_path"]
    assert list(tmp_path.glob("cursor-*.md"))


def test_edited_competitor_list_is_what_gets_researched():
    s = new_session()
    run(s.start("Cursor"))
    edited = [{"name": "Only One", "url": "https://one.example.com", "rationale": "mine"}]
    run(s.resume({"approved": True, "competitors": edited}))
    assert list(s.snapshot().values["profiles"]) == ["Only One"]


def test_events_carry_the_log_lines_for_the_progress_view():
    s = new_session()
    lines = [line for e in s.start("Cursor") for line in e.lines]
    assert any(line.startswith("discovery") for line in lines)


def test_rejecting_without_feedback_ends_the_run_without_saving(tmp_path):
    s = new_session()
    run(s.start("Cursor"))
    run(s.resume({"approved": True}))
    run(s.resume({"approved": False}))
    snap = s.snapshot()
    assert snap.phase == "done" and not snap.values.get("saved_path")
    assert not list(tmp_path.glob("*.md"))


def test_feedback_loops_back_to_the_briefing_checkpoint():
    s = new_session()
    run(s.start("Cursor"))
    run(s.resume({"approved": True}))
    run(s.resume({"approved": False, "feedback": "shorter"}))
    assert s.snapshot().phase == "awaiting_briefing"


def test_empty_resume_payload_is_refused():
    s = new_session()
    run(s.start("Cursor"))
    with pytest.raises(ValueError):
        s.resume({})


def test_a_failed_node_leaves_a_resumable_checkpoint(monkeypatch):
    calls = {"n": 0}
    real = nodes.critic

    def flaky(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("critic exploded")
        return real(state)

    monkeypatch.setattr(nodes, "critic", flaky)
    s = new_session()
    run(s.start("Cursor"))
    with pytest.raises(RuntimeError, match="critic exploded"):
        run(s.resume({"approved": True}))
    snap = s.snapshot()
    assert snap.phase == "failed" and "critic exploded" in (snap.error or "")
    assert snap.values["profiles"]  # earlier work survived

    run(s.retry())  # continues from the checkpoint, does not start over
    assert s.snapshot().phase == "awaiting_briefing"
