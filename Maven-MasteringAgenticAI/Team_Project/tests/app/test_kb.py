"""The Knowledge Base tab: only decided claims offer findings, notes are required, contradicting the system needs more, nothing is learned silently."""
from __future__ import annotations

import json

import pytest

from claim_triage.app.claim_view import record_decision
from claim_triage.app.kb import MIN_NOTE, MIN_NOTE_DISAGREED, NOT_LEARNABLE, KnowledgeError, candidate_signals, signal_rows, submit_signals

NOTE = "A bill this far above peers needs the discharge summary read before anything else."
LONG_NOTE = "I overrode the system because the discharge summary documents an ICU stay that explains the cost, so this finding alone should not mean fraud."


@pytest.fixture(scope="module")
def learnable(filled):
    """Claims whose rules-only result has at least one finding that can be learned from."""
    _, store, ids, _ = filled
    out = []
    for cid in ids:
        a = store.get_assessment(cid)
        codes = [i["code"] for i in a["result"]["indicators"] if i["code"] not in NOT_LEARNABLE]
        if codes:
            out.append((cid, a["band"], codes))
        if len(out) == 6:
            break
    assert len(out) == 6
    return out


def test_nothing_is_offered_before_a_decision_and_a_hold_is_not_a_decision(filled, learnable):
    _, store, _, _ = filled
    assert candidate_signals(store) == []
    cid, band, _ = learnable[0]
    record_decision(store, cid, "rev-1", "hold", band, "waiting for a document", band)
    assert candidate_signals(store) == []


def test_a_decided_claim_offers_its_learnable_findings_only(filled, learnable):
    _, store, _, _ = filled
    cid, band, codes = learnable[1]
    record_decision(store, cid, "rev-1", "agree", band, "", band)
    mine = [c for c in candidate_signals(store) if c["claim_id"] == cid]
    assert sorted(c["code"] for c in mine) == sorted(codes) and all(c["status"] == "new" and not c["disagreed"] for c in mine)
    assert not {c["code"] for c in candidate_signals(store)} & NOT_LEARNABLE  # area and cover are recalculated for every claim
    c = mine[0]
    assert c["finding"] and c["text"] and c["evidence_ids"] and c["final_band"] == band and c["decision_by"] == "rev-1"


def test_a_note_is_required_and_everything_is_checked_before_anything_is_saved(filled, learnable):
    _, store, _, _ = filled
    cid = learnable[1][0]
    mine = [c for c in candidate_signals(store) if c["claim_id"] == cid]
    before = len(signal_rows(store))
    with pytest.raises(KnowledgeError, match="reviewer name"):
        submit_signals(store, " ", [dict(key=mine[0]["key"], note=NOTE)])
    with pytest.raises(KnowledgeError, match="Tick at least one"):
        submit_signals(store, "rev-1", [])
    with pytest.raises(KnowledgeError, match=f"at least {MIN_NOTE} characters"):
        submit_signals(store, "rev-1", [dict(key=mine[0]["key"], note="too short")])
    with pytest.raises(KnowledgeError, match="no decision yet"):
        submit_signals(store, "rev-1", [dict(key="CLM-999999|DUPLICATE_CLAIM", note=NOTE)])
    # one good pick and one bad pick: nothing is saved
    bad = dict(key=mine[-1]["key"], note="x")
    if len(mine) > 1:
        with pytest.raises(KnowledgeError):
            submit_signals(store, "rev-1", [dict(key=mine[0]["key"], note=NOTE), bad])
    assert len(signal_rows(store)) == before


def test_ticked_signals_are_saved_as_pending_and_cannot_be_proposed_twice(filled, learnable):
    _, store, _, _ = filled
    cid = learnable[1][0]
    mine = [c for c in candidate_signals(store) if c["claim_id"] == cid]
    ids = submit_signals(store, "rev-1", [dict(key=c["key"], note=NOTE) for c in mine])
    assert len(ids) == len(mine)
    rows = [r for r in signal_rows(store) if r["source_claim_id"] == cid]
    assert {r["status"] for r in rows} == {"pending"} and all(r["reviewer"] == "rev-1" and r["note"] == NOTE and not r["disagreed"] for r in rows)
    assert {c["status"] for c in candidate_signals(store) if c["claim_id"] == cid} == {"pending"}
    with pytest.raises(KnowledgeError, match="already waiting for the safety check"):
        submit_signals(store, "rev-1", [dict(key=mine[0]["key"], note=NOTE)])
    # an active signal is "already in the knowledge base"; a blocked one may be proposed again
    store.con.execute("UPDATE signals SET status = 'active' WHERE signal_id = ?", [ids[0]])
    with pytest.raises(KnowledgeError, match="already in the knowledge base"):
        submit_signals(store, "rev-1", [dict(key=mine[0]["key"], note=NOTE)])
    store.con.execute("UPDATE signals SET status = 'blocked' WHERE signal_id = ?", [ids[0]])
    assert [c["status"] for c in candidate_signals(store) if c["key"] == mine[0]["key"]] == ["blocked"]
    assert len(submit_signals(store, "rev-2", [dict(key=mine[0]["key"], note=NOTE)])) == 1


def test_contradicting_the_system_needs_a_longer_note_and_leaves_an_audit_tag(filled, learnable):
    _, store, _, _ = filled
    cid, band, codes = learnable[2]
    other = "green" if band != "green" else "amber"
    record_decision(store, cid, "rev-3", "override", other, "documents explain it", band)
    mine = [c for c in candidate_signals(store) if c["claim_id"] == cid]
    assert all(c["disagreed"] and c["final_band"] == other for c in mine)
    with pytest.raises(KnowledgeError, match=f"at least {MIN_NOTE_DISAGREED} characters.*contradicted the system"):
        submit_signals(store, "rev-3", [dict(key=mine[0]["key"], note=NOTE[:40])])
    submit_signals(store, "rev-3", [dict(key=mine[0]["key"], note=LONG_NOTE)])
    row = next(r for r in signal_rows(store) if r["source_claim_id"] == cid)
    assert row["disagreed"] is True and row["status"] == "pending"


def test_the_latest_decision_decides_what_is_offered(filled, learnable):
    _, store, _, _ = filled
    cid, band, _ = learnable[3]
    record_decision(store, cid, "rev-1", "agree", band, "", band)
    record_decision(store, cid, "rev-2", "override", "green" if band != "green" else "amber", "second look", band)
    assert all(c["decision_by"] == "rev-2" and c["disagreed"] for c in candidate_signals(store) if c["claim_id"] == cid)


def test_signals_never_reach_the_scorer():
    from claim_triage.data.config import ROOT

    for path in (ROOT / "src" / "claim_triage" / "scoring").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        forbidden = ("..app", "claim_triage.app", "app.duckdb", "FROM signals", "ApprovedSignal", "knowledge_text")
        assert not any(f in text for f in forbidden), path.name  # the scorer is plain arithmetic over findings; the knowledge base only informs agents


def kb_app(store, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from claim_triage.data.config import ROOT

    monkeypatch.setenv("CLAIM_TRIAGE_APP_DB", str(store.path))
    monkeypatch.setenv("CLAIM_TRIAGE_DATA_DIR", store.cfg["data_dir"])  # the scratch dataset, so the test needs no built data/ folder (a fresh clone has none)
    at = AppTest.from_file(str(ROOT / "src" / "claim_triage" / "app" / "dashboard.py"), default_timeout=60)
    at.session_state["page"] = "Knowledge base"
    return at.run()


class PassingGraph:
    """Stands in for the knowledge graph: pattern cards come back in a fixed order and the candidate signal is never near the top."""
    CODES = ["DUPLICATE_CLAIM", "BILL_OUTLIER_VS_PEERS", "NOMINEE_CHANGE_NEAR_EVENT", "DOC_DATE_MISMATCH", "REPEAT_INCIDENT_CLAIM", "DAMAGE_IN_PRIOR_INSPECTION",
             "PRE_EXISTING_NEAR_INCEPTION", "EARLY_DEATH_VS_HEALTH_HISTORY", "CROSS_POLICY_INCIDENT_MISMATCH", "SHARED_ENTITY_CLUSTER", "HIGH_FRAUD_AREA", "COVER_VS_INCOME_MISMATCH"]

    def __init__(self):
        self.calls = []

    def ranked_ids(self, query, candidate=None):
        return self.CODES + ([candidate] if candidate else [])

    def upsert(self, *args):
        self.calls.append(("upsert", args[0]))

    def set_status(self, signal_id, status, approved=None):
        self.calls.append(("status", signal_id, status))

    def close(self):
        pass


class UnreachableGraph:
    def __init__(self):
        raise ConnectionError("no route to the graph")


def test_the_page_walks_through_tick_note_update_safety_check_and_activation(filled, learnable, monkeypatch):
    _, store, _, _ = filled
    monkeypatch.setattr("claim_triage.learning.process.KnowledgeGraph", PassingGraph)
    cid, band, _ = learnable[4]
    record_decision(store, cid, "reviewer-1", "agree", band, "", band)
    mine = [c for c in candidate_signals(store) if c["claim_id"] == cid]
    key = mine[0]["key"]
    at = kb_app(store, monkeypatch)
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value == "Knowledge base" and any("Already proposed" in s.value for s in at.subheader)
    update = next(b for b in at.button if b.label == "Update knowledge base")
    assert update.disabled  # nothing ticked yet
    assert at.text_input(key=f"kb-note-0-{key}").disabled  # the note box opens when the finding is ticked

    at.checkbox(key=f"kb-add-0-{key}").check().run()  # ticked, but no note: the save is refused and nothing is written
    before = len(signal_rows(store))
    next(b for b in at.button if b.label == "Update knowledge base").click().run()
    assert any("at least" in e.value for e in at.error) and len(signal_rows(store)) == before

    at.text_input(key=f"kb-note-0-{key}").set_value(NOTE).run()
    next(b for b in at.button if b.label == "Update knowledge base").click().run()
    assert not at.exception and any("Added to the knowledge base" in s.value and "test searches" in s.value for s in at.success)
    saved = [r for r in signal_rows(store) if r["source_claim_id"] == cid and r["indicator_code"] == mine[0]["code"]]
    assert len(saved) == 1 and saved[0]["status"] == "active" and saved[0]["note"] == NOTE and saved[0]["reviewer"] == "reviewer-1"
    assert json.loads(saved[0]["gate_result"])["passed"] is True
    assert any("Safety check details" in e.label for e in at.expander)


def test_if_the_graph_cannot_be_reached_the_signal_stays_pending_and_can_be_retried(filled, learnable, monkeypatch):
    _, store, _, _ = filled
    monkeypatch.setattr("claim_triage.learning.process.KnowledgeGraph", UnreachableGraph)
    cid, band, _ = learnable[5]
    record_decision(store, cid, "reviewer-1", "agree", band, "", band)
    mine = [c for c in candidate_signals(store) if c["claim_id"] == cid]
    key = mine[0]["key"]
    at = kb_app(store, monkeypatch)
    at.checkbox(key=f"kb-add-0-{key}").check().run()
    at.text_input(key=f"kb-note-0-{key}").set_value(NOTE).run()
    next(b for b in at.button if b.label == "Update knowledge base").click().run()
    assert not at.exception and any("Still pending" in w.value and "could not run" in w.value for w in at.warning)
    row = next(r for r in signal_rows(store) if r["source_claim_id"] == cid and r["indicator_code"] == mine[0]["code"])
    assert row["status"] == "pending"  # fails closed: nothing was added
    retry = next(b for b in at.button if b.label.startswith("Run the safety check again"))
    monkeypatch.setattr("claim_triage.learning.process.KnowledgeGraph", PassingGraph)
    retry.click().run()
    after = next(r for r in signal_rows(store) if r["signal_id"] == row["signal_id"])
    detail = json.loads(after["gate_result"])
    # the retry really ran the checks. Here it is blocked, correctly: the earlier test already activated a near-identical "inflated billing" signal
    assert after["status"] in ("active", "blocked") and detail["checks"], detail
    assert after["status"] == "blocked" and "almost identical" in detail["summary"], detail["summary"]


def test_the_page_says_so_when_there_is_nothing_to_offer(tmp_path, monkeypatch, built_dataset):
    from claim_triage.app.store import Store

    cfg, _ = built_dataset
    empty = Store(cfg, path=tmp_path / "empty.duckdb")
    try:
        at = kb_app(empty, monkeypatch)
        assert not at.exception and any("Nothing to offer yet" in i.value for i in at.info)
    finally:
        empty.close()
