"""The Streamlit page, driven headlessly on stub data: both checkpoints and the save."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_STUBS", "true")
    monkeypatch.setenv("BRIEFINGS_DIR", str(tmp_path / "briefings"))
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "ck.sqlite"))
    st.cache_resource.clear()  # each test gets its own graph and database


def click(at: AppTest, label: str) -> AppTest:
    next(b for b in at.button if b.label == label).click()
    return at.run()


def headings(at: AppTest) -> list[str]:
    return [s.value for s in at.subheader]


def test_run_goes_through_both_checkpoints_and_saves(tmp_path):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception and "Stub mode" in at.sidebar.warning[0].value

    at = click(at, "Run analysis")
    assert not at.exception
    assert headings(at) == ["Checkpoint 1 of 2: confirm the competitors"]
    assert not (tmp_path / "briefings").exists()

    at = click(at, "Approve and research")
    assert headings(at) == ["Checkpoint 2 of 2: review the briefing"]
    assert not (tmp_path / "briefings").exists()  # nothing written before approval

    at = click(at, "Approve and save")
    assert not at.exception
    assert "Briefing saved" in at.success[0].value
    assert list((tmp_path / "briefings").glob("cursor-*.md"))


def test_dollar_signs_are_escaped_so_prices_do_not_render_as_latex():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = click(at, "Run analysis")
    at = click(at, "Approve and research")
    shown = " ".join(m.value for m in at.markdown)
    assert "\\$20/mo" in shown and "$20/mo" not in shown.replace("\\$20/mo", "")


def test_failed_step_shows_the_error_and_retry_continues(monkeypatch):
    from src.competitor_agent import nodes
    real, calls = nodes.critic, {"n": 0}

    def flaky(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("critic exploded")
        return real(state)

    monkeypatch.setattr(nodes, "critic", flaky)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = click(at, "Run analysis")
    at = click(at, "Approve and research")
    assert any("critic exploded" in e.value for e in at.error)
    assert any(b.label == "Retry from the last checkpoint" for b in at.button)

    at = click(at, "Retry from the last checkpoint")
    assert headings(at) == ["Checkpoint 2 of 2: review the briefing"]


def test_discard_saves_nothing(tmp_path):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = click(at, "Run analysis")
    at = click(at, "Approve and research")
    at = click(at, "Discard, save nothing")
    assert "nothing was saved" in at.info[0].value
    assert not (tmp_path / "briefings").exists()


def test_revise_needs_feedback_text():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at = click(at, "Run analysis")
    at = click(at, "Approve and research")
    revise = next(b for b in at.button if b.label == "Revise")
    assert revise.disabled
    at.text_area[0].input("Shorter please").run()
    assert not next(b for b in at.button if b.label == "Revise").disabled
    at = click(at, "Revise")
    assert headings(at) == ["Checkpoint 2 of 2: review the briefing"]
    assert any("Revised per feedback: Shorter please" in m.value for m in at.markdown)
