"""The review queue: riskiest first, customers by ID only, filters work, reviewed claims are marked. Plus a headless smoke test of the dashboard page."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from claim_triage.app.review_queue import BAND_RANK, apply_filters, load_queue, summarise
from claim_triage.data.config import ROOT


@pytest.fixture(scope="module")
def queue(filled):
    cfg, store, ids, _ = filled
    return load_queue(cfg, store), filled


def test_queue_has_every_assessed_claim_riskiest_first(queue):
    rows, (_, _, ids, bands) = queue
    assert len(rows) == len(ids) == 380
    trust = [r["trust_score"] for r in rows]
    assert trust == sorted(trust)  # least trusted first
    totals = summarise(rows)
    assert totals["total"] == 380 and totals["unreviewed"] == 380 and {b: totals[b] for b in BAND_RANK} == dict(bands)
    assert rows[0]["band"] == "red" and rows[-1]["band"] == "green"


def test_queue_rows_show_a_customer_by_id_only(queue):
    rows, _ = queue
    allowed = {"claim_id", "customer_id", "line", "filed_date", "amount", "band", "trust_score", "evidence_status", "top_reason", "flags", "missing", "reviewed", "has_agent_run", "assignee"}
    assert all(set(r) == allowed for r in rows)
    assert all(re.fullmatch(r"CUST-\d{6}", r["customer_id"]) for r in rows)
    # no name, phone, e-mail, address or PIN code appears in the text the queue shows
    text = " ".join(r["top_reason"] for r in rows)
    assert not re.search(r"\+91\d{10}|@example\.com|\bFlat \d+", text)


def test_filters_combine(queue):
    rows, _ = queue
    red_health = apply_filters(rows, bands=["red"], lines=["health"])
    assert red_health and all(r["band"] == "red" and r["line"] == "health" for r in red_health)
    one = rows[10]
    assert [r["claim_id"] for r in apply_filters(rows, search=one["claim_id"].lower())] == [one["claim_id"]]
    assert all(r["customer_id"] == one["customer_id"] for r in apply_filters(rows, search=one["customer_id"]))
    assert apply_filters(rows, bands=[], lines=[], only_unreviewed=False, search="") == rows
    assert apply_filters(rows, search="nothing-matches") == []


def test_a_decision_marks_the_claim_reviewed_and_the_latest_decision_wins(queue):
    rows, (cfg, store, ids, _) = queue
    target = rows[0]["claim_id"]
    store.add_decision(target, "reviewer-1", "agree", rows[0]["band"], "", rows[0]["band"])
    store.add_decision(target, "reviewer-2", "override", "amber", "needs a second look", rows[0]["band"])
    after = load_queue(cfg, store)
    mark = {r["claim_id"]: r["reviewed"] for r in after}
    assert mark[target] == "amber" and summarise(after)["unreviewed"] == 379
    assert apply_filters(after, only_unreviewed=True) and target not in {r["claim_id"] for r in apply_filters(after, only_unreviewed=True)}


def test_the_agent_view_lists_only_claims_with_a_saved_run(filled):
    cfg, store, ids, _ = filled
    assert load_queue(cfg, store, "agents") == []


def test_dashboard_source_never_touches_the_vault_or_the_answer_keys():
    for path in (ROOT / "src" / "claim_triage" / "app").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(s in text for s in ('["vault"]', '["labels"]', "vault.duckdb", "labels.duckdb", "labels_hard")), path.name


@pytest.mark.skipif(not (Path(ROOT) / "data" / "app.duckdb").exists(), reason="run the backfill once to create data/app.duckdb")
def test_dashboard_renders_without_errors():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "src" / "claim_triage" / "app" / "dashboard.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [m.label for m in at.metric][:2] == ["Claims in view", "Not yet reviewed"]
    assert at.header[0].value == "Review queue"
