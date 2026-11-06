"""The app database: every claim gets a rules-only assessment, results round-trip, decisions and signals are kept, and no answer keys are involved."""
from __future__ import annotations

import json

import pytest

from claim_triage.app.backfill import backfill_agents
from claim_triage.baseline import assess_claim
from claim_triage.tools import ToolBox


def test_every_claim_awaiting_assessment_gets_a_rules_assessment(filled):
    cfg, store, ids, bands = filled
    assert len(ids) == 380 and store.count_assessments() == {"rules": 380}
    assert sum(bands.values()) == 380 and set(bands) <= {"red", "amber", "green"}
    row = store.get_assessment(ids[0])
    assert row["system"] == "rules" and row["band"] in ("red", "amber", "green") and 0 <= row["trust_score"] <= 1
    assert row["result"]["reasons"] and row["source"].startswith("rules-only")


def test_stored_result_matches_a_fresh_assessment(filled):
    cfg, store, ids, _ = filled
    box = ToolBox(cfg, graph=False)
    try:
        for cid in ids[::95]:
            box.returned_ids.clear()
            fresh = assess_claim(box, cid)
            stored = store.get_assessment(cid)
            assert stored["band"] == fresh.band and stored["trust_score"] == pytest.approx(fresh.trust_score)
            assert stored["result"]["reasons"] == fresh.reasons
    finally:
        box.close()


def test_putting_an_assessment_again_replaces_it_and_unknown_systems_are_refused(filled):
    _, store, ids, _ = filled
    first = store.get_assessment(ids[1])["result"]
    store.put_assessment(ids[1], "rules", dict(first, band="amber", trust_score=0.5), "test")
    assert store.get_assessment(ids[1])["band"] == "amber" and store.count_assessments()["rules"] == 380
    with pytest.raises(ValueError):
        store.put_assessment(ids[1], "oracle", first, "test")


def test_saved_agent_runs_are_stored_as_they_were_saved(filled, tmp_path):
    _, store, ids, _ = filled
    record = dict(case_id="CASE-0001", claim_id=ids[2], band="red", trust=0.12, reasons=["a reason"],
                  trace=dict(proposed=["DUPLICATE_CLAIM"], summary="s", investigator=dict(tools=["get_claim_facts"], models=["m"]), verifier=dict(reviews=[])))
    (tmp_path / "run_1.jsonl").write_text(json.dumps(record) + "\n" + json.dumps(dict(record, claim_id="CLM-999999")) + "\n", encoding="utf-8")
    assert backfill_agents(store, set(ids), tmp_path) == 1  # the claim that does not exist in the data is skipped
    row = store.get_assessment(ids[2], "agents")
    assert row["band"] == "red" and row["result"]["proposed"] == ["DUPLICATE_CLAIM"] and row["source"] == "saved agentic run (run_1.jsonl)"


def test_decisions_record_disagreement_with_the_system(filled):
    _, store, ids, _ = filled
    store.add_decision(ids[3], "reviewer-1", "agree", "red", "", system_band="red")
    store.add_decision(ids[3], "reviewer-1", "override", "green", "documents explain it", system_band="red")
    rows = store.decisions_for(ids[3])
    assert [r["disagrees_with_system"] for r in sorted(rows, key=lambda r: r["final_band"])] == [True, False]  # green disagrees, red agrees


def test_new_signals_start_pending(filled):
    _, store, ids, _ = filled
    sid = store.add_signal(ids[4], "BILL_OUTLIER_VS_PEERS", "A bill 3x the norm with no medical reason in the summary.", [ids[4]], "reviewer-1")
    rows = store.signals("pending")
    assert [r["signal_id"] for r in rows if r["signal_id"] == sid] == [sid] and not store.signals("active")
