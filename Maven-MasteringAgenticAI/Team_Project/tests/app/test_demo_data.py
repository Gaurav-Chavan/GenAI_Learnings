"""The public demo: a safe data snapshot, a demo switch that stops model calls, and an entry file that works without any keys."""
from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import pytest

from claim_triage.app.claim_view import record_decision
from claim_triage.app.kb import NOT_LEARNABLE
from claim_triage.app.demo_data import DEMO_FILES, build_demo_data
from claim_triage.data.config import ROOT, paths

DASHBOARD = str(ROOT / "src" / "claim_triage" / "app" / "dashboard.py")
ENTRY = ROOT / "deploy" / "streamlit_app.py"


def _count(path: Path, table: str) -> int:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


# ---------- the snapshot ----------

def test_the_snapshot_holds_only_the_safe_layer_and_the_assessments(filled, tmp_path):
    cfg, store, ids, _ = filled
    cid = ids[-7]
    record_decision(store, cid, "rev-1", "escalate", "red", "second opinion", "red", escalate_to="senior-1")  # reviewer work that must not be copied
    store.add_signal(cid, "BILL_OUTLIER", "text", [], "rev-1", "a note long enough to count", disagreed=False)
    out = build_demo_data(cfg, tmp_path / "demo", source=store)
    assert sorted(p.name for p in (tmp_path / "demo").iterdir()) == sorted(DEMO_FILES) and [p.name for p in out] == list(DEMO_FILES)
    app = tmp_path / "demo" / "app.duckdb"
    assert _count(app, "assessments") == sum(store.count_assessments().values()) > 0
    assert all(_count(app, t) == 0 for t in ("decisions", "signals", "assignments"))  # the demo starts with no decisions, lessons or owners
    silver = tmp_path / "demo" / "silver.duckdb"
    assert _count(silver, "claims") == _count(paths(cfg)["silver"], "claims")
    con = duckdb.connect(str(silver), read_only=True)
    try:
        tables = {t for (t,) in con.execute("SHOW TABLES").fetchall()}
    finally:
        con.close()
    assert not tables & {"labels", "vault", "identities", "claim_labels"}  # no identity data and no answer keys


def test_rebuilding_replaces_the_old_snapshot(filled, tmp_path):
    cfg, store, *_ = filled
    for _ in range(2):
        build_demo_data(cfg, tmp_path / "demo", source=store)
    assert sorted(p.name for p in (tmp_path / "demo").iterdir()) == sorted(DEMO_FILES)


def test_the_committed_snapshot_is_exactly_the_two_safe_files():
    folder = ROOT / "demo_data"
    if not folder.exists():
        pytest.skip("run `uv run python -m claim_triage.app.demo_data` to create demo_data/")
    assert sorted(p.name for p in folder.iterdir()) == sorted(DEMO_FILES)
    assert all(_count(folder / "app.duckdb", t) == 0 for t in ("decisions", "signals", "assignments"))


# ---------- the demo switch ----------

def test_demo_mode_switches_off_the_safety_check_and_says_so(filled, monkeypatch):
    from streamlit.testing.v1 import AppTest

    cfg, store, ids, _ = filled
    cid = next(c for c in ids if any(i["code"] not in NOT_LEARNABLE for i in store.get_assessment(c)["result"]["indicators"]) and not store.decisions_for(c))
    band = store.get_assessment(cid)["band"]
    record_decision(store, cid, "rev-1", "agree", band, "", band)  # so that the knowledge-base page has a finding to offer
    monkeypatch.setenv("CLAIM_TRIAGE_DATA_DIR", cfg["data_dir"])
    monkeypatch.setenv("CLAIM_TRIAGE_APP_DB", str(store.path))
    monkeypatch.setenv("CLAIM_TRIAGE_DEMO", "1")
    at = AppTest.from_file(DASHBOARD, default_timeout=60)
    at.session_state["page"] = "Knowledge base"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("switched off in the public demo" in i.value for i in at.info) and any("Public demo" in i.value for i in at.sidebar.info)
    update = [b for b in at.button if b.label == "Update knowledge base"]
    assert update and all(b.disabled for b in update)
    monkeypatch.setenv("CLAIM_TRIAGE_DEMO", "0")  # the normal app is unchanged
    normal = AppTest.from_file(DASHBOARD, default_timeout=60)
    normal.session_state["page"] = "Knowledge base"
    normal.run()
    assert not normal.exception and not any("public demo" in i.value for i in normal.info)


# ---------- the entry file ----------

def test_the_entry_file_runs_on_the_bundled_data_with_no_keys(tmp_path, monkeypatch):
    if not (ROOT / "demo_data").exists():
        pytest.skip("run `uv run python -m claim_triage.app.demo_data` to create demo_data/")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    for name in ("CLAIM_TRIAGE_DATA_DIR", "CLAIM_TRIAGE_DEMO", "CLAIM_TRIAGE_APP_DB", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "")  # registers the old value for restoring; the entry file sets what it needs
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("CLAIM_TRIAGE_APP_DB")
    at = AppTest.from_file(str(ENTRY), default_timeout=90).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value == "Review queue" and [m.label for m in at.metric][:2] == ["Claims in view", "Not yet reviewed"]
    assert at.metric[0].value == "380" and any("Public demo" in i.value for i in at.sidebar.info)
    mine = Path(at.session_state["app_db_path"])
    assert mine.parent == tmp_path / "claim_triage_demo" and mine.exists()
    other = AppTest.from_file(str(ENTRY), default_timeout=90).run()  # a second visitor gets a different private copy
    assert other.session_state["app_db_path"] != at.session_state["app_db_path"]


def test_the_entry_file_and_its_requirements_stay_small_and_safe():
    text = ENTRY.read_text(encoding="utf-8")
    assert not any(s in text for s in ('["vault"]', '["labels"]', "vault.duckdb", "labels.duckdb", "gold.duckdb", "API_Keys", "Neo4j-"))
    reqs = (ENTRY.parent / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "streamlit" in reqs and "duckdb" in reqs
    assert not any(heavy in reqs for heavy in ("litellm", "neo4j", "anthropic", "openai", "faker", "mcp"))  # the demo never calls a model
    assert {p.name for p in ENTRY.parent.iterdir()} <= {"README.md", "requirements.txt", "streamlit_app.py"}  # nothing else rides along with the deployed entry
