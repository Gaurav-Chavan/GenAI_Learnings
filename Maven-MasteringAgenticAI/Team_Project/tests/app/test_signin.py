"""The password-protected full app: the sign-in gate, the closed state, secrets reaching the code, and the files that get deployed."""
from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path

import pytest

from claim_triage.app.signin import GATE, SignInGate
from claim_triage.data.config import ROOT

ENTRY = ROOT / "deploy_full" / "streamlit_app.py"
PASSWORD = "correct-horse-battery"
FAKE_NEO4J = {"NEO4J_URI": "neo4j+s://fake-id.databases.neo4j.io", "NEO4J_USERNAME": "neo4j", "NEO4J_PASSWORD": "fake-graph-password-123", "NEO4J_DATABASE": "neo4j"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------- the gate ----------

def test_the_right_password_opens_and_a_wrong_one_does_not():
    gate = SignInGate()
    assert gate.attempt(PASSWORD, PASSWORD) == "ok" and gate.attempt("nope", PASSWORD) == "wrong"
    assert gate.attempt("", "") == "wrong" and gate.attempt("anything", "") == "wrong"  # no password configured: nothing matches


def test_eight_wrong_tries_pause_everyone_until_the_oldest_ages_out():
    clock = Clock()
    gate = SignInGate(max_failures=8, window_seconds=300, clock=clock)
    for _ in range(7):
        assert gate.attempt("x", PASSWORD) == "wrong"
        clock.now += 10
    assert gate.paused_for() == 0 and gate.attempt(PASSWORD, PASSWORD) == "ok"  # seven mistakes are forgiven
    assert gate.attempt("x", PASSWORD) == "wrong" and gate.paused_for() > 0  # the eighth pauses it
    assert gate.attempt(PASSWORD, PASSWORD) == "paused"  # even the right password is refused meanwhile, and the try is not counted
    clock.now += 100
    assert 0 < gate.paused_for() <= 200 and gate.attempt(PASSWORD, PASSWORD) == "paused"
    clock.now += 200  # the first wrong try is now more than 300 seconds old
    assert gate.paused_for() == 0 and gate.attempt(PASSWORD, PASSWORD) == "ok"


def test_reset_opens_the_gate_again():
    gate = SignInGate(max_failures=2)
    gate.attempt("x", PASSWORD), gate.attempt("y", PASSWORD)
    assert gate.paused_for() > 0
    gate.reset()
    assert gate.paused_for() == 0


# ---------- the app ----------

@pytest.fixture
def app(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    if not (ROOT / "demo_data").exists():
        pytest.skip("run `uv run python -m claim_triage.app.demo_data` to create demo_data/")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    for name in (*FAKE_NEO4J, "CLAIM_TRIAGE_DATA_DIR", "CLAIM_TRIAGE_DEMO", "CLAIM_TRIAGE_APP_DB"):
        monkeypatch.setenv(name, "x")  # registers the old state for restoring; the entry file sets what it needs
        monkeypatch.delenv(name)
    GATE.reset()

    def make(secrets: dict | None = None):
        at = AppTest.from_file(str(ENTRY), default_timeout=90)
        for key, value in (secrets or {}).items():
            at.secrets[key] = value
        return at

    yield make
    GATE.reset()


def sign_in(at, password: str):
    at.text_input[0].input(password)
    return at.button[0].click().run()


def test_without_a_password_secret_the_app_stays_closed(app):
    at = app({**FAKE_NEO4J}).run()  # the graph login is there, but no APP_PASSWORD
    assert not at.exception and any("closed" in e.value for e in at.error)
    assert not at.header and not at.text_input and "NEO4J_URI" not in os.environ and "CLAIM_TRIAGE_DEMO" not in os.environ


def test_a_wrong_password_is_refused_and_the_right_one_opens_the_full_dashboard(app):
    at = app({"APP_PASSWORD": PASSWORD, **FAKE_NEO4J}).run()
    assert not at.exception and at.text_input[0].proto.type == 1 and not at.header
    assert "NEO4J_URI" not in os.environ  # nothing is loaded before sign-in
    at = sign_in(at, "not the password")
    assert any("not right" in e.value for e in at.error) and not at.header and "NEO4J_URI" not in os.environ
    at = sign_in(at, PASSWORD)
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value == "Review queue" and at.metric[0].value == "380"
    assert not any("public demo" in i.value.lower() for i in at.sidebar.info)  # this is the full app, not the demo
    assert os.environ["CLAIM_TRIAGE_DEMO"] == "0" and all(os.environ[k] == v for k, v in FAKE_NEO4J.items())
    assert Path(at.session_state["app_db_path"]) == Path(tempfile.gettempdir()) / "claim_triage_full" / "app.duckdb"
    shown = repr(at.main) + repr(at.sidebar)
    assert PASSWORD not in shown and FAKE_NEO4J["NEO4J_PASSWORD"] not in shown  # no secret is ever drawn on the page


def test_the_full_app_turns_the_safety_check_on(app):
    at = sign_in(app({"APP_PASSWORD": PASSWORD, **FAKE_NEO4J}).run(), PASSWORD)
    at.session_state["page"] = "Knowledge base"
    at.run()
    assert not at.exception and not any("switched off" in i.value for i in at.info)


def test_eight_wrong_passwords_pause_sign_in_for_every_visitor(app):
    secrets = {"APP_PASSWORD": PASSWORD}
    at = app(secrets).run()
    for _ in range(8):
        at = sign_in(at, "guess")
    assert GATE.paused_for() > 0
    other = app(secrets).run()  # another visitor, even with the right password
    assert any("paused" in w.value for w in other.warning) and not other.text_input and not other.header
    GATE.reset()
    assert sign_in(app(secrets).run(), PASSWORD).header[0].value == "Review queue"


# ---------- what gets deployed ----------

def test_the_deployed_files_hold_no_secret_and_need_no_model_library():
    reqs = (ROOT / "deploy_full" / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "streamlit" in reqs and "neo4j" in reqs and "duckdb" in reqs
    assert not any(lib in reqs for lib in ("litellm", "anthropic", "openai", "faker", "mcp"))  # the dashboard never calls a language model
    example = tomllib.loads((ROOT / "deploy_full" / "secrets.example.toml").read_text(encoding="utf-8"))
    assert set(example) == {"APP_PASSWORD", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"}
    assert "<" in example["NEO4J_PASSWORD"] and "<" in example["NEO4J_URI"]  # placeholders only
    assert {p.name for p in (ROOT / "deploy_full").iterdir()} == {"streamlit_app.py", "requirements.txt", "secrets.example.toml"}
    text = ENTRY.read_text(encoding="utf-8")
    assert not any(s in text for s in ('["vault"]', '["labels"]', "vault.duckdb", "labels.duckdb", "gold.duckdb", "API_Keys", "Neo4j-", "print("))
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "secrets.toml" in ignore  # a real secrets file can never be committed by accident
