"""Escalate, decision tags and claim owners: the three review features added after the first dashboard."""
from __future__ import annotations

import json

import duckdb
import pytest

from claim_triage.app.claim_view import TAGS, DecisionError, record_decision
from claim_triage.app.kb import NOT_LEARNABLE, candidate_signals
from claim_triage.app.review_queue import apply_filters, load_queue
from claim_triage.app.store import Store


def _row(cfg, store, cid):
    return next(r for r in load_queue(cfg, store) if r["claim_id"] == cid)


# ---------- escalate ----------

def test_an_escalation_needs_a_person_a_different_person_and_a_reason(filled):
    _, store, ids, _ = filled
    cid = ids[0]
    with pytest.raises(DecisionError, match="who you are escalating"):
        record_decision(store, cid, "rev-1", "escalate", "red", "needs a senior eye", "red")
    with pytest.raises(DecisionError, match="someone other than yourself"):
        record_decision(store, cid, "rev-1", "escalate", "red", "needs a senior eye", "red", escalate_to=" REV-1 ")
    with pytest.raises(DecisionError, match="why you are escalating"):
        record_decision(store, cid, "rev-1", "escalate", "red", "  ", "red", escalate_to="senior-1")
    assert store.decisions_for(cid) == [] and store.assignee_of(cid) is None  # nothing was saved by the failed attempts


def test_an_escalation_keeps_the_claim_open_and_hands_it_over(filled):
    cfg, store, ids, _ = filled
    cid = ids[1]
    band = _row(cfg, store, cid)["band"]
    record_decision(store, cid, "rev-1", "escalate", "green" if band != "green" else "red", "outside my limit", band, escalate_to="senior-1")
    d = store.decisions_for(cid)[0]
    assert d["decision"] == "escalate" and d["escalated_to"] == "senior-1" and d["final_band"] == band and not d["disagrees_with_system"]  # the band argument is ignored
    row = _row(cfg, store, cid)
    assert row["reviewed"] is None and row["assignee"] == "senior-1"  # still open, now owned by the person it went to
    record_decision(store, cid, "senior-1", "agree", band, "", band)
    assert _row(cfg, store, cid)["reviewed"] == band  # the new owner can close it


def test_an_escalated_claim_offers_no_lessons_until_someone_decides_it(filled):
    _, store, ids, _ = filled
    cid = next(c for c in ids if any(i["code"] not in NOT_LEARNABLE for i in store.get_assessment(c)["result"]["indicators"]) and not store.decisions_for(c))
    band = store.get_assessment(cid)["band"]
    record_decision(store, cid, "rev-1", "escalate", band, "second opinion please", band, escalate_to="senior-1")
    assert not [c for c in candidate_signals(store) if c["claim_id"] == cid]
    record_decision(store, cid, "senior-1", "agree", band, "", band)
    assert [c for c in candidate_signals(store) if c["claim_id"] == cid]


# ---------- tags ----------

def test_tags_are_checked_stored_and_optional(filled):
    _, store, ids, _ = filled
    cid = ids[-1]
    with pytest.raises(DecisionError, match="Unknown tag: made_up"):
        record_decision(store, cid, "rev-1", "agree", "red", "", "red", tags=["wording", "made_up"])
    assert store.decisions_for(cid) == []
    record_decision(store, cid, "rev-1", "override", "green", "documents explain it", "red", tags=["evidence_unsupported", "wording", "wording"])
    record_decision(store, cid, "rev-2", "agree", "red", "", "red")
    newest, older = store.decisions_for(cid)
    assert newest["tags"] == [] and older["tags"] == ["evidence_unsupported", "wording"]  # duplicates dropped, order kept
    assert set(older["tags"]) <= set(TAGS)


def test_tags_can_go_with_a_hold_or_an_escalation(filled):
    _, store, ids, _ = filled
    cid = ids[-2]
    record_decision(store, cid, "rev-1", "hold", "red", "need the bill", "red", tags=["evidence_missing"])
    record_decision(store, cid, "rev-1", "escalate", "red", "unclear rule", "red", tags=["rule_misread"], escalate_to="senior-2")
    assert [d["tags"] for d in store.decisions_for(cid)] == [["rule_misread"], ["evidence_missing"]]


# ---------- owners ----------

def test_a_claim_can_be_given_reassigned_and_released(filled):
    _, store, ids, _ = filled
    cid = ids[-3]
    assert store.assignee_of(cid) is None
    store.assign(cid, " rev-2 ", "rev-1")
    assert store.assignee_of(cid) == "rev-2"
    store.assign(cid, "rev-3", "rev-1")
    assert store.assignee_of(cid) == "rev-3" and store.assignments()[cid] == "rev-3"
    assert len([r for r in store.rows("SELECT * FROM assignments WHERE claim_id = ?", cid)]) == 1
    store.assign(cid, "  ", "rev-1")
    assert store.assignee_of(cid) is None and cid not in store.assignments()


def test_the_queue_shows_and_filters_by_owner(filled):
    cfg, store, ids, _ = filled
    store.assign(ids[-4], "Rev-9", "rev-1")
    store.assign(ids[-5], "rev-8", "rev-1")
    rows = load_queue(cfg, store)
    mine = apply_filters(rows, assignee="rev-9")  # not case-sensitive
    assert [r["claim_id"] for r in mine] == [ids[-4]]
    assert len(apply_filters(rows, assignee=None)) == len(rows) == len(apply_filters(rows, assignee="  "))
    assert apply_filters(rows, assignee="nobody-here") == []
    assert _row(cfg, store, ids[-4])["assignee"] == "Rev-9" and _row(cfg, store, ids[-6])["assignee"] is None


# ---------- an app database made before these features ----------

def test_an_older_database_gains_the_new_columns_and_keeps_its_decisions(built_dataset, tmp_path):
    cfg, _ = built_dataset
    path = tmp_path / "old.duckdb"
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE decisions (decision_id VARCHAR PRIMARY KEY, claim_id VARCHAR, reviewer VARCHAR, decision VARCHAR, final_band VARCHAR,
                   note VARCHAR, system_band VARCHAR, disagrees_with_system BOOLEAN, created_at TIMESTAMP)""")
    con.execute("INSERT INTO decisions VALUES ('DEC-old', 'CLM-000001', 'rev-1', 'agree', 'red', '', 'red', FALSE, TIMESTAMP '2026-10-01 10:00:00')")
    con.close()
    store = Store(cfg, path=path)
    try:
        old = store.decisions_for("CLM-000001")[0]
        assert old["decision_id"] == "DEC-old" and old["tags"] == [] and old["escalated_to"] is None
        record_decision(store, "CLM-000001", "rev-1", "escalate", "red", "check", "red", tags=["wording"], escalate_to="senior-1")
        assert json.loads(store.rows("SELECT tags FROM decisions WHERE decision = 'escalate'")[0]["tags"]) == ["wording"]
        assert store.assignee_of("CLM-000001") == "senior-1"
    finally:
        store.close()
