"""Agent tests with a scripted fake model: no network, no cost, same result every run.

They check the guard rails around the models (what code accepts, drops and caps), not how clever the models are; that is the evaluation harness's job.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import duckdb
import pytest

from claim_triage.agents.base import parse_json_object
from claim_triage.agents.explainer import Explainer, faithful
from claim_triage.agents.investigator import Investigator, allowed_codes, to_result
from claim_triage.agents.verifier import MITIGATIONS, POLICY_CODES, Verifier, evidence_check, fact_holds
from claim_triage.contracts import FinalResult, Indicator, MissingItem
from claim_triage.data.config import paths
from claim_triage.llm import LLMResult
from claim_triage.tools import ToolBox


def reply(content=None, tool_calls=None, model="fake/model") -> LLMResult:
    return LLMResult(content=content, tool_calls=tool_calls or [], model=model, fell_back=False, latency_ms=1, input_tokens=10, output_tokens=5, cost_usd=0.0)


def call(name: str, call_id: str = "c1", **args) -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(args)}


class FakeLLM:
    """Plays back scripted replies in order and records what it was offered."""
    limits = {"max_tool_calls": 2, "max_tool_calls_cross_policy": 3}

    def __init__(self, *replies: LLMResult):
        self.replies, self.offered = list(replies), []

    def chat(self, messages, variant="default", tools=None, json_mode=False, max_tokens=None):
        self.offered.append([t["function"]["name"] for t in tools or []])
        return self.replies.pop(0)


def ind(code="BILL_OUTLIER_VS_PEERS", confidence=0.8, evidence=("CLM-000001",), hard=False) -> Indicator:
    return Indicator(code=code, severity="high", confidence=confidence, evidence_ids=list(evidence), explanation="x", hard_rule=hard)


@pytest.fixture(scope="module")
def cfg(built_dataset):
    return built_dataset[0]


@pytest.fixture()
def box(cfg):
    b = ToolBox(cfg, graph=False)
    yield b
    b.close()


@pytest.fixture(scope="module")
def health_claim(cfg) -> str:
    con = duckdb.connect(str(paths(cfg)["gold"]), read_only=True)
    try:
        return con.execute("SELECT claim_id FROM claim_features WHERE line = 'health' ORDER BY claim_id LIMIT 1").fetchone()[0]
    finally:
        con.close()


# ---------- reply parsing ----------

def test_parse_json_object_handles_fences_and_chatter():
    assert parse_json_object('Sure!\n```json\n{"a": 1}\n```') == {"a": 1}
    for bad in (None, "", "no json here"):
        with pytest.raises(ValueError):
            parse_json_object(bad)


# ---------- Investigator: result parsing ----------

def test_to_result_drops_unknown_codes_and_bad_confidence_and_clamps():
    allowed = allowed_codes("health", cross=False)
    args = {"indicators": [
        {"code": "NOT_A_CODE", "confidence": 0.9, "evidence_ids": ["CLM-1"]},
        {"code": "DUPLICATE_CLAIM", "confidence": "high", "evidence_ids": ["CLM-1"]},
        {"code": "BILL_OUTLIER_VS_PEERS", "confidence": 7, "evidence_ids": ["CLM-1"], "hard_rule": True}],
        "missing_evidence": [{"document": "itemised_bill", "reason": "r"}, {"document": "made_up_doc", "reason": "r"}]}
    result, notes = to_result(args, "CLM-000001", allowed)
    assert [i.code for i in result.indicators] == ["BILL_OUTLIER_VS_PEERS"]
    assert result.indicators[0].confidence == 1.0  # clamped
    assert not result.indicators[0].hard_rule  # only a duplicate can be a hard rule
    assert [m.document for m in result.missing_evidence] == ["itemised_bill"]
    assert len(notes) == 2


def test_cross_policy_codes_only_with_cross_policy_checklist():
    assert "CROSS_POLICY_INCIDENT_MISMATCH" not in allowed_codes("health", cross=False)
    assert "CROSS_POLICY_INCIDENT_MISMATCH" in allowed_codes("health", cross=True)
    assert "NOMINEE_CHANGE_NEAR_EVENT" not in allowed_codes("motor", cross=False)


# ---------- Investigator: the loop ----------

def test_investigator_calls_a_tool_then_submits(box, health_claim):
    submit = call("submit_findings", "c2", indicators=[], missing_evidence=[], summary="nothing found")
    llm = FakeLLM(reply(tool_calls=[call("get_claim_facts", claim_id=health_claim)]), reply(tool_calls=[submit]))
    out = Investigator(box, llm).run(health_claim)
    assert out["meta"]["tools"] == ["get_claim_facts"] and out["meta"]["submitted"] and not out["meta"]["loop_guard"]
    assert out["result"].summary == "nothing found" and out["usage"].calls == 2


def test_investigator_budget_guard_stops_extra_tool_calls(box, health_claim):
    # budget is 2 (or 3 for cross-policy customers); the fake model keeps asking for more tools, then finally submits.
    greedy = [reply(tool_calls=[call("get_claim_facts", f"c{i}", claim_id=health_claim)]) for i in range(1, 8)]
    llm = FakeLLM(*greedy, reply(tool_calls=[call("submit_findings", "cz", indicators=[], summary="s")]))
    out = Investigator(box, llm).run(health_claim)
    assert out["meta"]["tool_calls"] <= out["meta"]["budget"]
    assert out["meta"]["loop_guard"]
    assert llm.offered[-1] == ["submit_findings"]  # once the budget is spent only the submit tool is offered


def test_investigator_that_never_submits_returns_empty_result(box, health_claim):
    llm = FakeLLM(reply(content="I think it is fine."), reply(content="Really fine."))
    out = Investigator(box, llm).run(health_claim)
    assert not out["meta"]["submitted"] and out["result"].indicators == []
    assert out["meta"]["notes"]


# ---------- Verifier step 1: plain code ----------

def test_evidence_check_drops_invented_ids_disallowed_codes_and_unproven_hard_rules():
    fake_box = SimpleNamespace(returned_ids={"CLM-000001", "CLM-000002"})
    findings = [
        ind("BILL_OUTLIER_VS_PEERS", evidence=("CLM-999999",)),                    # ID no tool returned
        ind("NOMINEE_CHANGE_NEAR_EVENT", evidence=("CLM-000001",)),                # not allowed for this claim
        ind("DUPLICATE_CLAIM", evidence=("CLM-000001", "CLM-999999"), hard=True),  # real + invented ID, hard rule not backed by facts
    ]
    kept, missing, dropped = evidence_check(fake_box, findings, {"BILL_OUTLIER_VS_PEERS", "DUPLICATE_CLAIM"}, {"dup_exact_invoice": False}, set(), [])
    assert [i.code for i in kept] == ["DUPLICATE_CLAIM"]
    assert kept[0].evidence_ids == ["CLM-000001"] and not kept[0].hard_rule
    assert {d["code"] for d in dropped} == {"BILL_OUTLIER_VS_PEERS", "NOMINEE_CHANGE_NEAR_EVENT"}


def test_evidence_check_keeps_hard_rule_only_with_an_exact_invoice_fact():
    fake_box = SimpleNamespace(returned_ids={"CLM-000001"})
    kept, _, _ = evidence_check(fake_box, [ind("DUPLICATE_CLAIM", hard=True)], {"DUPLICATE_CLAIM"}, {"dup_exact_invoice": True}, set(), [])
    assert kept[0].hard_rule


def test_evidence_check_ignores_missing_document_that_is_on_file():
    fake_box = SimpleNamespace(returned_ids=set())
    _, missing, dropped = evidence_check(fake_box, [], set(), {}, {"bill"}, [MissingItem(document="bill", reason="r"), MissingItem(document="fir", reason="r")])
    assert [m.document for m in missing] == ["fir"]
    assert dropped[0]["code"] == "missing:bill"


# ---------- Verifier step 2: the sceptic ----------

def review(llm_json: dict, findings: list[Indicator], facts: dict):
    llm = FakeLLM(reply(content=json.dumps(llm_json)))
    return Verifier(None, llm).review({"claim_id": "CLM-000001"}, facts, findings)


def test_fact_holds_accepts_only_real_mitigations():
    assert fact_holds("PRE_EXISTING_NEAR_INCEPTION", {"name": "declared_has_condition", "value": True}, {"declared_has_condition": True})
    assert not fact_holds("PRE_EXISTING_NEAR_INCEPTION", {"name": "declared_has_condition", "value": True}, {"declared_has_condition": False})
    assert not fact_holds("DUPLICATE_CLAIM", {"name": "dup_exact_invoice", "value": True}, {"dup_exact_invoice": True})  # evidence FOR the finding
    assert not fact_holds("EARLY_DEATH_VS_HEALTH_HISTORY", {"name": "doc_death_certificate", "value": True}, {"doc_death_certificate": True})
    assert not fact_holds("BILL_OUTLIER_VS_PEERS", {"name": "doc_itemised_bill", "value": True}, {"doc_itemised_bill": True})
    assert not fact_holds("BILL_OUTLIER_VS_PEERS", "not a dict", {})
    assert not fact_holds("DOC_DATE_MISMATCH", {"name": "doc_date_gap_days", "value": 50}, {"doc_date_gap_days": 50})
    assert fact_holds("DOC_DATE_MISMATCH", {"name": "doc_date_gap_days", "value": 1}, {"doc_date_gap_days": 1})


def test_cross_policy_admission_before_accident_is_never_a_recording_difference():
    facts = {"linked_incident_gap_days": 1, "admit_before_linked_incident_days": 3}
    assert not fact_holds("CROSS_POLICY_INCIDENT_MISMATCH", {"name": "linked_incident_gap_days", "value": 1}, facts)


def test_every_finding_code_has_a_mitigation_entry():
    from claim_triage.agents.investigator import ALLOWED, CROSS_POLICY_EXTRA
    codes = {c for line in ALLOWED.values() for c in line} | set(CROSS_POLICY_EXTRA)
    assert codes - POLICY_CODES <= set(MITIGATIONS)


def test_verifier_ignores_a_challenge_without_a_real_supporting_fact():
    out, log, _ = review({"reviews": [{"code": "BILL_OUTLIER_VS_PEERS", "verdict": "weakened", "confidence_multiplier": 0.5,
                                       "supporting_fact": {"name": "doc_itemised_bill", "value": True}, "reason": "it is itemised"}]},
                         [ind()], {"doc_itemised_bill": True, "bill_peer_n": 40})
    assert out[0].confidence == 0.8 and log[0]["verdict"] == "supported" and "ignored" in log[0]["note"]


def test_verifier_applies_a_challenge_with_a_real_supporting_fact():
    out, log, _ = review({"reviews": [{"code": "PRE_EXISTING_NEAR_INCEPTION", "verdict": "weakened", "confidence_multiplier": 0.5,
                                       "supporting_fact": {"name": "declared_has_condition", "value": True}, "reason": "declared",
                                       "counter_evidence": ["declared on the application"]}]},
                         [ind("PRE_EXISTING_NEAR_INCEPTION")], {"declared_has_condition": True})
    assert out[0].confidence == pytest.approx(0.4) and "declared on the application" in out[0].counter_evidence and log[0]["verdict"] == "weakened"


def test_verifier_can_drop_but_never_raise_or_add():
    facts = {"declared_has_condition": True}
    out, _, _ = review({"reviews": [{"code": "PRE_EXISTING_NEAR_INCEPTION", "verdict": "unsupported", "confidence_multiplier": 0,
                                     "supporting_fact": {"name": "declared_has_condition", "value": True}, "reason": "declared"}]},
                       [ind("PRE_EXISTING_NEAR_INCEPTION")], facts)
    assert out == []
    out, _, _ = review({"reviews": [{"code": "BILL_OUTLIER_VS_PEERS", "verdict": "supported", "confidence_multiplier": 5, "reason": "ok"},
                                    {"code": "SOMETHING_NEW", "verdict": "supported", "confidence_multiplier": 1, "reason": "new"}]},
                       [ind(confidence=0.6)], {})
    assert [i.code for i in out] == ["BILL_OUTLIER_VS_PEERS"] and out[0].confidence == 0.6


def test_verifier_does_not_challenge_policy_signals_and_skips_the_model_call():
    llm = FakeLLM()  # any call would raise IndexError
    out, log, meta = Verifier(None, llm).review({"claim_id": "CLM-000001"}, {}, [ind("HIGH_FRAUD_AREA", 0.5), ind("COVER_VS_INCOME_MISMATCH", 0.5)])
    assert [i.confidence for i in out] == [0.5, 0.5] and meta["status"] == "nothing to challenge" and not llm.offered
    assert all(entry["verdict"] == "policy signal: not challenged" for entry in log)


def test_verifier_keeps_findings_when_the_model_is_unavailable_or_replies_with_junk():
    out, _, meta = review({"unexpected": "shape"}, [ind()], {})  # valid JSON, no reviews: everything stays as proposed
    assert out[0].confidence == 0.8
    llm = FakeLLM(reply(content="not json at all"))
    out, log, meta = Verifier(None, llm).review({"claim_id": "CLM-000001"}, {}, [ind(), ind("HIGH_FRAUD_AREA")])
    assert [i.code for i in out] == ["BILL_OUTLIER_VS_PEERS", "HIGH_FRAUD_AREA"] and meta["status"].startswith("skipped")


# ---------- Explainer: faithfulness check ----------

CORPUS = "Claim CLM-000123 billed 42000 which is 2.6 times the peer median; earlier claim CLM-000099."


def test_faithful_accepts_text_that_only_uses_known_facts():
    assert faithful(["The bill on CLM-000123 is 2.6 times the usual amount and needs checking."], 1, CORPUS) is None


@pytest.mark.parametrize("text, why", [
    ("CLM-000777 looks similar.", "unknown ID"),
    ("The bill is 3.9 times the usual amount.", "unknown number"),
    ("This is proven fraud.", "banned"),
    ("The claim is approved.", "banned"),
])
def test_faithful_rejects_invented_or_overconfident_text(text, why):
    assert why.split()[0] in (faithful([text], 1, CORPUS) or "")


def test_faithful_rejects_wrong_reason_count():
    assert "expected 2" in faithful(["only one"], 2, CORPUS)


class RecordingLLM:
    def __init__(self, content):
        self.content, self.max_tokens = content, None

    def chat(self, messages, variant="default", tools=None, json_mode=False, max_tokens=None):
        self.max_tokens = max_tokens
        return reply(self.content)


def _red_result():
    return FinalResult(claim_id="CLM-000123", trust_score=0.1, band="red", evidence_status="sufficient", reasons=["Original sentence."], next_action="Review.",
                       indicators=[Indicator(code="BILL_OUTLIER_VS_PEERS", confidence=0.9, evidence_ids=["CLM-000123"], explanation="The bill is 2.6 times the usual amount.")])


def test_explainer_leaves_room_for_the_models_hidden_thinking():
    """With 900 tokens the small model spent them all on thinking and answered nothing (about a third of saved explanations fell back)."""
    llm = RecordingLLM(json.dumps({"reasons": ["The bill on CLM-000123 is 2.6 times the usual amount and needs checking."]}))
    reasons, meta = Explainer(llm).explain(_red_result())
    assert llm.max_tokens >= 2000 and meta["status"] == "ok" and reasons[0].startswith("The bill")


def test_explainer_keeps_the_original_sentences_when_the_model_answers_nothing():
    reasons, meta = Explainer(RecordingLLM(None)).explain(_red_result())
    assert reasons == ["Original sentence."] and meta["status"].startswith("fallback")
