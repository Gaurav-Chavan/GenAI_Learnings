"""Everything the claim page needs, as plain functions: the claim's facts, the customer as a reviewer may see them, and the decision rules.

A decision always belongs to a human reviewer. The system's band is only a recommendation, and a disagreement is recorded, never blocked.
"""
from __future__ import annotations

import duckdb

from ..data.config import paths
from .plain import document_name
from .store import Store

CHOICES = {"agree": "Agree with the system's band", "override": "Change the band", "hold": "Hold: more information needed", "escalate": "Escalate to someone else"}
BANDS = ("red", "amber", "green")
# what a reviewer can say was wrong with the system's output; they become labels for later evaluation
TAGS = {"evidence_unsupported": "The evidence did not support the finding", "evidence_missing": "Evidence I needed was not shown", "reasoning_wrong": "The reasoning is unclear or wrong",
        "rule_misread": "A policy or rule was misread", "wording": "The wording or tone is off"}


class DecisionError(ValueError):
    """The reviewer's input cannot be saved as it stands. The message is written for the reviewer."""


def claim_overview(cfg: dict, claim_id: str) -> dict | None:
    """The claim and its customer as a reviewer may see them: customer ID, coarse bands and a generalised location, never a name or contact detail."""
    silver = duckdb.connect(str(paths(cfg)["silver"]), read_only=True)
    try:
        cur = silver.execute("SELECT c.claim_id, c.customer_id, c.policy_id, c.line, c.filed_date, c.incident_date, c.admit_date, c.discharge_date, c.amount, c.invoice_no, "
                             "c.incident_district, c.description, p.provider_id, p.name AS provider_name, p.district AS provider_district, pr.name AS procedure_name "
                             "FROM claims c LEFT JOIN providers p USING (provider_id) LEFT JOIN procedures pr ON pr.code = c.procedure_code WHERE c.claim_id = ?", [claim_id])
        row = cur.fetchone()
        if not row:
            return None
        claim = dict(zip([d[0] for d in cur.description], row))
        claim["documents"] = [dict(doc_id=r[0], doc_type=r[1], name=document_name(r[1]), doc_date=r[2]) for r in
                              silver.execute("SELECT doc_id, doc_type, doc_date FROM documents WHERE claim_id = ? ORDER BY doc_id", [claim_id]).fetchall()]
        cur = silver.execute("SELECT age_band, gender, occupation_category, income_band, state, district, urban_rural, tenure_band, generalisation_level "
                             "FROM reviewer_customers WHERE customer_id = ?", [claim["customer_id"]])
        r = cur.fetchone()
        claim["customer"] = dict(zip([d[0] for d in cur.description], r)) if r else {}
        return claim
    finally:
        silver.close()


def customer_facts(customer: dict) -> list[tuple[str, str]]:
    """The reviewer's view of a customer. Location, gender and age are generalised for small groups, so this can show 'withheld'."""
    if not customer:
        return []
    where = customer["district"] if customer["generalisation_level"] == 0 else customer["state"] if customer["district"] == "(state level)" else "withheld"
    return [("Age band", customer["age_band"]), ("Gender", customer["gender"]), ("Occupation", customer["occupation_category"]), ("Income band", customer["income_band"]),
            ("Location", where), ("Customer for", customer["tenure_band"])]


def record_decision(store: Store, claim_id: str, reviewer: str, choice: str, band: str, note: str, system_band: str,
                    tags: list[str] | None = None, escalate_to: str = "") -> str:
    """Validate and save one decision. 'agree' keeps the system's band, 'override' sets the reviewer's band (a note is required), 'hold' keeps the claim open,
    'escalate' keeps it open and hands it to someone else (a note and the person's name are required). Tags are optional on every choice."""
    if not reviewer.strip():
        raise DecisionError("Please enter your reviewer name in the sidebar first.")
    if choice not in CHOICES:
        raise DecisionError("Choose what you decide.")
    tags = list(dict.fromkeys(tags or []))
    unknown = [t for t in tags if t not in TAGS]
    if unknown:
        raise DecisionError(f"Unknown tag: {', '.join(unknown)}.")
    note, escalate_to = note.strip(), escalate_to.strip()
    if choice == "escalate":
        if not escalate_to:
            raise DecisionError("Please say who you are escalating this claim to.")
        if escalate_to.lower() == reviewer.strip().lower():
            raise DecisionError("Escalate to someone other than yourself.")
        if not note:
            raise DecisionError("Please say why you are escalating this claim.")
    if choice == "override":
        if band not in BANDS:
            raise DecisionError("Choose the band you are changing it to.")
        if band == system_band:
            raise DecisionError("That is the system's own band. Choose 'Agree' instead, or pick a different band.")
        if not note:
            raise DecisionError("Please write a short reason for changing the band.")
        final = band
    else:
        final = system_band
    if choice == "hold" and not note:
        raise DecisionError("Please say what information is needed.")
    decision_id = store.add_decision(claim_id, reviewer.strip(), choice, final, note, system_band, tags=tags, escalated_to=escalate_to if choice == "escalate" else "")
    if choice == "escalate":
        store.assign(claim_id, escalate_to, reviewer)  # the claim now belongs to the person it was escalated to
    return decision_id


def next_unreviewed(queue_rows: list[dict], current: str | None) -> str | None:
    """The next claim after `current` (in queue order) that has no decision yet; wraps around; None when everything is reviewed."""
    open_ids = [r["claim_id"] for r in queue_rows if r["reviewed"] is None and r["claim_id"] != current]
    if not open_ids:
        return None
    order = [r["claim_id"] for r in queue_rows]
    after = [c for c in open_ids if current in order and order.index(c) > order.index(current)]
    return (after or open_ids)[0]
