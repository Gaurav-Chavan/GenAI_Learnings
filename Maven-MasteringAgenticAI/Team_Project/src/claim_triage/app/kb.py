"""The Knowledge Base tab's logic: which findings a reviewer may propose for the knowledge base, and how a proposal is saved.

Nothing is learned silently. Only findings from claims the reviewer has already decided are offered, each needs a written note, and a signal from a
claim where the reviewer contradicted the system needs a longer note and carries an audit tag. A saved signal starts as `pending`; it reaches agents
only after the safety check (a later step) makes it `active`. The scorer never reads signals.
"""
from __future__ import annotations

import json

from .plain import finding_name
from .store import Store

# statistical signals are computed fresh for every claim from the data, so there is nothing to "learn" from them
NOT_LEARNABLE = {"HIGH_FRAUD_AREA", "COVER_VS_INCOME_MISMATCH"}
MIN_NOTE = 15
MIN_NOTE_DISAGREED = 50
TAKEN = ("pending", "active")  # a signal in one of these states cannot be proposed again; a blocked one can


class KnowledgeError(ValueError):
    """A proposal cannot be saved as it stands. The message is written for the reviewer and names the signal."""


def key_of(claim_id: str, code: str) -> str:
    return f"{claim_id}|{code}"


def candidate_signals(store: Store) -> list[dict]:
    """One candidate per finding of every claim the reviewer has decided (a hold or an escalation is not a decision), newest decision first.

    status: new | pending | active | blocked (blocked ones may be proposed again).
    """
    decided = store.rows("SELECT claim_id, reviewer, decision, final_band, system_band, disagrees_with_system, note, created_at FROM decisions "
                         "WHERE decision NOT IN ('hold', 'escalate') QUALIFY row_number() OVER (PARTITION BY claim_id ORDER BY created_at DESC) = 1 ORDER BY created_at DESC")
    latest = {}
    for s in store.rows("SELECT source_claim_id, indicator_code, status FROM signals ORDER BY created_at"):
        latest[key_of(s["source_claim_id"], s["indicator_code"])] = s["status"]  # the newest signal for the same finding decides its status
    out = []
    for d in decided:
        a = store.get_assessment(d["claim_id"], "rules")
        if not a:
            continue
        for ind in a["result"].get("indicators") or []:
            if ind["code"] in NOT_LEARNABLE:
                continue
            key = key_of(d["claim_id"], ind["code"])
            out.append(dict(key=key, claim_id=d["claim_id"], code=ind["code"], finding=finding_name(ind["code"]), text=ind["explanation"],
                            evidence_ids=ind["evidence_ids"], decision_by=d["reviewer"], system_band=d["system_band"], final_band=d["final_band"],
                            disagreed=bool(d["disagrees_with_system"]), decision_note=d["note"] or "", decided_at=d["created_at"], status=latest.get(key, "new")))
    return out


def submit_signals(store: Store, reviewer: str, picks: list[dict]) -> list[str]:
    """Save the ticked candidates as pending signals. picks: [{'key': 'CLM-...|CODE', 'note': '...'}]. All are checked first; any error stops the whole save."""
    if not reviewer.strip():
        raise KnowledgeError("Please enter your reviewer name in the sidebar first.")
    if not picks:
        raise KnowledgeError("Tick at least one signal to add.")
    by_key = {c["key"]: c for c in candidate_signals(store)}
    problems, ready = [], []
    for p in picks:
        c = by_key.get(p["key"])
        label = f"{c['finding']} ({c['claim_id']})" if c else p["key"]
        note = (p.get("note") or "").strip()
        need = MIN_NOTE_DISAGREED if c and c["disagreed"] else MIN_NOTE
        if c is None:
            problems.append(f"{label}: this claim has no decision yet, or the finding does not exist.")
        elif c["status"] in TAKEN:
            problems.append(f"{label}: already {'waiting for the safety check' if c['status'] == 'pending' else 'in the knowledge base'}.")
        elif len(note) < need:
            why = " You contradicted the system on this claim, so please explain the lesson in more detail." if c["disagreed"] else ""
            problems.append(f"{label}: please write a note of at least {need} characters.{why}")
        else:
            ready.append((c, note))
    if problems:
        raise KnowledgeError("\n".join(problems))
    return [store.add_signal(c["claim_id"], c["code"], c["text"], c["evidence_ids"], reviewer.strip(), note, disagreed=c["disagreed"]) for c, note in ready]


def signal_rows(store: Store) -> list[dict]:
    """Every signal proposed so far, newest first, with its evidence IDs as a list."""
    rows = store.rows("SELECT * FROM signals ORDER BY created_at DESC")
    for r in rows:
        r["evidence_ids"] = json.loads(r["evidence_ids"] or "[]")
        r["finding"] = finding_name(r["indicator_code"])
    return rows
