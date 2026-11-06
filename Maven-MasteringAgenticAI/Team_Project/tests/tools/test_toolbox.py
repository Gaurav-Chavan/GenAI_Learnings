"""Tool tests: read-only, validated, point-in-time, no personal data, no labels. Graph tests skip if Neo4j is unreachable."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

import duckdb
import pytest

from claim_triage.data.config import paths
from claim_triage.data.quality import canary
from claim_triage.tools import ToolBox, ToolError
from claim_triage.tools import toolbox as tb

EXPECTED_TOOLS = {"get_claim", "get_claim_facts", "get_area_signal", "get_customer_summary", "get_policy", "get_person_timeline",
                  "find_similar_claims", "get_provider_stats", "get_vehicle_inspections", "find_shared_entities", "search_documents", "search_knowledge",
                  "get_claim_documents"}


@pytest.fixture(scope="module")
def cfg(built_dataset):
    return built_dataset[0]


@pytest.fixture(scope="module")
def box(cfg):
    b = ToolBox(cfg, graph=False)
    yield b
    b.close()


@pytest.fixture(scope="module")
def live(cfg):
    b = ToolBox(cfg, graph=True)
    try:
        b.driver()
        b._read_graph("RETURN 1 AS ok")
    except Exception as exc:  # noqa: BLE001
        b.close()
        pytest.skip(f"Neo4j not reachable: {type(exc).__name__}")
    yield b
    b.close()


def cases(cfg, scenario, kind):
    con = duckdb.connect(str(paths(cfg)["labels"]), read_only=True)
    try:
        return [r[0] for r in con.execute("select claim_id from labels where scenario_id=? and kind=? order by case_id", [scenario, kind]).fetchall()]
    finally:
        con.close()


def facts(box, claim_id):
    return box.call("get_claim_facts", claim_id=claim_id)["records"][0]


# ------------------------------------------------------------------ registry and validation
def test_registry_and_function_specs(box):
    assert set(box.tools) == EXPECTED_TOOLS
    for spec in box.specs():
        fn = spec["function"]
        assert fn["description"] and fn["parameters"]["additionalProperties"] is False
        assert set(fn["parameters"]["required"]) <= set(fn["parameters"]["properties"])


@pytest.mark.parametrize("name,args", [
    ("get_claim", {"claim_id": "CLM-1; DROP"}), ("get_claim", {"claim_id": "clm-000001"}), ("get_policy", {"policy_id": "POL-12"}),
    ("get_person_timeline", {"customer_id": "CUST-000001", "as_of": "31/08/2026"}), ("get_claim", {}),
    ("get_claim", {"claim_id": "CLM-000001", "extra": 1}), ("search_documents", {"query": "   "}),
    ("search_documents", {"query": "bill", "source": "everything"}), ("get_vehicle_inspections", {"vehicle_token": "MH12AB1234"}),
])
def test_bad_arguments_are_rejected(box, name, args):
    with pytest.raises(ToolError):
        box.call(name, **args)


def test_unknown_tool_and_unknown_record(box):
    with pytest.raises(ToolError):
        box.call("delete_everything")
    with pytest.raises(ToolError):
        box.call("get_claim", claim_id="CLM-999999")


def test_limits_are_clamped(box, cfg):
    cust = box.call("get_claim_facts", claim_id=cases(cfg, "H1", "suspicious")[0])["records"][0]["customer_id"]
    assert box.call("get_person_timeline", customer_id=cust, limit=10_000)["args"]["limit"] == tb.MAX_LIMIT
    assert box.call("get_person_timeline", customer_id=cust, limit=3)["count"] <= 3


def test_cypher_is_fixed_and_read_only():
    write = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD|INSERT)\b", re.I)
    for text in tb.ALL_CYPHER:
        assert not write.search(text), text
        for call in re.findall(r"CALL\s+([\w.]+)", text):
            assert call == "db.index.fulltext.queryNodes"


# ------------------------------------------------------------------ what the tools return
def test_claim_policy_and_customer_lookups(box, cfg):
    cid = cases(cfg, "H1", "suspicious")[0]
    claim = box.call("get_claim", claim_id=cid)
    rec = claim["records"][0]
    assert rec["claim_id"] == cid and rec["documents"] and rec["provider"]["name"]
    assert cid in claim["evidence_ids"] and rec["documents"][0]["doc_id"] in claim["evidence_ids"]
    policy = box.call("get_policy", policy_id=rec["policy_id"])["records"][0]
    assert policy["customer_id"] == rec["customer_id"] and "nominee_token" in policy
    summary = box.call("get_customer_summary", customer_id=rec["customer_id"])["records"][0]
    assert "pincode" not in summary and summary["customer_id"] == rec["customer_id"]


def test_timeline_never_returns_anything_after_the_as_of_date(box, cfg):
    cid = cases(cfg, "T1", "suspicious")[0]
    f = facts(box, cid)
    as_of = date.fromisoformat(f["filed_date"]) - timedelta(days=45)
    out = box.call("get_person_timeline", customer_id=f["customer_id"], as_of=as_of.isoformat(), limit=100)
    assert out["records"] and all(r["event_date"] <= as_of.isoformat() for r in out["records"])
    later = box.call("get_person_timeline", customer_id=f["customer_id"], as_of=f["filed_date"], limit=100)
    assert later["count"] > out["count"]


def test_similar_claims_find_duplicates_for_suspicious_but_not_honest_cases(box, cfg):
    flagged = ("same_invoice_number", "near_duplicate_same_customer_procedure_provider_dates_amount")
    for cid in cases(cfg, "H1", "suspicious"):
        reasons = {r for m in box.call("find_similar_claims", claim_id=cid)["records"] for r in m["reasons"]}
        assert reasons & set(flagged), cid
    for cid in cases(cfg, "H1", "honest"):
        reasons = {r for m in box.call("find_similar_claims", claim_id=cid)["records"] for r in m["reasons"]}
        assert not reasons & set(flagged), cid


def test_similar_claims_only_return_earlier_claims(box, cfg):
    for cid in cases(cfg, "M1", "suspicious")[:5] + cases(cfg, "X4", "suspicious")[:5]:
        filed = box.call("get_claim", claim_id=cid)["records"][0]["filed_date"]
        assert all(m["filed_date"] < filed for m in box.call("find_similar_claims", claim_id=cid)["records"])


def test_vehicle_inspections_support_the_prior_damage_scenario(box, cfg):
    for cid in cases(cfg, "M2", "suspicious"):
        claim = box.call("get_claim", claim_id=cid)["records"][0]
        out = box.call("get_vehicle_inspections", vehicle_token=claim["vehicle_token"], as_of=claim["filed_date"])
        inspections = [r for r in out["records"] if r["event_type"] == "vehicle_inspected" and r["claim_id"] is None]
        claimed = set((claim["damage_items"] or "").split(";"))
        assert inspections and len(claimed & set(inspections[0]["details"]["damage"])) >= 2
        assert not any(r["event_type"] == "vehicle_repaired" and r["event_date"] > inspections[0]["event_date"] for r in out["records"])
    for cid in cases(cfg, "M2", "honest"):
        claim = box.call("get_claim", claim_id=cid)["records"][0]
        out = box.call("get_vehicle_inspections", vehicle_token=claim["vehicle_token"], as_of=claim["filed_date"])
        assert any(r["event_type"] == "vehicle_repaired" for r in out["records"])


def test_area_signal_explains_itself_in_plain_words(box, cfg):
    seen = {True: 0, False: 0}
    for scenario in ("H1", "H2", "X1"):
        for cid in cases(cfg, scenario, "honest"):
            rec = box.call("get_area_signal", claim_id=cid)["records"][0]
            seen[rec["is_high"]] += 1
            assert ("more often than average" in rec["reason"]) == rec["is_high"] and rec["location_shown"] in rec["reason"]
    assert seen[True] and seen[False]


def test_provider_stats_compare_with_peers(box, cfg):
    cid = cases(cfg, "H2", "suspicious")[0]
    claim = box.call("get_claim", claim_id=cid)["records"][0]
    out = box.call("get_provider_stats", provider_id=claim["provider_id"], procedure_code=claim["procedure_code"], as_of=claim["filed_date"])
    rec = out["records"][0]
    assert rec["procedure"]["peer_claims"] >= 1 and rec["procedure"]["peer_median"] > 0
    assert isinstance(rec["claims_last_90d"], int) and rec["reg_status"] in ("registered", "unverified")


def test_evidence_ids_are_remembered_so_a_verifier_can_check_them(cfg):
    box = ToolBox(cfg, graph=False)
    cid = cases(cfg, "H1", "suspicious")[0]
    box.call("get_claim", claim_id=cid)
    assert box.unknown_evidence([cid, "CLM-999999"]) == ["CLM-999999"]
    assert box.calls[0]["tool"] == "get_claim" and box.calls[0]["ms"] >= 0
    box.close()


def test_no_tool_output_contains_personal_data_labels_or_pins(box, cfg):
    chunks = []
    con = duckdb.connect(str(paths(cfg)["gold"]), read_only=True)
    eval_claims = [r[0] for r in con.execute("select claim_id from claim_features order by claim_id").fetchall()]
    con.close()
    for cid in eval_claims:
        claim = box.call("get_claim", claim_id=cid)["records"][0]
        out = [box.call("get_claim_facts", claim_id=cid), box.call("get_area_signal", claim_id=cid), box.call("find_similar_claims", claim_id=cid),
               box.call("get_customer_summary", customer_id=claim["customer_id"]), box.call("get_policy", policy_id=claim["policy_id"]),
               box.call("get_person_timeline", customer_id=claim["customer_id"], as_of=claim["filed_date"], limit=100)]
        out.append({"claim": claim})
        if claim["provider_id"]:
            out.append(box.call("get_provider_stats", provider_id=claim["provider_id"], procedure_code=claim["procedure_code"], as_of=claim["filed_date"]))
        if claim["vehicle_token"]:
            out.append(box.call("get_vehicle_inspections", vehicle_token=claim["vehicle_token"], as_of=claim["filed_date"]))
        chunks.append(json.dumps(out, default=str))
    text = "\n".join(chunks)
    assert canary(cfg, layers=(), extra_text=text) == []
    for forbidden in ("expected_band", "expected_indicators", "is_fraud", "scenario_id", "hotspot_pin", "pincode"):
        assert forbidden not in text, forbidden


# ------------------------------------------------------------------ graph-backed tools (skipped when Neo4j is unreachable)
def test_shared_entities_reveal_rings_but_not_households(live, cfg):
    for cid in cases(cfg, "X4", "suspicious"):
        claim = live.call("get_claim", claim_id=cid)["records"][0]
        out = live.call("find_shared_entities", customer_id=claim["customer_id"], as_of=claim["filed_date"])
        assert max((r["n_unrelated"] for r in out["records"] if r["type"] == "shared_payout_account"), default=0) >= 3, cid
    for cid in cases(cfg, "X4", "honest"):
        claim = live.call("get_claim", claim_id=cid)["records"][0]
        out = live.call("find_shared_entities", customer_id=claim["customer_id"], as_of=claim["filed_date"])
        assert max((r["n_unrelated"] for r in out["records"]), default=0) < 3, cid
        assert any(r["household_members"] for r in out["records"]), cid  # relatives share a phone and an address


def test_shared_entities_respect_the_as_of_date(live, cfg):
    cid = cases(cfg, "X4", "suspicious")[0]
    claim = live.call("get_claim", claim_id=cid)["records"][0]
    early = live.call("find_shared_entities", customer_id=claim["customer_id"], as_of="2023-08-01")
    assert all(r["n_unrelated"] == 0 for r in early["records"])


def test_document_search_ranks_matches_and_respects_dates(live):
    hits = live.call("search_documents", query="itemised bill", limit=3)
    assert hits["count"] == 3 and all("tem" in h["text"].lower() for h in hits["records"])
    assert live.call("search_documents", query="itemised bill", as_of="2020-01-01")["count"] == 0
    assert live.call("search_documents", query="critical illness cancer", source="claims")["count"] >= 1
    odd = live.call("search_documents", query='bill" OR (1=1) *:*')  # query operators are stripped, so this is just words
    assert odd["args"]["query"] == "bill OR 1 1"


def test_a_write_through_the_graph_session_is_refused(live):
    from neo4j import READ_ACCESS
    driver = live.driver()
    with driver.session(database=live._database, default_access_mode=READ_ACCESS) as session:
        tx = session.begin_transaction()
        try:
            with pytest.raises(Exception):
                tx.run("CREATE (n:__ReadOnlyProbe {t: 1})").consume()
        finally:
            tx.rollback()  # even if a write were somehow allowed, nothing is kept
    assert live._read_graph("MATCH (n:__ReadOnlyProbe) RETURN count(n) AS n")[0]["n"] == 0


def test_graph_tool_output_has_no_personal_data(live, cfg):
    chunks = []
    for cid in cases(cfg, "X4", "suspicious")[:5] + cases(cfg, "X3", "suspicious")[:5]:
        claim = live.call("get_claim", claim_id=cid)["records"][0]
        chunks.append(json.dumps(live.call("find_shared_entities", customer_id=claim["customer_id"], as_of=claim["filed_date"]), default=str))
    chunks.append(json.dumps(live.call("search_documents", query="bill discharge", limit=25), default=str))
    assert canary(cfg, layers=(), extra_text="\n".join(chunks)) == []
