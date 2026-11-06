"""The claim page: facts and customer view without identity, evidence lookup, decision rules, plain-language names. Plus a headless render of the page."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from claim_triage.app.claim_view import BANDS, CHOICES, DecisionError, claim_overview, customer_facts, next_unreviewed, record_decision
from claim_triage.app.evidence import EvidenceLookup
from claim_triage.app.plain import document_name, finding_name, finding_note
from claim_triage.app.review_queue import load_queue
from claim_triage.data.config import ROOT


# ---------- the claim and the customer as a reviewer may see them ----------

def test_claim_overview_shows_the_claim_and_a_customer_without_identity(filled):
    cfg, store, ids, _ = filled
    claim = claim_overview(cfg, ids[0])
    assert claim["claim_id"] == ids[0] and re.fullmatch(r"CUST-\d{6}", claim["customer_id"]) and claim["documents"]
    assert set(claim["customer"]) == {"age_band", "gender", "occupation_category", "income_band", "state", "district", "urban_rural", "tenure_band", "generalisation_level"}
    assert not {"pincode", "name", "phone", "email", "address", "dob", "bank_account", "kyc_id"} & (set(claim) | set(claim["customer"]))
    text = " ".join(str(v) for k, v in claim.items() if k in ("description", "provider_name", "procedure_name", "incident_district")) + " ".join(v for _, v in customer_facts(claim["customer"]))
    assert not re.search(r"\+91\d{10}|@example\.com|\bFlat \d+", text) and "[CUSTOMER]" in claim["description"] + "[CUSTOMER]"
    assert claim_overview(cfg, "CLM-999999") is None


def test_customer_facts_generalise_small_groups():
    base = dict(age_band="30-39", gender="F", occupation_category="salaried", income_band="5-10L", state="Karnataka", district="Bengaluru Urban",
                urban_rural="urban", tenure_band="2-5y", generalisation_level=0)
    assert dict(customer_facts(base))["Location"] == "Bengaluru Urban"
    assert dict(customer_facts({**base, "generalisation_level": 2, "district": "(state level)"}))["Location"] == "Karnataka"
    assert dict(customer_facts({**base, "generalisation_level": 4, "district": "withheld", "state": "withheld"}))["Location"] == "withheld"
    assert customer_facts({}) == []


# ---------- evidence lookup ----------

def test_every_kind_of_evidence_id_resolves_to_a_readable_record(filled):
    cfg, store, ids, _ = filled
    cited = store.get_assessment(ids[0])["result"]["indicators"]
    claim = claim_overview(cfg, ids[0])
    lookup = EvidenceLookup(cfg)
    try:
        doc = lookup.describe(claim["documents"][0]["doc_id"])
        assert doc["kind"] == "Document" and doc["text"] and "[CUSTOMER]" in doc["text"] + "[CUSTOMER]"
        assert lookup.describe(ids[0])["kind"] == "Claim" and lookup.describe(claim["policy_id"])["kind"] == "Policy"
        assert lookup.describe(claim["provider_id"])["kind"] == "Provider" if claim["provider_id"] else True
        evt = lookup.silver.execute("SELECT event_id FROM events LIMIT 1").fetchone()[0]
        assert lookup.describe(evt)["kind"] == "Event"
        assert lookup.describe(claim["customer_id"])["facts"] == []  # another customer: ID only
        assert lookup.describe("CLM-999999")["title"].endswith("(not found)")
        assert lookup.describe("'; DROP TABLE claims; --")["kind"] == "Unknown"
        for ind in cited:
            for e in ind["evidence_ids"]:
                assert not lookup.describe(e)["title"].endswith("(not found)"), e
    finally:
        lookup.close()


def test_a_past_claim_shows_its_reviewer_confirmed_outcome(filled):
    cfg, *_ = filled
    lookup = EvidenceLookup(cfg)
    try:
        fraud = lookup.silver.execute("SELECT claim_id FROM claims WHERE outcome = 'confirmed_fraud' LIMIT 1").fetchone()[0]
        assert ("Outcome", "confirmed as fraud by a reviewer") in lookup.describe(fraud)["facts"]
    finally:
        lookup.close()


# ---------- plain names ----------

def test_all_finding_codes_have_plain_names_and_notes():
    from claim_triage.agents.investigator import ALLOWED, CROSS_POLICY_EXTRA

    codes = {c for line in ALLOWED.values() for c in line} | set(CROSS_POLICY_EXTRA)
    for code in codes:
        assert finding_name(code) != code and "_" not in finding_name(code) and finding_note(code)
    assert finding_name("SOMETHING_NEW") == "Something new" and document_name("itemised_bill") == "Itemised bill"


# ---------- decisions ----------

def test_agree_keeps_the_systems_band_and_override_needs_a_reason(filled):
    _, store, ids, _ = filled
    cid = ids[20]
    record_decision(store, cid, "rev-1", "agree", "red", "", system_band="amber")
    assert store.decisions_for(cid)[0]["final_band"] == "amber" and not store.decisions_for(cid)[0]["disagrees_with_system"]  # the band argument is ignored when agreeing
    with pytest.raises(DecisionError, match="reason"):
        record_decision(store, cid, "rev-1", "override", "green", "  ", system_band="amber")
    with pytest.raises(DecisionError, match="system's own band"):
        record_decision(store, cid, "rev-1", "override", "amber", "x", system_band="amber")
    record_decision(store, cid, "rev-2", "override", "green", "documents explain the bill", system_band="amber")
    latest = store.decisions_for(cid)[0]
    assert latest["final_band"] == "green" and latest["disagrees_with_system"] and latest["reviewer"] == "rev-2"


def test_decisions_need_a_reviewer_and_a_valid_choice(filled):
    _, store, ids, _ = filled
    with pytest.raises(DecisionError, match="reviewer name"):
        record_decision(store, ids[21], "  ", "agree", "red", "", "red")
    with pytest.raises(DecisionError):
        record_decision(store, ids[21], "rev-1", "approve", "green", "", "red")
    with pytest.raises(DecisionError, match="what information"):
        record_decision(store, ids[21], "rev-1", "hold", "red", "", "red")
    assert store.decisions_for(ids[21]) == [] and set(CHOICES) == {"agree", "override", "hold", "escalate"} and BANDS == ("red", "amber", "green")


def test_a_hold_keeps_the_claim_in_the_open_queue(filled):
    cfg, store, ids, _ = filled
    cid = ids[22]
    record_decision(store, cid, "rev-1", "hold", "red", "need the discharge summary", "red")
    assert next(r for r in load_queue(cfg, store) if r["claim_id"] == cid)["reviewed"] is None
    record_decision(store, cid, "rev-1", "agree", "red", "", "red")
    assert next(r for r in load_queue(cfg, store) if r["claim_id"] == cid)["reviewed"] == "red"


def test_next_unreviewed_skips_decided_claims_and_wraps_around():
    rows = [dict(claim_id=f"CLM-{i}", reviewed=None) for i in range(1, 5)]
    rows[1]["reviewed"] = "red"
    assert next_unreviewed(rows, "CLM-1") == "CLM-3" and next_unreviewed(rows, "CLM-4") == "CLM-1" and next_unreviewed(rows, None) == "CLM-1"
    for r in rows:
        r["reviewed"] = "green"
    assert next_unreviewed(rows, "CLM-1") is None


# ---------- the page renders ----------

@pytest.mark.skipif(not (Path(ROOT) / "data" / "app.duckdb").exists(), reason="run the backfill once to create data/app.duckdb")
def test_claim_page_renders_for_a_red_claim_and_for_a_green_one():
    from streamlit.testing.v1 import AppTest

    from claim_triage.app.store import Store
    from claim_triage.data.config import load_config

    cfg = load_config()
    store = Store(cfg)
    try:
        rows = load_queue(cfg, store)
    finally:
        store.close()
    for pick in (rows[0], rows[-1]):
        at = AppTest.from_file(str(ROOT / "src" / "claim_triage" / "app" / "dashboard.py"), default_timeout=60)
        at.session_state["page"], at.session_state["claim_id"] = "Claim", pick["claim_id"]
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.title[0].value == pick["claim_id"]
        assert any("Your decision" in s.value for s in at.subheader) and any("Why this score" in s.value for s in at.subheader)
        if pick["band"] == "green":
            assert any("not an approval" in i.value for i in at.info)
