"""The Investigator: one agent, a bounded tool-calling loop, checklists (skills) loaded as needed.

It gets the claim and a customer summary, decides which read-only tools to call, and ends by calling `submit_findings`. It can never
score, approve or reject anything, and it can only use the ToolBox. Evidence IDs it cites are checked later (Verifier step 1).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..contracts import Indicator, InvestigationResult, MissingItem
from ..llm import LLM
from ..tools import ToolBox, ToolError
from .base import Usage

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
DOCUMENTS = ("bill", "itemised_bill", "discharge_summary", "repair_estimate", "fir", "inspection_report", "death_certificate",
             "claim_form", "cause_of_death", "nominee_consent", "provider_registration")
GENERIC = {"HIGH_FRAUD_AREA", "COVER_VS_INCOME_MISMATCH", "SHARED_ENTITY_CLUSTER"}
ALLOWED = {"health": GENERIC | {"DUPLICATE_CLAIM", "BILL_OUTLIER_VS_PEERS", "PRE_EXISTING_NEAR_INCEPTION"},
           "term": GENERIC | {"NOMINEE_CHANGE_NEAR_EVENT", "DOC_DATE_MISMATCH", "EARLY_DEATH_VS_HEALTH_HISTORY"},
           "motor": GENERIC | {"REPEAT_INCIDENT_CLAIM", "DAMAGE_IN_PRIOR_INSPECTION"}}
CROSS_POLICY_EXTRA = {"CROSS_POLICY_INCIDENT_MISMATCH", "EARLY_DEATH_VS_HEALTH_HISTORY", "PRE_EXISTING_NEAR_INCEPTION"}
LOW_SEVERITY = {"HIGH_FRAUD_AREA", "COVER_VS_INCOME_MISMATCH"}
MAX_TOOL_TEXT = 6000

SUBMIT = {"type": "function", "function": {
    "name": "submit_findings",
    "description": "Submit your final findings for this claim. Call this exactly once, when you are done investigating.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["indicators", "missing_evidence", "summary"], "properties": {
        "indicators": {"type": "array", "items": {"type": "object", "required": ["code", "confidence", "evidence_ids", "explanation"], "properties": {
            "code": {"type": "string"}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_ids": {"type": "array", "items": {"type": "string"}}, "explanation": {"type": "string"},
            "counter_evidence": {"type": "array", "items": {"type": "string"}}, "hard_rule": {"type": "boolean"}}}},
        "missing_evidence": {"type": "array", "items": {"type": "object", "required": ["document", "reason"], "properties": {
            "document": {"type": "string", "enum": list(DOCUMENTS)}, "reason": {"type": "string"}}}},
        "summary": {"type": "string"}}}}}


def read_skill(name: str) -> str:
    return (SKILLS_DIR / f"{name}.md").read_text(encoding="utf-8")


def allowed_codes(line: str, cross: bool) -> set[str]:
    return set(ALLOWED[line]) | (CROSS_POLICY_EXTRA if cross else set())


def compact(result: dict) -> str:
    """Tool result as text for the model, trimmed so one call cannot flood the conversation."""
    text = json.dumps({k: v for k, v in result.items() if k != "args"}, default=str, separators=(",", ":"))
    return text if len(text) <= MAX_TOOL_TEXT else text[:MAX_TOOL_TEXT] + '..."(truncated)"'


def to_result(args: dict, claim_id: str, allowed: set[str]) -> tuple[InvestigationResult, list[str]]:
    notes, indicators = [], []
    for item in args.get("indicators") or []:
        code = item.get("code")
        if code not in allowed:
            notes.append(f"dropped indicator with unknown or not-allowed code {code!r}")
            continue
        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0))))
        except (TypeError, ValueError):
            notes.append(f"dropped {code}: confidence is not a number")
            continue
        indicators.append(Indicator(code=code, severity="low" if code in LOW_SEVERITY else "high", confidence=confidence,
                                    evidence_ids=[str(e) for e in item.get("evidence_ids") or []], explanation=str(item.get("explanation", ""))[:500],
                                    counter_evidence=[str(c)[:300] for c in item.get("counter_evidence") or []],
                                    hard_rule=bool(item.get("hard_rule")) and code == "DUPLICATE_CLAIM"))
    missing = [MissingItem(document=m["document"], reason=str(m.get("reason", ""))[:300]) for m in args.get("missing_evidence") or []
               if isinstance(m, dict) and m.get("document") in DOCUMENTS]
    return InvestigationResult(claim_id=claim_id, agent_type="investigator", indicators=indicators, missing_evidence=missing,
                               summary=str(args.get("summary", ""))[:600]), notes


class Investigator:
    MAX_TURNS = 16

    def __init__(self, box: ToolBox, llm: LLM | None = None):
        self.box = box
        self.llm = llm or LLM("investigator")

    def run(self, claim_id: str) -> dict:
        box, limits = self.box, self.llm.limits
        claim = box.call("get_claim", claim_id=claim_id)["records"][0]
        summary = box.call("get_customer_summary", customer_id=claim["customer_id"])["records"][0]
        cross = (summary.get("policies_total") or 0) > 1
        variant = "cross_policy" if cross else "default"
        budget = limits["max_tool_calls_cross_policy"] if cross else limits["max_tool_calls"]
        allowed = allowed_codes(claim["line"], cross)
        system = "\n\n".join([read_skill("common"), read_skill(claim["line"])] + ([read_skill("cross_policy")] if cross else []))
        system += f"\n\nYou have a budget of {budget} tool calls (the claim and customer summary below were fetched for you and do not count)."
        context = {"claim": {k: claim[k] for k in ("claim_id", "line", "filed_date", "incident_date", "amount", "provider_id", "procedure_code", "invoice_no",
                                                  "customer_id", "policy_id", "vehicle_token", "payout_token", "damage_items", "documents", "description")},
                   "customer_summary": {k: summary[k] for k in ("customer_id", "policies_total", "policies_active", "lines", "claims_last_12m", "claims_last_36m",
                                                               "contact_changes", "nominee_changes", "customers_sharing_phone", "customers_sharing_bank")},
                   "checklists_loaded": [claim["line"]] + (["cross_policy"] if cross else []),
                   "reminder": f"When a tool takes as_of, pass {claim['filed_date']} (this claim's filing date)."}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": "Investigate this claim.\n" + json.dumps(context, default=str)}]
        tool_specs = box.specs() + [SUBMIT]
        usage, tools_called, seen, notes = Usage(), [], {}, []
        submitted, loop_guard, nudged, failed = None, False, False, None

        for turn in range(self.MAX_TURNS):
            offered = tool_specs if len(tools_called) < budget else [SUBMIT]
            resp = self.llm.chat(messages, variant=variant, tools=offered, max_tokens=2000)
            usage.add(resp)
            assistant = {"role": "assistant", "content": resp.content or ""}
            if resp.tool_calls:
                assistant["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in resp.tool_calls]
            messages.append(assistant)
            if not resp.tool_calls:
                if nudged:
                    failed = "the investigator ended without submitting findings"
                    break
                nudged = True
                messages.append({"role": "user", "content": "Please finish by calling submit_findings."})
                continue
            for call in resp.tool_calls:
                name = call["name"]
                try:
                    args = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                    text = "error: the arguments were not valid JSON"
                else:
                    if name == "submit_findings":
                        submitted = args
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": "received"})
                        continue
                    if len(tools_called) >= budget:
                        loop_guard = True
                        text = "error: tool budget used up; call submit_findings now"
                    else:
                        tools_called.append(name)
                        key = (name, json.dumps(args, sort_keys=True, default=str))
                        if key in seen:
                            text = seen[key] + " (this exact call was already made)"
                        else:
                            try:
                                text = compact(box.call(name, **args))
                            except (ToolError, TypeError) as exc:
                                text = f"error: {exc}"
                            seen[key] = text
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
            if submitted is not None:
                break
            if len(tools_called) >= budget and not loop_guard:
                loop_guard = True
                messages.append({"role": "user", "content": "Tool budget used up. Call submit_findings now with what you have."})

        if submitted is None:
            result = InvestigationResult(claim_id=claim_id, agent_type="investigator", summary=failed or "no findings submitted")
            notes.append(failed or "no findings submitted")
        else:
            result, parse_notes = to_result(submitted, claim_id, allowed)
            notes += parse_notes
        meta = dict(variant=variant, checklists=context["checklists_loaded"], budget=budget, tool_calls=len(tools_called), tools=tools_called,
                    loop_guard=loop_guard, submitted=submitted is not None, notes=notes, allowed=sorted(allowed), **usage.summary())
        return {"result": result, "meta": meta, "usage": usage}
