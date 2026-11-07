"""Skeleton smoke tests: the control flow works end to end on stub data."""
import uuid

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.competitor_agent.graph import build_graph


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_STUBS", "true")
    monkeypatch.setenv("BRIEFINGS_DIR", str(tmp_path))


def _start(company="Cursor"):
    graph = build_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    return graph, cfg, graph.invoke({"company": company, "last_step": "start"}, cfg)


def test_pauses_at_competitor_review_first():
    _, _, result = _start()
    assert result["__interrupt__"][0].value["checkpoint"] == "approve_competitors"
    assert len(result["competitors"]) == 3


def test_full_run_retries_gap_then_saves_after_approval(tmp_path):
    graph, cfg, result = _start()
    result = graph.invoke(Command(resume={"approved": True}), cfg)  # approve competitors
    assert result["__interrupt__"][0].value["checkpoint"] == "approve_briefing"
    assert "not found" not in result["briefing_md"]  # gap was retried and filled
    assert result["retries"] == 1
    assert not result.get("saved_path")  # nothing written before approval

    result = graph.invoke(Command(resume={"approved": True}), cfg)
    assert list(tmp_path.glob("cursor-*.md"))
    assert result["saved_path"]


def test_rejection_without_feedback_saves_nothing(tmp_path):
    graph, cfg, _ = _start()
    graph.invoke(Command(resume={"approved": True}), cfg)
    result = graph.invoke(Command(resume={"approved": False}), cfg)
    assert not result.get("saved_path")
    assert not list(tmp_path.glob("*.md"))


def test_rejection_with_feedback_revises_then_asks_again():
    graph, cfg, _ = _start()
    graph.invoke(Command(resume={"approved": True}), cfg)
    result = graph.invoke(Command(resume={"approved": False, "feedback": "shorter"}), cfg)
    assert result["__interrupt__"][0].value["checkpoint"] == "approve_briefing"
    assert "Revised per feedback: shorter" in result["briefing_md"]
