"""The reviewer dashboard.

    uv run streamlit run src/claim_triage/app/dashboard.py

Customers appear by ID only. A green band is never an approval: a human reviewer decides every claim.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from claim_triage.app.claim_view import BANDS, CHOICES, TAGS, DecisionError, claim_overview, customer_facts, next_unreviewed, record_decision
from claim_triage.app.eval_view import BANDS as EVAL_BANDS
from claim_triage.app.eval_view import NOT_MEASURED, confusion, fairness, knowledge_counts, load_runs, per_scenario
from claim_triage.app.evidence import EvidenceLookup
from claim_triage.app.kb import KnowledgeError, candidate_signals, signal_rows, submit_signals
from claim_triage.app.plain import document_name, finding_name, finding_note
from claim_triage.app.review_queue import BAND_ICON, BAND_LABEL, apply_filters, load_queue, summarise
from claim_triage.app.store import OPEN_DECISIONS, Store
from claim_triage.data.config import load_config
from claim_triage.learning.gate import GateResult

st.set_page_config(page_title="Claim triage", page_icon="\U0001F6E1️", layout="wide")

DEMO = os.environ.get("CLAIM_TRIAGE_DEMO") == "1"  # the public demo: no model calls, and decisions are not kept
SYSTEM_CHOICES = {"Rules-only (every claim)": "rules", "Saved agent runs (evaluated claims)": "agents"}
PAGES = ["Review queue", "Claim", "Knowledge base", "Evaluation"]
SIGNAL_STATUS = {"pending": "\U0001F552 Waiting for the safety check", "active": "✅ In the knowledge base", "blocked": "⛔ Blocked by the safety check", "new": "New"}


def goto(page: str, claim_id: str | None = None) -> None:
    """Button callback: runs before the page redraws, so it may set the navigation widget."""
    st.session_state["page"] = page
    if claim_id:
        st.session_state["claim_id"] = claim_id


def band_text(band: str) -> str:
    return f"{BAND_ICON[band]} {BAND_LABEL[band]}"


def facts_table(pairs: list[tuple[str, str]]) -> None:
    """A small two-column list of facts: labels down the side, values beside them."""
    st.table(pd.DataFrame({"": [str(v) for _, v in pairs]}, index=[k for k, _ in pairs]))


# ------------------------------------------------------------------ review queue
def queue_page(cfg: dict, store: Store, system: str) -> None:
    rows = load_queue(cfg, store, system)
    if not rows:
        st.info("No assessed claims yet. Run `uv run python -m claim_triage.app.backfill` once, then reload this page.")
        return
    totals = summarise(rows)
    cols = st.columns(5)
    cols[0].metric("Claims in view", totals["total"])
    cols[1].metric("Not yet reviewed", totals["unreviewed"])
    cols[2].metric(f"{BAND_ICON['red']} Red", totals["red"])
    cols[3].metric(f"{BAND_ICON['amber']} Review needed", totals["amber"])
    cols[4].metric(f"{BAND_ICON['green']} Green", totals["green"])

    f1, f2, f3, f4 = st.columns([2, 2, 1.4, 2])
    bands = f1.multiselect("Band", list(BAND_LABEL), format_func=band_text)
    lines = f2.multiselect("Line", ["health", "term", "motor"])
    only_open = f3.toggle("Not reviewed only")
    mine = f3.toggle("Assigned to me", help="Shows only claims given to the reviewer name in the sidebar, including claims escalated to you.")
    search = f4.text_input("Find a claim or customer ID", placeholder="CLM-001653 or CUST-000123")
    me = st.session_state.get("reviewer", "").strip()
    if mine and not me:
        st.warning("Enter your reviewer name in the sidebar to see the claims assigned to you.")
    shown = apply_filters(rows, bands, lines, only_open, search, assignee=me if mine and me else None)
    if mine and not me:
        shown = []

    st.caption(f"{len(shown)} claims, least trusted first. Select a row, then open it.")
    table = pd.DataFrame([{
        "Band": band_text(r["band"]), "Trust": r["trust_score"], "Claim": r["claim_id"], "Customer": r["customer_id"],
        "Line": r["line"], "Filed": r["filed_date"], "Amount": r["amount"], "Why (first reason)": r["top_reason"], "Findings": r["flags"],
        "Missing docs": r["missing"], "Assigned to": r["assignee"] or "-", "Reviewed": band_text(r["reviewed"]) if r["reviewed"] else "-",
    } for r in shown])
    event = st.dataframe(table, hide_index=True, width="stretch", height=430, on_select="rerun", selection_mode="single-row", key=f"queue-{system}",
                         column_config={"Trust": st.column_config.ProgressColumn("Trust", min_value=0.0, max_value=1.0, format="%.2f", width="small"),
                                        "Amount": st.column_config.NumberColumn("Amount", format="₹ %d"),
                                        "Why (first reason)": st.column_config.TextColumn(width="large")})
    picked = event.selection.rows
    if picked:
        chosen = shown[picked[0]]
        st.session_state["claim_id"] = chosen["claim_id"]
        st.button(f"Open {chosen['claim_id']}  →", type="primary", on_click=goto, args=("Claim", chosen["claim_id"]))
    elif st.session_state.get("claim_id"):
        st.button(f"Back to {st.session_state['claim_id']}  →", on_click=goto, args=("Claim",))


# ------------------------------------------------------------------ claim page
def findings_section(result: dict, system: str) -> list[str]:
    """Show the findings behind the score; return every evidence ID they cite."""
    cited: list[str] = []
    st.subheader("What was found")
    indicators = sorted(result.get("indicators") or [], key=lambda i: -i["confidence"])
    if system == "rules" or indicators:
        if not indicators:
            st.write("Nothing suspicious was found by the checks that were run.")
        for ind in indicators:
            with st.expander(f"{finding_name(ind['code'])}  ·  confidence {ind['confidence']:.0%}", expanded=ind["confidence"] >= 0.5):
                st.write(ind["explanation"])
                if ind.get("hard_rule"):
                    st.caption("Hard rule: an identical invoice number was found, which caps the trust score on its own.")
                for c in ind.get("counter_evidence") or []:
                    st.markdown(f"- Could be innocent: {c}")
                if ind["evidence_ids"]:
                    st.caption("Evidence: " + ", ".join(f"`{e}`" for e in ind["evidence_ids"]))
                st.caption(finding_note(ind["code"]))
                cited += ind["evidence_ids"]
    else:  # a saved agent run lists what the Investigator proposed and what the Verifier did with it
        st.write(result.get("summary") or "No summary was saved with this run.")
        reviews = result.get("verifier_reviews") or []
        for code in result.get("proposed", []):
            review = next((r for r in reviews if r["code"] == code), None)
            verdict = f" — second opinion: {review['verdict']} (x{review['multiplier']}). {review['reason']}" if review else ""
            st.markdown(f"- **{finding_name(code)}**{verdict}")
        if result.get("tools"):
            st.caption("Tools the Investigator used: " + ", ".join(result["tools"]))
    return list(dict.fromkeys(cited))


def record_section(cfg: dict, claim: dict, cited: list[str]) -> None:
    st.subheader("Look at a record")
    options = list(dict.fromkeys(cited + [d["doc_id"] for d in claim["documents"]]))
    if not options:
        st.caption("This assessment cites no records.")
        return
    pick = st.selectbox("Evidence cited, and documents on file", options, key=f"record-{claim['claim_id']}")
    lookup = EvidenceLookup(cfg)
    try:
        rec = lookup.describe(pick)
    finally:
        lookup.close()
    with st.container(border=True):
        st.markdown(f"**{rec['kind']}: {rec['title']}**  \n`{rec['id']}`")
        if rec["facts"]:
            facts_table(rec["facts"])
        if rec["text"]:
            st.info(rec["text"])


def decision_panel(store: Store, claim_id: str, system: str, system_band: str) -> None:
    st.subheader("Your decision")
    st.caption("You decide this claim. The system's band is a recommendation, and disagreeing with it is fine; it is recorded for audit.")
    history = store.decisions_for(claim_id)
    for d in history:
        mark = " · disagreed with the system" if d["disagrees_with_system"] else ""
        outcome = "" if d["decision"] in OPEN_DECISIONS else " → " + band_text(d["final_band"])
        to = f" to **{d['escalated_to']}**" if d.get("escalated_to") else ""
        tag_line = "  \n  Tags: " + ", ".join(TAGS.get(t, t) for t in d["tags"]) if d["tags"] else ""
        note_line = f"  \n  _{d['note']}_" if d["note"] else ""
        st.markdown(f"- {d['created_at']:%d %b %H:%M} · **{d['reviewer']}** · {CHOICES.get(d['decision'], d['decision'])}{to}{outcome}{mark}{tag_line}{note_line}")
    n = st.session_state.setdefault("saves", 0)
    with st.form(f"decision-{claim_id}-{n}"):
        choice = st.radio("What do you decide?", list(CHOICES), format_func=CHOICES.get)
        band = st.selectbox("New band (used only if you change it)", BANDS, index=BANDS.index(system_band), format_func=band_text)
        escalate_to = st.text_input("Escalate to (used only if you escalate)", placeholder="Reviewer name, for example senior-1")
        note = st.text_area("Reason or note", placeholder="Required if you change the band, hold the claim or escalate it.")
        tags = st.multiselect("What was wrong with the system's output? (optional)", list(TAGS), format_func=TAGS.get,
                              help="Tags are kept with your decision and help us measure where the system goes wrong.")
        if st.form_submit_button("Save decision", type="primary"):
            try:
                record_decision(store, claim_id, st.session_state.get("reviewer", ""), choice, band, note, system_band, tags=tags, escalate_to=escalate_to)
            except DecisionError as exc:
                st.error(str(exc))
            else:
                st.session_state["saves"] = n + 1
                st.session_state["just_saved"] = claim_id
                st.rerun()


def owner_panel(store: Store, claim_id: str) -> None:
    """Who owns this claim: shown on the claim page and changed by hand here (an escalation also sets it)."""
    owner = store.assignee_of(claim_id)
    with st.expander(f"Owner of this claim: {owner or 'nobody yet'}"):
        new_owner = st.text_input("Give this claim to", value=owner or "", key=f"owner-{claim_id}-{owner}", placeholder="Reviewer name; leave empty to remove the owner")
        if st.button("Save owner", key=f"save-owner-{claim_id}"):
            by = st.session_state.get("reviewer", "").strip()
            if not by:
                st.error("Please enter your reviewer name in the sidebar first.")
            else:
                store.assign(claim_id, new_owner, by)
                st.rerun()


def claim_page(cfg: dict, store: Store, system: str) -> None:
    claim_id = st.session_state.get("claim_id")
    st.button("← Back to the queue", on_click=goto, args=("Review queue",))
    if not claim_id:
        st.info("Pick a claim in the review queue first.")
        return
    claim = claim_overview(cfg, claim_id)
    row = store.get_assessment(claim_id, system)
    if claim is None or (row is None and system == "rules"):
        st.error(f"No assessment is stored for {claim_id}.")
        return
    if row is None:
        st.info("There is no saved agent run for this claim, so the rules-only assessment is shown.")
        system, row = "rules", store.get_assessment(claim_id, "rules")
    result = row["result"]

    st.title(claim_id)
    st.caption(f"Customer {claim['customer_id']}  ·  {claim['line']} claim  ·  filed {claim['filed_date']}  ·  ₹{claim['amount']:,}")
    owner = store.assignee_of(claim_id)
    st.caption(f"Assigned to {owner}" if owner else "Not assigned to anyone yet")
    if st.session_state.get("just_saved") == claim_id:
        last = (store.decisions_for(claim_id) or [{}])[0]
        st.success(f"Escalated to {last['escalated_to']}. The claim stays open in the queue." if last.get("decision") == "escalate" else "Decision saved to the audit trail.")
        nxt = next_unreviewed(load_queue(cfg, store, system), claim_id)
        if nxt:
            st.button(f"Next claim to review: {nxt}  →", type="primary", on_click=goto, args=("Claim", nxt))
        st.session_state.pop("just_saved")

    m = st.columns(4)
    m[0].metric("Band", band_text(row["band"]))
    m[1].metric("Trust score", f"{row['trust_score']:.2f}")
    m[2].metric("Evidence", "Enough on file" if row["evidence_status"] == "sufficient" else "Missing or not checked" if row["evidence_status"] == "insufficient" else "Not recorded")
    m[3].metric("Findings", len(result.get("indicators") or result.get("proposed") or []))
    st.progress(float(row["trust_score"]), text="Trust: 0 = very low, 1 = high. Below 0.3 red, 0.3 to 0.8 review needed, above 0.8 green.")
    if row["band"] == "green":
        st.info("Green is not an approval. A reviewer still decides every claim.")

    left, right = st.columns([2, 1], gap="large")
    with left:
        st.subheader("Why this score")
        for reason in result.get("reasons", []):
            st.markdown(f"- {reason}")
        cited = findings_section(result, system)
        missing = result.get("missing_evidence") or []
        todo = result.get("outstanding_checks") or []
        if missing or todo:
            st.subheader("Still to check")
            for item in missing:
                st.markdown(f"- **{document_name(item['document'])}** is missing. {item['reason']}")
            for line in todo:
                if not any(document_name(i["document"]) in line for i in missing):
                    st.markdown(f"- {line}")
        if result.get("next_action"):
            st.markdown(f"**Suggested next step:** {result['next_action']}")
        if result.get("counterfactual"):
            st.caption(result["counterfactual"])
        record_section(cfg, claim, cited)
        st.caption(f"Source: {row['source']}.")
    with right:
        with st.container(border=True):
            st.subheader("The claim")
            facts = [("Provider", claim["provider_name"] or "-"), ("Procedure", claim["procedure_name"] or "-"), ("Admitted", claim["admit_date"] or "-"),
                     ("Discharged", claim["discharge_date"] or "-"), ("Incident", claim["incident_date"] or "-"), ("Invoice", claim["invoice_no"] or "-")]
            facts_table([(k, v) for k, v in facts if v != "-"])
            st.caption("Documents on file: " + (", ".join(d["name"] for d in claim["documents"]) or "none"))
        with st.container(border=True):
            st.subheader("The customer")
            st.caption(f"{claim['customer_id']}. Only coarse bands are shown; the person cannot be identified.")
            facts_table(customer_facts(claim["customer"]))
        with st.container(border=True):
            decision_panel(store, claim_id, system, row["band"])
        owner_panel(store, claim_id)


# ------------------------------------------------------------------ knowledge base
def kb_page(store: Store) -> None:
    st.header("Knowledge base")
    st.markdown("The system can learn from your decisions, but **nothing is learned silently**. Tick the findings worth keeping and say why. "
                "Each ticked finding is saved as *pending*, checked so it does not make the system worse, and only then offered to the agents as background. "
                "It never changes a score by itself.")
    if DEMO:
        st.info("The safety check calls AI models and the knowledge graph, so it is switched off in the public demo. You can still tick findings to see the page, "
                "but nothing is sent.")
    for r in st.session_state.pop("kb_results", []):
        label = f"{r['finding']} ({r['claim_id']})"
        if r["status"] == "active":
            st.success(f"Added to the knowledge base: {label}. {r['summary']}")
        elif r["status"] == "blocked":
            st.error(f"Not added: {label}. {r['summary']}")
        else:
            st.warning(f"Still pending: {label}. {r['summary']}")
    candidates = candidate_signals(store)
    offered = [c for c in candidates if c["status"] in ("new", "blocked")]
    st.subheader("Findings from claims you have decided")
    if not candidates:
        st.info("Nothing to offer yet. Decide a claim on the Claim page first; its findings will appear here.")
    elif not offered:
        st.info("Every finding from your decided claims has already been proposed. See the list below.")
    else:
        n = st.session_state.setdefault("kb_n", 0)
        picks = []
        for c in offered[:50]:
            with st.container(border=True):
                tick, body = st.columns([0.07, 0.93])
                add = tick.checkbox("Add to the knowledge base", key=f"kb-add-{n}-{c['key']}", label_visibility="collapsed")
                flag = ("  \n:orange[You contradicted the system on this claim, so a longer note is needed.]" if c["disagreed"]
                        else "  \n:orange[Blocked before; you may try again.]" if c["status"] == "blocked" else "")
                body.markdown(f"**{c['finding']}**  ·  claim `{c['claim_id']}`  ·  your decision: {band_text(c['final_band'])}{flag}")
                body.write(c["text"])
                note = body.text_input("Note (required): what should the system remember from this?", key=f"kb-note-{n}-{c['key']}", disabled=not add,
                                       placeholder="At least 15 characters (50 if you contradicted the system)")
                if add:
                    picks.append({"key": c["key"], "note": note})
        if len(offered) > 50:
            st.caption(f"Showing the 50 newest of {len(offered)} findings.")
        st.caption(f"{len(picks)} ticked. Area and cover findings are not offered: they are recalculated from the data for every claim, so there is nothing to learn.")
        if st.button("Update knowledge base", type="primary", disabled=not picks or DEMO):
            try:
                saved = submit_signals(store, st.session_state.get("reviewer", ""), picks)
            except KnowledgeError as exc:
                st.error(str(exc))
            else:
                st.session_state["kb_results"], st.session_state["kb_n"] = run_safety_check(store, saved), n + 1
                st.rerun()
    st.subheader("Already proposed")
    rows = signal_rows(store)
    if not rows:
        st.caption("No signals proposed yet.")
        return
    waiting = [r["signal_id"] for r in rows if r["status"] == "pending"]
    if waiting and st.button(f"Run the safety check again on {len(waiting)} waiting signal(s)", disabled=DEMO):
        st.session_state["kb_results"] = run_safety_check(store, waiting)
        st.rerun()
    st.dataframe(pd.DataFrame([{
        "Status": SIGNAL_STATUS.get(r["status"], r["status"]), "Finding": r["finding"], "What was seen": r["plain_text"], "Claim": r["source_claim_id"],
        "Your note": r["note"], "By": r["reviewer"], "Proposed": r["created_at"], "Audit tag": "Contradicted the system" if r.get("disagreed") else "",
        "Safety check": GateResult.from_json(r["gate_result"]).get("summary", "-"),
    } for r in rows]), hide_index=True, width="stretch")
    for r in rows:
        detail = GateResult.from_json(r["gate_result"])
        if detail.get("checks"):
            with st.expander(f"Safety check details: {r['finding']} ({r['source_claim_id']}) · {SIGNAL_STATUS.get(r['status'], r['status'])}"):
                for c in detail["checks"]:
                    mark = "✅" if c["ok"] else "❌"
                    st.markdown(f"- {mark} **{c['check']}**: {c['detail']}")


def run_safety_check(store: Store, signal_ids: list[str]) -> list[dict]:
    """Run the safety check on the given pending signals and return one display-ready dict per signal."""
    from claim_triage.learning.process import process_pending

    with st.spinner("Running the safety check on the test searches..."):
        results = process_pending(store, load_config(), only=signal_ids)
    names = {r["signal_id"]: r for r in signal_rows(store)}
    return [dict(r, finding=names[r["signal_id"]]["finding"], claim_id=names[r["signal_id"]]["source_claim_id"]) for r in results]


# ------------------------------------------------------------------ evaluation
def _pct(x: float | None) -> float | None:
    return None if x is None else round(100 * x, 1)


def eval_page(store: Store) -> None:
    st.header("Evaluation")
    st.warning("**Read this first.** Everything below comes from tests on made-up data. The AI agents were run on every dev and held-out case with the small model, and the two "
               "agree. **On the original clean data the plain rules beat the agents (99% to 99.5% against 91.5% to 93%).** The agents win only on the hard cases, which were "
               "designed to need reading text and looking up a hospital's record. The larger model closes most of the gap on the two weakest scenarios, at about 18 times the cost per claim. "
               "Each line says what it does and does not show.")
    runs = load_runs()
    if not runs:
        st.info("No result files found in the eval folder yet. Run the evaluation harness first.")
        return

    st.subheader("Runs so far")
    table = pd.DataFrame([{
        "Run": r["label"], "System": r["system"], "Cases": r["metrics"]["cases"], "Right band (%)": _pct(r["metrics"]["right_band"]),
        "Fraud called green (%)": _pct(r["metrics"]["fraud_called_green"]), "Honest called red (%)": _pct(r["metrics"]["honest_called_red"]),
        "Cost ($)": r["metrics"]["cost_total"], "What it tells us": r["note"],
    } for r in runs])
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Right band (%)": st.column_config.ProgressColumn("Right band (%)", min_value=0, max_value=100, format="%.1f"),
        "Run": st.column_config.TextColumn(width="large"), "What it tells us": st.column_config.TextColumn(width="large"),
        "Cost ($)": st.column_config.NumberColumn(format="%.2f")})
    st.caption("Right band: the system's band (red, review needed, green) matched what the test expected. Fraud called green is the number that matters most: it should be near 0. "
               "Honest called red is the cost of over-flagging. Agent runs were made on earlier builds of the data (the original 300 claims are unchanged).")
    st.bar_chart(table.set_index("Run")["Right band (%)"], horizontal=True, height=60 + 38 * len(table))

    st.subheader("The hard cases: what each setup can and cannot do")
    by_key = {r["key"]: r for r in runs}
    setups = [("Rules only", "baseline_hard:dev", "baseline_hard:heldout"), ("3 AI agents, no retrieval", "haiku_none_hard:dev", "haiku_none_hard_heldout:heldout"),
              ("3 AI agents with retrieval", "haiku_rerank_hard:dev", "haiku_rerank_hard_heldout:heldout")]
    rows = []
    for name, dev_key, held_key in setups:
        row = {"Setup": name}
        for label, key in (("dev", dev_key), ("held-out", held_key)):
            sc = {s["scenario"]: s for s in per_scenario(by_key[key]["rows"])} if key in by_key else {}
            for sid, what in (("D1", "big bill, reason in the text"), ("D2", "slightly high bill, bad hospital record")):
                row[f"{what} ({label})"] = f"{sc[sid]['right']} of {sc[sid]['cases']}" if sid in sc else "-"
        rows.append(row)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("The first kind can only be solved by reading the discharge summary; the second only by looking up the hospital's record of confirmed frauds. "
                   "These cases were designed to need exactly those abilities, so they show what the abilities are worth, not how often they matter in real claims.")

    st.subheader("Look closer at one run")
    pick = st.selectbox("Run", [r["key"] for r in runs], format_func=lambda k: by_key[k]["label"])
    chosen = by_key[pick]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**What the test expected (rows) vs what the system said (columns)**")
        grid = confusion(chosen["rows"])
        st.dataframe(pd.DataFrame([[grid[e][p] for p in EVAL_BANDS] for e in EVAL_BANDS], index=[f"expected {b}" for b in EVAL_BANDS], columns=list(EVAL_BANDS)), width="stretch")
    with c2:
        m = chosen["metrics"]
        st.markdown("**Totals**")
        facts = [("Cases", m["cases"]), ("Right band", f"{_pct(m['right_band'])}%"), ("Fraud flagged (red or review)", f"{_pct(m['fraud_flagged'])}%" if m["fraud_flagged"] is not None else "-"),
                 ("Claims the system could not assess", m["unavailable"]),
                 ("Reasons that fell back to the original sentences", f"{m['explainer_fallbacks']} of {m['explainer_runs']}" if m["explainer_runs"] else "-"), ("Cost per case", f"${m['cost_per_case']}" if m["cost_per_case"] else "none (no AI)"),
                 ("Median time per claim", f"{m['seconds_median']} s" if m["seconds_median"] else "instant")]
        facts_table(facts)
        if m["explainer_runs"]:
            st.caption("The Explainer only rewrites the wording; it never changes a score or band, and a failed rewrite keeps the original sentences. Every saved run here used a token "
                       "limit (900) that the small model often used up thinking, so it answered nothing. The limit is now 2500; re-run to see the lower number.")
    st.dataframe(pd.DataFrame([{"Scenario": s["name"], "Cases": s["cases"], "Right band": s["right"], "Fraud called green": s["fraud_called_green"], "Honest called red": s["honest_called_red"]}
                               for s in per_scenario(chosen["rows"])]), hide_index=True, width="stretch")

    baseline_rows = [row for r in runs if r["prefix"] == "baseline" for row in r["rows"]]
    if baseline_rows:
        st.subheader("Fairness check (rules only)")
        st.dataframe(pd.DataFrame([{"Group of honest claims": f["slice"], "Claims": f["honest_claims"], "Called red (%)": _pct(f["called_red"]), "Sent to review (%)": _pct(f["sent_to_review"])}
                                   for f in fairness(baseline_rows)]), hide_index=True, width="stretch")
        st.caption("Honest claims should not be treated differently by gender. An honest claim from a high-fraud area is sent to review by policy, so that group is expected to differ. "
                   "This check has not been run for the agents yet.")

    st.subheader("What the knowledge base looks like now")
    counts = {r["status"]: r["n"] for r in store.rows("SELECT status, count(*) AS n FROM signals GROUP BY status")}
    k = st.columns(4)
    k[0].metric("Pattern cards and clauses", sum(knowledge_counts().values()))
    k[1].metric("Reviewer signals live", counts.get("active", 0))
    k[2].metric("Waiting for the safety check", counts.get("pending", 0))
    k[3].metric("Blocked by the safety check", counts.get("blocked", 0))

    st.subheader("Not measured yet")
    for item in NOT_MEASURED:
        st.markdown(f"- {item}")


# ------------------------------------------------------------------ shell
def main() -> None:
    cfg = load_config()
    # lets a test or a demo use its own copy of the app database; the public demo gives each visitor's session a private copy
    override = st.session_state.get("app_db_path") or os.environ.get("CLAIM_TRIAGE_APP_DB")
    store = Store(cfg, path=Path(override) if override else None)
    try:
        st.session_state.setdefault("page", PAGES[0])
        with st.sidebar:
            st.title("\U0001F6E1️ Claim triage")
            st.radio("Go to", PAGES, key="page")
            st.text_input("Reviewer", value="reviewer-1", key="reviewer")
            view = st.radio("Assessment shown", list(SYSTEM_CHOICES), help="Rules-only covers every claim and costs nothing to produce. Agent runs are saved samples.")
            st.caption("Customers are shown by ID only. A green band is not an approval: a reviewer decides every claim.")
            st.caption(f"Stored assessments: {json.dumps(store.count_assessments())}")
            if DEMO:
                st.info("Public demo with synthetic data only. Decisions you save are kept only until the app restarts.")
        system = SYSTEM_CHOICES[view]
        if st.session_state["page"] == "Review queue":
            st.header("Review queue")
            queue_page(cfg, store, system)
        elif st.session_state["page"] == "Claim":
            claim_page(cfg, store, system)
        elif st.session_state["page"] == "Knowledge base":
            kb_page(store)
        else:
            eval_page(store)
    finally:
        store.close()


main()
